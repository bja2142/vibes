from __future__ import annotations

from dataclasses import dataclass

from .models import RevealMode, Worksheet
from .solution_phrase import solution_letters


@dataclass(frozen=True)
class WorksheetValidationReport:
    is_valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    reconstructed_answer: str
    distinct_letter_answer_map: dict[str, tuple[str, ...]]


class WorksheetValidator:
    def validate(self, worksheet: Worksheet) -> WorksheetValidationReport:
        errors: list[str] = []
        warnings: list[str] = []

        solved_map = worksheet.solved_problem_map()
        reconstructed_answer = ""
        distinct_letter_answer_values: dict[str, list[str]] = {}

        if not worksheet.reward_content.prompt_text or not worksheet.reward_content.solution_phrase:
            errors.append("reward content must include both prompt text and solution phrase")
        if len(worksheet.problems) != worksheet.spec.problem_count:
            errors.append("problem count does not match worksheet spec")
        if worksheet.spec.reveal_mode == RevealMode.COLOR_BY_NUMBER:
            active_assignments = worksheet.active_assignments()
            if len(active_assignments) != len(worksheet.problems):
                errors.append("color-by-number worksheet must assign one color to every problem")
            answer_values = set()
            color_labels = set()
            for assignment in active_assignments:
                solved_problem = solved_map[assignment.problem_id]
                if assignment.answer_value != solved_problem.normalized_answer:
                    errors.append(f"assignment answer mismatch for {assignment.problem_id}")
                if assignment.answer_value in answer_values:
                    errors.append(f"color-by-number answer value {assignment.answer_value!r} is duplicated")
                answer_values.add(assignment.answer_value)
                color_labels.add(assignment.reveal_token)
                distinct_letter_answer_values.setdefault(assignment.reveal_token, []).append(assignment.answer_value)

            grid_cells = worksheet.spec.layout.color_grid_cells
            grid_labels = {cell for row in grid_cells for cell in row}
            if worksheet.spec.layout.color_grid_size <= 0 or not grid_cells:
                errors.append("color-by-number worksheet is missing color-grid data")
            elif any(len(row) != worksheet.spec.layout.color_grid_size for row in grid_cells):
                errors.append("color-by-number grid is not square")
            if grid_labels != color_labels:
                errors.append("color-by-number grid labels do not match assigned palette labels")
        else:
            slot_assignments = worksheet.slot_assignments()
            reveal_letters = solution_letters(worksheet.reward_content.solution_phrase)
            reconstructed_answer = "".join(assignment.reveal_token for assignment in slot_assignments)
            answer_to_letter_map: dict[str, str] = {}

            expected_slot_indexes = list(range(len(reveal_letters)))
            actual_slot_indexes = [assignment.answer_slot_index for assignment in slot_assignments]
            if actual_slot_indexes != expected_slot_indexes:
                errors.append("answer-slot coverage is incomplete or out of order")

            for assignment in slot_assignments:
                solved_problem = solved_map[assignment.problem_id]
                if assignment.answer_value != solved_problem.normalized_answer:
                    errors.append(f"assignment answer mismatch for {assignment.problem_id}")

                letter_answers = distinct_letter_answer_values.setdefault(assignment.reveal_token, [])
                if assignment.answer_value not in letter_answers:
                    letter_answers.append(assignment.answer_value)

                existing_letter = answer_to_letter_map.get(assignment.answer_value)
                if existing_letter is None:
                    answer_to_letter_map[assignment.answer_value] = assignment.reveal_token
                elif existing_letter != assignment.reveal_token:
                    errors.append(
                        f"answer value {assignment.answer_value!r} is shared by distinct letters "
                        f"{existing_letter!r} and {assignment.reveal_token!r}"
                    )

            if reconstructed_answer != "".join(reveal_letters):
                errors.append("final answer cannot be reconstructed from slot assignments")

            distractor_count = len([assignment for assignment in worksheet.letter_assignments if assignment.is_distractor])
            if distractor_count == 0:
                warnings.append("worksheet contains no distractor problems")

        distinct_letter_answer_map = {
            letter: tuple(answer_values)
            for letter, answer_values in distinct_letter_answer_values.items()
        }

        return WorksheetValidationReport(
            is_valid=not errors,
            errors=tuple(errors),
            warnings=tuple(warnings),
            reconstructed_answer=reconstructed_answer,
            distinct_letter_answer_map=distinct_letter_answer_map,
        )
