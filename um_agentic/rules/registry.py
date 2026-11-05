from pathlib import Path
from typing import Protocol

import yaml

from um_agentic.contracts import CriteriaEvaluation, CriteriaSet, EvidenceItem, UMCase
from um_agentic.errors import InvalidRequest


class LicensedCriteriaAdapter(Protocol):
    def evaluate(
        self, case: UMCase, evidence: tuple[EvidenceItem, ...]
    ) -> CriteriaEvaluation: ...


class CriteriaRegistry:
    def __init__(self, path: Path | None = None) -> None:
        root = path or Path(__file__).parents[1] / "criteria"
        self.sets = tuple(
            CriteriaSet.model_validate(yaml.safe_load(file.read_text()))
            for file in sorted(root.glob("*.yaml"))
        )
        identities = [(c.criteria_set_id, c.version) for c in self.sets]
        if len(set(identities)) != len(identities):
            raise InvalidRequest("Criteria versions must be unique and immutable.")

    def for_case(self, case: UMCase) -> CriteriaSet:
        matches = [
            criteria for criteria in self.sets
            if criteria.applies_to.line == case.line_of_business
            and criteria.applies_to.codes.system == case.service.code_system
            and case.service.code in criteria.applies_to.codes.values
        ]
        if not matches:
            raise InvalidRequest("No versioned criteria are configured for this service.")
        if len({c.criteria_set_id for c in matches}) != 1:
            raise InvalidRequest("Multiple criteria sets apply. A human must select the policy.")
        return max(matches, key=lambda c: tuple(int(part) for part in c.version.split(".")))
