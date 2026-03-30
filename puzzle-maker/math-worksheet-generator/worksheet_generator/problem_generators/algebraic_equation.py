from __future__ import annotations

from random import Random

from ..models import LearnerBand
from .base import GeneratedProblem


class AlgebraicEquationProblemGenerator:
    family = "algebraic_equation"

    def supports(self, learner_band: LearnerBand) -> bool:
        return learner_band in {
            LearnerBand.PRE_ALGEBRA,
            LearnerBand.ALGEBRA,
            LearnerBand.GEOMETRY,
        }

    def generate(self, rng: Random, difficulty: int, learner_band: LearnerBand) -> GeneratedProblem:
        difficulty = max(1, min(5, difficulty))
        if difficulty == 1:
            return self._generate_one_step_equation(rng, difficulty, learner_band)
        if difficulty == 2:
            return self._generate_two_step_equation(rng, difficulty, learner_band)
        if difficulty == 3:
            return self._generate_variable_both_sides_equation(rng, difficulty, learner_band)
        if difficulty == 4:
            return self._generate_system_of_equations(rng, difficulty, learner_band)
        return self._generate_polynomial_equation(rng, difficulty, learner_band)

    def _answer_limit(self, difficulty: int) -> int:
        return {
            1: 9,
            2: 20,
            3: 30,
            4: 20,
            5: 12,
        }[difficulty]

    def _generate_one_step_equation(self, rng: Random, difficulty: int, learner_band: LearnerBand) -> GeneratedProblem:
        answer = rng.randint(1, self._answer_limit(difficulty))
        addend = rng.randint(1, 9)
        total = answer + addend
        return GeneratedProblem(
            family=self.family,
            prompt=f"Solve for x: x + {addend} = {total}",
            canonical_answer=str(answer),
            difficulty=difficulty,
            learner_band=learner_band,
            verification_kind="equation",
            metadata={"template": "x_plus_a_equals_b", "a": addend, "b": total},
        )

    def _generate_two_step_equation(self, rng: Random, difficulty: int, learner_band: LearnerBand) -> GeneratedProblem:
        answer = rng.randint(1, self._answer_limit(difficulty))
        factor = rng.randint(2, 6)
        offset = rng.randint(1, 9)
        total = factor * answer + offset
        return GeneratedProblem(
            family=self.family,
            prompt=f"Solve for x: {factor}x + {offset} = {total}",
            canonical_answer=str(answer),
            difficulty=difficulty,
            learner_band=learner_band,
            verification_kind="equation",
            metadata={"template": "ax_plus_b_equals_c", "a": factor, "b": offset, "c": total},
        )

    def _generate_variable_both_sides_equation(self, rng: Random, difficulty: int, learner_band: LearnerBand) -> GeneratedProblem:
        answer = rng.randint(1, self._answer_limit(difficulty))
        left_factor = rng.randint(2, 5)
        right_factor = rng.randint(1, left_factor - 1)
        left_offset = rng.randint(1, 12)
        right_offset = ((left_factor - right_factor) * answer) + left_offset
        return GeneratedProblem(
            family=self.family,
            prompt=f"Solve for x: {left_factor}x + {left_offset} = {right_factor}x + {right_offset}",
            canonical_answer=str(answer),
            difficulty=difficulty,
            learner_band=learner_band,
            verification_kind="equation",
            metadata={
                "template": "ax_plus_b_equals_cx_plus_d",
                "a": left_factor,
                "b": left_offset,
                "c": right_factor,
                "d": right_offset,
            },
        )

    def _generate_system_of_equations(self, rng: Random, difficulty: int, learner_band: LearnerBand) -> GeneratedProblem:
        answer = rng.randint(1, self._answer_limit(difficulty))
        y_value = rng.randint(1, self._answer_limit(difficulty) + 5)
        total = answer + y_value
        return GeneratedProblem(
            family=self.family,
            prompt=f"Solve for x: x + y = {total} and y = {y_value}",
            canonical_answer=str(answer),
            difficulty=difficulty,
            learner_band=learner_band,
            verification_kind="equation",
            metadata={"template": "system_x_plus_y_and_y_value", "sum_total": total, "y_value": y_value},
        )

    def _generate_polynomial_equation(self, rng: Random, difficulty: int, learner_band: LearnerBand) -> GeneratedProblem:
        root_one = rng.randint(1, self._answer_limit(difficulty))
        root_two = rng.randint(root_one + 1, self._answer_limit(difficulty) + 6)
        coefficient = 1
        linear_term = -(root_one + root_two)
        constant_term = root_one * root_two
        return GeneratedProblem(
            family=self.family,
            prompt=(
                f"Solve for x: x² "
                f"{'+' if linear_term >= 0 else '-'} {abs(linear_term)}x "
                f"{'+' if constant_term >= 0 else '-'} {abs(constant_term)} = 0. "
                "Give the smaller integer root."
            ),
            canonical_answer=str(root_one),
            difficulty=difficulty,
            learner_band=learner_band,
            verification_kind="equation",
            metadata={
                "template": "quadratic_smaller_root",
                "a": coefficient,
                "b": linear_term,
                "c": constant_term,
                "smaller_root": root_one,
                "larger_root": root_two,
            },
        )
