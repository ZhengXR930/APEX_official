"""MELON prompt-injection detector boundary."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Sequence


@dataclass(frozen=True)
class Detection:
    blocked: bool
    score: float


class MELON:
    """Compare task and observation embeddings with a configurable threshold."""

    def __init__(self, embed: Callable[[str], Sequence[float]], threshold: float = 0.1):
        self.embed = embed
        self.threshold = float(threshold)

    @staticmethod
    def cosine(left: Sequence[float], right: Sequence[float]) -> float:
        numerator = sum(a * b for a, b in zip(left, right))
        lnorm = math.sqrt(sum(value * value for value in left))
        rnorm = math.sqrt(sum(value * value for value in right))
        return numerator / (lnorm * rnorm) if lnorm and rnorm else 0.0

    def check(self, task: str, observation: str) -> Detection:
        similarity = self.cosine(self.embed(task), self.embed(observation))
        score = 1.0 - similarity
        return Detection(score >= self.threshold, score)
