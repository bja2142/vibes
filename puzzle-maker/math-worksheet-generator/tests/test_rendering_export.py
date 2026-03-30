from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from io import BytesIO

from PIL import Image
from pypdf import PdfReader

from worksheet_generator.exporter import WorksheetExportService
from worksheet_generator.models import ApprovalState, DifficultyRange, LearnerBand, ReadingLevelAssessment, RevealMode, RewardContent, WorksheetSpec
from worksheet_generator.problem_generators import ProblemGenerationService
from worksheet_generator.rendering import (
    PreviewProblem,
    arithmetic_layout_kind,
    WorksheetRenderer,
    answer_chip_width_for_card,
    lookup_chip_width_for_rows,
    preview_problems,
)
from worksheet_generator.sample_data import build_color_by_number_sample, build_letter_bank_sample, build_pre_algebra_sample
from worksheet_generator.worksheet_assembly import WorksheetAssemblyService


SNAPSHOT_PATH = Path(__file__).resolve().parent / "snapshots" / "render_metrics.json"


def fixture_worksheets():
    return [
        build_letter_bank_sample(),
        build_color_by_number_sample(),
        build_pre_algebra_sample(),
    ]


def test_render_metrics_match_snapshot() -> None:
    snapshots = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    renderer = WorksheetRenderer()

    for worksheet in fixture_worksheets():
        preview = renderer.render(worksheet, solution=False)
        solution = renderer.render(worksheet, solution=True)
        expected = snapshots[worksheet.worksheet_id]

        assert {
            "page_width": preview.metrics.page_width,
            "page_height": preview.metrics.page_height,
            "problem_count": preview.metrics.problem_count,
            "slot_count": preview.metrics.slot_count,
            "content_bottom": preview.metrics.content_bottom,
            "content_fits_page": preview.metrics.content_fits_page,
        } == expected["preview"]
        assert {
            "page_width": solution.metrics.page_width,
            "page_height": solution.metrics.page_height,
            "problem_count": solution.metrics.problem_count,
            "slot_count": solution.metrics.slot_count,
            "content_bottom": solution.metrics.content_bottom,
            "content_fits_page": solution.metrics.content_fits_page,
        } == expected["solution"]


def test_export_service_writes_svg_png_and_pdf(tmp_path: Path) -> None:
    worksheet = build_pre_algebra_sample()
    exporter = WorksheetExportService()

    preview, solution = exporter.export_preview_and_solution(
        worksheet,
        tmp_path,
        preview_stem="pre-algebra-preview",
        solution_stem="pre-algebra-solution",
    )

    for variant in (preview, solution):
        assert {output.output_format for output in variant.outputs} == {"svg", "png", "pdf"}
        for output in variant.outputs:
            path = Path(output.output_path)
            assert path.exists() is True
            assert path.stat().st_size > 0


def test_letter_bank_render_normalizes_lookup_and_solution_letters_to_uppercase() -> None:
    worksheet = build_letter_bank_sample()
    lowered = replace(
        worksheet,
        reward_content=replace(worksheet.reward_content, solution_phrase=worksheet.reward_content.solution_phrase.lower()),
        letter_assignments=[
            replace(assignment, reveal_token=assignment.reveal_token.lower())
            for assignment in worksheet.letter_assignments
        ],
    )

    rendered = WorksheetRenderer().render(lowered, solution=True)

    assert ">m<" not in rendered.svg
    assert ">r<" not in rendered.svg
    assert ">M<" in rendered.svg
    assert ">R<" in rendered.svg


def test_letter_bank_render_ignores_non_letter_solution_characters() -> None:
    worksheet = build_letter_bank_sample()
    legacy_phrase = replace(
        worksheet,
        reward_content=replace(worksheet.reward_content, solution_phrase="MATH! RULES??"),
    )

    rendered = WorksheetRenderer().render(legacy_phrase, solution=True)

    assert rendered.metrics.problem_count == 9
    assert rendered.metrics.slot_count == 9
    assert ">!<" not in rendered.svg
    assert ">?<" not in rendered.svg


def test_problem_cards_use_compact_q_labels_and_strip_extra_copy() -> None:
    worksheet = build_pre_algebra_sample()

    rendered = WorksheetRenderer().render(worksheet, solution=False)

    assert "<title>Algebra Worksheet</title>" in rendered.svg
    assert "Q01 - Solve for x:" in rendered.svg
    assert "Solve for x: x + 2 = 10" not in rendered.svg
    assert "Validated printable layout" not in rendered.svg
    assert "Solve a problem, find the answer value" not in rendered.svg


def test_letter_bank_preview_questions_follow_slot_order_not_generation_order() -> None:
    worksheet = build_letter_bank_sample()
    reordered = replace(
        worksheet,
        letter_assignments=[
            replace(worksheet.letter_assignments[0], answer_slot_index=1, reveal_token="O"),
            replace(worksheet.letter_assignments[1], answer_slot_index=0, reveal_token="C"),
            *worksheet.letter_assignments[2:],
        ],
    )

    preview = preview_problems(reordered)

    assert preview[0].problem_id == worksheet.letter_assignments[1].problem_id
    assert preview[0].reveal == "C"
    assert preview[1].problem_id == worksheet.letter_assignments[0].problem_id
    assert preview[1].reveal == "O"


