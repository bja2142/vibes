from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import json
import logging
from typing import Protocol

from PIL import Image, ImageDraw

from .image_compositing import composite_foreground_over_background, normalize_background_to_reference_size
from .image_styling import build_stricter_worksheet_styling_prompt
from .image_styling_service import StyledWorksheetImageArtifact, WorksheetImageStyler, WorksheetImageStylingRequest
from .logging_utils import log_event


@dataclass(frozen=True)
class VerificationMismatch:
    x: int
    y: int
    expected_rgba: tuple[int, int, int, int]
    actual_rgba: tuple[int, int, int, int]


@dataclass(frozen=True)
class StyledWorksheetVerificationReport:
    passed: bool
    sampled_points: int
    mismatch_count: int
    mismatch_ratio: float
    note: str
    mismatches: tuple[VerificationMismatch, ...] = ()


@dataclass(frozen=True)
class StyledWorksheetAttempt:
    attempt_number: int
    prompt: str
    styled_artifact: StyledWorksheetImageArtifact
    composited_image_bytes: bytes
    verification_report: StyledWorksheetVerificationReport


@dataclass(frozen=True)
class StyledWorksheetRetryResult:
    verified: bool
    attempts: tuple[StyledWorksheetAttempt, ...]
    final_styled_artifact: StyledWorksheetImageArtifact | None
    final_composited_image_bytes: bytes | None
    final_prompt: str | None
    final_report: StyledWorksheetVerificationReport | None


class StyledWorksheetVerifier(Protocol):
    def verify(
        self,
        *,
        background_bytes: bytes,
        foreground_bytes: bytes,
        composited_bytes: bytes,
    ) -> StyledWorksheetVerificationReport:
        ...


class PixelPreservingStyledWorksheetVerifier:
    def __init__(
        self,
        *,
        sample_step: int = 6,
        alpha_threshold: int = 32,
        color_tolerance: int = 2,
        max_recorded_mismatches: int = 200,
    ) -> None:
        self._sample_step = sample_step
        self._alpha_threshold = alpha_threshold
        self._color_tolerance = color_tolerance
        self._max_recorded_mismatches = max_recorded_mismatches

    def verify(
        self,
        *,
        background_bytes: bytes,
        foreground_bytes: bytes,
        composited_bytes: bytes,
    ) -> StyledWorksheetVerificationReport:
        background = Image.open(BytesIO(background_bytes)).convert("RGBA")
        foreground = Image.open(BytesIO(foreground_bytes)).convert("RGBA")
        composited = Image.open(BytesIO(composited_bytes)).convert("RGBA")
        if background.size != foreground.size or background.size != composited.size:
            return StyledWorksheetVerificationReport(
                passed=False,
                sampled_points=0,
                mismatch_count=1,
                mismatch_ratio=1.0,
                note=(
                    f"image size mismatch: background={background.size}, foreground={foreground.size}, composited={composited.size}"
                ),
            )

        expected = Image.alpha_composite(background, foreground)
        sampled_points = 0
        total_mismatch_count = 0
        mismatches: list[VerificationMismatch] = []
        for y in range(0, foreground.height, self._sample_step):
            for x in range(0, foreground.width, self._sample_step):
                foreground_pixel = foreground.getpixel((x, y))
                if foreground_pixel[3] < self._alpha_threshold:
                    continue
                sampled_points += 1
                expected_pixel = expected.getpixel((x, y))
                actual_pixel = composited.getpixel((x, y))
                if any(abs(expected_pixel[index] - actual_pixel[index]) > self._color_tolerance for index in range(4)):
                    total_mismatch_count += 1
                    if len(mismatches) < self._max_recorded_mismatches:
                        mismatches.append(
                            VerificationMismatch(
                                x=x,
                                y=y,
                                expected_rgba=expected_pixel,
                                actual_rgba=actual_pixel,
                            )
                        )

        mismatch_count = len(mismatches)
        mismatch_ratio = (mismatch_count / sampled_points) if sampled_points else 1.0
        if sampled_points == 0:
            return StyledWorksheetVerificationReport(
                passed=False,
                sampled_points=0,
                mismatch_count=1,
                mismatch_ratio=1.0,
                note="semantic foreground did not provide any sampled verification points",
            )
        mismatch_ratio = (total_mismatch_count / sampled_points) if sampled_points else 1.0
        if total_mismatch_count > 0:
            return StyledWorksheetVerificationReport(
                passed=False,
                sampled_points=sampled_points,
                mismatch_count=total_mismatch_count,
                mismatch_ratio=mismatch_ratio,
                note=f"semantic foreground mismatch at {total_mismatch_count} sampled points",
                mismatches=tuple(mismatches),
            )
        return StyledWorksheetVerificationReport(
            passed=True,
            sampled_points=sampled_points,
            mismatch_count=0,
            mismatch_ratio=0.0,
            note="semantic foreground pixels preserved in the composited styled worksheet",
            mismatches=(),
        )


