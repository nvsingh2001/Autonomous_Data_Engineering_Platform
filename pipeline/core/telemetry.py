import os
import json
from config import LANGSMITH_TRACING, LANGCHAIN_TRACING_V2, LANGSMITH_API_KEY
from logging_setup import get_logger

_LOG = get_logger("Telemetry")

# Set once by setup_telemetry(); consumed by set_thread().
_initialized = False
_span_processor = None


def new_thread_id(prefix: str) -> str:
    """A fresh LangSmith thread id, e.g. 'pipeline-019f21c5-…' (uuid7 sorts by time)."""
    try:
        from langsmith import uuid7

        return f"{prefix}-{uuid7()}"
    except Exception:
        import uuid

        return f"{prefix}-{uuid.uuid4()}"


def set_thread(thread_id: str | None) -> None:
    """Group all spans started from now on into one LangSmith thread.

    LangSmith's threads view groups traces whose runs carry a `thread_id` metadata
    key — and it must be on EVERY run in the trace, not just the root, or token/cost
    aggregation breaks. OtelSpanProcessor.set_metadata stamps it on each span at
    on_start, which is exactly that. Pass None to stop tagging (end of the scope).
    No-op when tracing is disabled."""
    if _span_processor is None:
        return
    _span_processor.set_metadata({"thread_id": thread_id} if thread_id else {})


class _TokenUsage:
    """Minimal token usage carrier set on BaseLLM instances for OTel instrumentation."""

    def __init__(self, prompt: int, completion: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion


def _patch_llm_usage_capture() -> None:
    """Patch BaseLLM._emit_call_completed_event to stash the call's token usage on the
    instance as `last_token_usage`, where the OTel instrumentor's `_set_response_attributes`
    reads it to set the span's input/output token attributes. Key aliases cover every
    provider this project can use: openai-style (prompt_tokens/completion_tokens),
    anthropic/gemini-style (input_tokens, prompt_token_count), and Bedrock Converse,
    which passes AWS's raw camelCase dict (inputTokens/outputTokens) straight through.

    Why a monkeypatch and not a public API: the OTel span wrapper reads
    `last_token_usage` synchronously, right after `call()` returns, so usage must be
    stashed before `call()` finishes. crewai's supported surfaces can't do that:
    `crewai_event_bus.on(LLMCallCompletedEvent)` runs handlers in a ThreadPoolExecutor
    (emit() returns a Future nobody awaits — a listener races the span reader), and the
    1.15 `after_llm_call` hook context carries no usage field at all. Re-evaluate if
    either ever offers synchronous delivery with usage attached."""
    from crewai.llms.base_llm import BaseLLM

    original_emit = BaseLLM._emit_call_completed_event

    def patched_emit(self, *args, **kwargs):
        usage = kwargs.get("usage")
        if usage is None and len(args) >= 6:
            usage = args[5]

        if isinstance(usage, dict):
            # Use `is not None` so a legitimate 0 token count is preserved
            # and not shadowed by a subsequent key's non-zero value.
            prompt = next(
                (
                    usage[k]
                    for k in (
                        "prompt_tokens",
                        "prompt_token_count",
                        "input_tokens",
                        "inputTokens",
                    )
                    if usage.get(k) is not None
                ),
                0,
            )
            completion = next(
                (
                    usage[k]
                    for k in (
                        "completion_tokens",
                        "candidates_token_count",
                        "output_tokens",
                        "outputTokens",
                    )
                    if usage.get(k) is not None
                ),
                0,
            )
            self.last_token_usage = _TokenUsage(prompt, completion)
        else:
            self.last_token_usage = None

        return original_emit(self, *args, **kwargs)

    BaseLLM._emit_call_completed_event = patched_emit


def _wrap_native_llm_calls(tracer) -> list[str]:
    """CrewAIInstrumentor only wraps the legacy `crewai.llm.LLM.call`, but crewai>=1.x's
    `LLM()` factory returns native provider classes (BedrockCompletion, OpenAICompletion,
    ...) that subclass BaseLLM directly and define their own `call` — so the instrumentor's
    LLM spans (inputs/outputs/token usage) never fire for them. Wrap each native class's
    own `call` with the instrumentor's wrap_llm_call so every provider is traced.
    Returns the list of wrapped class names."""
    import importlib
    import pkgutil

    from wrapt import wrap_function_wrapper
    from opentelemetry.instrumentation.crewai import instrumentation as otel_inst
    from crewai.llms.base_llm import BaseLLM
    import crewai.llms.providers as providers_pkg

    # Histograms None mirrors the instrumentor's own metrics-disabled path.
    wrapper = otel_inst.wrap_llm_call(tracer, None, None)
    wrapped: set[type] = set()
    for m in pkgutil.iter_modules(providers_pkg.__path__):
        try:
            mod = importlib.import_module(f"crewai.llms.providers.{m.name}.completion")
        except Exception:
            continue  # provider SDK not installed
        for name in dir(mod):
            obj = getattr(mod, name)
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseLLM)
                and obj is not BaseLLM  # skip the abstract base re-exported everywhere
                and "call" in obj.__dict__  # only the defining class, not re-exports
                and obj not in wrapped
            ):
                wrap_function_wrapper(obj.__module__, f"{obj.__qualname__}.call", wrapper)
                wrapped.add(obj)
    return sorted(c.__name__ for c in wrapped)


