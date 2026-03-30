from __future__ import annotations

from dataclasses import dataclass, field
from random import Random
from typing import Protocol

from ..models import LearnerBand


@dataclass(frozen=True)
class GeneratedProblem:
    family: str
    prompt: str
    canonical_answer: str
    difficulty: int
    learner_band: LearnerBand
    operator: str | None = None
    operands: tuple[int, ...] = ()
    verification_kind: str = "binary_operation"
    metadata: dict[str, int | str] = field(default_factory=dict)


class ProblemGenerator(Protocol):
    family: str

    def supports(self, learner_band: LearnerBand) -> bool:
        ...

    def generate(self, rng: Random, difficulty: int, learner_band: LearnerBand) -> GeneratedProblem:
        ...
