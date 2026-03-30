from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from random import Random
import string

from .models import LetterAssignment, Problem, SolvedProblem
from .solution_phrase import solution_letters


class MappingConstraintError(ValueError):
    pass


@dataclass(frozen=True)
class MappingResult:
    assignments: tuple[LetterAssignment, ...]
    distinct_letter_answer_map: dict[str, tuple[str, ...]]
    slot_count: int
    distractor_problem_ids: tuple[str, ...]


class MappingEngine:
    def assign(
        self,
        solution_phrase: str,
        problems: tuple[Problem, ...] | list[Problem],
        solved_problems: tuple[SolvedProblem, ...] | list[SolvedProblem],
        distractor_count: int = 0,
        repeat_letter_mode: str = "auto",
        seed: int | None = None,
    ) -> MappingResult:
        if repeat_letter_mode not in {"auto", "shared", "split"}:
            raise MappingConstraintError(f"unsupported repeat_letter_mode {repeat_letter_mode!r}")

        reveal_letters = solution_letters(solution_phrase)
        if not reveal_letters:
            raise MappingConstraintError("solution phrase must contain at least one letter")

        problem_map = {problem.problem_id: problem for problem in problems}
        solved_map = {problem.problem_id: problem for problem in solved_problems}
        ordered_problem_ids = [problem.problem_id for problem in problems]
        answer_groups: dict[str, list[str]] = {}
        rng = Random(42 if seed is None else seed)

        for problem_id in ordered_problem_ids:
            answer_value = solved_map[problem_id].normalized_answer
            answer_groups.setdefault(answer_value, []).append(problem_id)

        distinct_letters_in_order: list[str] = []
        for letter in reveal_letters:
            if letter not in distinct_letters_in_order:
                distinct_letters_in_order.append(letter)

        required_counts = Counter(reveal_letters)
        assigned_answers_by_letter: dict[str, tuple[str, ...]] = {}
        available_groups = {
            answer_value: list(problem_ids)
            for answer_value, problem_ids in answer_groups.items()
        }

        for letter in sorted(distinct_letters_in_order, key=lambda item: (-required_counts[item], distinct_letters_in_order.index(item))):
            required_count = required_counts[letter]
            shared_candidates = [
                answer_value
                for answer_value, problem_ids in available_groups.items()
                if len(problem_ids) >= required_count
            ]
            split_candidates = [
                answer_value
                for answer_value, problem_ids in available_groups.items()
                if problem_ids
            ]
            split_possible = len(split_candidates) >= required_count
            use_split = False

            if required_count > 1:
                if repeat_letter_mode == "split":
                    use_split = True
                elif repeat_letter_mode == "auto":
                    if shared_candidates and split_possible:
                        use_split = rng.choice([False, True])
                    elif split_possible and not shared_candidates:
                        use_split = True

            if use_split:
                if len(split_candidates) < required_count:
                    raise MappingConstraintError(
                        f"unable to find {required_count} unique answer groups for repeated letter {letter!r}"
                    )
                chosen_answers = tuple(rng.sample(split_candidates, required_count))
                assigned_answers_by_letter[letter] = chosen_answers
                for answer_value in chosen_answers:
                    del available_groups[answer_value]
                continue

            matching_answer = next(iter(shared_candidates), None)
            if matching_answer is None:
                raise MappingConstraintError(
                    f"unable to find {required_count} problem(s) for letter {letter!r}"
                )
            assigned_answers_by_letter[letter] = (matching_answer,)
            del available_groups[matching_answer]

        assignments: list[LetterAssignment] = []
        remaining_ids_by_answer = {
            answer_value: list(problem_ids)
            for answer_value, problem_ids in answer_groups.items()
        }
        occurrence_index_by_letter: dict[str, int] = {}

        for slot_index, letter in enumerate(reveal_letters):
            answer_values = assigned_answers_by_letter[letter]
            if len(answer_values) == 1:
                answer_value = answer_values[0]
            else:
                occurrence_index = occurrence_index_by_letter.get(letter, 0)
                answer_value = answer_values[occurrence_index]
                occurrence_index_by_letter[letter] = occurrence_index + 1
            problem_id = remaining_ids_by_answer[answer_value].pop(0)
            assignments.append(
                LetterAssignment(
                    problem_id=problem_id,
                    answer_value=answer_value,
                    reveal_token=letter,
                    answer_slot_index=slot_index,
                    is_distractor=False,
                )
            )

        used_problem_ids = {assignment.problem_id for assignment in assignments}
        distractor_problem_ids = [problem_id for problem_id in ordered_problem_ids if problem_id not in used_problem_ids]
        distractor_tokens = self._build_distractor_tokens(
            reveal_letters=reveal_letters,
            distractor_count=distractor_count,
            rng=rng,
        )
        distractor_answers = self._build_distractor_answers(
            used_answer_values=set(answer_groups.keys()),
            distractor_count=distractor_count,
        )
        for index in range(distractor_count):
            assignments.append(
                LetterAssignment(
                    problem_id=f"D{index + 1:02d}",
                    answer_value=distractor_answers[index],
                    reveal_token=distractor_tokens[index],
                    answer_slot_index=None,
                    is_distractor=True,
                )
            )

        return MappingResult(
            assignments=tuple(assignments),
            distinct_letter_answer_map=assigned_answers_by_letter,
            slot_count=len(reveal_letters),
            distractor_problem_ids=tuple(f"D{index + 1:02d}" for index in range(distractor_count)),
        )

    def _build_distractor_tokens(
        self,
        *,
        reveal_letters: list[str],
        distractor_count: int,
        rng: Random,
    ) -> list[str]:
        if distractor_count <= 0:
            return []
        solution_set = {letter.upper() for letter in reveal_letters}
        candidate_letters = [letter for letter in string.ascii_uppercase if letter not in solution_set]
        if not candidate_letters:
            candidate_letters = list(string.ascii_uppercase)
        return [candidate_letters[index % len(candidate_letters)] for index in range(distractor_count)]

    def _build_distractor_answers(
        self,
        *,
        used_answer_values: set[str],
        distractor_count: int,
    ) -> list[str]:
        if distractor_count <= 0:
            return []
        if used_answer_values and all(value.lstrip("-").isdigit() for value in used_answer_values):
            numeric_values = [int(value) for value in used_answer_values]
            candidate = max(numeric_values) + 1
            distractors: list[str] = []
            while len(distractors) < distractor_count:
                value = str(candidate)
                if value not in used_answer_values:
                    distractors.append(value)
                candidate += 1
            return distractors

        distractors = []
        candidate = 1
        while len(distractors) < distractor_count:
            value = f"X{candidate:02d}"
            if value not in used_answer_values:
                distractors.append(value)
            candidate += 1
        return distractors
