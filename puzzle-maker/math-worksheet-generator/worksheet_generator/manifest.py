from __future__ import annotations

from pathlib import Path
import json

from .models import (
    ApprovalState,
    DifficultyRange,
    LayoutSettings,
    LearnerBand,
    LetterAssignment,
    Problem,
    ReadingLevelAssessment,
    RenderedWorksheet,
    RevealMode,
    RewardContent,
    RewardContentCandidate,
    SkillDifficultySetting,
    SolvedProblem,
    Worksheet,
    WorksheetSpec,
)


def worksheet_to_dict(worksheet: Worksheet) -> dict[str, object]:
    return {
        "spec": worksheet_spec_to_dict(worksheet.spec),
        "reward_content": reward_content_to_dict(worksheet.reward_content),
        "problems": [problem_to_dict(problem) for problem in worksheet.problems],
        "solved_problems": [solved_problem_to_dict(problem) for problem in worksheet.solved_problems],
        "letter_assignments": [letter_assignment_to_dict(assignment) for assignment in worksheet.letter_assignments],
        "rendered_outputs": [rendered_output_to_dict(rendered) for rendered in worksheet.rendered_outputs],
        "reward_content_candidates": [
            reward_content_candidate_to_dict(candidate) for candidate in worksheet.reward_content_candidates
        ],
    }


def worksheet_from_dict(data: dict[str, object]) -> Worksheet:
    return Worksheet(
        spec=worksheet_spec_from_dict(_require_dict(data, "spec")),
        reward_content=reward_content_from_dict(_require_dict(data, "reward_content")),
        problems=[problem_from_dict(item) for item in _require_list(data, "problems")],
        solved_problems=[solved_problem_from_dict(item) for item in _require_list(data, "solved_problems")],
        letter_assignments=[
            letter_assignment_from_dict(item) for item in _require_list(data, "letter_assignments")
        ],
        rendered_outputs=[
            rendered_output_from_dict(item) for item in data.get("rendered_outputs", [])
        ],
        reward_content_candidates=[
            reward_content_candidate_from_dict(item) for item in data.get("reward_content_candidates", [])
        ],
    )


def write_worksheet_manifest(path: Path, worksheet: Worksheet) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(worksheet_to_dict(worksheet), indent=2) + "\n", encoding="utf-8")


def read_worksheet_manifest(path: Path) -> Worksheet:
    return worksheet_from_dict(json.loads(path.read_text(encoding="utf-8")))


def worksheet_spec_to_dict(spec: WorksheetSpec) -> dict[str, object]:
    return {
        "worksheet_id": spec.worksheet_id,
        "learner_band": spec.learner_band.value,
        "skill_profile": spec.skill_profile,
        "selected_skills": [skill_setting_to_dict(setting) for setting in spec.selected_skills],
        "difficulty_range": difficulty_range_to_dict(spec.difficulty_range),
        "problem_count": spec.problem_count,
        "reveal_mode": spec.reveal_mode.value,
        "theme": spec.theme,
        "seed": spec.seed,
        "layout": layout_settings_to_dict(spec.layout),
    }


def worksheet_spec_from_dict(data: dict[str, object]) -> WorksheetSpec:
    return WorksheetSpec(
        worksheet_id=str(data["worksheet_id"]),
        learner_band=LearnerBand(str(data["learner_band"])),
        skill_profile=str(data["skill_profile"]),
        selected_skills=tuple(
            skill_setting_from_dict(item) for item in data.get("selected_skills", [])
        ),
        difficulty_range=difficulty_range_from_dict(_require_dict(data, "difficulty_range")),
        problem_count=int(data["problem_count"]),
        reveal_mode=RevealMode(str(data["reveal_mode"])),
        theme=_optional_str(data.get("theme")),
        seed=_optional_int(data.get("seed")),
        layout=layout_settings_from_dict(_require_dict(data, "layout")),
    )


def difficulty_range_to_dict(difficulty_range: DifficultyRange) -> dict[str, int]:
    return {
        "minimum": difficulty_range.minimum,
        "maximum": difficulty_range.maximum,
    }


def difficulty_range_from_dict(data: dict[str, object]) -> DifficultyRange:
    return DifficultyRange(minimum=int(data["minimum"]), maximum=int(data["maximum"]))


