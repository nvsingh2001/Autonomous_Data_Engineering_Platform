from abc import ABC, abstractmethod
from crewai import LLM
from config import LLM_TIMEOUT_SECONDS, LLM_MAX_RETRIES

# Watchdog for the OpenAI-SDK-backed providers (Ollama + cloud): crewai's native
# completion classes hand these straight to the SDK client, which enforces them
# per request. Bedrock is absent on purpose — its provider ignores extra kwargs
# and already builds its boto client with read_timeout=300 and 3 adaptive retries.
LLM_RESILIENCE_KWARGS: dict = {
    "timeout": LLM_TIMEOUT_SECONDS,
    "max_retries": LLM_MAX_RETRIES,
}


class LLMProvider(ABC):
    @abstractmethod
    def create(self, temperature: float) -> LLM: ...


class OllamaProvider(LLMProvider):
    def __init__(self, model_name: str, base_url: str, api_key: str | None = None):
        self._model_name = model_name
        self._base_url = base_url
        self._api_key = api_key

    def create(self, temperature: float) -> LLM:
        kwargs: dict = {
            "model": self._model_name,
            "temperature": temperature,
            "base_url": self._base_url,
            "extra_body": {"options": {"num_ctx": 8192}},
            **LLM_RESILIENCE_KWARGS,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        return LLM(**kwargs)


class BedrockProvider(LLMProvider):
    def __init__(self, model_name: str, region: str | None = None):
        self._model_name = model_name
        self._region = region

    def create(self, temperature: float) -> LLM:
        kwargs: dict = {
            "model": self._model_name,
            "temperature": temperature,
            "drop_params": True,
        }
        if self._region:
            # crewai's BedrockCompletion reads a `region_name` field (not
            # `aws_region_name`) — pass it directly instead of mutating the
            # process-wide AWS_DEFAULT_REGION env var, which would race
            # concurrent SQL/BI providers configured for different regions.
            kwargs["region_name"] = self._region
        llm = LLM(**kwargs)
        NO_STOP_SEQ = ("nemotron", "qwen", "kimi", "mistral", "deepseek", "grok", "glm")
        NO_NATIVE_FC = ("nemotron", "kimi", "mistral")
        model_lower = self._model_name.lower()
        if any(m in model_lower for m in NO_STOP_SEQ) and hasattr(
            llm, "_get_inference_config"
        ):
            original = llm._get_inference_config
            llm._get_inference_config = lambda: {
                k: v for k, v in original().items() if k != "stopSequences"
            }
        if any(m in model_lower for m in NO_NATIVE_FC):
            llm.supports_function_calling = lambda: False
        return llm


class CloudProvider(LLMProvider):
    def __init__(self, model_name: str):
        self._model_name = model_name

    def create(self, temperature: float) -> LLM:
        return LLM(
            model=self._model_name, temperature=temperature, **LLM_RESILIENCE_KWARGS
        )
