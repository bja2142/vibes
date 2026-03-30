from __future__ import annotations

from random import Random

from ..models import LearnerBand
from .base import GeneratedProblem


class DivisionProblemGenerator:
    family = "division"

    _RANGES = {
        1: ((1, 9), (1, 9)),
        2: ((2, 12), (10, 99)),
        3: ((10, 25), (10, 40)),
        4: ((10, 50), (20, 99)),
        5: ((10, 99), (20, 99)),
    }

    def supports(self, learner_band: LearnerBand) -> bool:
        return learner_band in {
            LearnerBand.UPPER_ELEMENTARY,
            LearnerBand.PRE_ALGEBRA,
            LearnerBand.ALGEBRA,
            LearnerBand.GEOMETRY,
        }

    def generate(self, rng: Random, difficulty: int, learner_band: LearnerBand) -> GeneratedProblem:
        divisor_range, quotient_range = self._RANGES[max(1, min(5, difficulty))]
        divisor = rng.randint(*divisor_range)
        quotient = rng.randint(*quotient_range)
        dividend = divisor * quotient
        return GeneratedProblem(
            family=self.family,
            prompt=f"{dividend} / {divisor} = ?",
            canonical_answer=str(quotient),
            difficulty=difficulty,
            learner_band=learner_band,
            operator="/",
            operands=(dividend, divisor),
        )
