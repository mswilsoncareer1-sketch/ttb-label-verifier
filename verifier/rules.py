"""Every pass/fail decision lives here, in plain Python with no model in the loop.

Inputs are what the applicant declared (ApplicationRecord) and what the vision
model transcribed (ExtractedLabel). Output is one FieldResult per field.
"""

from __future__ import annotations

import difflib
import re

from .models import (
    ApplicationRecord,
    BeverageClass,
    ExtractedLabel,
    FieldResult,
    Status,
    VerificationResult,
)
from .warning_text import CANONICAL_WARNING, HEADER, normalise, word_diff

# --------------------------------------------------------------- tolerances
# Allowed difference between stated and actual alcohol content, percentage
# points ABV. Spirits 27 CFR 5.65, malt beverages 27 CFR 7.65, wine 27 CFR 4.36.
SPIRITS_TOLERANCE = 0.3
MALT_TOLERANCE = 0.3
WINE_TOLERANCE_AT_OR_BELOW_14 = 1.5
WINE_TOLERANCE_ABOVE_14 = 1.0

# Net contents may differ by this much from rounding between unit systems.
NET_CONTENTS_TOLERANCE_ML = 2.0

# Text fields this similar (0 to 1) are close enough to deserve human eyes
# rather than an outright fail.
SIMILAR_ENOUGH_TO_REVIEW = 0.8

_SPIRITS_WORDS = (
    "whiskey whisky bourbon scotch vodka gin rum tequila mezcal brandy cognac "
    "armagnac liqueur cordial schnapps absinthe spirit spirits"
).split()
_WINE_WORDS = (
    "wine champagne sparkling cabernet merlot chardonnay pinot sauvignon "
    "riesling zinfandel syrah shiraz malbec rose rosé port sherry vermouth "
    "sake mead cider"
).split()
_MALT_WORDS = (
    "beer ale lager stout porter ipa pilsner pilsener malt hefeweizen "
    "saison bock"
).split()

_UNIT_TO_ML = {
    "ml": 1.0,
    "cl": 10.0,
    "l": 1000.0,
    "fl oz": 29.5735,
    "pint": 473.176,
    "quart": 946.353,
    "gallon": 3785.41,
}
_METRIC = {"ml", "cl", "l"}

_QUANTITY = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*"
    r"(milliliters?|millilitres?|ml|centiliters?|centilitres?|cl|"
    r"liters?|litres?|l|fluid\s*ounces?|fl\.?\s*oz\.?|oz\.?|"
    r"pints?|pt\.?|quarts?|qt\.?|gallons?|gal\.?)(?![a-z])",
    re.IGNORECASE,
)


# ------------------------------------------------------------------ helpers


def classify(class_type: str) -> BeverageClass:
    """Infer the commodity from the class/type wording."""
    words = set(re.findall(r"[a-zé]+", class_type.casefold()))
    if words & set(_SPIRITS_WORDS):
        return BeverageClass.SPIRITS
    if words & set(_MALT_WORDS):
        return BeverageClass.MALT
    if words & set(_WINE_WORDS):
        return BeverageClass.WINE
    return BeverageClass.UNKNOWN


def tolerance_for(beverage: BeverageClass, declared_abv: float) -> float | None:
    """Allowed ABV difference for the commodity, or None when unknown."""
    if beverage is BeverageClass.SPIRITS:
        return SPIRITS_TOLERANCE
    if beverage is BeverageClass.MALT:
        return MALT_TOLERANCE
    if beverage is BeverageClass.WINE:
        if declared_abv > 14.0:
            return WINE_TOLERANCE_ABOVE_14
        return WINE_TOLERANCE_AT_OR_BELOW_14
    return None


def parse_abv(text: str | None) -> float | None:
    """Pull an ABV figure out of label wording; fall back to proof / 2."""
    if not text:
        return None
    text = text.replace(",", ".")
    pct = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if pct:
        return float(pct.group(1))
    proof = re.search(r"(\d+(?:\.\d+)?)\s*(?:°\s*)?proof", text, re.IGNORECASE)
    if proof:
        return float(proof.group(1)) / 2
    bare = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", text)
    if bare:
        return float(bare.group(1))
    return None


def _unit_key(raw: str) -> str:
    raw = re.sub(r"[\s.]", "", raw.casefold())
    if raw.startswith("milli") or raw == "ml":
        return "ml"
    if raw.startswith("centi") or raw == "cl":
        return "cl"
    if raw.startswith("lit") or raw == "l":
        return "l"
    if raw.startswith(("fl", "oz")):
        return "fl oz"
    if raw.startswith(("pint", "pt")):
        return "pint"
    if raw.startswith(("quart", "qt")):
        return "quart"
    return "gallon"


def parse_net_contents_ml(text: str | None) -> float | None:
    """Convert a net contents statement to millilitres.

    When both metric and US customary appear ("750 mL (25.4 FL OZ)") the metric
    figure controls. US customary parts are summed ("1 PINT 8 FL OZ").
    """
    if not text:
        return None
    found = [
        (float(num.replace(",", ".")), _unit_key(unit))
        for num, unit in _QUANTITY.findall(text)
    ]
    if not found:
        return None
    for amount, unit in found:
        if unit in _METRIC:
            return amount * _UNIT_TO_ML[unit]
    return sum(amount * _UNIT_TO_ML[unit] for amount, unit in found)


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", normalise(text).casefold()).strip()


def _strip_punct(text: str) -> str:
    return re.sub(r"[^\w\s]", "", _fold(text)).replace("  ", " ").strip()


def _show(value: str | None) -> str:
    return value if value else "(not found)"


