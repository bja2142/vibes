from __future__ import annotations

from dataclasses import replace
from io import BytesIO
from pathlib import Path
import json
import logging
from typing import Any

from PIL import Image

from ..color_grid_generation import (
    PRESET_PICTURE_OPTIONS,
    ColorGridGenerationError,
    GeminiColorGridGenerator,
    PresetColorGridGenerator,
    difficulty_to_color_count,
    difficulty_to_grid_size,
)
from ..exporter import WorksheetExportService
from ..image_compositing import composite_foreground_over_background, tint_foreground
from ..image_styling import NoOpWorksheetStylingPromptRefiner, WorksheetStylingPromptRefiner
from ..image_styling_service import WorksheetImageStyler
from ..image_styling_verification import (
    PixelPreservingStyledWorksheetVerifier,
    StyledWorksheetVerifier,
    style_and_verify_with_retry,
    write_verification_artifacts,
)
from ..logging_utils import log_event
from ..manifest import read_worksheet_manifest, write_worksheet_manifest, worksheet_to_dict
from ..models import (
    ApprovalState,
    DifficultyRange,
    LayoutSettings,
    LearnerBand,
    LetterAssignment,
    RenderedWorksheet,
    RevealMode,
    RewardContent,
    RewardContentCandidate,
    SkillDifficultySetting,
    Worksheet,
    WorksheetSpec,
)
from ..problem_generators import ProblemGenerationService
from ..validator import WorksheetValidator
from ..solution_phrase import solution_slot_count
from ..worksheet_assembly import WorksheetAssemblyService


COLOR_SEQUENCE = [
    ("Soft Gray", "#edf1f4"),
    ("Leaf Green", "#6ba36f"),
    ("Sky Blue", "#67a6d8"),
    ("Coral", "#dd7c6b"),
    ("Plum", "#8a6bb8"),
    ("Stone Gray", "#9aa7b3"),
    ("Sun Yellow", "#f4c542"),
    ("Ocean Teal", "#3fa7a3"),
    ("Mango", "#f2a541"),
    ("Berry", "#c05c7a"),
    ("Fern", "#5b8e55"),
    ("Indigo", "#5f6fd9"),
    ("Clay", "#c9845b"),
    ("Rose Pink", "#d97a9f"),
    ("Slate Blue", "#6f7fc7"),
    ("Cedar Brown", "#9a6b4f"),
    ("Mint", "#71c6a8"),
    ("Amber", "#e6a63d"),
    ("Dusty Blue", "#7d9fbe"),
    ("Crimson", "#c65454"),
    ("Olive", "#879c47"),
    ("Lagoon", "#4e9fb6"),
    ("Lavender", "#a78bd0"),
    ("Copper", "#b8774d"),
    ("Pine", "#48725f"),
    ("Peach", "#efab85"),
    ("Midnight Blue", "#405b93"),
    ("Lime", "#9dc84a"),
    ("Brick", "#b35b4c"),
    ("Aqua", "#52c2c8"),
    ("Orchid", "#b66fcf"),
    ("Charcoal", "#6d7784"),
]

MOON_COLOR_SEQUENCE = [
    ("Midnight Black", "#111418"),
    ("Moon White", "#f6f7f8"),
    ("Silver Gray", "#cfd5db"),
    ("Lunar Gray", "#9da7b0"),
    ("Slate Gray", "#727c86"),
    ("Pale Gray", "#e5e8eb"),
    ("Ash Gray", "#b8c0c8"),
    ("Cool Gray", "#87929d"),
    ("Cloud White", "#fbfbfc"),
    ("Steel Gray", "#5f6974"),
    ("Frost Gray", "#d9dde2"),
    ("Graphite", "#444c55"),
]

STAR_COLOR_SEQUENCE = [
    ("Midnight Black", "#0f1217"),
    ("Star Gold", "#f4c542"),
    ("Warm Gold", "#e7b63d"),
    ("Pale Gold", "#ffe08a"),
    ("Starlight", "#fff6d8"),
    ("Moon White", "#fffdf4"),
    ("Amber Gold", "#d79c24"),
    ("Soft Gold", "#f2cd6a"),
    ("Warm White", "#fff8e8"),
    ("Honey Gold", "#c88c1f"),
    ("Silver White", "#f3f4f7"),
    ("Champagne", "#f6deb1"),
]

SMILE_COLOR_SEQUENCE = [
    ("Soft Cream", "#fff9df"),
    ("Charcoal", "#1f252d"),
    ("Bright Yellow", "#ffd84a"),
    ("Golden Yellow", "#f4c542"),
    ("Warm Gold", "#f0b92d"),
    ("Sun Yellow", "#ffe27a"),
    ("Honey", "#efc147"),
    ("Butter", "#ffe98a"),
    ("Amber", "#e6a63d"),
    ("Smile Red", "#d84a4a"),
    ("Cloud White", "#ffffff"),
    ("Deep Brown", "#6e4c2f"),
]

