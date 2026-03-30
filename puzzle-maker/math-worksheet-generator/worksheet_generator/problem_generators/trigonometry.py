from __future__ import annotations

from random import Random

from ..models import LearnerBand
from .base import GeneratedProblem


class TrigonometryProblemGenerator:
    family = "trigonometry_problem"

    def supports(self, learner_band: LearnerBand) -> bool:
        return learner_band == LearnerBand.GEOMETRY

    def generate(self, rng: Random, difficulty: int, learner_band: LearnerBand) -> GeneratedProblem:
        difficulty = max(1, min(5, difficulty))
        if difficulty <= 2:
            return self._generate_tangent_problem(rng, difficulty, learner_band)
        return self._generate_sine_problem(rng, difficulty, learner_band)

    def _generate_tangent_problem(self, rng: Random, difficulty: int, learner_band: LearnerBand) -> GeneratedProblem:
        ratios = ((3, 4), (4, 3), (5, 12), (8, 15))
        rise, run = rng.choice(ratios)
        scale = rng.randint(2, 2 + difficulty)
        adjacent = run * scale
        opposite = rise * scale
        return GeneratedProblem(
            family=self.family,
            prompt=(
                f"Use tangent: In the right triangle, tan(θ) = {rise}/{run} and the adjacent side is "
                f"{adjacent}. What is the opposite side?"
            ),
            canonical_answer=str(opposite),
            difficulty=difficulty,
            learner_band=learner_band,
            verification_kind="geometry",
            metadata={
                "template": "right_triangle_tangent",
                "ratio_numerator": rise,
                "ratio_denominator": run,
                "adjacent": adjacent,
                "opposite": opposite,
                "diagram_kind": "right_triangle",
                "base_label": str(adjacent),
                "vertical_label": "?",
                "hypotenuse_label": "",
                "angle_label": "θ",
            },
        )

    def _generate_sine_problem(self, rng: Random, difficulty: int, learner_band: LearnerBand) -> GeneratedProblem:
        ratios = ((3, 5), (5, 13), (8, 17))
        rise, hyp_ratio = rng.choice(ratios)
        scale = rng.randint(2, 1 + difficulty)
        hypotenuse = hyp_ratio * scale
        opposite = rise * scale
        return GeneratedProblem(
            family=self.family,
            prompt=(
                f"Use sine: In the right triangle, sin(θ) = {rise}/{hyp_ratio} and the hypotenuse is "
                f"{hypotenuse}. What is the opposite side?"
            ),
            canonical_answer=str(opposite),
            difficulty=difficulty,
            learner_band=learner_band,
            verification_kind="geometry",
            metadata={
                "template": "right_triangle_sine",
                "ratio_numerator": rise,
                "ratio_denominator": hyp_ratio,
                "hypotenuse": hypotenuse,
                "opposite": opposite,
                "diagram_kind": "right_triangle",
                "base_label": "",
                "vertical_label": "?",
                "hypotenuse_label": str(hypotenuse),
                "angle_label": "θ",
            },
        )
