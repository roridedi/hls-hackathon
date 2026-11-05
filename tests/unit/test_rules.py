from datetime import date

import pytest

from um_agentic.adapters.intake import IntakeEnvelope, normalize
from um_agentic.adapters.sources import extract_sources
from um_agentic.contracts import CriteriaSet, CriterionStatus
from um_agentic.rules import CriteriaRegistry, evaluate
from um_agentic.synthetic import golden_fixture


def glp1() -> CriteriaSet:
    return next(c for c in CriteriaRegistry().sets if c.criteria_set_id == "glp1_t2d")


@pytest.mark.parametrize(("a1c", "expected"), [(6.99, "NOT_MET"), (7.0, "MET"), (7.01, "MET")])
def test_exact_a1c_boundary(a1c: float, expected: str) -> None:
    envelope = IntakeEnvelope.model_validate(golden_fixture("G1")["request"])
    evidence = tuple(item.model_copy(update={"verified": True}) for item in extract_sources(normalize(envelope).sources))
    changed = tuple(
        item.model_copy(update={"normalized_value": a1c}) if item.fact_type == "lab_a1c" else item
        for item in evidence
    )
    result = evaluate(glp1(), changed, date(2026, 10, 30))
    assert next(r for r in result.results if r.criterion_id == "a1c_threshold").status.value == expected


@pytest.mark.parametrize("effective", [date(2025, 1, 1), date(2027, 1, 1), None])
def test_stale_future_or_undated_lab_is_unknown(effective: date | None) -> None:
    envelope = IntakeEnvelope.model_validate(golden_fixture("G1")["request"])
    evidence = tuple(item.model_copy(update={"verified": True}) for item in extract_sources(normalize(envelope).sources))
    evidence = tuple(
        item.model_copy(update={"effective_date": effective}) if item.fact_type == "lab_a1c" else item
        for item in evidence
    )
    result = evaluate(glp1(), evidence, date(2026, 10, 30))
    assert next(r for r in result.results if r.criterion_id == "a1c_threshold").status == CriterionStatus.UNKNOWN


def test_unverified_facts_never_satisfy_criteria() -> None:
    envelope = IntakeEnvelope.model_validate(golden_fixture("G1")["request"])
    result = evaluate(glp1(), extract_sources(normalize(envelope).sources), date(2026, 10, 30))
    assert all(r.status == CriterionStatus.UNKNOWN for r in result.results)