def _missing(field: str, expected: str, extracted: ExtractedLabel) -> FieldResult:
    """A field the model could not find. Illegible artwork gets a human look."""
    if extracted.legibility_notes:
        return FieldResult(
            field, Status.REVIEW, expected, "(not found)",
            f"Not found; the reader noted: {extracted.legibility_notes}",
        )
    return FieldResult(
        field, Status.FAIL, expected, "(not found)", "Not found on the label."
    )


# ------------------------------------------------------------------- checks


def check_text(
    field: str, expected: str, found: str | None, extracted: ExtractedLabel
) -> FieldResult:
    """Brand name and class/type: forgiving about case and spacing only."""
    if not found:
        return _missing(field, expected, extracted)
    if _fold(expected) == _fold(found):
        return FieldResult(field, Status.PASS, expected, found, "Matches.")
    if _strip_punct(expected) == _strip_punct(found):
        return FieldResult(
            field, Status.REVIEW, expected, found,
            "Same words, but punctuation differs.",
        )
    ratio = difflib.SequenceMatcher(a=_fold(expected), b=_fold(found)).ratio()
    if ratio >= SIMILAR_ENOUGH_TO_REVIEW:
        return FieldResult(
            field, Status.REVIEW, expected, found,
            f"Close but not identical ({ratio:.0%} similar). Possible typo.",
        )
    return FieldResult(
        field, Status.FAIL, expected, found, "Label does not match the application."
    )


def check_alcohol(
    application: ApplicationRecord, extracted: ExtractedLabel
) -> FieldResult:
    """Compare ABV within the commodity's tolerance."""
    field = "alcohol_content"
    expected, found = application.alcohol_content, extracted.alcohol_content
    if not found:
        return _missing(field, expected, extracted)

    declared = parse_abv(expected)
    stated = parse_abv(found)
    if declared is None:
        return FieldResult(
            field, Status.REVIEW, expected, found,
            "Could not read a percentage from the application.",
        )
    if stated is None:
        return FieldResult(
            field, Status.REVIEW, expected, found,
            "Could not read a percentage from the label.",
        )

    gap = abs(declared - stated)
    beverage = classify(application.class_type)
    allowed = tolerance_for(beverage, declared)

    if gap < 1e-9:
        return FieldResult(field, Status.PASS, expected, found, "Matches.")
    if allowed is None:
        return FieldResult(
            field, Status.REVIEW, expected, found,
            f"Differs by {gap:.2f} points and the commodity could not be "
            "inferred from the class/type, so no tolerance was applied.",
        )
    if gap <= allowed + 1e-9:
        return FieldResult(
            field, Status.PASS, expected, found,
            f"Differs by {gap:.2f} points, within the {allowed} point "
            f"{beverage.value} tolerance.",
        )
    return FieldResult(
        field, Status.FAIL, expected, found,
        f"Differs by {gap:.2f} points; the {beverage.value} tolerance is "
        f"{allowed}.",
    )


def check_net_contents(
    application: ApplicationRecord, extracted: ExtractedLabel
) -> FieldResult:
    """Compare volumes after converting both sides to millilitres."""
    field = "net_contents"
    expected, found = application.net_contents, extracted.net_contents
    if not found:
        return _missing(field, expected, extracted)

    want = parse_net_contents_ml(expected)
    have = parse_net_contents_ml(found)
    if want is None or have is None:
        side = "application" if want is None else "label"
        return FieldResult(
            field, Status.REVIEW, expected, found,
            f"Could not read a volume from the {side}.",
        )
    if abs(want - have) <= NET_CONTENTS_TOLERANCE_ML:
        return FieldResult(field, Status.PASS, expected, found, "Matches.")
    return FieldResult(
        field, Status.FAIL, expected, found,
        f"Label states {have:,.0f} mL; application states {want:,.0f} mL.",
    )


def check_warning(extracted: ExtractedLabel) -> FieldResult:
    """The statutory warning: exact wording, capitals, and bold header."""
    field = "government_warning"
    expected = CANONICAL_WARNING
    raw = extracted.government_warning
    if not raw:
        return _missing(field, expected, extracted)

    found = normalise(raw)
    problems: list[str] = []
    review: list[str] = []

    if not found.startswith(HEADER):
        if found.upper().startswith(HEADER):
            problems.append("'GOVERNMENT WARNING:' must be in capital letters.")
        else:
            problems.append("Does not begin with 'GOVERNMENT WARNING:'.")

    if found != expected:
        if found.casefold() == expected.casefold():
            if not problems:
                review.append("Wording matches, but capitalisation differs.")
        else:
            problems.append("Wording differs: " + "; ".join(word_diff(expected, found)))

    if extracted.warning_header_bold is False:
        problems.append("'GOVERNMENT WARNING' is not in bold type.")
    elif extracted.warning_header_bold is None:
        review.append("Could not tell whether the header is bold.")

    if problems:
        return FieldResult(field, Status.FAIL, expected, raw, " ".join(problems))
    if review:
        return FieldResult(field, Status.REVIEW, expected, raw, " ".join(review))
    return FieldResult(field, Status.PASS, expected, raw, "Exact statutory text.")


def verify(
    application: ApplicationRecord, extracted: ExtractedLabel
) -> VerificationResult:
    """Run every check and collect the results."""
    return VerificationResult(
        label_id=application.label_id,
        extracted=extracted,
        fields=[
            check_text(
                "brand_name", application.brand_name, extracted.brand_name, extracted
            ),
            check_text(
                "class_type", application.class_type, extracted.class_type, extracted
            ),
            check_alcohol(application, extracted),
            check_net_contents(application, extracted),
            check_warning(extracted),
        ],
    )
