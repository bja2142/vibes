from __future__ import annotations

from worksheet_generator.models import DifficultyRange, LearnerBand, RevealMode, SkillDifficultySetting, WorksheetSpec
from random import Random

from worksheet_generator.problem_generators import AdditionProblemGenerator, GeneratedProblem, ProblemGenerationService, verify_generated_problem


def test_problem_generation_is_seeded_and_repeatable() -> None:
    spec = WorksheetSpec(
        worksheet_id="seeded-demo",
        learner_band=LearnerBand.UPPER_ELEMENTARY,
        skill_profile="mixed_operations",
        difficulty_range=DifficultyRange(minimum=1, maximum=3),
        problem_count=8,
        reveal_mode=RevealMode.LETTER_BANK,
        seed=303,
    )
    service = ProblemGenerationService()

    first = service.generate_problem_set(spec, problem_id_prefix="T")
    second = service.generate_problem_set(spec, problem_id_prefix="T")

    assert [problem.prompt for problem in first.problems] == [problem.prompt for problem in second.problems]
    assert [solved.canonical_answer for solved in first.solved_problems] == [
        solved.canonical_answer for solved in second.solved_problems
    ]


def test_early_arithmetic_skill_profile_uses_supported_families() -> None:
    service = ProblemGenerationService()

    families = service.available_families(LearnerBand.EARLY_ARITHMETIC, "mixed_operations")

    assert families == ("addition", "subtraction")


def test_generated_problem_verification_rejects_wrong_answer() -> None:
    invalid_problem = GeneratedProblem(
        family="addition",
        prompt="2 + 3 = ?",
        operator="+",
        operands=(2, 3),
        canonical_answer="9",
        difficulty=1,
        learner_band=LearnerBand.EARLY_ARITHMETIC,
    )

    assert verify_generated_problem(invalid_problem) is False


def test_algebra_and_geometry_profiles_are_supported_for_the_right_bands() -> None:
    service = ProblemGenerationService()

    assert service.available_families(LearnerBand.PRE_ALGEBRA, "algebra") == ("algebraic_equation",)
    assert service.available_families(LearnerBand.ALGEBRA, "algebra") == ("algebraic_equation",)
    assert service.available_families(LearnerBand.GEOMETRY, "algebra") == ("algebraic_equation",)
    assert service.available_families(LearnerBand.GEOMETRY, "geometry") == ("geometry_problem",)


def test_arithmetic_difficulty_scales_operand_size_meaningfully() -> None:
    generator = AdditionProblemGenerator()
    low = generator.generate(Random(17), 1, LearnerBand.UPPER_ELEMENTARY)
    high = generator.generate(Random(17), 5, LearnerBand.UPPER_ELEMENTARY)

    assert max(low.operands) < 10
    assert min(high.operands) >= 1_000
    assert max(high.operands) <= 9_999


def test_algebraic_equation_difficulty_progression_changes_problem_family_shape() -> None:
    service = ProblemGenerationService()
    prompts = []
    for difficulty in range(1, 6):
        spec = WorksheetSpec(
            worksheet_id=f"equation-{difficulty}",
            learner_band=LearnerBand.ALGEBRA,
            skill_profile="algebra",
            difficulty_range=DifficultyRange(minimum=difficulty, maximum=difficulty),
            problem_count=1,
            reveal_mode=RevealMode.LETTER_BANK,
            seed=41 + difficulty,
        )
        generated = service.generate_problem_set(spec)
        problem = generated.generated[0]
        assert verify_generated_problem(problem) is True
        prompts.append(problem.prompt)

    assert "Solve for x: x +" in prompts[0]
    assert "2x" in prompts[1] or "3x" in prompts[1] or "4x" in prompts[1]
    assert " = " in prompts[2] and "x +" in prompts[2]
    assert "x + y" in prompts[3]
    assert "x²" in prompts[4]


def test_geometry_profile_progresses_from_measurement_to_trig() -> None:
    service = ProblemGenerationService()
    prompts = []
    for difficulty in range(1, 6):
        spec = WorksheetSpec(
            worksheet_id=f"geometry-{difficulty}",
            learner_band=LearnerBand.GEOMETRY,
            skill_profile="geometry",
            difficulty_range=DifficultyRange(minimum=difficulty, maximum=difficulty),
            problem_count=1,
            reveal_mode=RevealMode.LETTER_BANK,
            seed=91 + difficulty,
        )
        generated = service.generate_problem_set(spec)
        problem = generated.generated[0]
        assert verify_generated_problem(problem) is True
        prompts.append(problem.prompt)

    assert "rectangle has perimeter" in prompts[0]
    assert "rectangle has area" in prompts[1]
    assert "hypotenuse" in prompts[2]
    assert "tan(θ)" in prompts[3]
    assert "sin(θ)" in prompts[4]


def test_selected_skills_drive_problem_families_and_per_skill_difficulty() -> None:
    service = ProblemGenerationService()
    spec = WorksheetSpec(
        worksheet_id="selected-skills",
        learner_band=LearnerBand.UPPER_ELEMENTARY,
        skill_profile="mixed_skills",
        selected_skills=(
            SkillDifficultySetting("addition", DifficultyRange(1, 1)),
            SkillDifficultySetting("multiplication", DifficultyRange(3, 3)),
        ),
        difficulty_range=DifficultyRange(minimum=1, maximum=3),
        problem_count=4,
        reveal_mode=RevealMode.LETTER_BANK,
        seed=77,
    )

    generated = service.generate_problem_set(spec)

    assert [problem.family for problem in generated.problems] == [
        "addition",
        "multiplication",
        "addition",
        "multiplication",
    ]
    assert [problem.difficulty for problem in generated.problems] == [1, 3, 1, 3]