def test_color_by_number_renders_color_labels_in_questions_without_palette_panel() -> None:
    worksheet = build_color_by_number_sample()

    rendered = WorksheetRenderer().render(worksheet, solution=False)

    assert "Color Palette" not in rendered.svg
    assert "Color: Sun Yellow" in rendered.svg
    assert "Color: Leaf Green" in rendered.svg
    assert 'font-size: 12px;' in rendered.svg


def test_lookup_chip_width_grows_for_long_answer_values() -> None:
    width = lookup_chip_width_for_rows(
        rows=[("12345", "A"), ("123456789012345", "B")],
        page_width=1080,
    )

    assert width > 150


def test_answer_chip_width_grows_for_long_solution_values() -> None:
    width = answer_chip_width_for_card(
        answers=["123456789012345"],
        card_width=480,
        has_color_labels=False,
    )

    assert width > 88


def test_arithmetic_layout_kind_detects_multi_digit_vertical_formats() -> None:
    assert arithmetic_layout_kind(
        PreviewProblem(problem_id="A", prompt="123 + 45 = ?", answer="168", reveal="A", family="addition", metadata={})
    ) == ("addition", 123, 45)
    assert arithmetic_layout_kind(
        PreviewProblem(problem_id="AA", prompt="809 - 76 = ?", answer="733", reveal="A", family="subtraction", metadata={})
    ) == ("subtraction", 809, 76)
    assert arithmetic_layout_kind(
        PreviewProblem(problem_id="B", prompt="123 x 45 = ?", answer="5535", reveal="B", family="multiplication", metadata={})
    ) == ("multiplication", 123, 45)
    assert arithmetic_layout_kind(
        PreviewProblem(
            problem_id="BB",
            prompt="5 * 2 = ?",
            answer="10",
            reveal="B",
            family="multiplication",
            metadata={"operator": "*", "operands": [5, 2]},
        )
    ) == ("multiplication", 5, 2)
    assert arithmetic_layout_kind(
        PreviewProblem(problem_id="C", prompt="1234 / 12 = ?", answer="102", reveal="C", family="division", metadata={})
    ) == ("division", 1234, 12)


def test_pdf_pagination_keeps_sections_and_question_rows_intact() -> None:
    worksheet = build_letter_bank_sample()
    overflowed = replace(
        worksheet,
        spec=replace(
            worksheet.spec,
            layout=replace(worksheet.spec.layout, page_height=520),
        ),
    )
    exporter = WorksheetExportService()
    rendered = WorksheetRenderer().render(overflowed, solution=False)

    offsets = exporter._page_offsets(rendered, exporter._export_height(rendered))

    assert len(offsets) >= 2
    assert offsets[0] == 0
    assert offsets[1] in {segment_top for segment_top, _ in rendered.keep_together_segments}

    limits = [
        overflowed.spec.layout.page_height - exporter.PDF_BOTTOM_MARGIN,
        *(offset + overflowed.spec.layout.page_height - exporter.PDF_CONTINUATION_TOP_MARGIN - exporter.PDF_BOTTOM_MARGIN for offset in offsets[1:]),
    ]
    starts = list(offsets)
    for top, bottom in rendered.keep_together_segments:
        assert any(top >= start and bottom <= limit for start, limit in zip(starts, limits))


def test_geometry_diagram_labels_use_centered_alignment() -> None:
    worksheet = build_pre_algebra_sample()
    geometry_like = replace(
        worksheet,
        spec=replace(worksheet.spec, learner_band=LearnerBand.GEOMETRY, skill_profile="geometry"),
        problems=[
            replace(
                worksheet.problems[0],
                family="geometry_problem",
                prompt="Use tangent: In the right triangle, tan(θ) = 3/4 and the adjacent side is 12. What is the opposite side?",
                metadata={
                    "diagram_kind": "right_triangle",
                    "base_label": "12",
                    "vertical_label": "?",
                    "hypotenuse_label": "15",
                    "angle_label": "θ",
                },
            )
        ]
        + worksheet.problems[1:],
    )

    rendered = WorksheetRenderer().render(geometry_like, solution=False)

    assert 'text-anchor="middle">12</text>' in rendered.svg
    assert 'transform="rotate(-28' in rendered.svg


