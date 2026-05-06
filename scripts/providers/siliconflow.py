"""SiliconFlow provider — PaddleOCR-VL-1.5 (free tier as of 2026-05)."""
from .base import VLMProvider


class SiliconFlowProvider(VLMProvider):
    name = "siliconflow"
    default_model = "PaddlePaddle/PaddleOCR-VL-1.5"
    env_var = "SILICONFLOW_API_KEY"
    endpoint = "https://api.siliconflow.cn/v1/chat/completions"

    def ocr_image(self, image_bytes, *, model=None, lang="ch"):
        md = self._chat_completion_image(
            self.endpoint,
            model or self.default_model,
            image_bytes,
        )
        return md, {}
