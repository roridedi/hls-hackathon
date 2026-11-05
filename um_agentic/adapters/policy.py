import math
import re
from collections import Counter
from typing import Protocol

from um_agentic.contracts import Contract, CriteriaSet


class PolicyHit(Contract):
    policy_id: str
    version: str
    text: str
    score: float


class PolicyRetriever(Protocol):
    def search(self, query: str, limit: int = 5) -> tuple[PolicyHit, ...]: ...


class InMemoryBM25:
    def __init__(self, criteria: tuple[CriteriaSet, ...]) -> None:
        self.documents = [
            (item.criteria_set_id, item.version, " ".join(c.description for c in item.criteria))
            for item in criteria
        ]

    def search(self, query: str, limit: int = 5) -> tuple[PolicyHit, ...]:
        terms = re.findall(r"\w+", query.lower())
        tokens = [re.findall(r"\w+", text.lower()) for _, _, text in self.documents]
        if not tokens or limit <= 0:
            return ()
        average_length = sum(map(len, tokens)) / len(tokens)
        scores: list[PolicyHit] = []
        for (identity, version, text), words in zip(self.documents, tokens, strict=True):
            counts = Counter(words)
            score = 0.0
            for term in terms:
                frequency = counts[term]
                document_frequency = sum(term in document for document in tokens)
                inverse = math.log(1 + (len(tokens) - document_frequency + 0.5) / (document_frequency + 0.5))
                score += inverse * frequency * 2.5 / (
                    frequency + 1.5 * (0.25 + 0.75 * len(words) / average_length)
                )
            if score:
                scores.append(PolicyHit(policy_id=identity, version=version, text=text, score=score))
        return tuple(sorted(scores, key=lambda hit: (-hit.score, hit.policy_id))[:limit])
