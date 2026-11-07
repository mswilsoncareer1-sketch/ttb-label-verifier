"""Rule tests. No API key or network needed."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from PIL import Image

from verifier import (
    CANONICAL_WARNING,
    ApplicationRecord,
    BeverageClass,
    ExtractedLabel,
    Status,
    verify,
)
from verifier.rules import (
    check_alcohol,
    check_net_contents,
    check_text,
    check_warning,
    classify,
    parse_abv,
    parse_net_contents_ml,
    tolerance_for,
)
from verifier.warning_text import normalise, word_diff


def app(**kw) -> ApplicationRecord:
    base = dict(
        label_id="T1",
        brand_name="Old Tom Distillery",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol.",
        net_contents="750 mL",
    )
    base.update(kw)
    return ApplicationRecord(**base)


def label(**kw) -> ExtractedLabel:
    base = dict(
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol. (90 Proof)",
        net_contents="750 mL",
        government_warning=CANONICAL_WARNING,
        warning_header_bold=True,
    )
    base.update(kw)
    return ExtractedLabel(**base)


# ------------------------------------------------------------ whole label


def test_compliant_label_passes():
    result = verify(app(), label())
    assert result.overall is Status.PASS
    assert all(f.status is Status.PASS for f in result.fields)


def test_any_fail_makes_label_fail():
    result = verify(app(), label(net_contents="700 mL"))
    assert result.overall is Status.FAIL


def test_review_without_fail_makes_label_review():
    result = verify(app(), label(warning_header_bold=None))
    assert result.overall is Status.REVIEW


def test_five_fields_checked():
    names = [f.field for f in verify(app(), label()).fields]
    assert names == [
        "brand_name", "class_type", "alcohol_content", "net_contents", "government_warning"
    ]


# ------------------------------------------------------------ text fields


@pytest.mark.parametrize(
    "found",
    ["Old Tom Distillery", "OLD TOM DISTILLERY", "old  tom\ndistillery", " Old Tom Distillery "],
)
def test_brand_ignores_case_and_spacing(found):
    assert check_text("brand_name", "Old Tom Distillery", found, label()).status is Status.PASS


def test_brand_typographic_apostrophe_matches():
    r = check_text("brand_name", "Stone's Throw", "STONE’S THROW", label())
    assert r.status is Status.PASS


def test_brand_punctuation_only_difference_is_review():
    r = check_text("brand_name", "Stone's Throw", "Stones Throw", label())
    assert r.status is Status.REVIEW


def test_brand_close_typo_is_review():
    r = check_text("brand_name", "Silver Creek", "Silver Crest", label())
    assert r.status is Status.REVIEW


def test_brand_different_is_fail():
    r = check_text("brand_name", "Old Tom Distillery", "Harbor Light Brewing", label())
    assert r.status is Status.FAIL


def test_class_type_mismatch_fails():
    r = check_text("class_type", "Vodka", "London Dry Gin", label())
    assert r.status is Status.FAIL


def test_missing_field_fails():
    r = check_text("brand_name", "Old Tom", None, label())
    assert r.status is Status.FAIL


def test_missing_field_with_legibility_note_is_review():
    r = check_text("brand_name", "Old Tom", None, label(legibility_notes="glare over top third"))
    assert r.status is Status.REVIEW
    assert "glare" in r.message


# ---------------------------------------------------------------- alcohol


@pytest.mark.parametrize(
    "text,expected",
    [
        ("45% Alc./Vol.", 45.0),
        ("ALC. 13.5% BY VOL.", 13.5),
        ("5.0% ABV", 5.0),
        ("90 Proof", 45.0),
        ("45% Alc./Vol. (90 Proof)", 45.0),
        ("12,5 % vol", 12.5),
        ("40", 40.0),
        ("no number here", None),
        (None, None),
    ],
)
def test_parse_abv(text, expected):
    assert parse_abv(text) == expected


@pytest.mark.parametrize(
    "class_type,beverage",
    [
        ("Kentucky Straight Bourbon Whiskey", BeverageClass.SPIRITS),
        ("London Dry Gin", BeverageClass.SPIRITS),
        ("Cabernet Sauvignon", BeverageClass.WINE),
        ("Rosé Wine", BeverageClass.WINE),
        ("India Pale Ale", BeverageClass.MALT),
        ("Robust Porter", BeverageClass.MALT),
        ("Hard Seltzer", BeverageClass.UNKNOWN),
    ],
)
def test_classify(class_type, beverage):
    assert classify(class_type) is beverage


def test_tolerances_match_cfr():
    assert tolerance_for(BeverageClass.SPIRITS, 40) == 0.3
    assert tolerance_for(BeverageClass.MALT, 5) == 0.3
    assert tolerance_for(BeverageClass.WINE, 14.0) == 1.5
    assert tolerance_for(BeverageClass.WINE, 15.0) == 1.0
    assert tolerance_for(BeverageClass.UNKNOWN, 5) is None


def test_spirits_within_tolerance_passes():
    r = check_alcohol(app(alcohol_content="45%"), label(alcohol_content="45.3%"))
    assert r.status is Status.PASS


def test_spirits_outside_tolerance_fails():
    r = check_alcohol(app(alcohol_content="40%"), label(alcohol_content="43%"))
    assert r.status is Status.FAIL


def test_wine_inside_wide_tolerance_passes():
    a = app(class_type="Cabernet Sauvignon", alcohol_content="13%")
    assert check_alcohol(a, label(alcohol_content="13.5%")).status is Status.PASS


def test_strong_wine_uses_narrow_tolerance():
    a = app(class_type="Port Wine", alcohol_content="19%")
    assert check_alcohol(a, label(alcohol_content="20.2%")).status is Status.FAIL


def test_malt_outside_tolerance_fails():
    a = app(class_type="India Pale Ale", alcohol_content="5.0%")
    assert check_alcohol(a, label(alcohol_content="5.5%")).status is Status.FAIL


def test_unknown_commodity_difference_is_review():
    a = app(class_type="Hard Seltzer", alcohol_content="5%")
    assert check_alcohol(a, label(alcohol_content="5.2%")).status is Status.REVIEW


def test_proof_only_label_converts():
    assert check_alcohol(app(), label(alcohol_content="90 Proof")).status is Status.PASS


def test_unreadable_abv_is_review():
    assert check_alcohol(app(), label(alcohol_content="strong")).status is Status.REVIEW


# ----------------------------------------------------------- net contents


@pytest.mark.parametrize(
    "text,ml",
    [
        ("750 mL", 750),
        ("750ML", 750),
        ("75 cl", 750),
        ("1 L", 1000),
        ("1.75 Liters", 1750),
        ("750 mL (25.4 FL OZ)", 750),
        ("12 FL. OZ.", 12 * 29.5735),
        ("1 PINT 8 FL OZ", 473.176 + 8 * 29.5735),
        ("nothing", None),
    ],
)
def test_parse_net_contents(text, ml):
    got = parse_net_contents_ml(text)
    if ml is None:
        assert got is None
    else:
        assert got == pytest.approx(ml, abs=0.01)


def test_net_contents_cross_unit_rounding_passes():
    r = check_net_contents(app(net_contents="750 mL"), label(net_contents="25.4 FL OZ"))
    assert r.status is Status.PASS


def test_net_contents_mismatch_fails():
    r = check_net_contents(app(net_contents="750 mL"), label(net_contents="700 mL"))
    assert r.status is Status.FAIL
    assert "700" in r.message


# ---------------------------------------------------------------- warning


def test_warning_exact_passes():
    assert check_warning(label()).status is Status.PASS


def test_warning_across_line_breaks_passes():
    broken = CANONICAL_WARNING.replace(" (2)", "\n(2)").replace("women ", "women\n  ")
    assert check_warning(label(government_warning=broken)).status is Status.PASS


def test_warning_curly_quotes_and_nbsp_pass():
    text = CANONICAL_WARNING.replace(" ", " ", 3)
    assert check_warning(label(government_warning=text)).status is Status.PASS


def test_warning_title_case_header_fails():
    text = CANONICAL_WARNING.replace("GOVERNMENT WARNING:", "Government Warning:")
    r = check_warning(label(government_warning=text))
    assert r.status is Status.FAIL
    assert "capital" in r.message


def test_warning_not_bold_fails():
    r = check_warning(label(warning_header_bold=False))
    assert r.status is Status.FAIL
    assert "bold" in r.message


def test_warning_bold_unknown_is_review():
    assert check_warning(label(warning_header_bold=None)).status is Status.REVIEW


def test_warning_altered_word_fails_with_diff():
    text = CANONICAL_WARNING.replace("should not drink", "should never drink")
    r = check_warning(label(government_warning=text))
    assert r.status is Status.FAIL
    assert "expected 'not', label reads 'never'" in r.message


def test_warning_missing_comma_fails():
    text = CANONICAL_WARNING.replace("machinery,", "machinery")
    assert check_warning(label(government_warning=text)).status is Status.FAIL


def test_warning_missing_entirely_fails():
    assert check_warning(label(government_warning=None)).status is Status.FAIL


def test_warning_body_case_only_is_review():
    head, body = CANONICAL_WARNING.split(": ", 1)
    text = f"{head}: {body.upper()}"
    assert check_warning(label(government_warning=text)).status is Status.REVIEW


def test_word_diff_reports_missing_and_extra():
    changes = word_diff("a b c d", "a c d e")
    assert "missing 'b'" in changes
    assert "extra 'e'" in changes


def test_word_diff_caps_long_output():
    changes = word_diff("a b c d e f g h", "1 b 2 d 3 f 4 h 5", limit=2)
    assert changes[-1].startswith("and ")


def test_normalise_collapses_whitespace_only():
    assert normalise("A\n  b\t’C") == "A b 'C"


# ------------------------------------------------- extraction (mocked API)


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (3000, 1000), "white").save(buf, format="PNG")
    return buf.getvalue()


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.sent = None
        self.messages = self

    def create(self, **kwargs):
        self.sent = kwargs
        block = SimpleNamespace(type="tool_use", name="record_label_text", input=self.payload)
        return SimpleNamespace(content=[block])


def test_extract_parses_tool_output_and_downscales():
    from verifier.extract import MAX_EDGE_PX, extract_label

    client = FakeClient(
        dict(brand_name="OLD TOM", class_type=None, alcohol_content="45%",
             net_contents=" 750 mL ", government_warning=CANONICAL_WARNING,
             warning_header_bold=True, legibility_notes="")
    )
    out = extract_label(_png(), client, model="test-model")
    assert out.brand_name == "OLD TOM"
    assert out.class_type is None
    assert out.net_contents == "750 mL"
    assert client.sent["tool_choice"]["name"] == "record_label_text"

    import base64
    data = client.sent["messages"][0]["content"][0]["source"]["data"]
    sent = Image.open(io.BytesIO(base64.b64decode(data)))
    assert max(sent.size) == MAX_EDGE_PX


def test_extract_rejects_non_image():
    from verifier.extract import ExtractionError, extract_label

    with pytest.raises(ExtractionError):
        extract_label(b"not an image", FakeClient({}))


def test_batch_turns_missing_image_into_review_row():
    from verifier.batch import LabelJob, verify_batch

    good = FakeClient(
        dict(brand_name="Old Tom Distillery", class_type="Kentucky Straight Bourbon Whiskey",
             alcohol_content="45%", net_contents="750 mL",
             government_warning=CANONICAL_WARNING, warning_header_bold=True,
             legibility_notes="")
    )
    results = verify_batch(
        [LabelJob(app(label_id="A"), _png()), LabelJob(app(label_id="B"), None)], good
    )
    assert [r.label_id for r in results] == ["A", "B"]
    assert results[0].overall is Status.PASS
    assert results[1].overall is Status.REVIEW
    assert results[1].error
