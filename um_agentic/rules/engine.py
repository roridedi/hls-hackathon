from datetime import date
from operator import eq, ge, gt, le, lt

from um_agentic.contracts import (
    Contract,
    Contradiction,
    CriteriaEvaluation,
    CriteriaSet,
    CriterionResult,
    CriterionStatus,
    EvidenceItem,
    Rule,
)


class RuleResult(Contract):
    status: CriterionStatus
    evidence_ids: tuple[str, ...]
    rationale: str


def _therapy_days(item: EvidenceItem) -> int | None:
    if not isinstance(item.normalized_value, dict) or item.unit != "days":
        return None
    days = item.normalized_value.get("days")
    return days if type(days) is int and days >= 0 else None


def _result(status: CriterionStatus, items: tuple[EvidenceItem, ...], reason: str) -> RuleResult:
    return RuleResult(
        status=status, evidence_ids=tuple(sorted({e.evidence_id for e in items})), rationale=reason,
    )


def _combine(results: tuple[RuleResult, ...], any_of: bool) -> RuleResult:
    if any_of:
        selected = tuple(result for result in results if result.status == CriterionStatus.MET)
        if selected:
            status = CriterionStatus.MET
        elif any(r.status == CriterionStatus.UNKNOWN for r in results):
            status, selected = CriterionStatus.UNKNOWN, results
        else:
            status, selected = CriterionStatus.NOT_MET, results
    elif any(r.status == CriterionStatus.NOT_MET for r in results):
        status, selected = CriterionStatus.NOT_MET, results
    elif any(r.status == CriterionStatus.UNKNOWN for r in results):
        status, selected = CriterionStatus.UNKNOWN, results
    else:
        status, selected = CriterionStatus.MET, results
    return RuleResult(
        status=status,
        evidence_ids=tuple(sorted({e for r in selected for e in r.evidence_ids})),
        rationale=" ".join(dict.fromkeys(r.rationale for r in selected)),
    )


def evaluate_rule(rule: Rule, evidence: tuple[EvidenceItem, ...], as_of: date) -> RuleResult:
    if rule.any_of is not None:
        return _combine(tuple(evaluate_rule(r, evidence, as_of) for r in rule.any_of), True)
    if rule.all_of is not None:
        return _combine(tuple(evaluate_rule(r, evidence, as_of) for r in rule.all_of), False)
    if rule.any_evidence is not None:
        selector = rule.any_evidence
        items = tuple(item for item in evidence if item.fact_type == selector.fact_type)
        matches = tuple(
            item for item in items
            if (selector.code_prefix is None or (
                isinstance(item.normalized_value, str)
                and item.normalized_value.startswith(selector.code_prefix)
            ))
            and (selector.target is None or (
                isinstance(item.normalized_value, dict)
                and item.normalized_value.get("target") == selector.target
                and item.normalized_value.get("present") is True
            ))
        )
        if matches:
            return _result(CriterionStatus.MET, matches, "Verified evidence matches the required fact.")
        return _result(
            CriterionStatus.NOT_MET if items else CriterionStatus.UNKNOWN, items,
            "Verified facts do not match the requirement." if items else "Required evidence is missing.",
        )
    if rule.therapy_duration is not None:
        therapy = rule.therapy_duration
        candidates = tuple(
            item for item in evidence
            if item.fact_type == "therapy_duration"
            and isinstance(item.normalized_value, dict)
            and item.normalized_value.get("drug_class") == therapy.drug_class
        )
        valid = tuple(
            item for item in candidates
            if _therapy_days(item) is not None
        )
        if not valid:
            return _result(CriterionStatus.UNKNOWN, candidates, "A supported therapy duration is missing.")
        matches = tuple(
            item for item in valid
            if (days := _therapy_days(item)) is not None and days >= therapy.min_days
        )
        if matches and len(matches) != len(valid):
            return _result(CriterionStatus.UNKNOWN, valid, "Conflicting therapy durations require review.")
        return _result(
            CriterionStatus.MET if matches else CriterionStatus.NOT_MET, matches or valid,
            "Verified therapy duration meets the requirement." if matches
            else "Verified therapy duration is below the required duration.",
        )
    assert rule.evidence_value is not None
    value_rule = rule.evidence_value
    candidates = tuple(item for item in evidence if item.fact_type == value_rule.fact_type)
    expected_unit = {
        "lab_a1c": "%", "bmi": "kg/m2", "back_pain_duration": "days",
    }.get(value_rule.fact_type)
    valid_values: list[EvidenceItem] = []
    met: list[EvidenceItem] = []
    compare = {"gt": gt, "gte": ge, "lt": lt, "lte": le, "eq": eq}[value_rule.op]
    for item in candidates:
        if expected_unit is not None and item.unit != expected_unit:
            continue
        if value_rule.within_days is not None and (
            item.effective_date is None
            or not 0 <= (as_of - item.effective_date).days <= value_rule.within_days
        ):
            continue
        actual, expected = item.normalized_value, value_rule.value
        if isinstance(expected, bool):
            if type(actual) is not bool or value_rule.op != "eq":
                continue
        elif isinstance(expected, (float, int)):
            if not isinstance(actual, (float, int)) or isinstance(actual, bool):
                continue
        elif not isinstance(actual, str) or value_rule.op != "eq":
            continue
        valid_values.append(item)
        if compare(actual, expected):
            met.append(item)
    if not valid_values:
        return _result(CriterionStatus.UNKNOWN, candidates, "Required current evidence is missing or invalid.")
    if met and len(met) != len(valid_values):
        return _result(CriterionStatus.UNKNOWN, tuple(valid_values), "Conflicting supported values require review.")
    return _result(
        CriterionStatus.MET if met else CriterionStatus.NOT_MET, tuple(met or valid_values),
        "Verified value meets the criterion." if met else "Verified value does not meet the criterion.",
    )


def evaluate(
    criteria: CriteriaSet,
    evidence: tuple[EvidenceItem, ...],
    as_of: date,
    contradictions: tuple[Contradiction, ...] = (),
) -> CriteriaEvaluation:
    disputed = {identity for item in contradictions for identity in item.evidence_ids}
    verified = tuple(item for item in evidence if item.verified and item.evidence_id not in disputed)
    results: list[CriterionResult] = []
    for criterion in criteria.criteria:
        if criterion.requires_clinical_judgment:
            result = RuleResult(
                status=CriterionStatus.UNKNOWN, evidence_ids=(),
                rationale="This criterion requires human clinical judgment.",
            )
        else:
            assert criterion.rule is not None
            result = evaluate_rule(criterion.rule, verified, as_of)
        results.append(CriterionResult(criterion_id=criterion.id, **result.model_dump()))
    overall_result = _combine(tuple(
        RuleResult(**result.model_dump(exclude={"criterion_id"})) for result in results
    ), criteria.logic.any_of is not None)
    overall = {
        CriterionStatus.MET: "ALL_MET",
        CriterionStatus.NOT_MET: "GAPS",
        CriterionStatus.UNKNOWN: "INDETERMINATE",
    }[overall_result.status]
    return CriteriaEvaluation(
        criteria_set_id=criteria.criteria_set_id, version=criteria.version,
        results=tuple(results), overall=overall,
    )
