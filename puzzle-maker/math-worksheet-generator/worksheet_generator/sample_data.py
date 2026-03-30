from __future__ import annotations

from pathlib import Path

from .color_grid_generation import PresetColorGridGenerator, difficulty_to_color_count, difficulty_to_grid_size
from .manifest import write_worksheet_manifest
from .models import (
    ApprovalState,
    DifficultyRange,
    LayoutSettings,
    LearnerBand,
    LetterAssignment,
    Problem,
    ReadingLevelAssessment,
    RevealMode,
    RewardContent,
    RewardContentCandidate,
    SolvedProblem,
    Worksheet,
    WorksheetSpec,
)
from .problem_generators import ProblemGenerationService
from .worksheet_assembly import WorksheetAssemblyService


def build_letter_bank_sample() -> Worksheet:
    spec = WorksheetSpec(
        worksheet_id="letter-bank-poc",
        learner_band=LearnerBand.UPPER_ELEMENTARY,
        skill_profile="mixed_operations",
        difficulty_range=DifficultyRange(minimum=1, maximum=2),
        problem_count=9,
        reveal_mode=RevealMode.LETTER_BANK,
        theme="classroom humor",
        seed=101,
        layout=LayoutSettings(),
    )
    reward_content = RewardContent(
        prompt_text="What subject gives your brain a workout and your pencil a purpose?",
        solution_phrase="MATH RULES",
        theme="classroom humor",
        source="direct_input",
        approval_state=ApprovalState.APPROVED,
        style="question",
        reading_level_assessment=ReadingLevelAssessment(
            learner_band=LearnerBand.UPPER_ELEMENTARY,
            passed=True,
            word_count=16,
            sentence_count=1,
            long_word_count=2,
            notes=["Short classroom-safe prompt approved for upper elementary readers."],
        ),
    )
    candidate = RewardContentCandidate(
        prompt_text=reward_content.prompt_text,
        solution_phrase=reward_content.solution_phrase,
        theme=reward_content.theme,
        source="reviewed_fixture",
        approval_state=ApprovalState.APPROVED,
        style=reward_content.style,
        reading_level_assessment=reward_content.reading_level_assessment,
        review_notes=["Fixture candidate reviewed and approved."],
    )

    problems = [
        Problem("P1", "9 + 4 = ?", "addition", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
        Problem("P2", "6 + 2 = ?", "addition", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
        Problem("P3", "14 - 9 = ?", "subtraction", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
        Problem("P4", "3 x 4 = ?", "multiplication", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
        Problem("P5", "18 - 11 = ?", "subtraction", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
        Problem("P6", "20 - 11 = ?", "subtraction", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
        Problem("P7", "15 - 4 = ?", "subtraction", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
        Problem("P8", "5 x 2 = ?", "multiplication", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
        Problem("P9", "16 - 10 = ?", "subtraction", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
    ]
    solved_problems = [
        SolvedProblem("P1", "13", "13"),
        SolvedProblem("P2", "8", "8"),
        SolvedProblem("P3", "5", "5"),
        SolvedProblem("P4", "12", "12"),
        SolvedProblem("P5", "7", "7"),
        SolvedProblem("P6", "9", "9"),
        SolvedProblem("P7", "11", "11"),
        SolvedProblem("P8", "10", "10"),
        SolvedProblem("P9", "6", "6"),
    ]
    assignments = [
        LetterAssignment("P1", "13", "M", 0),
        LetterAssignment("P2", "8", "A", 1),
        LetterAssignment("P3", "5", "T", 2),
        LetterAssignment("P4", "12", "H", 3),
        LetterAssignment("P5", "7", "R", 4),
        LetterAssignment("P6", "9", "U", 5),
        LetterAssignment("P7", "11", "L", 6),
        LetterAssignment("P8", "10", "E", 7),
        LetterAssignment("P9", "6", "S", 8),
    ]
    return Worksheet(
        spec=spec,
        reward_content=reward_content,
        problems=problems,
        solved_problems=solved_problems,
        letter_assignments=assignments,
        reward_content_candidates=[candidate],
    )


def build_color_by_number_sample() -> Worksheet:
    picture_generator = PresetColorGridGenerator()
    palette_labels = ["Sun Yellow", "Leaf Green", "Sky Blue", "Coral"]
    spec = WorksheetSpec(
        worksheet_id="color-by-number-poc",
        learner_band=LearnerBand.EARLY_ARITHMETIC,
        skill_profile="subtraction_and_addition",
        difficulty_range=DifficultyRange(minimum=1, maximum=1),
        problem_count=difficulty_to_color_count(1),
        reveal_mode=RevealMode.COLOR_BY_NUMBER,
        theme="patterns",
        seed=202,
        layout=LayoutSettings(
            color_palette={
                "Sun Yellow": "#f4c542",
                "Leaf Green": "#6ba36f",
                "Sky Blue": "#67a6d8",
                "Coral": "#dd7c6b",
            }
        ),
    )
    reward_content = RewardContent(
        prompt_text="What cheerful face shape should appear when you color the picture?",
        solution_phrase="SMILE",
        theme="patterns",
        source="direct_input",
        approval_state=ApprovalState.APPROVED,
        style="activity",
        reading_level_assessment=ReadingLevelAssessment(
            learner_band=LearnerBand.EARLY_ARITHMETIC,
            passed=True,
            word_count=12,
            sentence_count=1,
            long_word_count=1,
            notes=["Short instruction prompt approved for early arithmetic readers."],
        ),
    )
    candidate = RewardContentCandidate(
        prompt_text=reward_content.prompt_text,
        solution_phrase=reward_content.solution_phrase,
        theme=reward_content.theme,
        source="reviewed_fixture",
        approval_state=ApprovalState.APPROVED,
        style=reward_content.style,
        reading_level_assessment=reward_content.reading_level_assessment,
        review_notes=["Fixture candidate reviewed and approved."],
    )

    problems = [
        Problem("C1", "8 - 4 = ?", "subtraction", 1, "integer", LearnerBand.EARLY_ARITHMETIC),
        Problem("C2", "10 - 3 = ?", "subtraction", 1, "integer", LearnerBand.EARLY_ARITHMETIC),
        Problem("C3", "6 + 3 = ?", "addition", 1, "integer", LearnerBand.EARLY_ARITHMETIC),
        Problem("C4", "3 x 4 = ?", "multiplication", 1, "integer", LearnerBand.EARLY_ARITHMETIC),
    ]
    solved_problems = [
        SolvedProblem("C1", "4", "4"),
        SolvedProblem("C2", "7", "7"),
        SolvedProblem("C3", "9", "9"),
        SolvedProblem("C4", "12", "12"),
    ]
    assignments = [
        LetterAssignment("C1", "4", "Sun Yellow", None),
        LetterAssignment("C2", "7", "Leaf Green", None),
        LetterAssignment("C3", "9", "Sky Blue", None),
        LetterAssignment("C4", "12", "Coral", None),
    ]
    color_grid = picture_generator.generate(
        preset_name="smile",
        grid_size=difficulty_to_grid_size(spec.difficulty_range.maximum),
        palette_labels=palette_labels,
    )
    spec = WorksheetSpec(
        worksheet_id=spec.worksheet_id,
        learner_band=spec.learner_band,
        skill_profile=spec.skill_profile,
        difficulty_range=spec.difficulty_range,
        problem_count=spec.problem_count,
        reveal_mode=spec.reveal_mode,
        theme=spec.theme,
        seed=spec.seed,
        layout=LayoutSettings(
            color_palette=spec.layout.color_palette,
            color_grid_size=color_grid.grid_size,
            color_grid_cells=[list(row) for row in color_grid.cells],
            color_grid_source=color_grid.source,
            color_grid_name=color_grid.name,
        ),
    )
    return Worksheet(
        spec=spec,
        reward_content=reward_content,
        problems=problems,
        solved_problems=solved_problems,
        letter_assignments=assignments,
        reward_content_candidates=[candidate],
    )


def build_pre_algebra_sample() -> Worksheet:
    spec = WorksheetSpec(
        worksheet_id="pre-algebra-poc",
        learner_band=LearnerBand.PRE_ALGEBRA,
        skill_profile="algebra",
        difficulty_range=DifficultyRange(minimum=2, maximum=4),
        problem_count=6,
        reveal_mode=RevealMode.LETTER_BANK,
        theme="patterns and variables",
        seed=404,
        layout=LayoutSettings(page_height=1600),
    )
    reward_content = RewardContent(
        prompt_text="Solve each equation for x, then use the answers to reveal the final algebra clue.",
        solution_phrase="VALUES",
        theme="patterns and variables",
        source="direct_input",
        approval_state=ApprovalState.APPROVED,
        style="question",
        reading_level_assessment=ReadingLevelAssessment(
            learner_band=LearnerBand.PRE_ALGEBRA,
            passed=True,
            word_count=13,
            sentence_count=1,
            long_word_count=3,
            notes=["Prompt approved as readable for a pre-algebra worksheet preview with simple solve-for-x equations."],
        ),
    )
    candidate = RewardContentCandidate(
        prompt_text=reward_content.prompt_text,
        solution_phrase=reward_content.solution_phrase,
        theme=reward_content.theme,
        source="reviewed_fixture",
        approval_state=ApprovalState.APPROVED,
        style=reward_content.style,
        reading_level_assessment=reward_content.reading_level_assessment,
        review_notes=["Fixture candidate reviewed and approved for the pre-algebra layout sample."],
    )

    problems = [
        Problem("PA1", "x + 2 = 10", "linear_equation", 2, "integer", LearnerBand.PRE_ALGEBRA),
        Problem("PA2", "x - 4 = -3", "linear_equation", 2, "integer", LearnerBand.PRE_ALGEBRA),
        Problem("PA3", "3x = 36", "linear_equation", 2, "integer", LearnerBand.PRE_ALGEBRA),
        Problem("PA4", "x + 7 = 12", "linear_equation", 2, "integer", LearnerBand.PRE_ALGEBRA),
        Problem("PA5", "x - 6 = 3", "linear_equation", 2, "integer", LearnerBand.PRE_ALGEBRA),
        Problem("PA6", "x / 3 = 1", "linear_equation", 2, "integer", LearnerBand.PRE_ALGEBRA),
    ]
    solved_problems = [
        SolvedProblem("PA1", "8", "8"),
        SolvedProblem("PA2", "1", "1"),
        SolvedProblem("PA3", "12", "12"),
        SolvedProblem("PA4", "5", "5"),
        SolvedProblem("PA5", "9", "9"),
        SolvedProblem("PA6", "3", "3"),
    ]
    assignments = [
        LetterAssignment("PA1", "8", "V", 0),
        LetterAssignment("PA2", "1", "A", 1),
        LetterAssignment("PA3", "12", "L", 2),
        LetterAssignment("PA4", "5", "U", 3),
        LetterAssignment("PA5", "9", "E", 4),
        LetterAssignment("PA6", "3", "S", 5),
    ]

    return Worksheet(
        spec=spec,
        reward_content=reward_content,
        problems=problems,
        solved_problems=solved_problems,
        letter_assignments=assignments,
        reward_content_candidates=[candidate],
    )


def export_sample_manifests(fixtures_dir: Path) -> dict[str, Path]:
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "letter_bank": fixtures_dir / "letter-bank-poc.json",
        "color_by_number": fixtures_dir / "color-by-number-poc.json",
        "pre_algebra": fixtures_dir / "pre-algebra-poc.json",
    }
    write_worksheet_manifest(outputs["letter_bank"], build_letter_bank_sample())
    write_worksheet_manifest(outputs["color_by_number"], build_color_by_number_sample())
    write_worksheet_manifest(outputs["pre_algebra"], build_pre_algebra_sample())
    return outputs


def build_problem_generation_demo_report() -> dict[str, object]:
    service = ProblemGenerationService()
    spec = WorksheetSpec(
        worksheet_id="problem-generation-demo",
        learner_band=LearnerBand.UPPER_ELEMENTARY,
        skill_profile="mixed_operations",
        difficulty_range=DifficultyRange(minimum=1, maximum=3),
        problem_count=8,
        reveal_mode=RevealMode.LETTER_BANK,
        theme="generator-demo",
        seed=303,
    )
    first_run = service.generate_problem_set(spec, problem_id_prefix="D")
    second_run = service.generate_problem_set(spec, problem_id_prefix="D")

    return {
        "worksheet_id": spec.worksheet_id,
        "learner_band": spec.learner_band.value,
        "skill_profile": spec.skill_profile,
        "seed": first_run.seed,
        "difficulty_range": {
            "minimum": spec.difficulty_range.minimum,
            "maximum": spec.difficulty_range.maximum,
        },
        "families": list(first_run.families),
        "deterministic_repeat_match": [
            (problem.prompt, solved.canonical_answer)
            for problem, solved in zip(first_run.problems, first_run.solved_problems)
        ]
        == [
            (problem.prompt, solved.canonical_answer)
            for problem, solved in zip(second_run.problems, second_run.solved_problems)
        ],
        "problems": [
            {
                "problem_id": problem.problem_id,
                "family": problem.family,
                "difficulty": problem.difficulty,
                "prompt": problem.prompt,
                "answer": solved.canonical_answer,
                "verified": solved.verified,
            }
            for problem, solved in zip(first_run.problems, first_run.solved_problems)
        ],
    }


def build_mapping_validation_demo_report() -> dict[str, object]:
    assembly_service = WorksheetAssemblyService()
    reward_content = RewardContent(
        prompt_text="What night-sky word has two O sounds in the middle?",
        solution_phrase="MOON",
        theme="space",
        source="direct_input",
        approval_state=ApprovalState.APPROVED,
        style="question",
        reading_level_assessment=ReadingLevelAssessment(
            learner_band=LearnerBand.UPPER_ELEMENTARY,
            passed=True,
            word_count=12,
            sentence_count=1,
            long_word_count=1,
            notes=["Short repeated-letter mapping demo approved for upper elementary readers."],
        ),
    )
    spec = WorksheetSpec(
        worksheet_id="mapping-validation-demo",
        learner_band=LearnerBand.UPPER_ELEMENTARY,
        skill_profile="subtraction_and_addition",
        difficulty_range=DifficultyRange(minimum=1, maximum=2),
        problem_count=4,
        reveal_mode=RevealMode.LETTER_BANK,
        theme="space",
        seed=515,
    )
    result = assembly_service.build_worksheet(
        spec,
        reward_content,
        max_attempts=12,
        distractor_count=0,
        problem_id_prefix="M",
    )

    slot_assignments = result.worksheet.slot_assignments()
    distractors = [assignment for assignment in result.worksheet.letter_assignments if assignment.is_distractor]
    solved_map = result.worksheet.solved_problem_map()

    return {
        "worksheet_id": result.worksheet.worksheet_id,
        "attempts_used": result.attempts_used,
        "is_valid": result.validation_report.is_valid,
        "reconstructed_answer": result.validation_report.reconstructed_answer,
        "distinct_letter_answer_map": result.validation_report.distinct_letter_answer_map,
        "repeat_letter_behavior": {
            letter: ("split" if len(answer_values) > 1 else "shared")
            for letter, answer_values in result.validation_report.distinct_letter_answer_map.items()
        },
        "warnings": list(result.validation_report.warnings),
        "errors": list(result.validation_report.errors),
        "slot_assignments": [
            {
                "problem_id": assignment.problem_id,
                "slot_index": assignment.answer_slot_index,
                "letter": assignment.reveal_token,
                "answer_value": assignment.answer_value,
            }
            for assignment in slot_assignments
        ],
        "distractors": [
            {
                "problem_id": assignment.problem_id,
                "answer_value": solved_map[assignment.problem_id].normalized_answer,
            }
            for assignment in distractors
        ],
    }
