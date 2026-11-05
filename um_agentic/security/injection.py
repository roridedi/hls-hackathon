import re
import unicodedata

PATTERNS = tuple(re.compile(pattern, re.IGNORECASE | re.DOTALL) for pattern in (
    r"ignore\s+(?:all\s+)?(?:prior|previous|system|above)\s+(?:instructions|prompts|rules)",
    r"(?:system|assistant|developer)\s*(?:message|prompt)?\s*:",
    r"(?:override|bypass)\s+(?:the\s+)?(?:criteria|safety|instructions|policy)",
    r"(?:send|exfiltrate|upload)\s+.{0,80}(?:https?://|credentials|secret)",
    r"<\|(?:im_start|system|assistant)",
    r"(?:automatically|always)\s+(?:approve|deny)\s+(?:this|the)\s+(?:request|case)",
))


def detect_injection(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Cf")
    return any(pattern.search(normalized) for pattern in PATTERNS)
