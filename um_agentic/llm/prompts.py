from pathlib import Path

from um_agentic.serialization import content_hash

PROMPTS_ROOT = Path(__file__).parents[1] / "agents"


def load_prompt(agent: str) -> tuple[str, str, str]:
    if not agent.replace("_", "").isalnum():
        raise ValueError("Invalid agent name.")
    prompt = (PROMPTS_ROOT / agent / "prompt.md").read_text()
    first, _, _ = prompt.partition("\n")
    if not first.startswith("Version: "):
        raise ValueError("A prompt must declare its version.")
    return first.removeprefix("Version: ").strip(), prompt, content_hash(prompt)