def style_and_verify_with_retry(
    *,
    styler: WorksheetImageStyler,
    source_image_bytes: bytes,
    semantic_foreground_bytes: bytes,
    prompt: str,
    verifier: StyledWorksheetVerifier | None = None,
    source_mime_type: str = "image/png",
) -> StyledWorksheetRetryResult:
    verifier = verifier or PixelPreservingStyledWorksheetVerifier()
    logger = logging.getLogger("worksheet_generator.image_styling_retry")
    attempts: list[StyledWorksheetAttempt] = []
    current_prompt = prompt

    for attempt_index in range(2):
        log_event(logger, "worksheet_styling_attempt_started", verbosity="normal", attempt=attempt_index + 1)
        styled_artifact = styler.style_image(
            WorksheetImageStylingRequest(
                prompt=current_prompt,
                source_image_bytes=source_image_bytes,
                source_mime_type=source_mime_type,
            )
        )
        normalized_background_bytes = normalize_background_to_reference_size(
            background_bytes=styled_artifact.image_bytes,
            reference_bytes=source_image_bytes,
        )
        normalized_artifact = StyledWorksheetImageArtifact(
            image_bytes=normalized_background_bytes,
            mime_type="image/png",
            model=styled_artifact.model,
            prompt=styled_artifact.prompt,
            response_id=styled_artifact.response_id,
            response_text=styled_artifact.response_text,
            raw_response_json=styled_artifact.raw_response_json,
        )
        composited_bytes = composite_foreground_over_background(
            background_bytes=normalized_artifact.image_bytes,
            foreground_bytes=semantic_foreground_bytes,
        )
        report = verifier.verify(
            background_bytes=normalized_artifact.image_bytes,
            foreground_bytes=semantic_foreground_bytes,
            composited_bytes=composited_bytes,
        )
        attempt = StyledWorksheetAttempt(
            attempt_number=attempt_index + 1,
            prompt=current_prompt,
            styled_artifact=normalized_artifact,
            composited_image_bytes=composited_bytes,
            verification_report=report,
        )
        attempts.append(attempt)
        log_event(
            logger,
            "worksheet_styling_attempt_completed",
            verbosity="normal",
            attempt=attempt_index + 1,
            verification_passed=report.passed,
            mismatch_count=report.mismatch_count,
            mismatch_ratio=report.mismatch_ratio,
        )
        if report.passed:
            return StyledWorksheetRetryResult(
                verified=True,
                attempts=tuple(attempts),
                final_styled_artifact=normalized_artifact,
                final_composited_image_bytes=composited_bytes,
                final_prompt=current_prompt,
                final_report=report,
            )
        if attempt_index == 0:
            current_prompt = build_stricter_worksheet_styling_prompt(current_prompt, verification_note=report.note)
            log_event(logger, "worksheet_styling_retry_scheduled", verbosity="normal", next_attempt=2, reason=report.note)

    final_attempt = attempts[-1]
    return StyledWorksheetRetryResult(
        verified=False,
        attempts=tuple(attempts),
        final_styled_artifact=final_attempt.styled_artifact,
        final_composited_image_bytes=final_attempt.composited_image_bytes,
        final_prompt=final_attempt.prompt,
        final_report=final_attempt.verification_report,
    )


def write_verification_report_json(report: StyledWorksheetVerificationReport, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "passed": report.passed,
        "sampled_points": report.sampled_points,
        "mismatch_count": report.mismatch_count,
        "mismatch_ratio": report.mismatch_ratio,
        "note": report.note,
        "mismatches": [
            {
                "x": mismatch.x,
                "y": mismatch.y,
                "expected_rgba": list(mismatch.expected_rgba),
                "actual_rgba": list(mismatch.actual_rgba),
            }
            for mismatch in report.mismatches
        ],
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output_path


def write_verification_debug_overlay(
    *,
    composited_bytes: bytes,
    report: StyledWorksheetVerificationReport,
    output_path: Path,
) -> Path:
    image = Image.open(BytesIO(composited_bytes)).convert("RGBA")
    draw = ImageDraw.Draw(image)
    for mismatch in report.mismatches:
        draw.rectangle((mismatch.x - 3, mismatch.y - 3, mismatch.x + 3, mismatch.y + 3), outline="#ff2d2d", width=2)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG")
    return output_path


def write_verification_artifacts(
    *,
    composited_bytes: bytes,
    report: StyledWorksheetVerificationReport,
    output_dir: Path,
    stem: str = "styled-verification",
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = write_verification_report_json(report, output_dir / f"{stem}-report.json")
    overlay_path = write_verification_debug_overlay(
        composited_bytes=composited_bytes,
        report=report,
        output_path=output_dir / f"{stem}-overlay.png",
    )
    return report_path, overlay_path
