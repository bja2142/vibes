from __future__ import annotations

import json
import logging
from pathlib import Path
from io import BytesIO

from PIL import Image

from worksheet_generator.image_styling import (
    ART_STYLES,
    DEFAULT_IMAGE_REFINEMENT_MODEL,
    GeminiWorksheetStylingPromptRefiner,
    WorksheetImageStylingPromptRequest,
    WorksheetStylingPromptRefinementError,
    build_stricter_worksheet_styling_prompt,
    build_worksheet_styling_prompt,
)
from worksheet_generator.image_styling_service import (
    GeminiWorksheetImageStylingService,
    StyledWorksheetImageArtifact,
    WorksheetImageStylingError,
    WorksheetImageStylingRequest,
    write_styled_image_artifact,
    write_styling_debug_metadata,
)
from worksheet_generator.image_styling_verification import (
    PixelPreservingStyledWorksheetVerifier,
    StyledWorksheetVerificationReport,
    style_and_verify_with_retry,
    write_verification_artifacts,
)


def test_style_catalog_matches_expected_reference_entries() -> None:
    assert "watercolor" in ART_STYLES
    assert "pixel-art" in ART_STYLES
    assert "chalkboard" in ART_STYLES
    assert len(ART_STYLES) >= 15


def test_build_worksheet_styling_prompt_includes_preservation_rules() -> None:
    prompt = build_worksheet_styling_prompt(
        WorksheetImageStylingPromptRequest(
            theme="space",
            style_name="watercolor",
            color_mode="color",
            ink_saver=False,
            additional_guidance="",
            title="Space Worksheet",
            prompt_text="What clue points to the stars?",
            learner_band_label="Upper Elementary",
            reveal_mode_label="Letter Bank",
        )
    )

    assert "Restyle this existing math worksheet image" in prompt
    assert "theme" in prompt.lower()
    assert "CRITICAL PRESERVATION RULES" in prompt
    assert "Every visible text character must remain exactly the same" in prompt
    assert "Do not move, resize, crop, rotate, warp, or relayout the worksheet" in prompt
    assert "Decorate only the page background, border treatment, margins, and other empty whitespace" in prompt
    assert "Leave all final answer boxes and student-answer blanks empty" in prompt


def test_build_worksheet_styling_prompt_respects_black_and_white_ink_saver() -> None:
    prompt = build_worksheet_styling_prompt(
        WorksheetImageStylingPromptRequest(
            theme="robots",
            style_name="blueprint",
            color_mode="black_and_white",
            ink_saver=True,
            additional_guidance="Add a light circuit-pattern border in the whitespace.",
            title="Robot Worksheet",
            prompt_text="What clue points to gears?",
            learner_band_label="Algebra",
            reveal_mode_label="Letter Bank",
        )
    )

    assert "Black and white" in prompt
    assert "ink-saving" in prompt
    assert "Additional user guidance" in prompt
    assert "circuit-pattern border" in prompt


def test_stricter_styling_prompt_preserves_empty_answer_boxes() -> None:
    prompt = build_stricter_worksheet_styling_prompt("Base worksheet prompt.")

    assert "Leave every final answer box and student-answer blank empty" in prompt


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def __init__(self, text: str) -> None:
        self._text = text

    def generate_content(self, *, model: str, contents: str):  # noqa: ANN001
        assert model == DEFAULT_IMAGE_REFINEMENT_MODEL
        assert "Keep all preservation rules intact." in contents
        return _FakeResponse(self._text)


class _FakeClient:
    def __init__(self, text: str) -> None:
        self.models = _FakeModels(text)


def test_prompt_refiner_returns_plain_text_response() -> None:
    refiner = GeminiWorksheetStylingPromptRefiner.__new__(GeminiWorksheetStylingPromptRefiner)
    refiner._model = DEFAULT_IMAGE_REFINEMENT_MODEL  # noqa: SLF001
    refiner._client = _FakeClient("Refined prompt text.")  # noqa: SLF001
    refiner._logger = logging.getLogger("test.image_styling")  # noqa: SLF001

    result = GeminiWorksheetStylingPromptRefiner.refine(refiner, "Base prompt.")

    assert result == "Refined prompt text."


