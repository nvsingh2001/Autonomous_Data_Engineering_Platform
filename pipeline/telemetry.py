import os
import json

def setup_telemetry():
    """Sets up OpenTelemetry tracing and LangSmith integration if configured in the environment."""
    os.environ["CREWAI_TELEMETRY_OPT_OUT"] = "true"
    
    tracing_enabled = os.environ.get("LANGSMITH_TRACING") == "true" or os.environ.get("LANGCHAIN_TRACING_V2") == "true"

    if tracing_enabled:
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

            # 1. Patch to capture token usage on LLM instance (since crewai v1.14 does not set last_token_usage)
            original_emit = BaseLLM._emit_call_completed_event

            def patched_emit(self, *args, **kwargs):
                usage = kwargs.get("usage")
                if usage is None and len(args) >= 6:
                    usage = args[5]
                    
                if usage and isinstance(usage, dict):
                    class TokenUsage:
                        def __init__(self, prompt, completion):
                            self.prompt_tokens = prompt
                            self.completion_tokens = completion
                    
                    prompt = usage.get("prompt_tokens") or usage.get("prompt_token_count") or usage.get("input_tokens") or 0
                    completion = usage.get("completion_tokens") or usage.get("candidates_token_count") or usage.get("output_tokens") or 0
                    self.last_token_usage = TokenUsage(prompt, completion)
                else:
                    self.last_token_usage = None
                    
                return original_emit(self, *args, **kwargs)

            BaseLLM._emit_call_completed_event = patched_emit

            # 2. Patch to serialize LLM tool calls correctly (so they display as tool call nodes in LangSmith)
            def is_tool_call_list(obj):
                if not isinstance(obj, list) or len(obj) == 0:
                    return False
                first = obj[0]
                if hasattr(first, "function") or (isinstance(first, dict) and "function" in first):
                    return True
                return False

            def convert_response_tool_call(tc):
                if hasattr(tc, "function"):
                    fn = tc.function
                    name = getattr(fn, "name", "")
                    raw_args = getattr(fn, "arguments", "{}")
                    tc_id = getattr(tc, "id", "")
                else:
                    fn = tc.get("function") or {}
                    name = fn.get("name", "")
                    raw_args = fn.get("arguments", "{}")
                    tc_id = tc.get("id", "")
                    
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception:
                    args = raw_args
                    
                return {
                    "type": "tool_call",
                    "id": tc_id,
                    "name": name,
                    "arguments": args
                }

            def patched_response_to_otel_output(response) -> str | None:
                if response is None:
                    return None
                if is_tool_call_list(response):
                    parts = [convert_response_tool_call(tc) for tc in response]
                    return json.dumps([{
                        "role": "assistant",
                        "parts": parts
                    }])
                text = str(response)
                if not text:
                    return None
                return json.dumps([{
                    "role": "assistant",
                    "parts": [{"type": "text", "content": text}]
                }])

            otel_inst._response_to_otel_output = patched_response_to_otel_output
            otel_utils._response_to_otel_output = patched_response_to_otel_output
            print("[Telemetry] Tracing patches applied for LLM tool calls and token usages.")

            current_provider = trace.get_tracer_provider()
            if isinstance(current_provider, TracerProvider):
                tracer_provider = current_provider
            else:
                tracer_provider = TracerProvider()
                trace.set_tracer_provider(tracer_provider)
            
            tracer_provider.add_span_processor(OtelSpanProcessor())
            CrewAIInstrumentor().instrument(tracer_provider=tracer_provider)
            OpenAIInstrumentor().instrument(tracer_provider=tracer_provider)
            print("[Telemetry] LangSmith OpenTelemetry instrumentation initialized successfully.")
        except Exception as e:
            print(f"[Telemetry] Warning: Failed to import or instrument OpenTelemetry/LangSmith tracing: {e}")
    else:
        os.environ["OTEL_SDK_DISABLED"] = "true"
        print("[Telemetry] Tracing is disabled (no LANGSMITH_TRACING or LANGCHAIN_TRACING_V2 env variables).")
