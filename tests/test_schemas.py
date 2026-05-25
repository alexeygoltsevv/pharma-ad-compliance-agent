from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from pharma_ad_compliance.schemas import (
    CaseRef,
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


def test_violation_is_frozen():
    v = Violation(
        rule_id=RuleId.ART24_OTHER,
        severity=Severity.WARNING,
        explanation="...",
    )
    with pytest.raises(ValidationError):
        v.severity = Severity.CRITICAL  # type: ignore[misc]


def test_violation_is_hashable():
    """Aggregator dedupes by tuple keys derived from Violation — must hash cleanly."""
    v1 = Violation(
        rule_id=RuleId.ART24_OTHER,
        severity=Severity.WARNING,
        quote="x",
        explanation="...",
    )
    v2 = Violation(
        rule_id=RuleId.ART24_OTHER,
        severity=Severity.WARNING,
        quote="x",
        explanation="...",
    )
    # hash + put in dict/set without TypeError.
    assert hash(v1) == hash(v2)
    bucket = {v1: 1}
    bucket[v2] = 2  # same hash → may stay one or two slots, but must not raise
    assert {v1, v2}  # set construction


def test_violation_intent_hypothesis_defaults_to_none():
    """New `intent_hypothesis` field is optional — old payloads (without it) still validate."""
    v = Violation(
        rule_id=RuleId.ART24_OTHER,
        severity=Severity.WARNING,
        explanation="...",
    )
    assert v.intent_hypothesis is None


def test_violation_intent_hypothesis_roundtrips():
    """When set, intent_hypothesis survives JSON dump → load and stays on the model."""
    v = Violation(
        rule_id=RuleId.ART24_P3_NO_SIDE_EFFECTS,
        severity=Severity.CRITICAL,
        quote="полностью безопасен",
        explanation="Гарантия безопасности запрещена ч. 1 п. 8 ст. 24.",
        suggested_fix="хорошо переносится по данным КИ",
        intent_hypothesis=(
            "Бренд снимал страх побочки — дал абсолютное утверждение вместо описания "
            "профиля переносимости."
        ),
    )
    assert v.intent_hypothesis is not None
    assert v.intent_hypothesis.startswith("Бренд снимал страх")
    restored = Violation.model_validate_json(v.model_dump_json())
    assert restored.intent_hypothesis == v.intent_hypothesis
    # Round-trip via dict too (the pipeline frequently goes through model_dump).
    restored2 = Violation.model_validate(v.model_dump())
    assert restored2 == v


def test_compliance_report_diagnostics_default_empty():
    report = ComplianceReport(
        source_kind="text",
        drug_class=DrugClass.OTC,
        extracted_text="...",
    )
    assert report.diagnostics == {}


def test_compliance_report_with_failed_checkers_roundtrips():
    report = ComplianceReport(
        source_kind="text",
        drug_class=DrugClass.OTC,
        extracted_text="...",
        diagnostics={"failed_checkers": ["art24_p1_minors", "art24_other"]},
    )
    payload = report.model_dump_json()
    restored = ComplianceReport.model_validate_json(payload)
    assert restored.diagnostics == {
        "failed_checkers": ["art24_p1_minors", "art24_other"]
    }


def test_case_ref_minimal_payload():
    ref = CaseRef(
        case_id="2020-09-07-bionorica-canephron",
        date="2020-09-07",
        party="Бионорика",
        source="fas",
    )
    assert ref.fine_rub is None
    assert ref.url is None
    assert ref.short_quote is None


def test_case_ref_full_payload_roundtrips():
    ref = CaseRef(
        case_id="2020-09-07-bionorica-canephron",
        date="2020-09-07",
        party="Бионорика",
        fine_rub=200000,
        source="fas",
        url="https://example.com/x",
        short_quote="Канефрон Н — гарантия эффекта",
    )
    restored = CaseRef.model_validate_json(ref.model_dump_json())
    assert restored == ref


def test_case_ref_source_literal_validated():
    with pytest.raises(ValidationError):
        CaseRef(
            case_id="x", date="2020-01-01", party="x",
            source="invalid-source",  # type: ignore[arg-type]
        )


def test_case_ref_is_frozen():
    ref = CaseRef(case_id="x", date="2020-01-01", party="x", source="approved")
    with pytest.raises(ValidationError):
        ref.party = "y"  # type: ignore[misc]


def test_violation_precedents_default_empty():
    """Existing payloads (without `precedents`) still validate cleanly.

    Stored as a tuple (not list) so Violation stays hashable for aggregator
    dedup; old payloads emit `[]` in JSON and pydantic coerces transparently.
    """
    v = Violation(
        rule_id=RuleId.ART24_OTHER,
        severity=Severity.WARNING,
        explanation="...",
    )
    assert v.precedents == ()
    # JSON-shape stays array — pydantic serializes tuples as JSON arrays.
    import json as _json
    payload = _json.loads(v.model_dump_json())
    assert payload["precedents"] == []


def test_violation_precedents_roundtrip():
    ref = CaseRef(
        case_id="2024-05-07-nizhfarm-artra",
        date="2024-05-07",
        party="Нижфарм",
        fine_rub=None,
        source="fas",
    )
    v = Violation(
        rule_id=RuleId.ART24_P3_NO_SIDE_EFFECTS,
        severity=Severity.CRITICAL,
        explanation="...",
        precedents=[ref],
    )
    restored = Violation.model_validate_json(v.model_dump_json())
    assert len(restored.precedents) == 1
    assert restored.precedents[0] == ref
