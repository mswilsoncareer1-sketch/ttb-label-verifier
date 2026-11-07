"""Streamlit interface for the TTB label verification prototype."""

from __future__ import annotations

import csv
import html
import io
import os
import time

import streamlit as st

from verifier import ApplicationRecord, Status, VerificationResult, verify
from verifier.extract import DEFAULT_MODEL, ExtractionError, extract_label, make_client
from verifier.batch import LabelJob, verify_batch

st.set_page_config(page_title="Label Verification", page_icon="\N{WHITE HEAVY CHECK MARK}", layout="wide")

FIELD_LABELS = {
    "brand_name": "Brand name",
    "class_type": "Class / type",
    "alcohol_content": "Alcohol content",
    "net_contents": "Net contents",
    "government_warning": "Health warning",
}
STATUS_COLOR = {Status.PASS: "#1b7f3b", Status.FAIL: "#b3261e", Status.REVIEW: "#8a6100"}
STATUS_WORD = {Status.PASS: "Matches", Status.FAIL: "Does not match", Status.REVIEW: "Needs a look"}
MODELS = [DEFAULT_MODEL, "claude-sonnet-5-5", "claude-opus-5-5"]
REQUIRED_COLUMNS = ["label_id", "image_file", "brand_name", "class_type", "alcohol_content", "net_contents"]


# ----------------------------------------------------------------- helpers


def get_api_key() -> str | None:
    """Environment first, then Streamlit secrets, then the sidebar."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return os.getenv("ANTHROPIC_API_KEY")
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:  # noqa: BLE001 - no secrets file configured locally
        pass
    return st.session_state.get("api_key") or None


def badge(status: Status) -> str:
    return (
        f"<span style='background:{STATUS_COLOR[status]};color:white;padding:2px 10px;"
        f"border-radius:10px;font-size:0.85em;font-weight:600'>{STATUS_WORD[status]}</span>"
    )


def show_result(result: VerificationResult) -> None:
    """Render one label's verdict and per-field detail."""
    st.markdown(
        f"<h4 style='margin-bottom:0'>{html.escape(result.label_id)} &nbsp; {badge(result.overall)}</h4>",
        unsafe_allow_html=True,
    )
    if result.error:
        st.warning(result.error)
        return
    st.caption(f"Checked in {result.seconds:.1f} s")
    for f in result.fields:
        cols = st.columns([1.2, 1, 4])
        cols[0].markdown(f"**{FIELD_LABELS[f.field]}**")
        cols[1].markdown(badge(f.status), unsafe_allow_html=True)
        cols[2].markdown(html.escape(f.message))
        if f.status is not Status.PASS:
            with cols[2].expander("Application vs label"):
                st.markdown(f"**Application / required:** {f.expected}")
                st.markdown(f"**Label reads:** {f.found}")
    if result.extracted and result.extracted.legibility_notes:
        st.info(f"Reader note: {result.extracted.legibility_notes}")


def read_applications(data: bytes) -> list[ApplicationRecord]:
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    if not rows:
        raise ValueError("The CSV has no rows.")
    missing = [c for c in REQUIRED_COLUMNS if c not in rows[0]]
    if missing:
        raise ValueError("The CSV is missing column(s): " + ", ".join(missing))
    return [
        ApplicationRecord(**{c: (row.get(c) or "").strip() for c in REQUIRED_COLUMNS})
        for row in rows
    ]


def results_csv(results: list[VerificationResult]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["label_id", "overall", *[f"{k}_status" for k in FIELD_LABELS],
                     *[f"{k}_note" for k in FIELD_LABELS], "error"])
    for r in results:
        by = {f.field: f for f in r.fields}
        writer.writerow([
            r.label_id, STATUS_WORD[r.overall],
            *[STATUS_WORD[by[k].status] if k in by else "" for k in FIELD_LABELS],
            *[by[k].message if k in by else "" for k in FIELD_LABELS],
            r.error or "",
        ])
    return out.getvalue()


# ----------------------------------------------------------------- sidebar

