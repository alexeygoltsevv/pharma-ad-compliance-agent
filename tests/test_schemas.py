from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from pharma_ad_compliance.schemas import (
    ComplianceReport,
    Creative,
    DrugClass,
    ImageCreative,
    PdfCreative,
    RuleId,
    Severity,
    TextCreative,
    UrlCreative,
    Violation,
)

CreativeAdapter: TypeAdapter[Creative] = TypeAdapter(Creative)


def test_text_creative_requires_non_empty_text():
    with pytest.raises(ValidationError):
        TextCreative(text="")


def test_creative_discriminator_dispatches_correctly():
    text = CreativeAdapter.validate_python({"kind": "text", "text": "hello"})
    img = CreativeAdapter.validate_python({"kind": "image", "image_path": "/tmp/x.png"})
    url = CreativeAdapter.validate_python({"kind": "url", "url": "https://example.com/ad"})
    pdf = CreativeAdapter.validate_python({"kind": "pdf", "pdf_path": "/tmp/x.pdf"})

    assert isinstance(text, TextCreative)
    assert isinstance(img, ImageCreative) and img.image_path == Path("/tmp/x.png")
    assert isinstance(url, UrlCreative) and str(url.url).startswith("https://")
    assert isinstance(pdf, PdfCreative) and pdf.pdf_path == Path("/tmp/x.pdf")


def test_unknown_kind_rejected():
    with pytest.raises(ValidationError):
        CreativeAdapter.validate_python({"kind": "video", "text": "x"})


def test_severity_rank_ordering():
    assert Severity.CRITICAL.rank < Severity.WARNING.rank < Severity.RECOMMENDATION.rank


def test_violation_minimal_payload():
    v = Violation(
        rule_id=RuleId.ART24_P5_MANDATORY_DISCLAIMER,
        severity=Severity.CRITICAL,
        explanation="Отсутствует обязательное предупреждение.",
    )
    assert v.quote is None
    assert v.suggested_fix is None


def test_compliance_report_is_compliant_flag():
    report = ComplianceReport(
        source_kind="text",
        drug_class=DrugClass.OTC,
        extracted_text="...",
        violations=[
            Violation(
                rule_id=RuleId.ART24_OTHER,
                severity=Severity.RECOMMENDATION,
                explanation="Лучше переформулировать.",
            )
        ],
    )
    assert report.is_compliant is True


def test_compliance_report_critical_marks_noncompliant():
    report = ComplianceReport(
        source_kind="text",
        drug_class=DrugClass.OTC,
        extracted_text="...",
        violations=[
            Violation(
                rule_id=RuleId.ART24_P3_NO_SIDE_EFFECTS,
                severity=Severity.CRITICAL,
                explanation="...",
            )
        ],
    )
    assert report.is_compliant is False
    assert report.by_severity(Severity.CRITICAL)
