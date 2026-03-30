from __future__ import annotations

from dataclasses import dataclass
from random import Random

from ..models import LearnerBand, Problem, SolvedProblem, WorksheetSpec
from .addition import AdditionProblemGenerator
from .algebraic_equation import AlgebraicEquationProblemGenerator
from .base import GeneratedProblem, ProblemGenerator
from .division import DivisionProblemGenerator
from .geometry import GeometryProblemGenerator
from .multiplication import MultiplicationProblemGenerator
from .plane_geometry import PlaneGeometryProblemGenerator
from .subtraction import SubtractionProblemGenerator
from .trigonometry import TrigonometryProblemGenerator
from .validation import verify_generated_problem


_DEFAULT_SEED = 42


@dataclass(frozen=True)
class GeneratedProblemSet:
    seed: int
    families: tuple[str, ...]
    generated: tuple[GeneratedProblem, ...]
    problems: tuple[Problem, ...]
    solved_problems: tuple[SolvedProblem, ...]


class ProblemGenerationService:
    def __init__(self) -> None:
        self._generators: dict[str, ProblemGenerator] = {
            "addition": AdditionProblemGenerator(),
            "subtraction": SubtractionProblemGenerator(),
            "multiplication": MultiplicationProblemGenerator(),
            "division": DivisionProblemGenerator(),
            "algebraic_equation": AlgebraicEquationProblemGenerator(),
            "geometry_problem": GeometryProblemGenerator(),
            "plane_geometry_problem": PlaneGeometryProblemGenerator(),
            "trigonometry_problem": TrigonometryProblemGenerator(),
        }

    def available_families(self, learner_band: LearnerBand, skill_profile: str | None = None) -> tuple[str, ...]:
        requested = self._families_for_skill_profile(skill_profile)
        supported = [
            family
            for family in requested
            if family in self._generators and self._generators[family].supports(learner_band)
        ]
        if not supported:
            raise ValueError(f"no supported problem families for {learner_band.value} and skill profile {skill_profile!r}")
        return tuple(supported)

    def generate_problem_set(
        self,
        spec: WorksheetSpec,
        problem_id_prefix: str = "G",
        problem_id_width: int | None = None,
    ) -> GeneratedProblemSet:
        seed = spec.seed if spec.seed is not None else _DEFAULT_SEED
        rng = Random(seed)
        if spec.selected_skills:
            selections = self._selected_skill_families(spec)
            families = tuple(family for _, family in selections)
        else:
            families = self.available_families(spec.learner_band, spec.skill_profile)

        generated: list[GeneratedProblem] = []
        problems: list[Problem] = []
        solved_problems: list[SolvedProblem] = []
        skill_occurrence_totals: dict[str, int] = {}
        skill_occurrence_seen: dict[str, int] = {}
        if spec.selected_skills:
            selected_list = list(spec.selected_skills)
            for index in range(spec.problem_count):
                skill = selected_list[index % len(selected_list)].skill
                skill_occurrence_totals[skill] = skill_occurrence_totals.get(skill, 0) + 1

        for index in range(spec.problem_count):
            if spec.selected_skills:
                selected_list = list(spec.selected_skills)
                selection = selected_list[index % len(selected_list)]
                family = self._family_for_skill(selection.skill)
                generator = self._generators[family]
                seen = skill_occurrence_seen.get(selection.skill, 0)
                total = skill_occurrence_totals.get(selection.skill, 1)
                difficulty = self._difficulty_for_index(
                    selection.difficulty_range.minimum,
                    selection.difficulty_range.maximum,
                    total,
                    seen,
                )
                skill_occurrence_seen[selection.skill] = seen + 1
            else:
                family = families[index % len(families)]
                generator = self._generators[family]
                difficulty = self._difficulty_for_index(
                    spec.difficulty_range.minimum,
                    spec.difficulty_range.maximum,
                    spec.problem_count,
                    index,
                )
            generated_problem = generator.generate(rng, difficulty, spec.learner_band)
            if not verify_generated_problem(generated_problem):
                raise ValueError(f"generated {family} problem failed verification")

            problem_number = index + 1
            if problem_id_width is None:
                problem_id = f"{problem_id_prefix}{problem_number}"
            else:
                problem_id = f"{problem_id_prefix}{problem_number:0{problem_id_width}d}"
            generated.append(generated_problem)
            problems.append(
                Problem(
                    problem_id=problem_id,
                    prompt=generated_problem.prompt,
                    family=generated_problem.family,
                    difficulty=generated_problem.difficulty,
                    answer_format="integer",
                    learner_band=generated_problem.learner_band,
                    metadata={
                        **dict(generated_problem.metadata),
                        "operator": generated_problem.operator,
                        "operands": list(generated_problem.operands),
                    },
                )
            )
            solved_problems.append(
                SolvedProblem(
                    problem_id=problem_id,
                    canonical_answer=generated_problem.canonical_answer,
                    normalized_answer=generated_problem.canonical_answer,
                    verified=True,
                )
            )

        return GeneratedProblemSet(
            seed=seed,
            families=families,
            generated=tuple(generated),
            problems=tuple(problems),
            solved_problems=tuple(solved_problems),
        )

    def _selected_skill_families(self, spec: WorksheetSpec) -> tuple[tuple[str, str], ...]:
        supported: list[tuple[str, str]] = []
        for selection in spec.selected_skills:
            family = self._family_for_skill(selection.skill)
            if family in self._generators and self._generators[family].supports(spec.learner_band):
                supported.append((selection.skill, family))
        if not supported:
            raise ValueError(
                f"no supported problem families for {spec.learner_band.value} and selected skills "
                f"{[selection.skill for selection in spec.selected_skills]!r}"
            )
        return tuple(supported)

    def _family_for_skill(self, skill: str) -> str:
        mapping = {
            "addition": "addition",
            "subtraction": "subtraction",
            "multiplication": "multiplication",
            "division": "division",
            "algebra": "algebraic_equation",
            "geometry": "plane_geometry_problem",
            "trigonometry": "trigonometry_problem",
        }
        try:
            return mapping[skill]
        except KeyError as exc:
            raise ValueError(f"unknown skill {skill!r}") from exc

    def _families_for_skill_profile(self, skill_profile: str | None) -> tuple[str, ...]:
        if skill_profile == "addition":
            return ("addition",)
        if skill_profile == "subtraction":
            return ("subtraction",)
        if skill_profile == "multiplication":
            return ("multiplication",)
        if skill_profile == "division":
            return ("division",)
        if skill_profile == "subtraction_and_addition":
            return ("addition", "subtraction")
        if skill_profile == "multiplication_focus":
            return ("multiplication",)
        if skill_profile == "division_focus":
            return ("division",)
        if skill_profile in {"algebra", "pre_algebra_equations", "algebraic_equations"}:
            return ("algebraic_equation",)
        if skill_profile == "geometry":
            return ("geometry_problem",)
        if skill_profile == "trigonometry":
            return ("trigonometry_problem",)
        return ("addition", "subtraction", "multiplication", "division")

    def _difficulty_for_index(self, minimum: int, maximum: int, count: int, index: int) -> int:
        if count <= 1 or minimum == maximum:
            return minimum
        span = maximum - minimum
        return minimum + round((span * index) / (count - 1))