def skill_setting_to_dict(setting: SkillDifficultySetting) -> dict[str, object]:
    return {
        "skill": setting.skill,
        "difficulty_range": difficulty_range_to_dict(setting.difficulty_range),
    }


def skill_setting_from_dict(data: dict[str, object]) -> SkillDifficultySetting:
    return SkillDifficultySetting(
        skill=str(data["skill"]),
        difficulty_range=difficulty_range_from_dict(_require_dict(data, "difficulty_range")),
    )


def layout_settings_to_dict(layout: LayoutSettings) -> dict[str, object]:
    return {
        "page_width": layout.page_width,
        "page_height": layout.page_height,
        "problem_columns": layout.problem_columns,
        "show_slot_labels": layout.show_slot_labels,
        "max_color_options": layout.max_color_options,
        "color_palette": dict(layout.color_palette),
        "color_grid_size": layout.color_grid_size,
        "color_grid_cells": [list(row) for row in layout.color_grid_cells],
        "color_grid_source": layout.color_grid_source,
        "color_grid_name": layout.color_grid_name,
    }


def layout_settings_from_dict(data: dict[str, object]) -> LayoutSettings:
    return LayoutSettings(
        page_width=int(data.get("page_width", 1080)),
        page_height=int(data.get("page_height", 1500)),
        problem_columns=int(data.get("problem_columns", 3)),
        show_slot_labels=bool(data.get("show_slot_labels", True)),
        max_color_options=int(data.get("max_color_options", 32)),
        color_palette={str(key): str(value) for key, value in dict(data.get("color_palette", {})).items()},
        color_grid_size=int(data.get("color_grid_size", 0)),
        color_grid_cells=[[str(cell) for cell in row] for row in data.get("color_grid_cells", [])],
        color_grid_source=_optional_str(data.get("color_grid_source")),
        color_grid_name=_optional_str(data.get("color_grid_name")),
    )


def reward_content_candidate_to_dict(candidate: RewardContentCandidate) -> dict[str, object]:
    return {
        "prompt_text": candidate.prompt_text,
        "solution_phrase": candidate.solution_phrase,
        "theme": candidate.theme,
        "source": candidate.source,
        "approval_state": candidate.approval_state.value,
        "style": candidate.style,
        "language": candidate.language,
        "reading_level_assessment": (
            reading_level_assessment_to_dict(candidate.reading_level_assessment)
            if candidate.reading_level_assessment is not None
            else None
        ),
        "review_notes": list(candidate.review_notes),
    }


def reward_content_candidate_from_dict(data: dict[str, object]) -> RewardContentCandidate:
    return RewardContentCandidate(
        prompt_text=str(data["prompt_text"]),
        solution_phrase=str(data["solution_phrase"]),
        theme=_optional_str(data.get("theme")),
        source=str(data["source"]),
        approval_state=ApprovalState(str(data["approval_state"])),
        style=_optional_str(data.get("style")),
        language=str(data.get("language", "en")),
        reading_level_assessment=reading_level_assessment_from_optional(data.get("reading_level_assessment")),
        review_notes=[str(note) for note in data.get("review_notes", [])],
    )


def reward_content_to_dict(reward_content: RewardContent) -> dict[str, object]:
    return {
        "prompt_text": reward_content.prompt_text,
        "solution_phrase": reward_content.solution_phrase,
        "theme": reward_content.theme,
        "source": reward_content.source,
        "approval_state": reward_content.approval_state.value,
        "style": reward_content.style,
        "language": reward_content.language,
        "reading_level_assessment": (
            reading_level_assessment_to_dict(reward_content.reading_level_assessment)
            if reward_content.reading_level_assessment is not None
            else None
        ),
        "review_notes": list(reward_content.review_notes),
    }


def reward_content_from_dict(data: dict[str, object]) -> RewardContent:
    return RewardContent(
        prompt_text=str(data["prompt_text"]),
        solution_phrase=str(data["solution_phrase"]),
        theme=_optional_str(data.get("theme")),
        source=str(data["source"]),
        approval_state=ApprovalState(str(data["approval_state"])),
        style=_optional_str(data.get("style")),
        language=str(data.get("language", "en")),
        reading_level_assessment=reading_level_assessment_from_optional(data.get("reading_level_assessment")),
        review_notes=[str(note) for note in data.get("review_notes", [])],
    )


