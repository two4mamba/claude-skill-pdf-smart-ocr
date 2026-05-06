"""Mistral OCR provider — uses the dedicated /v1/ocr endpoint.

$2 / 1000 pages standard, $1 / 1000 pages with batch API. Free tier:
1 RPS, 500K TPM, 1B tokens/month, no credit card needed.

Unlike chat-completions providers, Mistral OCR has its own endpoint shape:
POST /v1/ocr  with  { model, document: { type: 'image_url', image_url: '...' } }
"""
from __future__ import annotations

import json

from .base import VLMProvider, OcrError


class MistralProvider(VLMProvider):
    name = "mistral"
    default_model = "mistral-ocr-latest"
    env_var = "MISTRAL_API_KEY"
    endpoint = "https://api.mistral.ai/v1/ocr"

    def ocr_image(self, image_bytes, *, model=None, lang="ch"):
        b64 = self._b64(image_bytes)
        payload = {
            "model": model or self.default_model,
            "document": {
                "type": "image_url",
                "image_url": f"data:image/png;base64,{b64}",
            },
            "include_image_base64": False,
        }
        headers = {"Authorization": f"Bearer {self._api_key()}"}
        resp = self._post_json(self.endpoint, headers, payload)
        # Mistral returns {pages: [{markdown: '...', ...}], ...}
        try:
            pages = resp["pages"]
            return "\n\n".join(p.get("markdown", "") for p in pages).strip()
        except (KeyError, TypeError) as e:
            raise OcrError(
                f"Unexpected Mistral OCR response: {json.dumps(resp)[:500]}"
            ) from e
