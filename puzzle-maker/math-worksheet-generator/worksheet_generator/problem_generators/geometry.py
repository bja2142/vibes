from __future__ import annotations

from random import Random

from ..models import LearnerBand
from .base import GeneratedProblem


class GeometryProblemGenerator:
    family = "geometry_problem"

    def supports(self, learner_band: LearnerBand) -> bool:
        return learner_band == LearnerBand.GEOMETRY

    def generate(self, rng: Random, difficulty: int, learner_band: LearnerBand) -> GeneratedProblem:
        difficulty = max(1, min(5, difficulty))
        if difficulty == 1:
            return self._generate_rectangle_perimeter_problem(rng, difficulty, learner_band)
        if difficulty == 2:
            return self._generate_rectangle_area_problem(rng, difficulty, learner_band)
        if difficulty == 3:
            return self._generate_right_triangle_leg_problem(rng, difficulty, learner_band)
        if difficulty == 4:
            return self._generate_tangent_problem(rng, difficulty, learner_band)
        return self._generate_sine_problem(rng, difficulty, learner_band)

    def _generate_rectangle_perimeter_problem(
        self,
        rng: Random,
        difficulty: int,
        learner_band: LearnerBand,
    ) -> GeneratedProblem:
        width = rng.randint(4, 12)
        length = rng.randint(width + 2, width + 12)
        perimeter = 2 * (length + width)
        return GeneratedProblem(
            family=self.family,
            prompt=f"Find the missing side: A rectangle has perimeter {perimeter} and length {length}. What is the width?",
            canonical_answer=str(width),
            difficulty=difficulty,
            learner_band=learner_band,
            verification_kind="geometry",
            metadata={
                "template": "rectangle_perimeter_missing_width",
                "length": length,
                "width": width,
                "perimeter": perimeter,
                "diagram_kind": "rectangle",
                "known_label": str(length),
                "missing_label": "w",
                "interior_label": f"P={perimeter}",
            },
        )

    def _generate_rectangle_area_problem(self, rng: Random, difficulty: int, learner_band: LearnerBand) -> GeneratedProblem:
        width = rng.randint(4, 12)
        length = rng.randint(width + 1, width + 8)
        area = length * width
        return GeneratedProblem(
            family=self.family,
            prompt=f"Find the missing side: A rectangle has area {area} and length {length}. What is the width?",
            canonical_answer=str(width),
            difficulty=difficulty,
            learner_band=learner_band,
            verification_kind="geometry",
            metadata={
                "template": "rectangle_area_missing_width",
                "length": length,
                "width": width,
                "area": area,
                "diagram_kind": "rectangle",
                "known_label": str(length),
                "missing_label": "w",
                "interior_label": f"A={area}",
            },
        )

    def _generate_right_triangle_leg_problem(self, rng: Random, difficulty: int, learner_band: LearnerBand) -> GeneratedProblem:
        triples = ((3, 4, 5), (5, 12, 13), (8, 15, 17), (7, 24, 25))
        known_leg, answer, hypotenuse = rng.choice(triples)
        if rng.choice((True, False)):
            known_leg, answer = answer, known_leg
        return GeneratedProblem(
            family=self.family,
            prompt=(
                "Find the missing side: In the right triangle, the hypotenuse is "
                f"{hypotenuse} and one leg is {known_leg}. What is the other leg?"
            ),
            canonical_answer=str(answer),
            difficulty=difficulty,
            learner_band=learner_band,
            verification_kind="geometry",
            metadata={
                "template": "right_triangle_missing_leg",
                "known_leg": known_leg,
                "missing_leg": answer,
                "hypotenuse": hypotenuse,
                "diagram_kind": "right_triangle",
                "base_label": str(known_leg),
                "vertical_label": "?",
                "hypotenuse_label": str(hypotenuse),
                "angle_label": "",
            },
        )

    def _generate_tangent_problem(self, rng: Random, difficulty: int, learner_band: LearnerBand) -> GeneratedProblem:
        ratios = ((3, 4), (4, 3), (5, 12), (8, 15))
        rise, run = rng.choice(ratios)
        scale = rng.randint(2, 5)
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
        scale = rng.randint(2, 4)
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
