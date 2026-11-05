from collections.abc import Sequence
from typing import Protocol

from um_agentic.contracts import CoverageResult, UMCase
from um_agentic.synthetic import SyntheticMember, generate_members


class EligibilityService(Protocol):
    def check(self, case: UMCase) -> CoverageResult: ...


class MockEligibility:
    def __init__(self, members: Sequence[SyntheticMember] | None = None) -> None:
        self.members = {member.member_token: member for member in (
            members if members is not None else generate_members()
        )}
        self.plan_rules = {
            ("PHARMACY", "RXNORM_CLASS", "GLP1"): True,
            ("PHARMACY", "RXNORM_CLASS", "GLP1_WEIGHT"): True,
            ("PHARMACY", "RXNORM_CLASS", "STEP_DRUG"): True,
            ("MEDICAL", "CPT", "72148"): True,
            ("MEDICAL", "CPT", "99213"): False,
        }

    def check(self, case: UMCase) -> CoverageResult:
        member = self.members.get(case.member_token)
        eligible = bool(
            member and member.eligible
            and member.coverage_start <= case.date_of_service <= member.coverage_end
        )
        key = (case.line_of_business.value, case.service.code_system, case.service.code)
        known_benefit = key in self.plan_rules
        pa_required = self.plan_rules.get(key, True)
        if not eligible:
            reason = "Eligibility could not be confirmed on the date of service."
        elif not known_benefit:
            reason = "No synthetic benefit rule is configured. Administrative review is required."
        elif not pa_required:
            reason = "The synthetic plan does not require prior authorization for this service."
        else:
            reason = "Eligibility and benefit confirmed. Prior authorization is required."
        return CoverageResult(
            eligible=eligible, benefit_covered=known_benefit,
            pa_required=pa_required, reason=reason, checked_date=case.date_of_service,
        )
