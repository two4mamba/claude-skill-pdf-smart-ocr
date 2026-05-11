"""Abstract base for VLM-based OCR providers."""
from __future__ import annotations

import base64
import html
import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Optional


DEFAULT_OCR_PROMPT = (
    "Extract all text from this page image as clean Markdown. Preserve:\n"
    "- Heading hierarchy (use #, ##, ###)\n"
    "- Bullet and numbered lists\n"
    "- Tables (as Markdown tables)\n"
    "- Math formulas (LaTeX inline $...$ or block $$...$$)\n"
    "- Code blocks (with language tag if recognizable)\n"
    "- Reading order (top-to-bottom, left-to-right by column)\n"
    "Do NOT add commentary or descriptions. Output only the page content as Markdown."
)


class OcrError(RuntimeError):
    """Raised on any OCR provider failure (HTTP, auth, parse)."""


class VLMProvider(ABC):
    """Abstract OCR provider.

    Two interaction patterns:
    - Image-based: one page image in, Markdown out (Mistral, OpenRouter, etc.)
    - PDF-native: whole PDF in, full Markdown + layout JSON out (Baidu PaddleOCR-VL)

    Set class attribute `supports_pdf = True` to opt into the PDF-native path.
    """

    # Subclasses override to declare PDF-native support.
    supports_pdf: bool = False

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def default_model(self) -> str: ...

    @property
    @abstractmethod
    def env_var(self) -> str:
        """Name of the env var that holds the API key for this provider."""

    @abstractmethod
    def ocr_image(self, image_bytes: bytes, *, model: Optional[str] = None,
                  lang: str = "ch") -> tuple[str, dict[str, bytes]]:
        """OCR a single page (PNG/JPEG bytes).

        Returns (markdown, images_dict):
        - markdown: page content
        - images_dict: {filename: raw_bytes} for figures the provider extracted.
                       Most providers return {} (no extraction support).
                       Mistral OCR fills this from its `pages[*].images` payload.
        """

    def ocr_pdf(self, pdf_bytes: bytes, file_name: str, *,
                model: Optional[str] = None,
                lang: str = "ch") -> tuple[str, dict[str, bytes], Optional[dict]]:
        """Optional: OCR an entire PDF in a single API call.

        Returns (markdown, images_dict, layout_json):
        - markdown: full document markdown (concatenated pages)
        - images_dict: {filename: raw_bytes} for all extracted figures
        - layout_json: provider-specific layout structure (bbox, types, etc.)
                       Used downstream for layout-preserving exports (.docx/.pdf).
                       None if the provider doesn't expose it.

        Default: NotImplementedError. Set `supports_pdf = True` and override
        to enable. The dispatcher in extract.py prefers this path when available.
        """
        raise NotImplementedError(
            f"{self.name} does not support whole-PDF OCR; use ocr_image per page."
        )

    # ---- shared helpers ----

    def _api_key(self) -> str:
        key = os.environ.get(self.env_var)
        if not key:
            raise OcrError(
                f"Missing API key. Set environment variable {self.env_var} "
                f"to use the {self.name} provider."
            )
        return key

    @staticmethod
    def _b64(image_bytes: bytes) -> str:
        return base64.b64encode(image_bytes).decode("ascii")

    @staticmethod
    def _post_json(url: str, headers: dict, payload: dict, timeout: int = 180) -> dict:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={
            **headers,
            "Content-Type": "application/json",
        }, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise OcrError(f"{e.code} from {url}: {err_body[:500]}") from e
        except urllib.error.URLError as e:
            raise OcrError(f"Network error to {url}: {e.reason}") from e

    def _chat_completion_image(
        self,
        url: str,
        model: str,
        image_bytes: bytes,
        prompt: str = DEFAULT_OCR_PROMPT,
        extra_headers: Optional[dict] = None,
    ) -> str:
        """OpenAI Chat-Completions style image+text request. Used by most providers."""
        headers = {
            "Authorization": f"Bearer {self._api_key()}",
            **(extra_headers or {}),
        }
        b64 = self._b64(image_bytes)
        payload = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }],
            "max_tokens": 4096,
            "temperature": 0.0,
        }
        resp = self._post_json(url, headers, payload)
        try:
            return html.unescape(resp["choices"][0]["message"]["content"])
        except (KeyError, IndexError) as e:
            raise OcrError(f"Unexpected response from {url}: {json.dumps(resp)[:500]}") from e