SUN_COLOR_SEQUENCE = [
    ("Sky Blue", "#9fd8ff"),
    ("Sun Yellow", "#f4c542"),
    ("Golden Yellow", "#f2b705"),
    ("Amber", "#db9300"),
    ("Light Gold", "#ffe083"),
    ("Warm Yellow", "#f7c84b"),
    ("Deep Gold", "#c98500"),
    ("Pale Blue", "#dff2ff"),
    ("Orange Gold", "#f0a000"),
    ("Bright Yellow", "#ffd84a"),
    ("Soft Blue", "#b9e4ff"),
    ("Honey Gold", "#dca62b"),
]

FLOWER_COLOR_SEQUENCE = [
    ("Sky Blue", "#9fd8ff"),
    ("Petal White", "#ffffff"),
    ("Center Yellow", "#f4c542"),
    ("Stem Green", "#5a9448"),
    ("Cream White", "#f6f3ea"),
    ("Warm White", "#f8f7f2"),
    ("Cloud White", "#fbfcfd"),
    ("Soft Ivory", "#f4f2ea"),
    ("Pale White", "#fdfcf8"),
    ("Mist White", "#f1f4f6"),
    ("Fern Green", "#4d8748"),
    ("Snow White", "#fcfcfb"),
    ("Leaf Green", "#6ca85a"),
]

APPLE_COLOR_SEQUENCE = [
    ("Soft Gray", "#f5f6f7"),
    ("Stem Brown", "#7b5331"),
    ("Leaf Green", "#5d9a57"),
    ("Apple Red", "#d84a4a"),
    ("Cherry Red", "#b82f3a"),
    ("Rose Red", "#e06666"),
    ("Crimson", "#9f2330"),
    ("Blush Red", "#ef8d8d"),
    ("Brick Red", "#a83a3a"),
    ("Warm Brown", "#8e633d"),
    ("Pale Red", "#f4a7a7"),
    ("Fern Green", "#4f814a"),
]

CAT_COLOR_SEQUENCE = [
    ("Soft White", "#f8f7f2"),
    ("Dark Brown", "#513a2d"),
    ("Orange", "#e08a2e"),
    ("Deep Orange", "#c86f1f"),
    ("Golden Orange", "#f0a13a"),
    ("Cream", "#f6d8a8"),
    ("Nose Pink", "#d87f88"),
    ("Charcoal", "#20262d"),
    ("Burnt Orange", "#a7531e"),
    ("Tan", "#d99d5e"),
    ("Peach", "#efbf8d"),
    ("Rust", "#b95f22"),
]

CLOWN_FISH_COLOR_SEQUENCE = [
    ("Ocean Mist", "#edf7fb"),
    ("Charcoal", "#26313a"),
    ("Clown Orange", "#f28c28"),
    ("Stripe White", "#ffffff"),
    ("Coral Orange", "#ef6a3b"),
    ("Bright Orange", "#ffab40"),
    ("Deep Orange", "#cc5f17"),
    ("Shell White", "#f6f7f8"),
]

BLUE_TANG_COLOR_SEQUENCE = [
    ("Soft White", "#f8fafb"),
    ("Outline Navy", "#183248"),
    ("Tang Blue", "#2e75b6"),
    ("Tail Yellow", "#f4c542"),
    ("Deep Blue", "#1f5c8f"),
    ("Aqua Blue", "#53a7df"),
    ("Bright Yellow", "#ffd84a"),
    ("Navy", "#21496b"),
]

TREE_COLOR_SEQUENCE = [
    ("Soft White", "#f8faf7"),
    ("Trunk Brown", "#7b5331"),
    ("Leaf Green", "#5c9a56"),
    ("Forest Green", "#3f7f40"),
    ("Moss Green", "#76aa63"),
    ("Olive Green", "#688b45"),
    ("Pale Green", "#99c27d"),
    ("Cedar Green", "#4d8b4a"),
    ("Fern Green", "#5a9448"),
    ("Sage Green", "#89b470"),
    ("Deep Green", "#2f6d35"),
    ("Canopy Green", "#6ea95d"),
]

CHRISTMAS_TREE_COLOR_SEQUENCE = [
    ("Snow White", "#f7f9fb"),
    ("Trunk Brown", "#7a5536"),
    ("Evergreen", "#2f6d3a"),
    ("Pine Green", "#24582e"),
    ("Forest Green", "#3b7d45"),
    ("Mistletoe", "#4f9350"),
    ("Spruce", "#35683d"),
    ("Cedar Green", "#2d5f33"),
    ("Frost White", "#ffffff"),
    ("Moss Green", "#567d45"),
    ("Needle Green", "#1f4d29"),
    ("Deep Green", "#234c2d"),
]

BUTTERFLY_COLOR_SEQUENCE = [
    ("Soft Sky", "#eef5ff"),
    ("Charcoal", "#2b2d33"),
    ("Indigo", "#6f7fc7"),
    ("Butter Yellow", "#f4c542"),
    ("Coral", "#dd7c6b"),
    ("Aqua", "#52c2c8"),
    ("Violet", "#9d7ad8"),
    ("Sky Blue", "#6fa7df"),
    ("Rose", "#d97a9f"),
    ("Gold", "#e6b84a"),
]

