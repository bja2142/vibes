from __future__ import annotations

from .models import ApprovalState, LearnerBand, RewardContentCandidate
from .reward_content_generation import RewardContentGenerationRequest, RewardContentGenerator
from .reward_content_review import RewardContentReviewer


class RewardContentService:
    def __init__(
        self,
        generator: RewardContentGenerator,
        reviewer: RewardContentReviewer | None = None,
    ) -> None:
        self._generator = generator
        self._reviewer = reviewer or RewardContentReviewer()

    def create_direct_candidate(
        self,
        learner_band: LearnerBand,
        prompt_text: str,
        solution_phrase: str,
        theme: str | None = None,
        style: str | None = None,
        language: str = "en",
    ) -> RewardContentCandidate:
        candidate = RewardContentCandidate(
            prompt_text=prompt_text,
            solution_phrase=solution_phrase,
            theme=theme,
            source="direct_input",
            approval_state=ApprovalState.PENDING,
            style=style,
            language=language,
            review_notes=["Direct-input reward content submitted for review."],
        )
        return self._reviewer.assess_candidate(candidate, learner_band)

    def generate_candidate(self, request: RewardContentGenerationRequest) -> RewardContentCandidate:
        candidate = self._generator.generate(request)
        return self._reviewer.assess_candidate(candidate, request.learner_band)

    def generate_candidate_for_solution(
        self,
        request: RewardContentGenerationRequest,
        solution_phrase: str,
    ) -> RewardContentCandidate:
        candidate = self._generator.generate_for_solution(request, solution_phrase)
        return self._reviewer.assess_candidate(candidate, request.learner_band)

    def reject_candidate(self, candidate: RewardContentCandidate, reason: str) -> RewardContentCandidate:
        return self._reviewer.reject_candidate(candidate, reason)

    def edit_candidate(
        self,
        candidate: RewardContentCandidate,
        learner_band: LearnerBand,
        prompt_text: str | None = None,
        solution_phrase: str | None = None,
    ) -> RewardContentCandidate:
        return self._reviewer.edit_candidate(candidate, learner_band, prompt_text, solution_phrase)

    def approve_candidate(self, candidate: RewardContentCandidate, learner_band: LearnerBand):
        return self._reviewer.approve_candidate(candidate, learner_band)

    def ensure_approved(self, reward_content) -> None:
        self._reviewer.ensure_approved(reward_content)
