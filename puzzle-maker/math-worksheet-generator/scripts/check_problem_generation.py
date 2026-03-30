from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worksheet_generator.models import DifficultyRange, LearnerBand, RevealMode, WorksheetSpec
from worksheet_generator.problem_generators import ProblemGenerationService, verify_generated_problem


def main() -> None:
    service = ProblemGenerationService()

    early_families = service.available_families(LearnerBand.EARLY_ARITHMETIC, "mixed_operations")
    upper_families = service.available_families(LearnerBand.UPPER_ELEMENTARY, "mixed_operations")

    spec = WorksheetSpec(
        worksheet_id="generation-check",
        learner_band=LearnerBand.UPPER_ELEMENTARY,
        skill_profile="mixed_operations",
        difficulty_range=DifficultyRange(minimum=1, maximum=3),
        problem_count=8,
        reveal_mode=RevealMode.LETTER_BANK,
        seed=404,
    )
    first_run = service.generate_problem_set(spec, problem_id_prefix="T")
    second_run = service.generate_problem_set(spec, problem_id_prefix="T")

    deterministic_match = [
        (problem.prompt, solved.canonical_answer)
        for problem, solved in zip(first_run.problems, first_run.solved_problems)
    ] == [
        (problem.prompt, solved.canonical_answer)
        for problem, solved in zip(second_run.problems, second_run.solved_problems)
    ]
    all_verified = all(verify_generated_problem(problem) for problem in first_run.generated)

    print(f"early_families={','.join(early_families)}")
    print(f"upper_families={','.join(upper_families)}")
    print(f"deterministic_match={deterministic_match}")
    print(f"all_verified={all_verified}")
    print("problems=")
    for problem, solved in zip(first_run.problems, first_run.solved_problems):
        print(
            f"  {problem.problem_id} | {problem.family} | difficulty={problem.difficulty} | "
            f"{problem.prompt} | answer={solved.canonical_answer}"
        )


if __name__ == "__main__":
    main()