ROCKET_COLOR_SEQUENCE = [
    ("Sky Blue", "#9fd8ff"),
    ("Jet Black", "#1f252d"),
    ("Silver", "#cdd4dc"),
    ("Steel", "#9ca7b3"),
    ("Gunmetal", "#505a66"),
    ("Chrome", "#e3e8ee"),
    ("Slate", "#6e7884"),
    ("Graphite", "#39424d"),
    ("Cloud White", "#ffffff"),
    ("Flame Gold", "#ffd166"),
    ("Flame Orange", "#f28c28"),
    ("Hot Red", "#df5b33"),
    ("Ash Gray", "#b3bcc7"),
]


def extend_palette_entries(
    base_entries: list[tuple[str, str]],
    *,
    color_count: int,
) -> list[tuple[str, str]]:
    if len(base_entries) >= color_count:
        return base_entries[:color_count]
    used_labels = {label for label, _ in base_entries}
    extended = list(base_entries)
    for label, color in COLOR_SEQUENCE:
        if label in used_labels:
            continue
        extended.append((label, color))
        used_labels.add(label)
        if len(extended) >= color_count:
            break
    return extended[:color_count]

ROCKET_COLOR_INDEX_BY_COUNT = {
    4: (0, 1, 2, 9),
    6: (0, 1, 2, 4, 9, 10),
    8: (0, 1, 2, 3, 4, 8, 9, 10),
    10: (0, 1, 2, 3, 4, 5, 6, 8, 9, 10),
    12: (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11),
}


class WorksheetRunGenerationError(ValueError):
    pass


class WorksheetRunCancelledError(WorksheetRunGenerationError):
    pass


