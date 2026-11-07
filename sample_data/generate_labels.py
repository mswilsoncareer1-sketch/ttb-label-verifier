"""Render ten synthetic test labels plus a matching applications.csv.

Two are fully compliant. Eight each break exactly one rule, so every check in
verifier/rules.py has a labelled example to exercise it.

    python sample_data/generate_labels.py
"""

from __future__ import annotations

import csv
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE / "labels"

BODY = (
    "(1) According to the Surgeon General, women should not drink alcoholic "
    "beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a car "
    "or operate machinery, and may cause health problems."
)

# Each row: application values, what the label actually prints, and the defect.
LABELS = [
    dict(id="01", app=("Old Tom Distillery", "Kentucky Straight Bourbon Whiskey", "45% Alc./Vol.", "750 mL"),
         expect="PASS: fully compliant"),
    dict(id="02", app=("Cedar Ridge Cellars", "Cabernet Sauvignon", "13% Alc./Vol.", "750 mL"),
         label=dict(abv="13.5% Alc./Vol."),
         expect="PASS: 0.5 point ABV gap is inside the 1.5 point wine tolerance"),
    dict(id="03", app=("Silver Creek", "Small Batch Rye Whiskey", "46% Alc./Vol.", "750 mL"),
         label=dict(brand="Silver Crest"),
         expect="REVIEW: brand name typo"),
    dict(id="04", app=("North Fork Vodka Co.", "Vodka", "40% Alc./Vol.", "1 L"),
         label=dict(cls="London Dry Gin"),
         expect="FAIL: class/type does not match"),
    dict(id="05", app=("Harbor Light Brewing", "India Pale Ale", "5.0% Alc./Vol.", "12 FL OZ"),
         label=dict(abv="5.5% Alc./Vol."),
         expect="FAIL: 0.5 point gap exceeds the 0.3 point malt tolerance"),
    dict(id="06", app=("Blue Mesa", "Blanco Tequila", "40% Alc./Vol.", "750 mL"),
         label=dict(net="700 mL"),
         expect="FAIL: net contents differ"),
    dict(id="07", app=("Lighthouse Rum", "Spiced Rum", "35% Alc./Vol.", "750 mL"),
         label=dict(header="Government Warning:"),
         expect="FAIL: warning header not in capitals"),
    dict(id="08", app=("Copper Kettle", "Straight Wheat Whiskey", "47% Alc./Vol.", "750 mL"),
         label=dict(header_bold=False),
         expect="FAIL: warning header not bold"),
    dict(id="09", app=("Meadowbrook Winery", "Riesling", "11% Alc./Vol.", "750 mL"),
         label=dict(body=BODY.replace("should not drink", "should never drink")),
         expect="FAIL: warning wording altered"),
    dict(id="10", app=("Iron Gate Spirits", "Brandy", "40% Alc./Vol.", "375 mL"),
         label=dict(no_warning=True),
         expect="FAIL: warning missing"),
]


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    names = ["DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"] if bold else [
        "DejaVuSans.ttf", "Arial.ttf", "arial.ttf"
    ]
    search = [
        "/usr/share/fonts/truetype/dejavu/",
        "/Library/Fonts/",
        "/System/Library/Fonts/Supplemental/",
        "C:/Windows/Fonts/",
        "",
    ]
    for folder in search:
        for name in names:
            try:
                return ImageFont.truetype(folder + name, size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def _centered(draw: ImageDraw.ImageDraw, y: int, text: str, font, width: int, fill="#1c1c1c") -> int:
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(((width - (box[2] - box[0])) // 2, y), text, font=font, fill=fill)
    return y + (box[3] - box[1]) + 18


def render(spec: dict) -> Image.Image:
    brand, cls, abv, net = spec["app"]
    over = spec.get("label", {})
    brand, cls = over.get("brand", brand), over.get("cls", cls)
    abv, net = over.get("abv", abv), over.get("net", net)

    w, h = 900, 1200
    img = Image.new("RGB", (w, h), "#f6f1e4")
    d = ImageDraw.Draw(img)
    d.rectangle([24, 24, w - 24, h - 24], outline="#3b2f1e", width=6)
    d.rectangle([40, 40, w - 40, h - 40], outline="#3b2f1e", width=2)

    y = 120
    y = _centered(d, y, brand.upper(), _font(True, 64), w)
    y += 30
    for line in textwrap.wrap(cls, 26):
        y = _centered(d, y, line, _font(False, 40), w)
    y += 80
    y = _centered(d, y, abv, _font(True, 44), w)
    y = _centered(d, y, net, _font(False, 40), w)

    if over.get("no_warning"):
        return img

    header = over.get("header", "GOVERNMENT WARNING:")
    head_font = _font(over.get("header_bold", True), 26)
    body_font = _font(False, 26)
    body = over.get("body", BODY)

    y = 760
    x0 = 80
    d.text((x0, y), header, font=head_font, fill="#1c1c1c")
    head_w = d.textlength(header + " ", font=head_font)

    # First line continues after the header; the rest wrap to the margin.
    words = body.split()
    line, x, max_x = [], x0 + head_w, w - 80
    lines: list[tuple[int, str]] = []
    for word in words:
        trial = " ".join(line + [word])
        if x + d.textlength(trial, font=body_font) > max_x and line:
            lines.append((int(x), " ".join(line)))
            line, x = [word], x0
        else:
            line.append(word)
    lines.append((int(x), " ".join(line)))

    for lx, text in lines:
        d.text((lx, y), text, font=body_font, fill="#1c1c1c")
        y += 38
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for spec in LABELS:
        name = f"label_{spec['id']}.png"
        render(spec).save(OUT / name)
        brand, cls, abv, net = spec["app"]
        rows.append(
            dict(label_id=f"COLA-{spec['id']}", image_file=name, brand_name=brand,
                 class_type=cls, alcohol_content=abv, net_contents=net,
                 expected_outcome=spec["expect"])
        )

    with open(OUT / "applications.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} labels and applications.csv to {OUT}")


if __name__ == "__main__":
    main()
