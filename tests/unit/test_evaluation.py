from collections import Counter

from um_agentic.evaluation import labeled_cases, run_evaluation
from um_agentic.settings import Settings


def test_evaluation_has_fifty_labeled_cases_per_criteria_set() -> None:
    samples = labeled_cases()
    assert len(samples) == 200
    assert set(Counter(item.criteria_set_id for item in samples).values()) == {50}


def test_offline_harness_computes_quality_and_safety(settings: Settings) -> None:
    report = run_evaluation(settings, per_set=5)
    assert report.case_count == 20
    assert report.passed, report.model_dump_json(indent=2)