def _extract_executor_tool_call(args, kwargs):
    """(name, args) from CrewAgentExecutor._execute_single_native_tool_call —
    keyword-only signature: (*, call_id, func_name, func_args, ...)."""
    return kwargs.get("func_name", "tool"), kwargs.get("func_args")


def _extract_llm_tool_call(args, kwargs):
    """(name, args) from BaseLLM._handle_tool_execution —
    (function_name, function_args, available_functions, from_task, from_agent)."""
    name = kwargs.get("function_name") or (args[0] if args else "tool")
    tool_args = kwargs.get("function_args", args[1] if len(args) > 1 else None)
    return name, tool_args


def _extract_react_tool_call(args, kwargs):
    """(name, args) from ToolUsage._use — (tool_string, tool, calling), called
    with keywords from ToolUsage.use()."""
    tool = kwargs.get("tool") or (args[1] if len(args) > 1 else None)
    calling = kwargs.get("calling") or (args[2] if len(args) > 2 else None)
    return getattr(tool, "name", "tool"), getattr(calling, "arguments", None)


def _extract_step_tool_call(args, kwargs):
    """(name, args) from agent_utils.execute_single_native_tool_call — first
    positional arg is the raw provider-shaped tool_call: OpenAI object/dict with
    "function", Bedrock Converse {"toolUseId", "name", "input"}, or Anthropic
    tool_use block."""
    tc = args[0] if args else kwargs.get("tool_call")
    fn = getattr(tc, "function", None)
    if fn is not None:
        return getattr(fn, "name", "tool"), _parse_tool_args(getattr(fn, "arguments", None))
    if isinstance(tc, dict):
        if "function" in tc:
            fn = tc.get("function") or {}
            return fn.get("name", "tool"), _parse_tool_args(fn.get("arguments"))
        if "name" in tc:
            return tc["name"], tc.get("input")
    if getattr(tc, "type", None) == "tool_use":
        return getattr(tc, "name", "tool"), getattr(tc, "input", None)
    return "tool", None


def _parse_tool_args(raw):
    """OpenAI-style arguments arrive as a JSON string; parse so the span input
    attribute holds a dict, not a double-encoded string."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return raw
    return raw


# Every synchronous tool-execution choke point in crewai 1.x. Async paths
# (_ause, aexecute_tool_and_check_finality) are not listed; the pipeline runs
# crews synchronously.
_TOOL_EXECUTION_TARGETS = (
    # Native function-calling loop: the executor calls llm.call(available_functions=None)
    # and runs tools itself. Wrapping here (not BaseTool.run) also captures cache hits
    # and hook-blocked calls, which never reach the tool.
    (
        "crewai.agents.crew_agent_executor",
        "CrewAgentExecutor._execute_single_native_tool_call",
        _extract_executor_tool_call,
    ),
    # Modern agents (crewai.agent.core → experimental AgentExecutor → StepExecutor)
    # execute tools via the module-level agent_utils.execute_single_native_tool_call.
    # step_executor from-imports it, so its namespace copy must be patched too —
    # wrapping only agent_utils would miss the reference step_executor already holds.
    (
        "crewai.agents.step_executor",
        "execute_single_native_tool_call",
        _extract_step_tool_call,
    ),
    (
        "crewai.utilities.agent_utils",
        "execute_single_native_tool_call",
        _extract_step_tool_call,
    ),
    # Provider-internal execution when llm.call() is handed available_functions directly.
    ("crewai.llms.base_llm", "BaseLLM._handle_tool_execution", _extract_llm_tool_call),
    # Legacy text/ReAct tool path.
    ("crewai.tools.tool_usage", "ToolUsage._use", _extract_react_tool_call),
)


def _set_tool_io_attributes(span, tool_args, result) -> None:
    """LangSmith's OTLP ingestion maps traceloop.entity.input/output to run
    inputs/outputs — the same mapping the instrumentor's task spans rely on."""
    if tool_args is not None:
        try:
            span.set_attribute("traceloop.entity.input", json.dumps(tool_args, default=str))
        except Exception:
            span.set_attribute("traceloop.entity.input", str(tool_args))
    # CrewAgentExecutor returns {"result": ..., "from_cache": ...}, StepExecutor's
    # path returns a NativeToolCallResult with .result; the rest return the string.
    if isinstance(result, dict):
        output = result.get("result")
    else:
        output = getattr(result, "result", result)
    if output is not None:
        span.set_attribute("traceloop.entity.output", str(output))


