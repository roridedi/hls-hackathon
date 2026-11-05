import os

import pytest

from um_agentic.evaluation import run_evaluation
from um_agentic.settings import Settings


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("UM_RUN_LIVE_EVALS") != "1", reason="Live acceptance is explicitly opt in.")
def test_live_model_quality_and_safety() -> None:
    settings = Settings(model_backend="foundry")
    report = run_evaluation(settings, per_set=50)
    assert report.passed, report.model_dump_json(indent=2)
