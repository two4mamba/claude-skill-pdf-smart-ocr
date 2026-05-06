"""OpenRouter provider — Qwen2.5-VL-72B-Instruct by default.

Note: Qwen2.5-72B-Instruct (without -VL) is text-only and CANNOT do OCR.
The default model below is the vision variant.
"""
from .base import VLMProvider


class OpenRouterProvider(VLMProvider):
    name = "openrouter"
    default_model = "qwen/qwen2.5-vl-72b-instruct"
    env_var = "OPENROUTER_API_KEY"
    endpoint = "https://openrouter.ai/api/v1/chat/completions"

    def ocr_image(self, image_bytes, *, model=None, lang="ch"):
        return self._chat_completion_image(
            self.endpoint,
            model or self.default_model,
            image_bytes,
            extra_headers={
                # OpenRouter recommends these for routing & analytics
                "HTTP-Referer": "https://github.com/two4mamba/claude-skill-pdf-smart-ocr",
                "X-Title": "pdf-smart-ocr",
            },
        )