def _tool_span_wrapper(tracer, extract_call):
    """A wrapt wrapper that runs the wrapped tool execution inside a span that
    LangSmith ingests as a run_type=tool run."""
    from opentelemetry.trace import SpanKind, Status, StatusCode

    def wrapper(wrapped, instance, args, kwargs):
        try:
            tool_name, tool_args = extract_call(args, kwargs)
        except Exception:
            tool_name, tool_args = "tool", None
        with tracer.start_as_current_span(
            f"{tool_name}.tool",
            kind=SpanKind.INTERNAL,
            attributes={
                "traceloop.span.kind": "tool",
                "langsmith.span.kind": "tool",
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": tool_name,
                "traceloop.entity.name": tool_name,
            },
        ) as span:
            try:
                result = wrapped(*args, **kwargs)
            except Exception as ex:
                span.set_status(Status(StatusCode.ERROR, str(ex)))
                raise
            _set_tool_io_attributes(span, tool_args, result)
            span.set_status(Status(StatusCode.OK))
            return result

    return wrapper


def _wrap_tool_runs(tracer) -> None:
    """opentelemetry-instrumentation-crewai creates no tool spans at all — it only wraps
    Crew.kickoff / Agent.execute_task / Task.execute_sync / LLM.call — so tool executions
    (inputs, outputs, latency, errors) never reach LangSmith. Wrap each tool-execution
    choke point (see _TOOL_EXECUTION_TARGETS) in a tool span."""
    from wrapt import wrap_function_wrapper

    for module, method, extract_call in _TOOL_EXECUTION_TARGETS:
        wrap_function_wrapper(module, method, _tool_span_wrapper(tracer, extract_call))


