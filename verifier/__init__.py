"""TTB label verification: model transcribes, code decides.

The rules layer is imported eagerly. Anything that touches the API client is
imported lazily, so the rules can be used and tested without the SDK.
"""

from __future__ import annotations

from .models import (
    ApplicationRecord,
    BeverageClass,
    ExtractedLabel,
    FieldResult,
    Status,
    VerificationResult,
)
from .rules import verify
from .warning_text import CANONICAL_WARNING

_LAZY = {
    "extract_label": "extract",
    "make_client": "extract",
    "ExtractionError": "extract",
    "LabelJob": "batch",
    "verify_one": "batch",
    "verify_batch": "batch",
}


def __getattr__(name: str):
    if name in _LAZY:
        from importlib import import_module

        return getattr(import_module(f".{_LAZY[name]}", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ApplicationRecord",
    "BeverageClass",
    "CANONICAL_WARNING",
    "ExtractedLabel",
    "FieldResult",
    "Status",
    "VerificationResult",
    "verify",
    *_LAZY,
]

__version__ = "1.0.0"
