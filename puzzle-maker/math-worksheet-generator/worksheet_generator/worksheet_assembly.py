from __future__ import annotations

from dataclasses import dataclass, replace

from .mapping_engine import MappingConstraintError, MappingEngine
from .models import RewardContent, Worksheet, WorksheetSpec
from .problem_generators import ProblemGenerationService
from .validator import WorksheetValidationReport, WorksheetValidator


class WorksheetAssemblyError(ValueError):
    pass


@dataclass(frozen=True)
class WorksheetAssemblyResult:
    worksheet: Worksheet
    validation_report: WorksheetValidationReport
    attempts_used: int


class WorksheetAssemblyService:
    def __init__(
        self,
        generation_service: ProblemGenerationService | None = None,
        mapping_engine: MappingEngine | None = None,
        validator: WorksheetValidator | None = None,
    ) -> None:
        self._generation_service = generation_service or ProblemGenerationService()
        self._mapping_engine = mapping_engine or MappingEngine()
        self._validator = validator or WorksheetValidator()

    def build_worksheet(
        self,
        spec: WorksheetSpec,
        reward_content: RewardContent,
        *,
        max_attempts: int = 10,
        distractor_count: int = 0,
        problem_id_prefix: str = "W",
        problem_id_width: int | None = None,
    ) -> WorksheetAssemblyResult:
        base_seed = spec.seed or 42
        last_error: Exception | None = None

        for attempt in range(max_attempts):
            attempt_spec = replace(spec, seed=base_seed + attempt)
            generated = self._generation_service.generate_problem_set(
                attempt_spec,
                problem_id_prefix=problem_id_prefix,
                problem_id_width=problem_id_width,
            )

            try:
                mapping = self._mapping_engine.assign(
                    reward_content.solution_phrase,
                    generated.problems,
                    generated.solved_problems,
                    distractor_count=distractor_count,
                    seed=attempt_spec.seed,
                )
                worksheet = Worksheet(
                    spec=attempt_spec,
                    reward_content=reward_content,
                    problems=list(generated.problems),
                    solved_problems=list(generated.solved_problems),
                    letter_assignments=list(mapping.assignments),
                )
                report = self._validator.validate(worksheet)
                if not report.is_valid:
                    raise WorksheetAssemblyError("; ".join(report.errors))
                return WorksheetAssemblyResult(
                    worksheet=worksheet,
                    validation_report=report,
                    attempts_used=attempt + 1,
                )
            except (MappingConstraintError, WorksheetAssemblyError) as exc:
                last_error = exc

        raise WorksheetAssemblyError(
            f"unable to build a valid worksheet after {max_attempts} attempt(s): {last_error}"
        )
