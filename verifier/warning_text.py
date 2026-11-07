"""The statutory health warning, its normalisation, and a word-level diff.

27 CFR 16.21 fixes the wording of the warning. 27 CFR 16.22 requires the words
"GOVERNMENT WARNING" in capital letters and bold type. If the regulation is
amended, CANONICAL_WARNING is the only line that needs to change.
"""

from __future__ import annotations

import difflib
import re

HEADER = "GOVERNMENT WARNING:"

CANONICAL_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not "
    "drink alcoholic beverages during pregnancy because of the risk of birth "
    "defects. (2) Consumption of alcoholic beverages impairs your ability to "
    "drive a car or operate machinery, and may cause health problems."
)

# Design tools swap these in silently. They are visually identical to a reader,
# so they are not treated as defects.
_TYPOGRAPHIC = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        " ": " ",
    }
)


def normalise(text: str) -> str:
    """Fold typographic glyphs and collapse whitespace. Nothing else.

    Case and punctuation are deliberately preserved: the warning is fixed
    wording, and a missing comma is a real deviation.
    """
    text = text.translate(_TYPOGRAPHIC)
    return re.sub(r"\s+", " ", text).strip()


def word_diff(expected: str, found: str, limit: int = 4) -> list[str]:
    """Describe where `found` departs from `expected`, one phrase per change."""
    exp_words = normalise(expected).split(" ")
    got_words = normalise(found).split(" ")
    matcher = difflib.SequenceMatcher(a=exp_words, b=got_words, autojunk=False)

    changes: list[str] = []
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op == "equal":
            continue
        want = " ".join(exp_words[a0:a1])
        have = " ".join(got_words[b0:b1])
        if op == "replace":
            changes.append(f"expected '{want}', label reads '{have}'")
        elif op == "delete":
            changes.append(f"missing '{want}'")
        elif op == "insert":
            changes.append(f"extra '{have}'")

    if len(changes) > limit:
        hidden = len(changes) - limit
        changes = changes[:limit] + [f"and {hidden} more difference(s)"]
    return changes
