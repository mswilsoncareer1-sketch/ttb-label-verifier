# Alcohol Label Verification

A prototype that checks alcohol beverage label artwork against the data on the
application, for TTB Compliance Division review. Standalone proof of concept;
not integrated with COLA.

It checks five fields (brand name, class/type, alcohol content, net contents and
the health warning statement) and reports each one as **matches**, **does not
match**, or **needs a look**.

---

## Quick start

```bash
git clone <this repository>
cd ttb-label-verifier

python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export ANTHROPIC_API_KEY=sk-ant-...                 # Windows: set ANTHROPIC_API_KEY=...
streamlit run app.py
```

The app opens at `http://localhost:8501`. Without the environment variable it
asks for a key in the sidebar instead; the key is held for the session only and
never written to disk.

### Try it with the sample labels

```bash
python sample_data/generate_labels.py
```

This writes ten labels and an `applications.csv` to `sample_data/labels/`. Two
are compliant and eight each break one rule; the CSV's `expected_outcome`
column says which. Open the **Batch** tab, upload `applications.csv` and all
ten images, and run it.

### Run the tests

```bash
pytest -q        # 67 tests, no API key or network needed
```

---

## Approach

### The model transcribes; code decides

The vision model is asked one thing: what does this artwork say. Every
pass/fail decision is made afterwards in `verifier/rules.py`, in ordinary
Python with no model in the loop.

This matters for a compliance tool. A model asked "is this label compliant?"
gives an answer that is hard to test, can vary between runs, and cannot be
explained to an applicant whose label was rejected. Splitting the two means the
decision logic is deterministic, unit tested, and reviewable by someone who does
not work on machine learning. Swapping the model later cannot silently change
which labels pass.

The model is also told to transcribe errors as printed rather than correct
them, since a model that helpfully fixes a typo would hide exactly the defect
the tool exists to find.

### The health warning is the strict field

The other four fields are the applicant's own words, so comparison forgives
case and spacing. The health warning is not: 27 CFR 16.21 fixes the wording and
27 CFR 16.22 requires "GOVERNMENT WARNING" in capitals and bold.

The warning is therefore compared character for character against the
statutory text. The only normalisation is collapsing whitespace (labels set the
warning across several printed lines, and a line break is not a defect) and
folding typographic quotes, dashes and non-breaking spaces to plain equivalents,
since a design tool's substitution produces a visually identical glyph.

When the warning fails, the reviewer gets a word-level diff rather than a bare
"does not match":

> expected 'not', label reads 'never'

### Tolerances follow the regulations

An exact ABV match would false-flag many compliant labels, so alcohol content
applies the published tolerance for the commodity, inferred from the
class/type wording:

| Commodity | Tolerance | Source |
|---|---|---|
| Distilled spirits | ±0.3 points | 27 CFR 5.65 |
| Malt beverages | ±0.3 points | 27 CFR 7.65 |
| Wine, 14% ABV or less | ±1.5 points | 27 CFR 4.36 |
| Wine, over 14% ABV | ±1.0 point | 27 CFR 4.36 |

If the commodity cannot be inferred, any difference goes to a human rather than
being guessed.

### Three outcomes, not two

"Needs a look" exists so the tool never has to pretend certainty. A brand name
that differs by one letter, a warning whose bold weight could not be judged, or
artwork the reader flagged as hard to read all go to a person rather than being
forced into pass or fail.

### Speed

1. **A fast model by default** (`claude-haiku-5-5`). The sidebar offers larger
   models for difficult artwork.
2. **Images downscaled to 1568 px on the long edge** before upload. Beyond that
   an image costs tokens and latency without adding legible detail.
3. **One call per label**, not one per field.

Batches run eight labels concurrently (`verifier/batch.py`). One unreadable or
missing file becomes a "needs a look" row rather than an exception that
discards the rest of the batch. Results can be downloaded as CSV.

---

## Layout

```
app.py                      Streamlit interface: single and batch modes
verifier/
  extract.py                vision call, image preparation, structured output
  rules.py                  all comparison logic and the tolerance table
  warning_text.py           statutory text, normalisation, word-level diff
  models.py                 dataclasses passed between the layers
  batch.py                  concurrent runner for batch submissions
tests/test_rules.py         67 tests; extraction is tested against a fake client
sample_data/
  generate_labels.py        renders ten test labels and applications.csv
```

`extract.py` is the only module that talks to the API, and `rules.py` has no
dependency on it, which is what makes the rules testable offline.

---

## Tools used

- **Python 3.11+**, **Streamlit** for the interface, **Pillow** for image
  preparation and for rendering the test fixtures.
- **Anthropic API**, `claude-haiku-5-5` by default, called with a forced tool
  schema so the transcription comes back as typed JSON rather than prose to be
  parsed.
- **pytest** for the tests.
- **Claude** was used as a coding assistant while building this. Design
  decisions, the regulatory research and the final code are my own, and I can
  walk through any part of it.

---

## Assumptions

1. **Artwork is reasonably legible.** Photos at an angle, with glare, or in poor
   light are out of scope. The reader reports what it could not read, and those
   fields are flagged for review rather than failed.
2. **One label image per application.** Front and back labels as separate files
   would need the row repeated, or a change to carry several images per row.
3. **The metric net contents statement controls** when a label shows both
   metric and US customary. Conversions allow 2 mL for rounding, since 750 mL is
   routinely printed as 25.4 fl oz (751.2 mL).
4. **Bold type is reported by the model, not measured.** Where it cannot tell,
   the field is flagged for review rather than passed.
5. **Commodity is inferred from the class/type text** by keyword. An application
   file that carried the commodity explicitly would remove the inference.
6. **The statutory text and tolerances are transcribed from the CFR as of this
   build.** They are clearly marked constants in `warning_text.py` and
   `rules.py`, so they can be updated without touching logic.

---

## Known limitations

- **No pixel-level typography check.** Type size and the "readily legible"
  requirements of 27 CFR 16.22 are not measured.
- **Only the five requested fields.** A full COLA review also covers
  appellation, vintage, sulfite declaration, name and address, and more.
- **No persistence.** Results live in the session; a real deployment would need
  an audit record of who checked what and when.
- **No authentication.** The deployed prototype is open to anyone with the URL.
- **Tolerances are simplified.** The CFR has further provisions (for example,
  wine tax class boundaries and "low alcohol" claims); only the headline figures
  are applied.

---

## Deploying

Built for Streamlit Community Cloud:

1. Push this repository to GitHub.
2. Create an app at [share.streamlit.io](https://share.streamlit.io) pointed at
   `app.py`.
3. Add the key under **Settings, Secrets**:

   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```

The app reads the key from the environment, then from Streamlit secrets, then
from the sidebar, so the same code runs locally and deployed.
