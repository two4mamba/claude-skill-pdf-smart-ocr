"""DeepInfra provider — DeepSeek-OCR (~$0.03 in / $0.10 out per M tokens)."""
from .base import VLMProvider


class DeepInfraProvider(VLMProvider):
    name = "deepinfra"
    default_model = "deepseek-ai/DeepSeek-OCR"
    env_var = "DEEPINFRA_API_KEY"
    endpoint = "https://api.deepinfra.com/v1/openai/chat/completions"

    def ocr_image(self, image_bytes, *, model=None, lang="ch"):
        return self._chat_completion_image(
            self.endpoint,
            model or self.default_model,
            image_bytes,
        )