with st.sidebar:
    st.header("Settings")
    key_configured = bool(os.getenv("ANTHROPIC_API_KEY"))
    try:
        key_configured = key_configured or "ANTHROPIC_API_KEY" in st.secrets
    except Exception:  # noqa: BLE001
        pass
    if key_configured:
        st.success("API key configured.")
    else:
        st.text_input("Anthropic API key", type="password", key="api_key",
                      help="Held for this session only. Never written to disk.")
    model = st.selectbox("Model", MODELS, index=0,
                         help="The default is fastest. Larger models can help with hard-to-read artwork.")
    st.divider()
    st.caption(
        "Checks brand name, class/type, alcohol content, net contents and the "
        "health warning statement. The model only reads the label; every "
        "pass/fail decision is made by tested rules."
    )

st.title("Alcohol Label Verification")
st.write("Compare label artwork against the application. Each field is reported as "
         "**matches**, **does not match**, or **needs a look**.")

single_tab, batch_tab = st.tabs(["Single label", "Batch"])

# ------------------------------------------------------------- single mode

with single_tab:
    left, right = st.columns([1, 1])
    with left:
        st.subheader("Application data")
        brand = st.text_input("Brand name", placeholder="Old Tom Distillery")
        class_type = st.text_input("Class / type", placeholder="Kentucky Straight Bourbon Whiskey")
        c1, c2 = st.columns(2)
        abv = c1.text_input("Alcohol content", placeholder="45% Alc./Vol.")
        net = c2.text_input("Net contents", placeholder="750 mL")
    with right:
        st.subheader("Label image")
        upload = st.file_uploader("Label artwork", type=["png", "jpg", "jpeg", "webp"],
                                  label_visibility="collapsed")
        if upload:
            st.image(upload)

    if st.button("Verify label", type="primary", disabled=not (upload and brand)):
        api_key = get_api_key()
        if not api_key:
            st.error("Add an API key in the sidebar first.")
        else:
            application = ApplicationRecord("Single label", brand, class_type, abv, net)
            start = time.perf_counter()
            with st.spinner("Reading the label..."):
                try:
                    extracted = extract_label(upload.getvalue(), make_client(api_key), model)
                    result = verify(application, extracted)
                except ExtractionError as exc:
                    result = VerificationResult("Single label", error=str(exc))
            result.seconds = time.perf_counter() - start
            show_result(result)

# -------------------------------------------------------------- batch mode

with batch_tab:
    st.write(
        "Upload a CSV with columns `" + "`, `".join(REQUIRED_COLUMNS)
        + "`, plus every image it names. Rows are matched to images by `image_file`."
    )
    csv_file = st.file_uploader("Applications CSV", type=["csv"], key="csv")
    images = st.file_uploader("Label images", type=["png", "jpg", "jpeg", "webp"],
                              accept_multiple_files=True, key="images")

    if st.button("Verify batch", type="primary", disabled=not (csv_file and images)):
        api_key = get_api_key()
        if not api_key:
            st.error("Add an API key in the sidebar first.")
            st.stop()
        try:
            applications = read_applications(csv_file.getvalue())
        except ValueError as exc:
            st.error(str(exc))
            st.stop()

        by_name = {img.name: img.getvalue() for img in images}
        jobs = [LabelJob(a, by_name.get(a.image_file)) for a in applications]
        progress = st.progress(0.0, text=f"Checking {len(jobs)} labels...")
        start = time.perf_counter()
        results = verify_batch(
            jobs, make_client(api_key), model,
            on_done=lambda done, total: progress.progress(done / total, text=f"{done} of {total} checked"),
        )
        elapsed = time.perf_counter() - start
        progress.empty()
        st.session_state["batch_results"] = (results, elapsed)

    if "batch_results" in st.session_state:
        results, elapsed = st.session_state["batch_results"]
        counts = {s: sum(r.overall is s for r in results) for s in Status}
        m = st.columns(4)
        m[0].metric("Labels", len(results))
        m[1].metric("Match", counts[Status.PASS])
        m[2].metric("Do not match", counts[Status.FAIL])
        m[3].metric("Need a look", counts[Status.REVIEW])
        st.caption(f"Batch finished in {elapsed:.1f} s")

        st.download_button("Download results CSV", results_csv(results),
                           file_name="verification_results.csv", mime="text/csv")

        order = {Status.FAIL: 0, Status.REVIEW: 1, Status.PASS: 2}
        for r in sorted(results, key=lambda r: order[r.overall]):
            with st.container(border=True):
                show_result(r)