def test_prompt_refiner_rejects_empty_response() -> None:
    refiner = GeminiWorksheetStylingPromptRefiner.__new__(GeminiWorksheetStylingPromptRefiner)
    refiner._model = DEFAULT_IMAGE_REFINEMENT_MODEL  # noqa: SLF001
    refiner._client = _FakeClient("   ")  # noqa: SLF001
    refiner._logger = logging.getLogger("test.image_styling")  # noqa: SLF001

    try:
        GeminiWorksheetStylingPromptRefiner.refine(refiner, "Base prompt.")
    except WorksheetStylingPromptRefinementError:
        pass
    else:
        raise AssertionError("expected empty refinement response to fail")


class _FakeInlineData:
    def __init__(self, data: bytes, mime_type: str = "image/png") -> None:
        self.data = data
        self.mime_type = mime_type


class _FakePart:
    def __init__(self, inline_data: _FakeInlineData | None = None) -> None:
        self.inline_data = inline_data


class _FakeContent:
    def __init__(self, parts):  # noqa: ANN001
        self.parts = parts


class _FakeCandidate:
    def __init__(self, parts):  # noqa: ANN001
        self.content = _FakeContent(parts)


class _FakeImageResponse:
    def __init__(self, *, image_bytes: bytes | None, mime_type: str = "image/png", response_text: str = "ok", response_id: str = "resp-1") -> None:
        self.response_id = response_id
        self.text = response_text
        self.candidates = [
            _FakeCandidate([_FakePart(_FakeInlineData(image_bytes, mime_type))] if image_bytes is not None else [_FakePart(None)])
        ]


class _FakeImageModels:
    def __init__(self, response):  # noqa: ANN001
        self.response = response
        self.calls = []

    def generate_content(self, *, model: str, contents, config):  # noqa: ANN001
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self.response


class _FakeImageClient:
    def __init__(self, response):  # noqa: ANN001
        self.models = _FakeImageModels(response)


def test_image_styling_service_returns_inline_image_artifact() -> None:
    service = GeminiWorksheetImageStylingService.__new__(GeminiWorksheetImageStylingService)
    service._model = "gemini-3.1-flash-image-preview"  # noqa: SLF001
    service._client = _FakeImageClient(_FakeImageResponse(image_bytes=b"png-bytes"))  # noqa: SLF001
    service._logger = logging.getLogger("test.image_styling_service")  # noqa: SLF001

    artifact = GeminiWorksheetImageStylingService.style_image(
        service,
        WorksheetImageStylingRequest(prompt="Style this worksheet.", source_image_bytes=b"source-bytes"),
    )

    assert artifact.image_bytes == b"png-bytes"
    assert artifact.mime_type == "image/png"
    assert artifact.model == "gemini-3.1-flash-image-preview"
    assert artifact.response_id == "resp-1"
    call = service._client.models.calls[0]  # noqa: SLF001
    assert call["model"] == "gemini-3.1-flash-image-preview"
    assert call["config"].response_modalities == ["IMAGE"]
    assert len(call["contents"]) == 2


def test_image_styling_service_rejects_missing_inline_image() -> None:
    service = GeminiWorksheetImageStylingService.__new__(GeminiWorksheetImageStylingService)
    service._model = "gemini-3.1-flash-image-preview"  # noqa: SLF001
    service._client = _FakeImageClient(_FakeImageResponse(image_bytes=None))  # noqa: SLF001
    service._logger = logging.getLogger("test.image_styling_service")  # noqa: SLF001

    try:
        GeminiWorksheetImageStylingService.style_image(
            service,
            WorksheetImageStylingRequest(prompt="Style this worksheet.", source_image_bytes=b"source-bytes"),
        )
    except WorksheetImageStylingError:
        pass
    else:
        raise AssertionError("expected missing inline image to fail")


def test_image_styling_service_logs_raw_response_on_failure(caplog) -> None:
    service = GeminiWorksheetImageStylingService.__new__(GeminiWorksheetImageStylingService)
    service._model = "gemini-3.1-flash-image-preview"  # noqa: SLF001
    service._client = _FakeImageClient(_FakeImageResponse(image_bytes=None, response_text="no image returned", response_id="resp-raw"))  # noqa: SLF001
    service._logger = logging.getLogger("worksheet_generator.image_styling_service")  # noqa: SLF001

    with caplog.at_level(logging.INFO, logger="worksheet_generator.image_styling_service"):
        try:
            GeminiWorksheetImageStylingService.style_image(
                service,
                WorksheetImageStylingRequest(prompt="Style this worksheet.", source_image_bytes=b"source-bytes"),
            )
        except WorksheetImageStylingError:
            pass
        else:
            raise AssertionError("expected missing inline image to fail")

    failure_records = [
        json.loads(record.message)
        for record in caplog.records
        if "worksheet_image_styling_failed" in record.message
    ]
    assert failure_records
    raw_response = json.loads(failure_records[-1]["response_json"])
    assert raw_response["response_id"] == "resp-raw"
    assert raw_response["text"] == "no image returned"


