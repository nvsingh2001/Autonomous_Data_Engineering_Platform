import os
import json
from config import LANGSMITH_TRACING, LANGCHAIN_TRACING_V2, LANGSMITH_API_KEY

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
    which passes AWS's raw camelCase dict (inputTokens/outputTokens) straight through."""
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
            print(
                "[Telemetry] WARNING: LANGSMITH_TRACING=true but LANGSMITH_API_KEY is not set — tracing disabled."
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
            import opentelemetry.instrumentation.crewai.utils as otel_utils

            _patch_llm_usage_capture()

            def is_tool_call_list(obj):
                if not isinstance(obj, list) or len(obj) == 0:
                    return False
                first = obj[0]
                if hasattr(first, "function") or (
                    isinstance(first, dict) and "function" in first
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
                elif isinstance(tc, dict):
                    fn = tc.get("function") or {}
                    name = fn.get("name", "")
                    raw_args = fn.get("arguments", None) or "{}"
                    tc_id = tc.get("id", "")
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
            otel_utils._response_to_otel_output = patched_response_to_otel_output
            print(
                "[Telemetry] Tracing patches applied for LLM tool calls and token usages."
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
            print(f"[Telemetry] Native LLM providers wrapped for tracing: {wrapped}")

            print(
                "[Telemetry] LangSmith OpenTelemetry instrumentation initialized successfully."
            )
        except Exception as e:
            print(
                f"[Telemetry] Warning: Failed to import or instrument OpenTelemetry/LangSmith tracing: {e}"
            )
    else:
        os.environ["OTEL_SDK_DISABLED"] = "true"
        print(
            "[Telemetry] Tracing is disabled (no LANGSMITH_TRACING or LANGCHAIN_TRACING_V2 env variables)."
        )
