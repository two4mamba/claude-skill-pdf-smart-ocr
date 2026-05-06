"""Mistral OCR provider — uses the dedicated /v1/ocr endpoint.

$2 / 1000 pages standard, $1 / 1000 pages with batch API. Free tier:
1 RPS, 500K TPM, 1B tokens/month, no credit card needed.

Unlike chat-completions providers, Mistral OCR has its own endpoint shape:
POST /v1/ocr  with  { model, document: { type: 'image_url', image_url: '...' } }
"""
from __future__ import annotations

import base64
import html
import json

from .base import VLMProvider, OcrError


class MistralProvider(VLMProvider):
    name = "mistral"
    default_model = "mistral-ocr-latest"
    env_var = "MISTRAL_API_KEY"
    endpoint = "https://api.mistral.ai/v1/ocr"

    def ocr_image(self, image_bytes, *, model=None, lang="ch"):
        b64_input = self._b64(image_bytes)
        payload = {
            "model": model or self.default_model,
            "document": {
                "type": "image_url",
                "image_url": f"data:image/png;base64,{b64_input}",
            },
            "include_image_base64": True,  # ask Mistral to inline figure bytes
        }
        headers = {"Authorization": f"Bearer {self._api_key()}"}
        resp = self._post_json(self.endpoint, headers, payload)
        try:
            pages = resp["pages"]
        except (KeyError, TypeError) as e:
            raise OcrError(
                f"Unexpected Mistral OCR response: {json.dumps(resp)[:500]}"
            ) from e

        # We always send a single page (one image), so pages has one entry.
        md_parts: list[str] = []
        images: dict[str, bytes] = {}
        for p in pages:
            md_parts.append(p.get("markdown", ""))
            for img in p.get("images", []) or []:
                img_id = img.get("id")
                b64 = img.get("image_base64") or ""
                if not img_id or not b64:
                    continue
                # Strip data-URL prefix if present
                if "," in b64:
                    b64 = b64.split(",", 1)[1]
                try:
                    images[img_id] = base64.b64decode(b64)
                except Exception:
                    continue  # skip malformed, keep going

        md = html.unescape("\n\n".join(md_parts).strip())
        return md, images