def test_write_styled_image_artifact_and_debug_metadata(tmp_path: Path) -> None:
    service = GeminiWorksheetImageStylingService.__new__(GeminiWorksheetImageStylingService)
    service._model = "gemini-3.1-flash-image-preview"  # noqa: SLF001
    service._client = _FakeImageClient(_FakeImageResponse(image_bytes=b"png-bytes"))  # noqa: SLF001
    service._logger = logging.getLogger("test.image_styling_service")  # noqa: SLF001
    artifact = GeminiWorksheetImageStylingService.style_image(
        service,
        WorksheetImageStylingRequest(prompt="Style this worksheet.", source_image_bytes=b"source-bytes"),
    )

    image_path = write_styled_image_artifact(artifact, tmp_path / "styled.png")
    debug_path = write_styling_debug_metadata(artifact, tmp_path / "styled-debug.json")

    assert image_path.read_bytes() == b"png-bytes"
    debug_text = debug_path.read_text(encoding="utf-8")
    assert "gemini-3.1-flash-image-preview" in debug_text
    assert "Style this worksheet." in debug_text


def _png_bytes(*, size: tuple[int, int], color: tuple[int, int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _foreground_png_bytes(size: tuple[int, int] = (12, 12)) -> bytes:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    width, height = size
    center_x = width // 2
    center_y = height // 2
    for x in range(2, width - 2):
        image.putpixel((x, center_y), (0, 0, 0, 255))
    for y in range(2, height - 2):
        image.putpixel((center_x, y), (0, 0, 0, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_pixel_preserving_verifier_passes_matching_composite() -> None:
    background = _png_bytes(size=(12, 12), color=(240, 240, 240, 255))
    foreground = _foreground_png_bytes()
    verifier = PixelPreservingStyledWorksheetVerifier(sample_step=1)

    from worksheet_generator.image_compositing import composite_foreground_over_background

    composited = composite_foreground_over_background(background_bytes=background, foreground_bytes=foreground)
    report = verifier.verify(
        background_bytes=background,
        foreground_bytes=foreground,
        composited_bytes=composited,
    )

    assert report.passed is True
    assert report.mismatch_count == 0
    assert report.sampled_points > 0


def test_pixel_preserving_verifier_reports_mismatch_points() -> None:
    background = _png_bytes(size=(12, 12), color=(240, 240, 240, 255))
    foreground = _foreground_png_bytes()
    verifier = PixelPreservingStyledWorksheetVerifier(sample_step=1, max_recorded_mismatches=2)

    from worksheet_generator.image_compositing import composite_foreground_over_background

    composited = Image.open(BytesIO(composite_foreground_over_background(background_bytes=background, foreground_bytes=foreground))).convert("RGBA")
    composited.putpixel((6, 6), (255, 0, 0, 255))
    composited.putpixel((6, 5), (255, 0, 0, 255))
    composited.putpixel((5, 6), (255, 0, 0, 255))
    buffer = BytesIO()
    composited.save(buffer, format="PNG")
    report = verifier.verify(
        background_bytes=background,
        foreground_bytes=foreground,
        composited_bytes=buffer.getvalue(),
    )

    assert report.passed is False
    assert report.mismatch_count == 3
    assert len(report.mismatches) == 2
    assert report.mismatch_ratio > 0


class _SequenceStyler:
    def __init__(self, image_bytes_sequence: list[bytes]) -> None:
        self._image_bytes_sequence = list(image_bytes_sequence)
        self.prompts: list[str] = []

    def style_image(self, request: WorksheetImageStylingRequest) -> StyledWorksheetImageArtifact:
        self.prompts.append(request.prompt)
        image_bytes = self._image_bytes_sequence.pop(0)
        return StyledWorksheetImageArtifact(
            image_bytes=image_bytes,
            mime_type="image/png",
            model="fake-image-model",
            prompt=request.prompt,
        )


class _SequenceVerifier:
    def __init__(self, reports: list[StyledWorksheetVerificationReport]) -> None:
        self._reports = list(reports)
        self.calls = 0

    def verify(self, *, background_bytes: bytes, foreground_bytes: bytes, composited_bytes: bytes) -> StyledWorksheetVerificationReport:
        self.calls += 1
        return self._reports.pop(0)


def test_style_and_verify_with_retry_retries_once_with_stricter_prompt() -> None:
    source_image = _png_bytes(size=(8, 8), color=(255, 255, 255, 255))
    semantic_foreground = _foreground_png_bytes((8, 8))
    styler = _SequenceStyler([source_image, source_image])
    verifier = _SequenceVerifier(
        [
            StyledWorksheetVerificationReport(
                passed=False,
                sampled_points=10,
                mismatch_count=1,
                mismatch_ratio=0.1,
                note="text mismatch near title",
            ),
            StyledWorksheetVerificationReport(
                passed=True,
                sampled_points=10,
                mismatch_count=0,
                mismatch_ratio=0.0,
                note="ok",
            ),
        ]
    )

    result = style_and_verify_with_retry(
        styler=styler,
        source_image_bytes=source_image,
        semantic_foreground_bytes=semantic_foreground,
        prompt="Base style prompt.",
        verifier=verifier,
    )

    assert result.verified is True
    assert len(result.attempts) == 2
    assert "RETRY REQUIREMENTS" in styler.prompts[1]
    assert "text mismatch near title" in styler.prompts[1]


def test_style_and_verify_with_retry_returns_failed_result_after_second_attempt() -> None:
    source_image = _png_bytes(size=(8, 8), color=(255, 255, 255, 255))
    semantic_foreground = _foreground_png_bytes((8, 8))
    styler = _SequenceStyler([source_image, source_image])
    verifier = _SequenceVerifier(
        [
            StyledWorksheetVerificationReport(
                passed=False,
                sampled_points=10,
                mismatch_count=2,
                mismatch_ratio=0.2,
                note="first failure",
            ),
            StyledWorksheetVerificationReport(
                passed=False,
                sampled_points=10,
                mismatch_count=1,
                mismatch_ratio=0.1,
                note="second failure",
            ),
        ]
    )

    result = style_and_verify_with_retry(
        styler=styler,
        source_image_bytes=source_image,
        semantic_foreground_bytes=semantic_foreground,
        prompt="Base style prompt.",
        verifier=verifier,
    )

    assert result.verified is False
    assert len(result.attempts) == 2
    assert result.final_report is not None
    assert result.final_report.note == "second failure"


def test_style_and_verify_with_retry_normalizes_styled_image_size_to_source() -> None:
    source_image = _png_bytes(size=(8, 8), color=(255, 255, 255, 255))
    semantic_foreground = _foreground_png_bytes((8, 8))
    larger_styled_image = _png_bytes(size=(12, 10), color=(210, 220, 255, 255))
    styler = _SequenceStyler([larger_styled_image])
    verifier = PixelPreservingStyledWorksheetVerifier(sample_step=1)

    result = style_and_verify_with_retry(
        styler=styler,
        source_image_bytes=source_image,
        semantic_foreground_bytes=semantic_foreground,
        prompt="Normalize size prompt.",
        verifier=verifier,
    )

    assert result.verified is True
    assert result.final_styled_artifact is not None
    normalized = Image.open(BytesIO(result.final_styled_artifact.image_bytes))
    composited = Image.open(BytesIO(result.final_composited_image_bytes))
    assert normalized.size == (8, 8)
    assert composited.size == (8, 8)
    assert result.final_styled_artifact.mime_type == "image/png"


def test_write_verification_artifacts_writes_json_and_overlay(tmp_path: Path) -> None:
    composited = _png_bytes(size=(12, 12), color=(255, 255, 255, 255))
    report = StyledWorksheetVerificationReport(
        passed=False,
        sampled_points=20,
        mismatch_count=1,
        mismatch_ratio=0.05,
        note="example failure",
        mismatches=(),
    )

    report_path, overlay_path = write_verification_artifacts(
        composited_bytes=composited,
        report=report,
        output_dir=tmp_path / "verification",
    )

    assert report_path.exists()
    assert overlay_path.exists()
    assert "example failure" in report_path.read_text(encoding="utf-8")
