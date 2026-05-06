"""VLM provider registry for pdf-smart-ocr.

Each provider is a class that takes a single page image (bytes, PNG/JPEG)
and returns Markdown text.
"""
from .base import VLMProvider, OcrError
from .mistral import MistralProvider
from .siliconflow import SiliconFlowProvider
from .deepinfra import DeepInfraProvider
from .openrouter import OpenRouterProvider


PROVIDERS: dict[str, type[VLMProvider]] = {
    "siliconflow": SiliconFlowProvider,  # default: free PaddleOCR-VL-1.5
    "mistral": MistralProvider,
    "deepinfra": DeepInfraProvider,
    "openrouter": OpenRouterProvider,
}


def get_provider(name: str) -> VLMProvider:
    name = name.lower()
    if name not in PROVIDERS:
        raise OcrError(
            f"Unknown VLM provider: {name!r}. "
            f"Available: {', '.join(PROVIDERS.keys())}"
        )
    return PROVIDERS[name]()


__all__ = ["VLMProvider", "OcrError", "PROVIDERS", "get_provider"]
