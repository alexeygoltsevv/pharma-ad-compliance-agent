"""Streamlit demo. Run with `streamlit run src/pharma_ad_compliance/app.py`."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import streamlit as st

from pharma_ad_compliance.pipeline import run_compliance
from pharma_ad_compliance.schemas import (
    ImageCreative,
    Severity,
    TextCreative,
    UrlCreative,
)

st.set_page_config(page_title="Pharma Ad Compliance", page_icon="💊", layout="wide")

st.title("💊 Pharma Ad Compliance")
st.caption(
    "Multi-agent compliance checker for Russian pharmaceutical advertising — "
    "FZ-38 article 24. Powered by Claude Agent SDK."
)

mode = st.radio("Input type", ["Text", "Image", "URL"], horizontal=True)

creative = None
if mode == "Text":
    text = st.text_area("Ad creative text", height=180, placeholder="Вставьте текст рекламного объявления…")
    if text.strip():
        creative = TextCreative(text=text)
elif mode == "Image":
    uploaded = st.file_uploader("Banner image", type=["png", "jpg", "jpeg", "webp", "gif"])
    if uploaded:
        suffix = Path(uploaded.name).suffix or ".png"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(uploaded.read())
        tmp.close()
        creative = ImageCreative(image_path=Path(tmp.name))
        st.image(tmp.name, caption=uploaded.name, width=320)
else:
    url = st.text_input("Landing page URL", placeholder="https://example.com/landing")
    if url.strip():
        creative = UrlCreative(url=url)  # type: ignore[arg-type]

include_rewrite = st.checkbox("Generate compliant rewrite", value=True)

if st.button("Run compliance check", type="primary", disabled=creative is None):
    with st.spinner("Running 6 rule checkers in parallel…"):
        report = asyncio.run(run_compliance(creative, include_rewrite=include_rewrite))

    cols = st.columns(3)
    cols[0].metric("Drug class", report.drug_class.value)
    cols[1].metric("Findings", len(report.violations))
    cols[2].metric("Compliant", "Yes" if report.is_compliant else "No")

    if report.violations:
        st.subheader("Findings")
        for sev in (Severity.CRITICAL, Severity.WARNING, Severity.RECOMMENDATION):
            chunk = report.by_severity(sev)
            if not chunk:
                continue
            with st.expander(f"{sev.value} ({len(chunk)})", expanded=sev is Severity.CRITICAL):
                for v in chunk:
                    st.markdown(f"**{v.rule_id.value}**")
                    if v.quote:
                        st.markdown(f"> {v.quote}")
                    st.markdown(v.explanation)
                    if v.suggested_fix:
                        st.markdown(f"_Suggested fix:_ {v.suggested_fix}")
                    st.divider()
    else:
        st.success("No violations found.")

    if report.rewritten_text:
        st.subheader("Compliant rewrite")
        st.write(report.rewritten_text)

    with st.expander("Raw JSON"):
        st.code(report.model_dump_json(indent=2), language="json")
