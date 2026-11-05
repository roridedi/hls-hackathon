from pydantic import JsonValue

from um_agentic.agents.base import AgentSpec, ReadTool
from um_agentic.agents.insights.schema import Output

SPEC = AgentSpec("insights", Output, (ReadTool.ANALYTICS,))


def summarize(inputs: dict[str, JsonValue]) -> Output:
    codes = inputs.get("codes")
    findings: list[str] = []
    if isinstance(codes, list):
        for code in codes:
            if isinstance(code, dict) and code.get("candidate_to_remove_pa") is True:
                findings.append(
                    f"Service {code['code']} has a high approval rate and meaningful volume. "
                    "Consider a human led review of its authorization requirement."
                )
    if not findings:
        findings.append("No service meets the configured approval and volume thresholds for an advisory review.")
    return Output(findings=tuple(findings))
