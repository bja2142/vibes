from __future__ import annotations

from dataclasses import replace
import re

from .models import ApprovalState, LearnerBand, ReadingLevelAssessment, RewardContent, RewardContentCandidate


class RewardContentStateError(ValueError):
    pass


class RewardContentApprovalError(ValueError):
    pass


class RewardContentValidationError(ValueError):
    pass


_BANNED_TERMS = {
    "blood",
    "kill",
    "weapon",
    "beer",
    "drugs",
    "hate",
    "violence",
}

_MAX_WORDS = {
    LearnerBand.EARLY_ARITHMETIC: 14,
    LearnerBand.UPPER_ELEMENTARY: 18,
    LearnerBand.PRE_ALGEBRA: 24,
    LearnerBand.ALGEBRA: 30,
    LearnerBand.GEOMETRY: 30,
}

_MAX_LONG_WORDS = {
    LearnerBand.EARLY_ARITHMETIC: 1,
    LearnerBand.UPPER_ELEMENTARY: 3,
    LearnerBand.PRE_ALGEBRA: 5,
    LearnerBand.ALGEBRA: 6,
    LearnerBand.GEOMETRY: 6,
}


class RewardContentReviewer:
    def assess_candidate(
        self,
        candidate: RewardContentCandidate,
        learner_band: LearnerBand,
    ) -> RewardContentCandidate:
        normalized = self._normalize_candidate(candidate)
        assessment = self._assess_text(normalized.prompt_text, normalized.solution_phrase, learner_band)
        review_notes = list(normalized.review_notes)
        if assessment.passed:
            review_notes.append("Reading-level and appropriateness checks passed.")
        else:
            review_notes.append("Reading-level or appropriateness checks failed.")
        return replace(normalized, reading_level_assessment=assessment, review_notes=review_notes)

    def reject_candidate(self, candidate: RewardContentCandidate, reason: str) -> RewardContentCandidate:
        return replace(
            candidate,
            approval_state=ApprovalState.REJECTED,
            review_notes=[*candidate.review_notes, f"Rejected: {reason}"],
        )

    def edit_candidate(
        self,
        candidate: RewardContentCandidate,
        learner_band: LearnerBand,
        prompt_text: str | None = None,
        solution_phrase: str | None = None,
    ) -> RewardContentCandidate:
        updated = replace(
            candidate,
            prompt_text=prompt_text or candidate.prompt_text,
            solution_phrase=solution_phrase or candidate.solution_phrase,
            approval_state=ApprovalState.EDITED,
            review_notes=[*candidate.review_notes, "Candidate edited before approval."],
        )
        return self.assess_candidate(updated, learner_band)

    def approve_candidate(
        self,
        candidate: RewardContentCandidate,
        learner_band: LearnerBand,
    ) -> RewardContent:
        if candidate.approval_state == ApprovalState.REJECTED:
            raise RewardContentStateError("rejected reward content cannot be approved without regeneration")

        assessed_candidate = candidate
        if assessed_candidate.reading_level_assessment is None:
            assessed_candidate = self.assess_candidate(candidate, learner_band)

        approval_note = "Approved for worksheet generation."
        if assessed_candidate.reading_level_assessment and not assessed_candidate.reading_level_assessment.passed:
            approval_note = "Approved for worksheet generation despite heuristic reading-level warnings."

        return RewardContent(
            prompt_text=assessed_candidate.prompt_text,
            solution_phrase=assessed_candidate.solution_phrase,
            theme=assessed_candidate.theme,
            source=assessed_candidate.source,
            approval_state=ApprovalState.APPROVED,
            style=assessed_candidate.style,
            language=assessed_candidate.language,
            reading_level_assessment=assessed_candidate.reading_level_assessment,
            review_notes=[*assessed_candidate.review_notes, approval_note],
        )

    def ensure_approved(self, reward_content: RewardContent) -> None:
        if reward_content.approval_state != ApprovalState.APPROVED:
            raise RewardContentApprovalError("worksheet generation is blocked until reward content is approved")

    def _assess_text(
        self,
        prompt_text: str,
        solution_phrase: str,
        learner_band: LearnerBand,
    ) -> ReadingLevelAssessment:
        combined_text = f"{prompt_text} {solution_phrase}"
        words = re.findall(r"[A-Za-z']+", combined_text)
        sentences = re.findall(r"[.!?]+", prompt_text) or ["."]
        long_words = [word for word in words if len(word) >= 8]
        lowered_words = {word.lower() for word in words}
        flagged_terms = sorted(lowered_words.intersection(_BANNED_TERMS))

        notes: list[str] = []
        max_words = _MAX_WORDS[learner_band]
        max_long_words = _MAX_LONG_WORDS[learner_band]

        if len(words) > max_words:
            notes.append(f"Word count {len(words)} exceeds target maximum {max_words}.")
        if len(long_words) > max_long_words:
            notes.append(f"Long-word count {len(long_words)} exceeds target maximum {max_long_words}.")
        if flagged_terms:
            notes.append(f"Flagged terms found: {', '.join(flagged_terms)}.")
        if not notes:
            notes.append("Content fits the configured reading-level heuristics.")

        return ReadingLevelAssessment(
            learner_band=learner_band,
            passed=not flagged_terms and len(words) <= max_words and len(long_words) <= max_long_words,
            word_count=len(words),
            sentence_count=len(sentences),
            long_word_count=len(long_words),
            flagged_terms=flagged_terms,
            notes=notes,
        )

    def _normalize_candidate(self, candidate: RewardContentCandidate) -> RewardContentCandidate:
        return replace(
            candidate,
            prompt_text=self._normalize_prompt_text(candidate.prompt_text),
            solution_phrase=self._normalize_solution_phrase(candidate.solution_phrase),
        )

    def _normalize_prompt_text(self, prompt_text: str) -> str:
        normalized = " ".join(prompt_text.split())
        if not normalized:
            raise RewardContentValidationError("prompt text must not be empty")
        return normalized

    def _normalize_solution_phrase(self, solution_phrase: str) -> str:
        normalized = " ".join(solution_phrase.split())
        if not normalized:
            raise RewardContentValidationError("solution phrase must not be empty")
        if not re.fullmatch(r"[A-Za-z ]+", normalized):
            raise RewardContentValidationError("solution phrase must contain letters and spaces only")
        if not re.search(r"[A-Za-z]", normalized):
            raise RewardContentValidationError("solution phrase must contain at least one letter")
        return normalized
