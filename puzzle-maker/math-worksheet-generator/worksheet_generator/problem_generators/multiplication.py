from __future__ import annotations

from random import Random

from ..models import LearnerBand
from .base import GeneratedProblem


class MultiplicationProblemGenerator:
    family = "multiplication"

    _RANGES = {
        1: ((0, 9), (0, 9)),
        2: ((10, 99), (2, 12)),
        3: ((100, 999), (10, 99)),
        4: ((1_000, 9_999), (10, 99)),
        5: ((1_000, 9_999), (10, 99)),
    }

    def supports(self, learner_band: LearnerBand) -> bool:
        return learner_band in {
            LearnerBand.UPPER_ELEMENTARY,
            LearnerBand.PRE_ALGEBRA,
            LearnerBand.ALGEBRA,
            LearnerBand.GEOMETRY,
        }

    def generate(self, rng: Random, difficulty: int, learner_band: LearnerBand) -> GeneratedProblem:
        left_range, right_range = self._RANGES[max(1, min(5, difficulty))]
        left = rng.randint(*left_range)
        right = rng.randint(*right_range)
        answer = left * right
        return GeneratedProblem(
            family=self.family,
            prompt=f"{left} x {right} = ?",
            canonical_answer=str(answer),
            difficulty=difficulty,
            learner_band=learner_band,
            operator="*",
            operands=(left, right),
        )
