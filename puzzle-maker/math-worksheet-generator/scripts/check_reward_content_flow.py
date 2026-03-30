from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worksheet_generator.models import LearnerBand
from worksheet_generator.reward_content_generation import (
    RewardContentGenerationRequest,
    StubGeminiRewardContentGenerator,
)
from worksheet_generator.reward_content_review import RewardContentApprovalError
from worksheet_generator.reward_content_service import RewardContentService


def main() -> None:
    service = RewardContentService(generator=StubGeminiRewardContentGenerator())

    direct_candidate = service.create_direct_candidate(
        learner_band=LearnerBand.UPPER_ELEMENTARY,
        prompt_text="What classroom clue points to fractions?",
        solution_phrase="Fractions",
        theme="fractions",
        style="question",
    )
    edited_candidate = service.edit_candidate(
        direct_candidate,
        learner_band=LearnerBand.UPPER_ELEMENTARY,
        prompt_text="What classroom clue points to fraction fun?",
    )
    approved_direct = service.approve_candidate(edited_candidate, LearnerBand.UPPER_ELEMENTARY)
    service.ensure_approved(approved_direct)

    assisted_candidate = service.generate_candidate(
        RewardContentGenerationRequest(
            theme="planets",
            learner_band=LearnerBand.EARLY_ARITHMETIC,
            preferred_style="pun",
        )
    )
    rejected_candidate = service.reject_candidate(assisted_candidate, "Tone needs a different classroom angle.")

    print(f"direct_state={direct_candidate.approval_state.value}")
    print(f"edited_state={edited_candidate.approval_state.value}")
    print(f"approved_state={approved_direct.approval_state.value}")
    print(f"assisted_state={assisted_candidate.approval_state.value}")
    print(f"rejected_state={rejected_candidate.approval_state.value}")

    try:
        service.approve_candidate(rejected_candidate, LearnerBand.EARLY_ARITHMETIC)
    except RewardContentApprovalError as exc:
        print(f"blocked_rejected={exc}")
    except ValueError as exc:
        print(f"blocked_rejected={exc}")


if __name__ == "__main__":
    main()
