"""Cloud OCR provider registry for pdf-smart-ocr.

Each provider is a class that takes a single page image (bytes, PNG/JPEG)
and returns Markdown text. PDF-native providers (e.g. Baidu) additionally
implement `ocr_pdf()` which the dispatcher prefers when available.
"""
from .base import VLMProvider, OcrError
from .mistral import MistralProvider
from .baidu import BaiduProvider
from .siliconflow import SiliconFlowProvider
from .deepinfra import DeepInfraProvider
from .openrouter import OpenRouterProvider


PROVIDERS: dict[str, type[VLMProvider]] = {
    # default: mistral — fastest cloud OCR with reliable layout fidelity.
    # Free tier (1 RPS, 1B tokens/month, no credit card) covers most personal use.
    "mistral": MistralProvider,
    # baidu — official PaddleOCR-VL hosted by Baidu AI Studio.
    # First 1000 pages free; ¥0.18/page pay-as-you-go. Async PDF-native API.
    # Returns rich layout JSON (bbox, types) → enables future docx/pdf export
    # with original layout preservation.
    "baidu": BaiduProvider,
    # openrouter — generic VLM (Qwen2.5-VL-72B etc.), useful when also doing QA.
    "openrouter": OpenRouterProvider,
    # deepinfra — chat-compat fallback. PaddleOCR-VL 0.9B is deprecated;
    # only useful with DeepSeek-OCR or other models hosted there.
    "deepinfra": DeepInfraProvider,
    # siliconflow — DEPRECATED. Free PaddleOCR-VL endpoint, but hallucinated
    # 3/7 pages in our tests (infinite "1. 2. 3. ..." numbered lists) and runs
    # at ~100s/page. Kept for reference only; do NOT use as default.
    "siliconflow": SiliconFlowProvider,
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