def reading_level_assessment_to_dict(assessment: ReadingLevelAssessment) -> dict[str, object]:
    return {
        "learner_band": assessment.learner_band.value,
        "passed": assessment.passed,
        "word_count": assessment.word_count,
        "sentence_count": assessment.sentence_count,
        "long_word_count": assessment.long_word_count,
        "flagged_terms": list(assessment.flagged_terms),
        "notes": list(assessment.notes),
    }


def reading_level_assessment_from_dict(data: dict[str, object]) -> ReadingLevelAssessment:
    return ReadingLevelAssessment(
        learner_band=LearnerBand(str(data["learner_band"])),
        passed=bool(data["passed"]),
        word_count=int(data["word_count"]),
        sentence_count=int(data["sentence_count"]),
        long_word_count=int(data["long_word_count"]),
        flagged_terms=[str(term) for term in data.get("flagged_terms", [])],
        notes=[str(note) for note in data.get("notes", [])],
    )


def problem_to_dict(problem: Problem) -> dict[str, object]:
    return {
        "problem_id": problem.problem_id,
        "prompt": problem.prompt,
        "family": problem.family,
        "difficulty": problem.difficulty,
        "answer_format": problem.answer_format,
        "learner_band": problem.learner_band.value,
        "metadata": dict(problem.metadata),
    }


def problem_from_dict(data: dict[str, object]) -> Problem:
    return Problem(
        problem_id=str(data["problem_id"]),
        prompt=str(data["prompt"]),
        family=str(data["family"]),
        difficulty=int(data["difficulty"]),
        answer_format=str(data["answer_format"]),
        learner_band=LearnerBand(str(data["learner_band"])),
        metadata=dict(data.get("metadata", {})),
    )


def solved_problem_to_dict(problem: SolvedProblem) -> dict[str, object]:
    return {
        "problem_id": problem.problem_id,
        "canonical_answer": problem.canonical_answer,
        "normalized_answer": problem.normalized_answer,
        "verified": problem.verified,
    }


def solved_problem_from_dict(data: dict[str, object]) -> SolvedProblem:
    return SolvedProblem(
        problem_id=str(data["problem_id"]),
        canonical_answer=str(data["canonical_answer"]),
        normalized_answer=str(data["normalized_answer"]),
        verified=bool(data.get("verified", True)),
    )


def letter_assignment_to_dict(assignment: LetterAssignment) -> dict[str, object]:
    return {
        "problem_id": assignment.problem_id,
        "answer_value": assignment.answer_value,
        "reveal_token": assignment.reveal_token,
        "answer_slot_index": assignment.answer_slot_index,
        "is_distractor": assignment.is_distractor,
    }


def letter_assignment_from_dict(data: dict[str, object]) -> LetterAssignment:
    return LetterAssignment(
        problem_id=str(data["problem_id"]),
        answer_value=str(data["answer_value"]),
        reveal_token=str(data["reveal_token"]),
        answer_slot_index=_optional_int(data.get("answer_slot_index")),
        is_distractor=bool(data.get("is_distractor", False)),
    )


def rendered_output_to_dict(rendered_output: RenderedWorksheet) -> dict[str, object]:
    return {
        "worksheet_id": rendered_output.worksheet_id,
        "output_format": rendered_output.output_format,
        "output_path": rendered_output.output_path,
    }


def rendered_output_from_dict(data: dict[str, object]) -> RenderedWorksheet:
    return RenderedWorksheet(
        worksheet_id=str(data["worksheet_id"]),
        output_format=str(data["output_format"]),
        output_path=str(data["output_path"]),
    )


def _require_dict(data: dict[str, object], key: str) -> dict[str, object]:
    value = data[key]
    if not isinstance(value, dict):
        raise TypeError(f"{key} must be a mapping")
    return value


def _require_list(data: dict[str, object], key: str) -> list[dict[str, object]]:
    value = data[key]
    if not isinstance(value, list):
        raise TypeError(f"{key} must be a list")
    result: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise TypeError(f"{key} entries must be mappings")
        result.append(item)
    return result


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def reading_level_assessment_from_optional(value: object) -> ReadingLevelAssessment | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError("reading_level_assessment must be a mapping")
    return reading_level_assessment_from_dict(value)
