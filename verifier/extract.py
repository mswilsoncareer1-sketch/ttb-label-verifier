"""The only module that talks to the vision model.

The model is asked what the artwork says, never whether it is compliant.
A forced tool schema makes the answer come back as typed JSON.
"""

from __future__ import annotations

import base64
import io
import os
from typing import Any

from PIL import Image, ImageOps

from .models import ExtractedLabel

DEFAULT_MODEL = os.getenv("TTB_MODEL", "claude-haiku-5-5")

# Beyond this the image costs tokens and latency without adding legible detail.
MAX_EDGE_PX = 1568

TOOL_NAME = "record_label_text"

TRANSCRIPTION_TOOL: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": "Record the text printed on an alcohol beverage label, exactly as it appears.",
    "input_schema": {
        "type": "object",
        "properties": {
            "brand_name": {
                "type": ["string", "null"],
                "description": "The brand name, exactly as printed.",
            },
            "class_type": {
                "type": ["string", "null"],
                "description": "The class or type designation, e.g. 'Kentucky Straight Bourbon Whiskey', exactly as printed.",
            },
            "alcohol_content": {
                "type": ["string", "null"],
                "description": "The full alcohol content statement, e.g. '45% Alc./Vol. (90 Proof)'.",
            },
            "net_contents": {
                "type": ["string", "null"],
                "description": "The full net contents statement, e.g. '750 mL'.",
            },
            "government_warning": {
                "type": ["string", "null"],
                "description": (
                    "The complete health warning statement, character for character, "
                    "preserving capitalisation and punctuation exactly. Do not correct "
                    "typos or restore missing words. Join printed line breaks with spaces."
                ),
            },
            "warning_header_bold": {
                "type": ["boolean", "null"],
                "description": "True if the words GOVERNMENT WARNING are in bold type, false if not, null if you cannot tell.",
            },
            "legibility_notes": {
                "type": "string",
                "description": "Anything you could not read clearly, and why. Empty string if everything was legible.",
            },
        },
        "required": [
            "brand_name",
            "class_type",
            "alcohol_content",
            "net_contents",
            "government_warning",
            "warning_header_bold",
            "legibility_notes",
        ],
    },
}

PROMPT = (
    "Transcribe this alcohol beverage label. Report what is printed, not what "
    "should be printed: if a word is misspelled, missing, or in the wrong case, "
    "transcribe it that way. If a field does not appear on the label, use null. "
    "Do not judge compliance."
)


class ExtractionError(RuntimeError):
    """The image could not be read or the model returned nothing usable."""


def prepare_image(data: bytes) -> tuple[str, str]:
    """Orient, downscale and re-encode. Returns (media_type, base64 data)."""
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)
    except Exception as exc:  # noqa: BLE001 - any decode failure is the same to the user
        raise ExtractionError(f"Not a readable image: {exc}") from exc

    image = image.convert("RGB")
    longest = max(image.size)
    if longest > MAX_EDGE_PX:
        scale = MAX_EDGE_PX / longest
        image = image.resize(
            (round(image.width * scale), round(image.height * scale)),
            Image.Resampling.LANCZOS,
        )

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return "image/jpeg", base64.standard_b64encode(buffer.getvalue()).decode()


def make_client(api_key: str):
    """Build an API client. Imported here so the rules layer never needs the SDK."""
    import anthropic

    return anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=60)


def extract_label(
    image_bytes: bytes, client: Any, model: str = DEFAULT_MODEL
) -> ExtractedLabel:
    """Send one label image to the model and return its transcription."""
    media_type, data = prepare_image(image_bytes)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=1500,
            tools=[TRANSCRIPTION_TOOL],
            tool_choice={"type": "tool", "name": TOOL_NAME},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": data},
                        },
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ],
        )
    except Exception as exc:  # noqa: BLE001 - surface API failures as one type
        raise ExtractionError(f"Model call failed: {exc}") from exc

    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
            return _to_label(block.input)
    raise ExtractionError("The model did not return a transcription.")


def _to_label(payload: dict[str, Any]) -> ExtractedLabel:
    def text(key: str) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    bold = payload.get("warning_header_bold")
    return ExtractedLabel(
        brand_name=text("brand_name"),
        class_type=text("class_type"),
        alcohol_content=text("alcohol_content"),
        net_contents=text("net_contents"),
        government_warning=text("government_warning"),
        warning_header_bold=bold if isinstance(bold, bool) else None,
        legibility_notes=text("legibility_notes") or "",
    )