class WorksheetRunGenerationService:
    def __init__(
        self,
        artifact_root: Path,
        assembly_service: WorksheetAssemblyService | None = None,
        export_service: WorksheetExportService | None = None,
        gemini_color_grid_generator: GeminiColorGridGenerator | None = None,
        worksheet_image_styler: WorksheetImageStyler | None = None,
        styling_prompt_refiner: WorksheetStylingPromptRefiner | None = None,
        styled_worksheet_verifier: StyledWorksheetVerifier | None = None,
        problem_generation_service: ProblemGenerationService | None = None,
        worksheet_validator: WorksheetValidator | None = None,
    ) -> None:
        self._artifact_root = artifact_root
        self._assembly_service = assembly_service or WorksheetAssemblyService()
        self._export_service = export_service or WorksheetExportService()
        self._preset_color_grid_generator = PresetColorGridGenerator()
        self._gemini_color_grid_generator = gemini_color_grid_generator
        self._worksheet_image_styler = worksheet_image_styler
        self._styling_prompt_refiner = styling_prompt_refiner or NoOpWorksheetStylingPromptRefiner()
        self._styled_worksheet_verifier = styled_worksheet_verifier or PixelPreservingStyledWorksheetVerifier()
        self._problem_generation_service = problem_generation_service or ProblemGenerationService()
        self._worksheet_validator = worksheet_validator or WorksheetValidator()
        self._logger = logging.getLogger("worksheet_generator.generation")

    def generate(
        self,
        *,
        worksheet_run_id: int,
        approved_draft: dict[str, object],
        parameters: dict[str, object],
        progress_callback: Any | None = None,
        should_cancel: Any | None = None,
    ) -> dict[str, object]:
        reward_content = self._reward_content_from_draft(approved_draft)
        reveal_mode = RevealMode(str(parameters["reveal_mode"]))
        learner_band = LearnerBand(str(parameters["learner_band"]))
        layout = self._build_layout(reveal_mode)
        slot_count = self._slot_count(reward_content.solution_phrase)
        distractor_count = max(0, int(parameters.get("decoy_count") or 0))
        spec = WorksheetSpec(
            worksheet_id=f"worksheet-run-{worksheet_run_id}",
            learner_band=learner_band,
            skill_profile=str(parameters["skill_profile"]),
            selected_skills=tuple(
                SkillDifficultySetting(
                    skill=str(entry["skill"]),
                    difficulty_range=DifficultyRange(
                        minimum=int(entry["difficulty_minimum"]),
                        maximum=int(entry["difficulty_maximum"]),
                    ),
                )
                for entry in parameters.get("selected_skills", [])
                if isinstance(entry, dict)
            ),
            difficulty_range=DifficultyRange(
                minimum=int(parameters["difficulty_minimum"]),
                maximum=int(parameters["difficulty_maximum"]),
            ),
            problem_count=slot_count,
            reveal_mode=reveal_mode,
            theme=str(parameters.get("theme") or approved_draft.get("theme") or "").strip() or None,
            seed=int(parameters.get("seed") or 1000 + worksheet_run_id),
            layout=layout,
        )
        log_event(
            self._logger,
            "worksheet_generation_service_started",
            verbosity="verbose",
            worksheet_run_id=worksheet_run_id,
            spec={
                "worksheet_id": spec.worksheet_id,
                "learner_band": spec.learner_band.value,
                "skill_profile": spec.skill_profile,
                "selected_skills": [
                    {
                        "skill": setting.skill,
                        "difficulty_minimum": setting.difficulty_range.minimum,
                        "difficulty_maximum": setting.difficulty_range.maximum,
                    }
                    for setting in spec.selected_skills
                ],
                "difficulty_minimum": spec.difficulty_range.minimum,
                "difficulty_maximum": spec.difficulty_range.maximum,
                "problem_count": spec.problem_count,
                "reveal_mode": spec.reveal_mode.value,
                "theme": spec.theme,
                "seed": spec.seed,
            },
        )
        self._raise_if_cancelled(should_cancel, "worksheet generation was cancelled before assembly began")

        try:
            if progress_callback:
                progress_callback("assemble worksheet content")
            if reveal_mode == RevealMode.COLOR_BY_NUMBER:
                result = self._build_color_by_number_result(
                    spec=spec,
                    reward_content=reward_content,
                    source=str(parameters.get("color_picture_source") or "preset"),
                    preset_name=str(parameters.get("color_picture_preset") or "smile"),
                )
            else:
                result = self._build_worksheet_result(
                    spec=spec,
                    reward_content=reward_content,
                    reveal_mode=reveal_mode,
                    distractor_count=distractor_count,
                )
        except (ValueError, ColorGridGenerationError) as exc:
            raise WorksheetRunGenerationError(str(exc)) from exc
        worksheet = result.worksheet
        log_event(
            self._logger,
            "worksheet_assembled",
            verbosity="normal",
            worksheet_run_id=worksheet_run_id,
            attempts_used=result.attempts_used,
            warnings=list(result.validation_report.warnings),
            errors=list(result.validation_report.errors),
        )
        self._raise_if_cancelled(should_cancel, "worksheet generation was cancelled after assembly")
        run_dir = self._artifact_root / f"run-{worksheet_run_id:05d}"
        if progress_callback:
            progress_callback("export preview and solution")
        preview, solution = self._export_service.export_preview_and_solution(
            worksheet,
            run_dir,
            preview_stem="worksheet-preview",
            solution_stem="worksheet-solution",
        )
        self._raise_if_cancelled(should_cancel, "worksheet generation was cancelled after export")
        log_event(
            self._logger,
            "worksheet_exported",
            verbosity="normal",
            worksheet_run_id=worksheet_run_id,
            run_dir=str(run_dir),
            preview_outputs=[output.output_path for output in preview.outputs],
            solution_outputs=[output.output_path for output in solution.outputs],
        )
        worksheet = replace(
            worksheet,
            rendered_outputs=[
                RenderedWorksheet(
                    worksheet_id=worksheet.worksheet_id,
                    output_format=output.output_format,
                    output_path=str(output.output_path),
                )
                for variant in (preview, solution)
                for output in variant.outputs
            ],
        )
        manifest_path = run_dir / "worksheet-manifest.json"
        if progress_callback:
            progress_callback("write manifest and metadata")
        write_worksheet_manifest(manifest_path, worksheet)
        metadata_path = run_dir / "worksheet-run.json"
        metadata_path.write_text(
            json.dumps(
                {
                    "worksheet": worksheet_to_dict(worksheet),
                    "validation": {
                        "is_valid": result.validation_report.is_valid,
                        "reconstructed_answer": result.validation_report.reconstructed_answer,
                        "distinct_letter_answer_map": result.validation_report.distinct_letter_answer_map,
                        "warnings": list(result.validation_report.warnings),
                        "errors": list(result.validation_report.errors),
                    },
                    "attempts_used": result.attempts_used,
                    "parameters": parameters,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self._raise_if_cancelled(should_cancel, "worksheet generation was cancelled after metadata write")

        if progress_callback:
            progress_callback("persist artifact records")
        artifact_records = [
            {"artifact_kind": "worksheet_manifest", "output_format": "json", "path": manifest_path, "display_name": "Worksheet Manifest"},
            {"artifact_kind": "worksheet_metadata", "output_format": "json", "path": metadata_path, "display_name": "Worksheet Run Metadata"},
        ]
        for variant in (preview, solution):
            for output in variant.outputs:
                artifact_records.append(
                    {
                        "artifact_kind": f"worksheet_{variant.variant_name}",
                        "output_format": output.output_format,
                        "path": Path(output.output_path),
                        "display_name": f"{variant.variant_name.title()} {output.output_format.upper()}",
                    }
                )

        self._raise_if_cancelled(should_cancel, "worksheet generation was cancelled before artifact persistence completed")
        return {
            "worksheet": worksheet,
            "artifacts": artifact_records,
            "thumbnail_path": run_dir / "worksheet-preview.png",
            "validation": result.validation_report,
            "attempts_used": result.attempts_used,
            "artifact_group": run_dir.name,
        }

    def apply_image_styling(
        self,
        *,
        worksheet_run_id: int,
        prompt_text: str,
        progress_callback: Any | None = None,
        should_cancel: Any | None = None,
    ) -> dict[str, object]:
        if self._worksheet_image_styler is None:
            raise WorksheetRunGenerationError("worksheet image styling is unavailable because the Gemini image styler is not configured")

        run_dir = self._artifact_root / f"run-{worksheet_run_id:05d}"
        manifest_path = run_dir / "worksheet-manifest.json"
        base_preview_path = run_dir / "worksheet-preview.png"
        if not manifest_path.exists() or not base_preview_path.exists():
            raise WorksheetRunGenerationError("cannot apply styling because the base worksheet artifacts are missing")

        worksheet = read_worksheet_manifest(manifest_path)
        styling_dir = run_dir / "styled"
        attempt_number, attempt_dir, display_suffix = self._styling_attempt_output_context(styling_dir)
        self._raise_if_cancelled(should_cancel, "worksheet styling was cancelled before foreground rendering")
        if progress_callback:
            progress_callback("render semantic foreground")
        foreground_variant = self._export_service.export_semantic_foreground_variant(
            worksheet,
            attempt_dir,
            stem="worksheet-preview-semantic-foreground",
            solution=False,
        )
        semantic_foreground_png_path = next(
            Path(output.output_path)
            for output in foreground_variant.outputs
            if output.output_format == "png"
        )

        base_prompt = prompt_text.strip()
        if not base_prompt:
            raise WorksheetRunGenerationError("cannot apply styling because the styling prompt is empty")
        self._raise_if_cancelled(should_cancel, "worksheet styling was cancelled before prompt refinement")
        if progress_callback:
            progress_callback("refine styling prompt")
        refined_prompt = self._styling_prompt_refiner.refine(base_prompt)
        log_event(
            self._logger,
            "worksheet_styling_prompt_ready",
            verbosity="normal",
            worksheet_run_id=worksheet_run_id,
            prompt=refined_prompt,
        )

        if progress_callback:
            progress_callback("apply Gemini styling")
        self._raise_if_cancelled(should_cancel, "worksheet styling was cancelled before Gemini styling")
        retry_result = style_and_verify_with_retry(
            styler=self._worksheet_image_styler,
            source_image_bytes=base_preview_path.read_bytes(),
            semantic_foreground_bytes=semantic_foreground_png_path.read_bytes(),
            prompt=refined_prompt,
            verifier=self._styled_worksheet_verifier,
        )
        self._raise_if_cancelled(should_cancel, "worksheet styling was cancelled after Gemini styling")
        if retry_result.final_styled_artifact is None or retry_result.final_composited_image_bytes is None or retry_result.final_report is None:
            raise WorksheetRunGenerationError("worksheet styling completed without a final artifact")

        if progress_callback:
            progress_callback("verify styled worksheet")
        if progress_callback:
            progress_callback("write styled artifacts")
        styled_png_path = attempt_dir / "worksheet-preview-styled.png"
        styled_png_path.write_bytes(retry_result.final_composited_image_bytes)
        styled_pdf_path = attempt_dir / "worksheet-preview-styled.pdf"
        self._write_png_pdf(retry_result.final_composited_image_bytes, styled_pdf_path)
        styled_raw_path = attempt_dir / "worksheet-preview-styled-background.png"
        styled_raw_path.write_bytes(retry_result.final_styled_artifact.image_bytes)
        overlay_check_artifacts = []
        semantic_foreground_bytes = semantic_foreground_png_path.read_bytes()
        for label, color_hex in (("Red", "#ff3b30"), ("Blue", "#007aff"), ("White", "#ffffff")):
            tinted_foreground_bytes = tint_foreground(
                foreground_bytes=semantic_foreground_bytes,
                color_hex=color_hex,
            )
            overlay_check_bytes = composite_foreground_over_background(
                background_bytes=retry_result.final_styled_artifact.image_bytes,
                foreground_bytes=tinted_foreground_bytes,
            )
            overlay_check_path = attempt_dir / f"worksheet-preview-overlay-check-{label.lower()}.png"
            overlay_check_path.write_bytes(overlay_check_bytes)
            overlay_check_artifacts.append(
                {
                    "artifact_kind": "worksheet_styled_overlay_check",
                    "output_format": "png",
                    "path": overlay_check_path,
                    "display_name": f"Overlay Check {label} PNG{display_suffix}",
                }
            )
        debug_path = attempt_dir / "worksheet-styling-debug.json"
        debug_path.write_text(
            json.dumps(
                {
                    "verified": retry_result.verified,
                    "attempts": [
                        {
                            "attempt_number": attempt.attempt_number,
                            "prompt": attempt.prompt,
                            "raw_response_json": attempt.styled_artifact.raw_response_json,
                            "verification": {
                                "passed": attempt.verification_report.passed,
                                "sampled_points": attempt.verification_report.sampled_points,
                                "mismatch_count": attempt.verification_report.mismatch_count,
                                "mismatch_ratio": attempt.verification_report.mismatch_ratio,
                                "note": attempt.verification_report.note,
                            },
                        }
                        for attempt in retry_result.attempts
                    ],
                    "final_prompt": retry_result.final_prompt,
                    "model": retry_result.final_styled_artifact.model,
                    "response_id": retry_result.final_styled_artifact.response_id,
                    "raw_response_json": retry_result.final_styled_artifact.raw_response_json,
                    "attempt_number": attempt_number,
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        verification_report_path, verification_overlay_path = write_verification_artifacts(
            composited_bytes=retry_result.final_composited_image_bytes,
            report=retry_result.final_report,
            output_dir=attempt_dir,
        )

        artifact_records = [
            {"artifact_kind": "worksheet_styled_preview", "output_format": "png", "path": styled_png_path, "display_name": f"Styled Preview PNG{display_suffix}"},
            {"artifact_kind": "worksheet_styled_preview", "output_format": "pdf", "path": styled_pdf_path, "display_name": f"Styled Preview PDF{display_suffix}"},
            {"artifact_kind": "worksheet_styled_background", "output_format": "png", "path": styled_raw_path, "display_name": f"Styled Background PNG{display_suffix}"},
            {"artifact_kind": "worksheet_styling_debug", "output_format": "json", "path": debug_path, "display_name": f"Worksheet Styling Debug{display_suffix}"},
            {"artifact_kind": "worksheet_styling_verification", "output_format": "json", "path": verification_report_path, "display_name": f"Worksheet Styling Verification{display_suffix}"},
            {"artifact_kind": "worksheet_styling_verification", "output_format": "png", "path": verification_overlay_path, "display_name": f"Worksheet Styling Verification Overlay{display_suffix}"},
        ]
        artifact_records.extend(overlay_check_artifacts)
        for output in foreground_variant.outputs:
            artifact_records.append(
                {
                    "artifact_kind": "worksheet_semantic_foreground",
                    "output_format": output.output_format,
                    "path": Path(output.output_path),
                    "display_name": f"Semantic Foreground {output.output_format.upper()}{display_suffix}",
                }
            )

        self._raise_if_cancelled(should_cancel, "worksheet styling was cancelled after styled artifacts were written")
        return {
            "verified": retry_result.verified,
            "attempts": retry_result.attempts,
            "artifact_group": str(Path(run_dir.name) / "styled" / attempt_dir.name),
            "thumbnail_path": styled_raw_path,
            "artifacts": artifact_records,
            "final_prompt": retry_result.final_prompt,
            "verification_report": retry_result.final_report,
            "style_check_artifact_path": verification_overlay_path,
        }

    def _raise_if_cancelled(self, should_cancel: Any | None, message: str) -> None:
        if should_cancel and should_cancel():
            raise WorksheetRunCancelledError(message)

    def _styling_attempt_output_context(self, styling_dir: Path) -> tuple[int, Path, str]:
        styling_dir.mkdir(parents=True, exist_ok=True)
        retry_numbers = sorted(
            int(path.name.split("-")[1])
            for path in styling_dir.iterdir()
            if path.is_dir() and path.name.startswith("retry-") and path.name.split("-")[1].isdigit()
        )
        has_legacy_root_artifacts = any(path.is_file() for path in styling_dir.iterdir())
        if retry_numbers:
            attempt_number = retry_numbers[-1] + 1
            attempt_dir = styling_dir / f"retry-{attempt_number:02d}"
            display_suffix = f" (Retry {attempt_number})"
            return attempt_number, attempt_dir, display_suffix
        if has_legacy_root_artifacts:
            attempt_number = 1
            attempt_dir = styling_dir / "retry-01"
            display_suffix = " (Retry 1)"
            return attempt_number, attempt_dir, display_suffix
        return 0, styling_dir, ""

    def _reward_content_from_draft(self, draft: dict[str, object]) -> RewardContent:
        if draft["approval_state"] != ApprovalState.APPROVED.value:
            raise WorksheetRunGenerationError("worksheet generation requires an approved reward content draft")
        return RewardContent(
            prompt_text=str(draft["prompt_text"]),
            solution_phrase=str(draft["solution_phrase"]),
            theme=str(draft["theme"]) if draft.get("theme") is not None else None,
            source=str(draft["source"]),
            approval_state=ApprovalState.APPROVED,
            style=str(draft["style"]) if draft.get("style") is not None else None,
            language=str(draft["language"]),
            reading_level_assessment=self._assessment_from_payload(draft.get("reading_level_assessment")),
            review_notes=list(draft.get("review_notes", [])),
        )

    def _write_png_pdf(self, png_bytes: bytes, output_path: Path) -> Path:
        image = Image.open(BytesIO(png_bytes)).convert("RGB")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path, format="PDF")
        return output_path

    def _assessment_from_payload(self, payload: object) -> object:
        if not payload:
            return None
        from ..models import ReadingLevelAssessment

        return ReadingLevelAssessment(
            learner_band=LearnerBand(str(payload["learner_band"])),
            passed=bool(payload["passed"]),
            word_count=int(payload["word_count"]),
            sentence_count=int(payload["sentence_count"]),
            long_word_count=int(payload["long_word_count"]),
            flagged_terms=list(payload.get("flagged_terms", [])),
            notes=list(payload.get("notes", [])),
        )

    def _build_layout(self, reveal_mode: RevealMode) -> LayoutSettings:
        if reveal_mode == RevealMode.COLOR_BY_NUMBER:
            return LayoutSettings(
                max_color_options=32,
                color_palette={},
            )
        return LayoutSettings(max_color_options=32)

    def _build_worksheet_result(
        self,
        *,
        spec: WorksheetSpec,
        reward_content: RewardContent,
        reveal_mode: RevealMode,
        distractor_count: int,
    ) -> Any:
        for offset in range(8):
            attempt_spec = replace(spec, seed=(spec.seed or 1000) + offset * 17)
            result = self._assembly_service.build_worksheet(
                attempt_spec,
                reward_content,
                max_attempts=12,
                distractor_count=distractor_count,
                problem_id_prefix="R",
                problem_id_width=2,
            )
            if reveal_mode != RevealMode.COLOR_BY_NUMBER or self._color_mode_has_unique_answers(result.worksheet):
                return result
        raise WorksheetRunGenerationError("unable to build a color-by-number worksheet with unique answer values")

    def _color_mode_has_unique_answers(self, worksheet: Worksheet) -> bool:
        answer_values = [assignment.answer_value for assignment in worksheet.slot_assignments()]
        return len(answer_values) == len(set(answer_values))

    def _build_color_by_number_result(
        self,
        *,
        spec: WorksheetSpec,
        reward_content: RewardContent,
        source: str,
        preset_name: str,
    ) -> Any:
        color_count = difficulty_to_color_count(spec.difficulty_range.maximum)
        palette_entries = self._palette_entries_for_picture(source=source, preset_name=preset_name, color_count=color_count)
        if len(palette_entries) < color_count:
            raise WorksheetRunGenerationError("color-by-number worksheet exceeded configured color label pool")
        color_labels = [label for label, _ in palette_entries]
        definition = self._generate_color_picture_definition(
            source=source,
            preset_name=preset_name,
            theme=str(spec.theme or reward_content.theme or "").strip(),
            solution_phrase=reward_content.solution_phrase,
            prompt_text=reward_content.prompt_text,
            difficulty_maximum=spec.difficulty_range.maximum,
            color_labels=color_labels,
        )
        palette_labels = self._palette_labels_for_definition(color_labels, definition.cells)
        palette_lookup = {label: color for label, color in palette_entries}
        palette = {label: palette_lookup[label] for label in palette_labels if label in palette_lookup}
        color_spec = replace(
            spec,
            problem_count=len(palette_labels),
            layout=replace(
                spec.layout,
                color_palette=palette,
                color_grid_size=definition.grid_size,
                color_grid_cells=[list(row) for row in definition.cells],
                color_grid_source=definition.source,
                color_grid_name=definition.name,
            ),
        )
        for offset in range(12):
            attempt_spec = replace(color_spec, seed=(color_spec.seed or 1000) + offset * 17)
            generated = self._problem_generation_service.generate_problem_set(
                attempt_spec,
                problem_id_prefix="R",
                problem_id_width=2,
            )
            answer_values = [solved.normalized_answer for solved in generated.solved_problems]
            if len(answer_values) != len(set(answer_values)):
                continue
            assignments = [
                LetterAssignment(
                    problem_id=problem.problem_id,
                    answer_value=solved.normalized_answer,
                    reveal_token=palette_labels[index],
                    answer_slot_index=None,
                    is_distractor=False,
                )
                for index, (problem, solved) in enumerate(zip(generated.problems, generated.solved_problems))
            ]
            worksheet = Worksheet(
                spec=attempt_spec,
                reward_content=reward_content,
                problems=list(generated.problems),
                solved_problems=list(generated.solved_problems),
                letter_assignments=assignments,
            )
            report = self._worksheet_validator.validate(worksheet)
            if report.is_valid:
                return type("ColorWorksheetResult", (), {
                    "worksheet": worksheet,
                    "validation_report": report,
                    "attempts_used": offset + 1,
                })()
        raise WorksheetRunGenerationError("unable to build a color-by-number worksheet with unique answer values")

    def _generate_color_picture_definition(
        self,
        *,
        source: str,
        preset_name: str,
        theme: str,
        solution_phrase: str,
        prompt_text: str,
        difficulty_maximum: int,
        color_labels: list[str],
    ) -> Any:
        grid_size = difficulty_to_grid_size(difficulty_maximum)
        if source == "preset":
            grid_size = max(grid_size, self._minimum_grid_size_for_preset(preset_name))
        if source == "gemini":
            if self._gemini_color_grid_generator is None:
                raise WorksheetRunGenerationError("Gemini color-grid generation is unavailable because GEMINI_API_KEY is not set")
            try:
                return self._gemini_color_grid_generator.generate(
                    theme=theme,
                    solution_phrase=solution_phrase,
                    prompt_text=prompt_text,
                    grid_size=grid_size,
                    palette_labels=color_labels,
                )
            except Exception as exc:
                fallback_preset = self._preset_name_for_solution_phrase(solution_phrase)
                if not fallback_preset:
                    raise
                fallback_grid_size = max(grid_size, self._minimum_grid_size_for_preset(fallback_preset))
                log_event(
                    self._logger,
                    "gemini_color_grid_fallback_to_preset",
                    verbosity="normal",
                    solution_phrase=solution_phrase,
                    preset_name=fallback_preset,
                    error=str(exc),
                )
                return self._preset_color_grid_generator.generate(
                    preset_name=fallback_preset,
                    grid_size=fallback_grid_size,
                    palette_labels=color_labels,
                )
        return self._preset_color_grid_generator.generate(
            preset_name=preset_name,
            grid_size=grid_size,
            palette_labels=color_labels,
        )

    def _minimum_grid_size_for_preset(self, preset_name: str) -> int:
        return {
            "christmas_tree": 34,
            "cat": 40,
            "clown_fish": 36,
            "blue_tang": 36,
            "butterfly": 34,
        }.get(preset_name, 16)

    def _palette_labels_for_definition(self, palette_labels: list[str], cells: list[list[str]]) -> list[str]:
        used_labels: list[str] = []
        seen: set[str] = set()
        for row in cells:
            for label in row:
                if label in palette_labels and label not in seen:
                    seen.add(label)
                    used_labels.append(label)
        return used_labels

    def _palette_entries_for_picture(
        self,
        *,
        source: str,
        preset_name: str,
        color_count: int,
    ) -> list[tuple[str, str]]:
        if source == "preset" and preset_name == "moon":
            return extend_palette_entries(MOON_COLOR_SEQUENCE, color_count=color_count)
        if source == "preset" and preset_name == "star":
            return extend_palette_entries(STAR_COLOR_SEQUENCE, color_count=color_count)
        if source == "preset" and preset_name == "smile":
            return extend_palette_entries(SMILE_COLOR_SEQUENCE, color_count=color_count)
        if source == "preset" and preset_name == "sun":
            return extend_palette_entries(SUN_COLOR_SEQUENCE, color_count=color_count)
        if source == "preset" and preset_name == "flower":
            return extend_palette_entries(FLOWER_COLOR_SEQUENCE, color_count=color_count)
        if source == "preset" and preset_name == "apple":
            return extend_palette_entries(APPLE_COLOR_SEQUENCE, color_count=color_count)
        if source == "preset" and preset_name == "cat":
            return extend_palette_entries(CAT_COLOR_SEQUENCE, color_count=color_count)
        if source == "preset" and preset_name == "clown_fish":
            return extend_palette_entries(CLOWN_FISH_COLOR_SEQUENCE, color_count=color_count)
        if source == "preset" and preset_name == "blue_tang":
            return extend_palette_entries(BLUE_TANG_COLOR_SEQUENCE, color_count=color_count)
        if source == "preset" and preset_name == "tree":
            return extend_palette_entries(TREE_COLOR_SEQUENCE, color_count=color_count)
        if source == "preset" and preset_name == "christmas_tree":
            return extend_palette_entries(CHRISTMAS_TREE_COLOR_SEQUENCE, color_count=color_count)
        if source == "preset" and preset_name == "butterfly":
            return extend_palette_entries(BUTTERFLY_COLOR_SEQUENCE, color_count=color_count)
        if source == "preset" and preset_name == "rocket":
            rocket_entries = [ROCKET_COLOR_SEQUENCE[index] for index in ROCKET_COLOR_INDEX_BY_COUNT.get(color_count, tuple(range(min(color_count, len(ROCKET_COLOR_SEQUENCE)))))]
            return extend_palette_entries(rocket_entries, color_count=color_count)
        return extend_palette_entries(COLOR_SEQUENCE, color_count=color_count)

    def _preset_name_for_solution_phrase(self, solution_phrase: str) -> str | None:
        normalized = " ".join(solution_phrase.strip().upper().split())
        mapping = {
            "SMILE": "smile",
            "HEART": "heart",
            "STAR": "star",
            "MOON": "moon",
            "SUN": "sun",
            "FLOWER": "flower",
            "APPLE": "apple",
            "TREE": "tree",
            "EVERGREEN": "christmas_tree",
            "CHRISTMAS TREE": "christmas_tree",
            "CAT": "cat",
            "CLOWN FISH": "clown_fish",
            "BLUE TANG": "blue_tang",
            "BUTTERFLY": "butterfly",
            "ROCKET": "rocket",
        }
        return mapping.get(normalized)

    def _slot_count(self, solution_phrase: str) -> int:
        return solution_slot_count(solution_phrase)