def test_export_service_extends_png_and_paginates_pdf_on_overflow(tmp_path: Path) -> None:
    worksheet = build_letter_bank_sample()
    overflowed = replace(
        worksheet,
        spec=replace(
            worksheet.spec,
            layout=replace(worksheet.spec.layout, page_height=520),
        ),
    )
    exporter = WorksheetExportService()

    preview, _ = exporter.export_preview_and_solution(
        overflowed,
        tmp_path,
        preview_stem="overflow-preview",
        solution_stem="overflow-solution",
    )

    preview_svg = Path(next(output.output_path for output in preview.outputs if output.output_format == "svg"))
    preview_png = Path(next(output.output_path for output in preview.outputs if output.output_format == "png"))
    preview_pdf = Path(next(output.output_path for output in preview.outputs if output.output_format == "pdf"))

    svg_text = preview_svg.read_text(encoding="utf-8")
    assert 'height="520"' not in svg_text
    assert 'height="1042"' in svg_text
    assert preview_png.exists() is True
    assert len(PdfReader(str(preview_pdf)).pages) >= 2


def test_semantic_foreground_svg_uses_transparent_page_and_strips_card_fill() -> None:
    worksheet = build_letter_bank_sample()

    rendered = WorksheetRenderer().render(worksheet, solution=False, semantic_foreground=True)

    assert ".page { fill: none; }" in rendered.svg
    assert ".card { fill: none; }" in rendered.svg
    assert "CRITICAL" not in rendered.svg


def test_semantic_foreground_omits_worksheet_title_text() -> None:
    worksheet = build_letter_bank_sample()

    rendered = WorksheetRenderer().render(worksheet, solution=False, semantic_foreground=True)

    assert "Mixed Operations Worksheet" not in rendered.body
    assert ">Prompt<" in rendered.body


def test_export_semantic_foreground_variant_writes_svg_and_png(tmp_path: Path) -> None:
    worksheet = build_pre_algebra_sample()
    exporter = WorksheetExportService()

    foreground = exporter.export_semantic_foreground_variant(
        worksheet,
        tmp_path,
        "pre-algebra-foreground",
        solution=False,
    )

    assert {output.output_format for output in foreground.outputs} == {"svg", "png"}
    for output in foreground.outputs:
        path = Path(output.output_path)
        assert path.exists() is True
        assert path.stat().st_size > 0


def test_composite_styled_variant_preserves_dark_foreground_pixels(tmp_path: Path) -> None:
    worksheet = build_letter_bank_sample()
    exporter = WorksheetExportService()
    foreground = exporter.export_semantic_foreground_variant(
        worksheet,
        tmp_path,
        "letter-bank-foreground",
        solution=False,
    )
    foreground_png = Path(next(output.output_path for output in foreground.outputs if output.output_format == "png"))
    foreground_image = Image.open(foreground_png).convert("RGBA")

    styled_background = Image.new("RGBA", foreground_image.size, (74, 143, 217, 255))
    background_buffer = BytesIO()
    styled_background.save(background_buffer, format="PNG")
    composited_path = exporter.composite_styled_variant(
        styled_image_bytes=background_buffer.getvalue(),
        foreground_png_bytes=foreground_png.read_bytes(),
        output_path=tmp_path / "composited.png",
    )

    composited = Image.open(composited_path).convert("RGBA")
    assert composited.size == foreground_image.size

    sampled_foreground_pixels = []
    for y in range(0, foreground_image.height, 8):
        for x in range(0, foreground_image.width, 8):
            pixel = foreground_image.getpixel((x, y))
            if pixel[3] > 200 and pixel[0] < 90 and pixel[1] < 90 and pixel[2] < 90:
                sampled_foreground_pixels.append((x, y))

    assert sampled_foreground_pixels
    preserved_samples = 0
    for x, y in sampled_foreground_pixels[:200]:
        red, green, blue, _ = composited.getpixel((x, y))
        if red < 100 and green < 120 and blue < 170:
            preserved_samples += 1

    assert preserved_samples > 0


def test_quadratic_smaller_root_note_moves_to_prompt_box() -> None:
    spec = WorksheetSpec(
        worksheet_id="quadratic-note",
        learner_band=LearnerBand.ALGEBRA,
        skill_profile="algebra",
        difficulty_range=DifficultyRange(minimum=5, maximum=5),
        problem_count=4,
        reveal_mode=RevealMode.LETTER_BANK,
        seed=77,
    )
    reward_content = RewardContent(
        prompt_text="Solve each equation and reveal the final clue.",
        solution_phrase="ROOT",
        theme="algebra",
        source="direct_input",
        approval_state=ApprovalState.APPROVED,
        style="question",
        reading_level_assessment=ReadingLevelAssessment(
            learner_band=LearnerBand.ALGEBRA,
            passed=True,
            word_count=8,
            sentence_count=1,
            long_word_count=1,
        ),
    )
    assembled = WorksheetAssemblyService(generation_service=ProblemGenerationService()).build_worksheet(
        spec=spec,
        reward_content=reward_content,
    )
    worksheet = assembled.worksheet

    rendered = WorksheetRenderer().render(worksheet, solution=False)

    assert "Note:" in rendered.svg
    assert "smaller integer root" in rendered.svg
    assert "Give the smaller integer root." not in rendered.svg
