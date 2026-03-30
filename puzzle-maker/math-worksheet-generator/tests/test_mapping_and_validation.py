from __future__ import annotations

from worksheet_generator.mapping_engine import MappingEngine
from worksheet_generator.models import (
    ApprovalState,
    DifficultyRange,
    LearnerBand,
    LetterAssignment,
    Problem,
    RenderedWorksheet,
    RevealMode,
    RewardContent,
    SolvedProblem,
    Worksheet,
    WorksheetSpec,
)
from worksheet_generator.validator import WorksheetValidator


def build_split_problem_set() -> tuple[list[Problem], list[SolvedProblem]]:
    problems = [
        Problem("M1", "12 + 3 = ?", "addition", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
        Problem("M2", "8 + 2 = ?", "addition", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
        Problem("M3", "9 - 4 = ?", "subtraction", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
        Problem("M4", "15 - 6 = ?", "subtraction", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
    ]
    solved = [
        SolvedProblem("M1", "15", "15"),
        SolvedProblem("M2", "10", "10"),
        SolvedProblem("M3", "5", "5"),
        SolvedProblem("M4", "9", "9"),
    ]
    return problems, solved


def build_shared_problem_set() -> tuple[list[Problem], list[SolvedProblem]]:
    problems = [
        Problem("S1", "12 + 3 = ?", "addition", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
        Problem("S2", "8 + 2 = ?", "addition", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
        Problem("S3", "14 - 4 = ?", "subtraction", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
        Problem("S4", "15 - 6 = ?", "subtraction", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
    ]
    solved = [
        SolvedProblem("S1", "15", "15"),
        SolvedProblem("S2", "10", "10"),
        SolvedProblem("S3", "10", "10"),
        SolvedProblem("S4", "9", "9"),
    ]
    return problems, solved


def build_reward_content() -> RewardContent:
    return RewardContent(
        prompt_text="What night-sky word has two O sounds in the middle?",
        solution_phrase="MOON",
        theme="space",
        source="direct_input",
        approval_state=ApprovalState.APPROVED,
        style="question",
    )


def build_spec(problem_count: int) -> WorksheetSpec:
    return WorksheetSpec(
        worksheet_id="mapping-test",
        learner_band=LearnerBand.UPPER_ELEMENTARY,
        skill_profile="subtraction_and_addition",
        difficulty_range=DifficultyRange(minimum=1, maximum=2),
        problem_count=problem_count,
        reveal_mode=RevealMode.LETTER_BANK,
        seed=515,
    )


def test_mapping_engine_supports_shared_repeat_letter_mode() -> None:
    problems, solved = build_shared_problem_set()
    result = MappingEngine().assign("MOON", problems, solved, repeat_letter_mode="shared")

    assert result.distinct_letter_answer_map["O"] == ("10",)


def test_mapping_engine_supports_split_repeat_letter_mode() -> None:
    problems, solved = build_split_problem_set()
    result = MappingEngine().assign("MOON", problems, solved, repeat_letter_mode="split", seed=515)

    assert len(result.distinct_letter_answer_map["O"]) == 2
    assert len(set(result.distinct_letter_answer_map["O"])) == 2


def test_validator_accepts_split_repeated_letters() -> None:
    problems, solved = build_split_problem_set()
    mapping = MappingEngine().assign("MOON", problems, solved, repeat_letter_mode="split", seed=515)
    worksheet = Worksheet(
        spec=build_spec(problem_count=len(problems)),
        reward_content=build_reward_content(),
        problems=problems,
        solved_problems=solved,
        letter_assignments=list(mapping.assignments),
        rendered_outputs=[RenderedWorksheet(worksheet_id="mapping-test", output_format="svg", output_path="/tmp/mock.svg")],
    )

    report = WorksheetValidator().validate(worksheet)

    assert report.is_valid is True
    assert report.reconstructed_answer == "MOON"
    assert len(report.distinct_letter_answer_map["O"]) == 2
    assert len(set(report.distinct_letter_answer_map["O"])) == 2


def test_validator_rejects_answer_ambiguity_across_distinct_letters() -> None:
    worksheet = Worksheet(
        spec=build_spec(problem_count=2),
        reward_content=RewardContent(
            prompt_text="Ambiguity test",
            solution_phrase="AB",
            theme="test",
            source="direct_input",
            approval_state=ApprovalState.APPROVED,
        ),
        problems=[
            Problem("A1", "4 + 4 = ?", "addition", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
            Problem("B1", "5 + 3 = ?", "addition", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
        ],
        solved_problems=[
            SolvedProblem("A1", "8", "8"),
            SolvedProblem("B1", "8", "8"),
        ],
        letter_assignments=[
            LetterAssignment("A1", "8", "A", 0),
            LetterAssignment("B1", "8", "B", 1),
        ],
    )

    report = WorksheetValidator().validate(worksheet)

    assert report.is_valid is False
    assert any("shared by distinct letters" in error for error in report.errors)


def test_worksheet_rejects_duplicate_problem_ids() -> None:
    try:
        Worksheet(
            spec=build_spec(problem_count=2),
            reward_content=RewardContent(
                prompt_text="Duplicate id test",
                solution_phrase="AB",
                theme="test",
                source="direct_input",
                approval_state=ApprovalState.APPROVED,
            ),
            problems=[
                Problem("A1", "4 + 4 = ?", "addition", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
                Problem("A1", "5 + 3 = ?", "addition", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
            ],
            solved_problems=[
                SolvedProblem("A1", "8", "8"),
                SolvedProblem("B1", "8", "8"),
            ],
            letter_assignments=[
                LetterAssignment("A1", "8", "A", 0),
                LetterAssignment("B1", "8", "B", 1),
            ],
        )
    except ValueError as exc:
        assert "duplicate problem ids" in str(exc)
    else:
        raise AssertionError("expected duplicate problem ids to be rejected")


def test_worksheet_rejects_duplicate_solved_problem_ids() -> None:
    try:
        Worksheet(
            spec=build_spec(problem_count=2),
            reward_content=RewardContent(
                prompt_text="Duplicate solved id test",
                solution_phrase="AB",
                theme="test",
                source="direct_input",
                approval_state=ApprovalState.APPROVED,
            ),
            problems=[
                Problem("A1", "4 + 4 = ?", "addition", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
                Problem("B1", "5 + 3 = ?", "addition", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
            ],
            solved_problems=[
                SolvedProblem("A1", "8", "8"),
                SolvedProblem("A1", "8", "8"),
            ],
            letter_assignments=[
                LetterAssignment("A1", "8", "A", 0),
                LetterAssignment("B1", "8", "B", 1),
            ],
        )
    except ValueError as exc:
        assert "duplicate problem ids" in str(exc)
    else:
        raise AssertionError("expected duplicate solved problem ids to be rejected")


def test_worksheet_rejects_duplicate_assignment_problem_ids() -> None:
    try:
        Worksheet(
            spec=build_spec(problem_count=2),
            reward_content=RewardContent(
                prompt_text="Duplicate assignment id test",
                solution_phrase="AB",
                theme="test",
                source="direct_input",
                approval_state=ApprovalState.APPROVED,
            ),
            problems=[
                Problem("A1", "4 + 4 = ?", "addition", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
                Problem("B1", "5 + 3 = ?", "addition", 1, "integer", LearnerBand.UPPER_ELEMENTARY),
            ],
            solved_problems=[
                SolvedProblem("A1", "8", "8"),
                SolvedProblem("B1", "8", "8"),
            ],
            letter_assignments=[
                LetterAssignment("A1", "8", "A", 0),
                LetterAssignment("A1", "8", "B", 1),
            ],
        )
    except ValueError as exc:
        assert "duplicate problem ids" in str(exc)
    else:
        raise AssertionError("expected duplicate assignment problem ids to be rejected")
