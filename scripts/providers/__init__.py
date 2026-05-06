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
    # default: mistral — fastest cloud OCR with reliable layout fidelity.
    # Free tier (1 RPS, 1B tokens/month, no credit card) covers most personal use.
    "mistral": MistralProvider,
    # siliconflow PaddleOCR-VL-1.5 is "free" but ~100s/page on free tier and
    # hallucinates on visually complex pages (PPT exports). Useful as backup
    # for clean text-only image PDFs.
    "siliconflow": SiliconFlowProvider,
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