def setup_telemetry():
    """Sets up OpenTelemetry tracing and LangSmith integration if configured in the environment.

    Idempotent — both the web server (at startup, so pre-run intent chats are traced)
    and crew.py (at import, for CLI mode) call this; only the first call instruments."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"

    tracing_enabled = LANGSMITH_TRACING or LANGCHAIN_TRACING_V2

    if tracing_enabled:
        if not LANGSMITH_API_KEY:
            _LOG.warning(
                "LANGSMITH_TRACING=true but LANGSMITH_API_KEY is not set — tracing disabled."
            )
            os.environ["OTEL_SDK_DISABLED"] = "true"
            return

        os.environ["OTEL_SDK_DISABLED"] = "false"
        try:
            from langsmith.integrations.otel import OtelSpanProcessor
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.instrumentation.crewai import CrewAIInstrumentor
            from opentelemetry.instrumentation.openai import OpenAIInstrumentor
            import opentelemetry.instrumentation.crewai.instrumentation as otel_inst

            try:
                import opentelemetry.instrumentation.crewai.utils as otel_utils
            except ImportError as e:
                _LOG.warning(
                    f"could not import crewai instrumentation "
                    f"utils module ({e}) — continuing without that patch."
                )
                otel_utils = None

            _patch_llm_usage_capture()

            def is_tool_call_list(obj):
                if not isinstance(obj, list) or len(obj) == 0:
                    return False
                first = obj[0]
                # OpenAI-style: object with .function / dict with "function" key
                if hasattr(first, "function") or (
                    isinstance(first, dict) and "function" in first
                ):
                    return True
                # Anthropic-style content block: .type == "tool_use"
                if getattr(first, "type", None) == "tool_use":
                    return True
                # Bedrock Converse toolUse dict ({"toolUseId", "name", "input"}) or
                # Anthropic tool_use dict ({"type": "tool_use", "id", "name", "input"})
                if isinstance(first, dict) and "name" in first:
                    if (
                        "toolUseId" in first
                        or "input" in first
                        or first.get("type") == "tool_use"
                    ):
                        return True
                return False

            def convert_response_tool_call(tc):
                if hasattr(tc, "function"):
                    fn = tc.function
                    name = getattr(fn, "name", "")
                    # getattr default only fires when the attribute is absent, not when it is
                    # None — use `or "{}"` to also handle an explicit null arguments field.
                    raw_args = getattr(fn, "arguments", None) or "{}"
                    tc_id = getattr(tc, "id", "")
                elif getattr(tc, "type", None) == "tool_use":
                    # Anthropic ToolUseBlock object: .id / .name / .input (already a dict)
                    name = getattr(tc, "name", "")
                    raw_args = getattr(tc, "input", None) or {}
                    tc_id = getattr(tc, "id", "")
                elif isinstance(tc, dict) and "function" in tc:
                    fn = tc.get("function") or {}
                    name = fn.get("name", "")
                    raw_args = fn.get("arguments", None) or "{}"
                    tc_id = tc.get("id", "")
                elif isinstance(tc, dict) and "name" in tc:
                    # Bedrock Converse toolUse / Anthropic tool_use dict — "input" is
                    # already a parsed dict, and Bedrock's id key is "toolUseId".
                    name = tc.get("name", "")
                    raw_args = tc.get("input", None) or {}
                    tc_id = tc.get("toolUseId") or tc.get("id", "")
                else:
                    return {"type": "tool_call", "id": "", "name": "", "arguments": {}}

                try:
                    args = (
                        json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    )
                except Exception:
                    args = raw_args

                return {
                    "type": "tool_call",
                    "id": tc_id,
                    "name": name,
                    "arguments": args,
                }

            def patched_response_to_otel_output(response) -> str | None:
                if response is None:
                    return None
                if is_tool_call_list(response):
                    parts = [convert_response_tool_call(tc) for tc in response]
                    return json.dumps([{"role": "assistant", "parts": parts}])
                text = str(response)
                if not text:
                    return None
                return json.dumps(
                    [
                        {
                            "role": "assistant",
                            "parts": [{"type": "text", "content": text}],
                        }
                    ]
                )

            otel_inst._response_to_otel_output = patched_response_to_otel_output
            if otel_utils is not None:
                otel_utils._response_to_otel_output = patched_response_to_otel_output
            _LOG.info(
                "Tracing patches applied for LLM tool calls and token usages."
            )

            current_provider = trace.get_tracer_provider()
            if isinstance(current_provider, TracerProvider):
                tracer_provider = current_provider
            else:
                tracer_provider = TracerProvider()
                trace.set_tracer_provider(tracer_provider)

            global _span_processor
            _span_processor = OtelSpanProcessor()
            tracer_provider.add_span_processor(_span_processor)
            CrewAIInstrumentor().instrument(tracer_provider=tracer_provider)
            OpenAIInstrumentor().instrument(tracer_provider=tracer_provider)

            # CrewAIInstrumentor only covers the legacy crewai.llm.LLM class; the
            # native provider classes the LLM() factory actually returns need their
            # own wrapping or no agent gets LLM spans (see _wrap_native_llm_calls).
            tracer = trace.get_tracer(
                otel_inst.__name__, tracer_provider=tracer_provider
            )
            wrapped = _wrap_native_llm_calls(tracer)
            _LOG.info(f"Native LLM providers wrapped for tracing: {wrapped}")

            _wrap_tool_runs(tracer)
            _LOG.info(
                "Tool executions wrapped for tracing "
                "(executor, provider-internal, and ReAct paths)."
            )

            _LOG.info(
                "LangSmith OpenTelemetry instrumentation initialized successfully."
            )
        except Exception as e:
            _LOG.warning(
                f"Failed to import or instrument OpenTelemetry/LangSmith tracing: {e}"
            )
    else:
        os.environ["OTEL_SDK_DISABLED"] = "true"
        _LOG.info(
            "Tracing is disabled (no LANGSMITH_TRACING or LANGCHAIN_TRACING_V2 env variables)."
        )
