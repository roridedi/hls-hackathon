import re
from typing import Protocol

from um_agentic.contracts import CaseAggregate, DeterminationOutcome, LetterDraftOutput
from um_agentic.errors import InvalidRequest
from um_agentic.settings import Configuration


class Translator(Protocol):
    def translate(self, text: str, language: str) -> str: ...


def plain_punctuation(text: str) -> str:
    return re.sub(r"\s+[-\u2013\u2014]\s+", ". ", text).replace("\u2013", ", ").replace("\u2014", ". ")


def reading_grade(text: str) -> float:
    words = re.findall(r"[A-Za-z]+", text)
    if not words:
        return 0
    sentences = max(1, len(re.findall(r"[.!?]+", text)))
    syllables = 0
    for word in words:
        lowered = word.lower()
        groups = len(re.findall(r"[aeiouy]+", lowered))
        if lowered.endswith("e") and not lowered.endswith(("le", "ye")) and groups > 1:
            groups -= 1
        syllables += max(1, groups)
    return max(0, 0.39 * len(words) / sentences + 11.8 * syllables / len(words) - 15.59)


def draft_text(case: CaseAggregate, config: Configuration) -> LetterDraftOutput:
    determination = case.determination
    if determination is None and not case.automatic_approval:
        raise InvalidRequest("A determination is required before drafting letters.")
    outcome = determination.outcome if determination else DeterminationOutcome.APPROVED
    heading = {
        DeterminationOutcome.APPROVED: "Your request was approved.",
        DeterminationOutcome.PARTIALLY_APPROVED: "Part of your request was approved.",
        DeterminationOutcome.DENIED: "Your request was not approved.",
        DeterminationOutcome.WITHDRAWN: "Your request was withdrawn.",
    }[outcome]
    reason = (
        determination.plain_language_reason if determination and determination.plain_language_reason
        else "The records provided support approval under the plan rules for this request."
    )
    reason_code = determination.specific_reason_code if determination else "RULES_APPROVAL"
    quantity = determination.approved_quantity if determination else case.case.service.quantity
    duration = determination.approved_duration_days if determination else case.case.service.days_supply or 1
    approval = (
        f"The approved amount is {quantity:g}. The approval covers {duration} days. "
        if quantity is not None else ""
    )
    criteria = (
        f"Criteria reference: {case.criteria_snapshot.criteria_set_id}, version {case.criteria_snapshot.version}. "
        if case.criteria_snapshot else ""
    )
    override = (
        "A licensed reviewer changed the earlier recommendation. "
        f"Reason for the change: {determination.override_rationale}. "
        if determination and determination.override_of_recommendation else ""
    )
    member = (
        f"{heading} This notice explains the determination for the care your provider requested. "
        f"Reason: {reason} {approval}{override}"
        f"{config.appeal_rights} "
        "You may ask your provider to explain this notice and help you understand your options. "
        "Language assistance and accessible formats are available through your plan."
    )
    provider = (
        f"Request decision: {outcome.value}. Service: {case.case.service.code}. "
        f"Specific reason code: {reason_code or 'CLINICIAN_APPROVAL'}. Reason: {reason} "
        f"{approval}{criteria}{override}{config.appeal_rights}"
    )
    return LetterDraftOutput(
        member_text=plain_punctuation(member), provider_text=plain_punctuation(provider),
    )


def validate_release_text(
    member_text: str, provider_text: str, case: CaseAggregate, config: Configuration,
) -> None:
    for text in (member_text, provider_text):
        if config.appeal_rights not in text:
            raise InvalidRequest("Every letter must retain the configured appeal rights.")
        if re.search(r"\s[-\u2013\u2014]\s", text):
            raise InvalidRequest("Use periods or commas instead of dash punctuation in letters.")
        if case.determination is not None:
            decision = case.determination
            if decision.plain_language_reason and plain_punctuation(decision.plain_language_reason) not in text:
                raise InvalidRequest("Every letter must retain the specific determination reason.")
            if decision.override_of_recommendation and plain_punctuation(decision.override_rationale or "") not in text:
                raise InvalidRequest("Every letter must retain the override rationale.")
    if reading_grade(member_text) > 8:
        raise InvalidRequest("The member letter exceeds the eighth grade readability target. Edit it before release.")
