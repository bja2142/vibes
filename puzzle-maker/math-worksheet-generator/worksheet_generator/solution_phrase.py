from __future__ import annotations


def solution_letters(solution_phrase: str) -> list[str]:
    return [character.upper() for character in solution_phrase if character.isalpha()]


def solution_slot_count(solution_phrase: str) -> int:
    return len(solution_letters(solution_phrase))
