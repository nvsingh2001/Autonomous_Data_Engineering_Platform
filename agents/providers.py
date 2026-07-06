import os
from abc import ABC, abstractmethod
from crewai import LLM


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
            kwargs["aws_region_name"] = self._region
            os.environ["AWS_DEFAULT_REGION"] = self._region
            os.environ["AWS_REGION_NAME"] = self._region
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
        return LLM(model=self._model_name, temperature=temperature)
