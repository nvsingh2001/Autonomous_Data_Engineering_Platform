import os
import json


class _TokenUsage:
    """Minimal token usage carrier set on BaseLLM instances for OTel instrumentation."""

    def __init__(self, prompt: int, completion: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion


def setup_telemetry():
    """Sets up OpenTelemetry tracing and LangSmith integration if configured in the environment."""
    os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"

    tracing_enabled = (
        os.environ.get("LANGSMITH_TRACING") == "true"
        or os.environ.get("LANGCHAIN_TRACING_V2") == "true"
    )

    if tracing_enabled:
        if not os.environ.get("LANGSMITH_API_KEY"):
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
                        (usage[k] for k in ("prompt_tokens", "prompt_token_count", "input_tokens")
                         if usage.get(k) is not None),
                        0,
                    )
                    completion = next(
                        (usage[k] for k in ("completion_tokens", "candidates_token_count", "output_tokens")
                         if usage.get(k) is not None),
                        0,
                    )
                    self.last_token_usage = _TokenUsage(prompt, completion)
                else:
                    self.last_token_usage = None

                return original_emit(self, *args, **kwargs)

            BaseLLM._emit_call_completed_event = patched_emit

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

            tracer_provider.add_span_processor(OtelSpanProcessor())
            CrewAIInstrumentor().instrument(tracer_provider=tracer_provider)
            OpenAIInstrumentor().instrument(tracer_provider=tracer_provider)

            try:
                import litellm

                if "langsmith" not in (litellm.success_callback or []):
                    litellm.success_callback = list(litellm.success_callback or []) + [
                        "langsmith"
                    ]
                if "langsmith" not in (litellm.failure_callback or []):
                    litellm.failure_callback = list(litellm.failure_callback or []) + [
                        "langsmith"
                    ]
                print(
                    "[Telemetry] LiteLLM LangSmith callback registered (Bedrock inputs/outputs/costs)."
                )
            except Exception as e:
                print(
                    f"[Telemetry] Warning: Failed to register LiteLLM LangSmith callback: {e}"
                )

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
