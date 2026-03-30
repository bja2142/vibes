from __future__ import annotations

from pathlib import Path

from worksheet_generator.exporter import WorksheetExportService
from worksheet_generator.models import ApprovalState, DifficultyRange, LearnerBand, RevealMode, RewardContent, WorksheetSpec
from worksheet_generator.worksheet_assembly import WorksheetAssemblyService


def test_full_worksheet_generation_and_export_round_trip(tmp_path: Path) -> None:
    spec = WorksheetSpec(
        worksheet_id="end-to-end-demo",
        learner_band=LearnerBand.UPPER_ELEMENTARY,
        skill_profile="subtraction_and_addition",
        difficulty_range=DifficultyRange(minimum=1, maximum=2),
        problem_count=4,
        reveal_mode=RevealMode.LETTER_BANK,
        seed=808,
    )
    reward_content = RewardContent(
        prompt_text="What classroom word tells you to keep trying?",
        solution_phrase="GROW",
        theme="classroom mindset",
        source="direct_input",
        approval_state=ApprovalState.APPROVED,
        style="question",
    )

    assembled = WorksheetAssemblyService().build_worksheet(
        spec,
        reward_content,
        max_attempts=12,
        distractor_count=0,
        problem_id_prefix="E",
    )
    preview, solution = WorksheetExportService().export_preview_and_solution(
        assembled.worksheet,
        tmp_path,
        preview_stem="end-to-end-preview",
        solution_stem="end-to-end-solution",
    )

    assert assembled.validation_report.is_valid is True
    assert assembled.validation_report.reconstructed_answer == "GROW"
    assert preview.rendered_page.metrics.content_fits_page is True
    assert solution.rendered_page.metrics.content_fits_page is True
    assert (tmp_path / "end-to-end-preview.pdf").exists() is True
    assert (tmp_path / "end-to-end-solution.png").exists() is True
