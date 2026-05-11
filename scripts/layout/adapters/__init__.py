"""Adapters convert provider-specific layout JSON into NormalizedLayout."""
from .baidu import adapt_baidu
from .mineru import adapt_mineru

__all__ = ["adapt_baidu", "adapt_mineru"]
