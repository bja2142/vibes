from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ApprovalState(str, Enum):
    PENDING = "pending"
    REJECTED = "rejected"
    EDITED = "edited"
    APPROVED = "approved"


class RevealMode(str, Enum):
    LETTER_BANK = "letter_bank"
    COLOR_BY_NUMBER = "color_by_number"


class LearnerBand(str, Enum):
    EARLY_ARITHMETIC = "early_arithmetic"
    UPPER_ELEMENTARY = "upper_elementary"
    PRE_ALGEBRA = "pre_algebra"
    ALGEBRA = "algebra"
    GEOMETRY = "geometry"


@dataclass
class DifficultyRange:
    minimum: int
    maximum: int

    def __post_init__(self) -> None:
        if self.minimum > self.maximum:
            raise ValueError("difficulty minimum must not exceed maximum")


@dataclass(frozen=True)
class SkillDifficultySetting:
    skill: str
    difficulty_range: DifficultyRange


@dataclass
class LayoutSettings:
    page_width: int = 1080
    page_height: int = 1500
    problem_columns: int = 3
    show_slot_labels: bool = True
    max_color_options: int = 32
    color_palette: dict[str, str] = field(default_factory=dict)
    color_grid_size: int = 0
    color_grid_cells: list[list[str]] = field(default_factory=list)
    color_grid_source: str | None = None
    color_grid_name: str | None = None


@dataclass
class WorksheetSpec:
    worksheet_id: str
    learner_band: LearnerBand
    skill_profile: str
    difficulty_range: DifficultyRange
    problem_count: int
    reveal_mode: RevealMode
    theme: str | None = None
    seed: int | None = None
    layout: LayoutSettings = field(default_factory=LayoutSettings)
    selected_skills: tuple[SkillDifficultySetting, ...] = ()


@dataclass
class RewardContentCandidate:
    prompt_text: str
    solution_phrase: str
    theme: str | None
    source: str
    approval_state: ApprovalState
    style: str | None = None
    language: str = "en"
    reading_level_assessment: ReadingLevelAssessment | None = None
    review_notes: list[str] = field(default_factory=list)


@dataclass
class RewardContent:
    prompt_text: str
    solution_phrase: str
    theme: str | None
    source: str
    approval_state: ApprovalState
    style: str | None = None
    language: str = "en"
    reading_level_assessment: ReadingLevelAssessment | None = None
    review_notes: list[str] = field(default_factory=list)


@dataclass
class ReadingLevelAssessment:
    learner_band: LearnerBand
    passed: bool
    word_count: int
    sentence_count: int
    long_word_count: int
    flagged_terms: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class Problem:
    problem_id: str
    prompt: str
    family: str
    difficulty: int
    answer_format: str
    learner_band: LearnerBand
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SolvedProblem:
    problem_id: str
    canonical_answer: str
    normalized_answer: str
    verified: bool = True


@dataclass
class LetterAssignment:
    problem_id: str
    answer_value: str
    reveal_token: str
    answer_slot_index: int | None
    is_distractor: bool = False


@dataclass
class RenderedWorksheet:
    worksheet_id: str
    output_format: str
    output_path: str


@dataclass
class Worksheet:
    spec: WorksheetSpec
    reward_content: RewardContent
    problems: list[Problem]
    solved_problems: list[SolvedProblem]
    letter_assignments: list[LetterAssignment]
    rendered_outputs: list[RenderedWorksheet] = field(default_factory=list)
    reward_content_candidates: list[RewardContentCandidate] = field(default_factory=list)

    def __post_init__(self) -> None:
        _raise_on_duplicate_problem_ids(
            [problem.problem_id for problem in self.problems],
            "problems must not contain duplicate problem ids",
        )
        _raise_on_duplicate_problem_ids(
            [problem.problem_id for problem in self.solved_problems],
            "solved problems must not contain duplicate problem ids",
        )
        _raise_on_duplicate_problem_ids(
            [
                assignment.problem_id
                for assignment in self.letter_assignments
                if not assignment.is_distractor
            ],
            "non-distractor letter assignments must not contain duplicate problem ids",
        )
        problem_ids = {problem.problem_id for problem in self.problems}
        solved_ids = {problem.problem_id for problem in self.solved_problems}
        active_assignment_ids = {
            assignment.problem_id
            for assignment in self.letter_assignments
            if not assignment.is_distractor
        }

        if self.reward_content.approval_state != ApprovalState.APPROVED:
            raise ValueError("worksheet generation is blocked until reward content is approved")
        if solved_ids != problem_ids:
            raise ValueError("solved problems must cover the same problem ids as problems")
        if active_assignment_ids != problem_ids:
            raise ValueError("non-distractor letter assignments must cover the same problem ids as problems")

    @property
    def worksheet_id(self) -> str:
        return self.spec.worksheet_id

    def problem_map(self) -> dict[str, Problem]:
        return {problem.problem_id: problem for problem in self.problems}

    def solved_problem_map(self) -> dict[str, SolvedProblem]:
        return {problem.problem_id: problem for problem in self.solved_problems}

    def assignment_map(self) -> dict[str, LetterAssignment]:
        return {assignment.problem_id: assignment for assignment in self.letter_assignments}

    def active_assignments(self) -> list[LetterAssignment]:
        problem_ids = {problem.problem_id for problem in self.problems}
        return [
            assignment
            for assignment in self.letter_assignments
            if not assignment.is_distractor and assignment.problem_id in problem_ids
        ]

    def slot_assignments(self) -> list[LetterAssignment]:
        return sorted(
            [assignment for assignment in self.letter_assignments if assignment.answer_slot_index is not None],
            key=lambda assignment: assignment.answer_slot_index or 0,
        )


def _raise_on_duplicate_problem_ids(problem_ids: list[str], message: str) -> None:
    if len(problem_ids) != len(set(problem_ids)):
        raise ValueError(message)
