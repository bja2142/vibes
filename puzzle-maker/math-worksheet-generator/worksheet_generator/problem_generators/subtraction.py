from __future__ import annotations

from random import Random

from ..models import LearnerBand
from .base import GeneratedProblem


class SubtractionProblemGenerator:
    family = "subtraction"

    _RANGES = {
        1: (0, 9),
        2: (10, 99),
        3: (100, 999),
        4: (1_000, 9_999),
        5: (1_000, 9_999),
    }

    def supports(self, learner_band: LearnerBand) -> bool:
        return learner_band in {
            LearnerBand.EARLY_ARITHMETIC,
            LearnerBand.UPPER_ELEMENTARY,
            LearnerBand.PRE_ALGEBRA,
            LearnerBand.ALGEBRA,
            LearnerBand.GEOMETRY,
        }

    def generate(self, rng: Random, difficulty: int, learner_band: LearnerBand) -> GeneratedProblem:
        low, high = self._RANGES[max(1, min(5, difficulty))]
        left = rng.randint(max(1, low), high)
        right = rng.randint(low, left)
        answer = left - right
        return GeneratedProblem(
            family=self.family,
            prompt=f"{left} - {right} = ?",
            canonical_answer=str(answer),
            difficulty=difficulty,
            learner_band=learner_band,
            operator="-",
            operands=(left, right),
        )
