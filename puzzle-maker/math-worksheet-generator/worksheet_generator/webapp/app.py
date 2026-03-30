from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import logging
import os
import re
import threading
import time
from typing import Any

from flask import Flask, jsonify, render_template, request, send_from_directory

from ..color_grid_generation import PRESET_PICTURE_OPTIONS, GeminiColorGridGenerator, difficulty_to_color_count
from ..image_styling import (
    COLOR_MODE_OPTIONS as IMAGE_STYLE_COLOR_MODES,
    DEFAULT_IMAGE_COLOR_MODE,
    DEFAULT_IMAGE_REFINEMENT_MODEL,
    DEFAULT_IMAGE_STYLE,
    GeminiWorksheetStylingPromptRefiner,
    STYLE_OPTIONS as IMAGE_STYLE_OPTIONS,
    WorksheetImageStylingPromptRequest,
    build_worksheet_styling_prompt,
)
from ..image_styling_service import GeminiWorksheetImageStylingService
from ..logging_utils import configure_application_logging, log_event
from ..models import ApprovalState, LearnerBand, RevealMode, RewardContentCandidate
from ..problem_generators import ProblemGenerationService
from ..rendering import skill_profile_title
from ..reward_content_generation import GeminiGenerationError, GeminiRewardContentGenerator, RewardContentGenerationRequest
from ..reward_content_review import RewardContentApprovalError, RewardContentStateError, RewardContentValidationError
from ..reward_content_service import RewardContentService
from ..solution_phrase import solution_slot_count
from .config import (
    get_gemini_image_model,
    get_gemini_model,
    get_log_verbosity,
    get_styling_confirmation_timeout_seconds,
    get_worksheet_generation_timeout_seconds,
    get_worksheet_styling_timeout_seconds,
    is_debug_ui_enabled,
    is_gemini_enabled,
    is_job_worker_enabled,
    load_app_paths,
)
from .db import ensure_storage_paths, initialize_database
from .generation_service import WorksheetRunCancelledError, WorksheetRunGenerationError, WorksheetRunGenerationService
from .maintenance import maintenance_snapshot, prune_orphan_artifacts
from .repository import AppRepository, JOB_PHASE_LABELS, RUN_LIFECYCLE_LABELS


SKILL_PROFILES = [
    {"value": "mixed_operations", "label": "Mixed Operations"},
    {"value": "subtraction_and_addition", "label": "Addition + Subtraction"},
    {"value": "multiplication_focus", "label": "Multiplication Focus"},
    {"value": "division_focus", "label": "Division Focus"},
    {"value": "algebra", "label": "Algebra"},
    {"value": "geometry", "label": "Geometry + Trigonometry"},
]

SKILL_OPTIONS = [
    {"value": "addition", "label": "Addition"},
    {"value": "subtraction", "label": "Subtraction"},
    {"value": "multiplication", "label": "Multiplication"},
    {"value": "division", "label": "Division"},
    {"value": "algebra", "label": "Algebra"},
    {"value": "geometry", "label": "Geometry"},
    {"value": "trigonometry", "label": "Trigonometry"},
]

MAX_IMAGE_ADDITIONAL_GUIDANCE_LENGTH = 500

REWARD_STYLES = [
    {"value": "riddle", "label": "Riddle"},
    {"value": "question", "label": "Question"},
    {"value": "pun", "label": "Pun"},
]

LANGUAGE_OPTIONS = [
    {"value": "en", "label": "English"},
]

LEARNER_BAND_PRESETS = {
    LearnerBand.EARLY_ARITHMETIC: {
        "description": "Uses the gentlest defaults for early number work and narrows the available skill families.",
        "default_reveal_mode": RevealMode.COLOR_BY_NUMBER.value,
        "default_skill_profile": "subtraction_and_addition",
        "default_selected_skills": [
            {"skill": "addition", "difficulty_minimum": 1, "difficulty_maximum": 2},
            {"skill": "subtraction", "difficulty_minimum": 1, "difficulty_maximum": 2},
        ],
        "default_difficulty_minimum": 1,
        "default_difficulty_maximum": 2,
        "default_decoy_percentage": 100,
        "default_color_picture_source": "preset",
        "default_color_picture_preset": "smile",
    },
    LearnerBand.UPPER_ELEMENTARY: {
        "description": "Starts from a balanced preset with broader arithmetic variety.",
        "default_reveal_mode": RevealMode.LETTER_BANK.value,
        "default_skill_profile": "mixed_operations",
        "default_selected_skills": [
            {"skill": "addition", "difficulty_minimum": 1, "difficulty_maximum": 3},
            {"skill": "subtraction", "difficulty_minimum": 1, "difficulty_maximum": 3},
            {"skill": "multiplication", "difficulty_minimum": 1, "difficulty_maximum": 3},
            {"skill": "division", "difficulty_minimum": 1, "difficulty_maximum": 3},
        ],
        "default_difficulty_minimum": 1,
        "default_difficulty_maximum": 3,
        "default_decoy_percentage": 100,
        "default_color_picture_source": "preset",
        "default_color_picture_preset": "star",
    },
    LearnerBand.PRE_ALGEBRA: {
        "description": "Starts with one-step and two-step algebra while keeping every worksheet field editable.",
        "default_reveal_mode": RevealMode.LETTER_BANK.value,
        "default_skill_profile": "algebra",
        "default_selected_skills": [
            {"skill": "algebra", "difficulty_minimum": 1, "difficulty_maximum": 2},
        ],
        "default_difficulty_minimum": 1,
        "default_difficulty_maximum": 2,
        "default_decoy_percentage": 100,
        "default_color_picture_source": "preset",
        "default_color_picture_preset": "heart",
    },
    LearnerBand.ALGEBRA: {
        "description": "Uses the same algebra profile but pushes the default range into more advanced equation work.",
        "default_reveal_mode": RevealMode.LETTER_BANK.value,
        "default_skill_profile": "algebra",
        "default_selected_skills": [
            {"skill": "algebra", "difficulty_minimum": 3, "difficulty_maximum": 5},
        ],
        "default_difficulty_minimum": 3,
        "default_difficulty_maximum": 5,
        "default_decoy_percentage": 100,
        "default_color_picture_source": "preset",
        "default_color_picture_preset": "star",
    },
    LearnerBand.GEOMETRY: {
        "description": "Defaults to geometry and trigonometry problems with rendered shape diagrams and side-length reasoning.",
        "default_reveal_mode": RevealMode.LETTER_BANK.value,
        "default_skill_profile": "geometry",
        "default_selected_skills": [
            {"skill": "geometry", "difficulty_minimum": 2, "difficulty_maximum": 4},
            {"skill": "trigonometry", "difficulty_minimum": 2, "difficulty_maximum": 5},
        ],
        "default_difficulty_minimum": 2,
        "default_difficulty_maximum": 5,
        "default_decoy_percentage": 100,
        "default_color_picture_source": "preset",
        "default_color_picture_preset": "moon",
    },
}

GENERATION_STAGE_PHASES = {
    "assemble worksheet content": "worksheet_generation_assemble",
    "export preview and solution": "worksheet_generation_export",
    "write manifest and metadata": "worksheet_generation_write_metadata",
    "persist artifact records": "worksheet_generation_persist_artifacts",
}

STYLING_STAGE_PHASES = {
    "render semantic foreground": "styling_render_foreground",
    "refine styling prompt": "styling_refine_prompt",
    "apply Gemini styling": "styling_apply_and_verify",
    "verify styled worksheet": "styling_write_artifacts",
    "write styled artifacts": "styling_write_artifacts",
}

STYLING_RETRYABLE_STATUSES = {"styled_failed_verification", "styled_failed_error"}
COLOR_BY_NUMBER_MAX_DIFFICULTY = 2
STYLING_CANCELLABLE_PHASES = {
    "queued",
    "styling_queued",
    "styling_retry_queued",
    "styling_prepare",
    "styling_retry_prepare",
    "styling_render_foreground",
    "styling_refine_prompt",
}


def _optional_int_arg(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return int(value)


def _skill_supported(problem_generation_service: ProblemGenerationService, learner_band: LearnerBand, skill: str) -> bool:
    try:
        family = problem_generation_service._family_for_skill(skill)  # type: ignore[attr-defined]
        generator = problem_generation_service._generators[family]  # type: ignore[attr-defined]
    except Exception:
        return False
    return generator.supports(learner_band)


def _supported_skills(problem_generation_service: ProblemGenerationService) -> list[dict[str, object]]:
    skills: list[dict[str, object]] = []
    for skill in SKILL_OPTIONS:
        supported_bands = [
            learner_band.value
            for learner_band in LearnerBand
            if _skill_supported(problem_generation_service, learner_band, str(skill["value"]))
        ]
        skills.append({**skill, "supported_learner_bands": supported_bands})
    return skills


def _selected_skills_from_legacy(skill_profile: str, difficulty_minimum: int, difficulty_maximum: int) -> list[dict[str, object]]:
    profile_map = {
        "subtraction_and_addition": ["addition", "subtraction"],
        "multiplication_focus": ["multiplication"],
        "division_focus": ["division"],
        "algebra": ["algebra"],
        "geometry": ["geometry", "trigonometry"],
        "addition": ["addition"],
        "subtraction": ["subtraction"],
        "multiplication": ["multiplication"],
        "division": ["division"],
        "trigonometry": ["trigonometry"],
        "mixed_operations": ["addition", "subtraction", "multiplication", "division"],
    }
    skills = profile_map.get(skill_profile, ["addition", "subtraction", "multiplication", "division"])
    return [
        {
            "skill": skill,
            "difficulty_minimum": difficulty_minimum,
            "difficulty_maximum": difficulty_maximum,
        }
        for skill in skills
    ]


def _normalize_selected_skills(
    payload: dict[str, object],
    *,
    learner_band: LearnerBand,
    problem_generation_service: ProblemGenerationService,
    reveal_mode: str,
) -> list[dict[str, object]]:
    raw_selected = payload.get("selected_skills")
    selected: list[dict[str, object]] = []
    if isinstance(raw_selected, list) and raw_selected:
        for entry in raw_selected:
            if not isinstance(entry, dict):
                continue
            skill = str(entry.get("skill", "")).strip()
            if not skill:
                continue
            minimum = int(entry.get("difficulty_minimum") or 1)
            maximum = int(entry.get("difficulty_maximum") or minimum)
            if minimum > maximum:
                raise ValueError(f"difficulty minimum must not exceed maximum for skill {skill}")
            if not _skill_supported(problem_generation_service, learner_band, skill):
                raise ValueError(f"skill {skill!r} is not supported for learner band {learner_band.value}")
            selected.append(
                {
                    "skill": skill,
                    "difficulty_minimum": minimum,
                    "difficulty_maximum": maximum,
                }
            )
    if selected:
        return selected
    legacy_profile = str(payload.get("skill_profile", "")).strip() or "mixed_operations"
    difficulty_minimum = int(payload.get("difficulty_minimum") or 1)
    difficulty_maximum = int(payload.get("difficulty_maximum") or difficulty_minimum)
    selected = _selected_skills_from_legacy(legacy_profile, difficulty_minimum, difficulty_maximum)
    for entry in selected:
        if not _skill_supported(problem_generation_service, learner_band, str(entry["skill"])):
            raise ValueError(f"skill profile {legacy_profile!r} is not supported for learner band {learner_band.value}")
    return selected


def _derived_skill_profile(selected_skills: list[dict[str, object]]) -> str:
    skill_names = [str(item["skill"]) for item in selected_skills]
    if not skill_names:
        return "mixed_operations"
    if len(skill_names) == 1:
        return skill_names[0]
    skill_set = set(skill_names)
    if skill_set == {"addition", "subtraction"}:
        return "subtraction_and_addition"
    if skill_set == {"addition", "subtraction", "multiplication", "division"}:
        return "mixed_operations"
    if skill_set == {"geometry", "trigonometry"}:
        return "geometry"
    return "mixed_skills"


def _selected_skill_difficulty_bounds(selected_skills: list[dict[str, object]]) -> tuple[int, int]:
    minimum = min(int(item["difficulty_minimum"]) for item in selected_skills)
    maximum = max(int(item["difficulty_maximum"]) for item in selected_skills)
    return minimum, maximum


def create_app(*, reward_content_service: RewardContentService | None = None) -> Flask:
    configure_application_logging()
    logger = logging.getLogger("worksheet_generator.webapp")
    paths = load_app_paths()
    ensure_storage_paths(paths.database_path, paths.artifact_root)
    initialize_database(paths.database_path)

    template_folder = str(Path(__file__).resolve().parent / "templates")
    static_folder = str(Path(__file__).resolve().parent / "static")
    app = Flask(__name__, template_folder=template_folder, static_folder=static_folder, static_url_path="/static")
    repository = AppRepository(paths.database_path)
    problem_generation_service = ProblemGenerationService()
    gemini_enabled = is_gemini_enabled()
    gemini_model = get_gemini_model()
    gemini_image_model = get_gemini_image_model()
    log_verbosity = get_log_verbosity()
    worksheet_generation_timeout_seconds = get_worksheet_generation_timeout_seconds()
    worksheet_styling_timeout_seconds = get_worksheet_styling_timeout_seconds()
    styling_confirmation_timeout_seconds = get_styling_confirmation_timeout_seconds()
    if reward_content_service is None and gemini_enabled:
        reward_content_service = RewardContentService(
            generator=GeminiRewardContentGenerator(api_key=os.environ["GEMINI_API_KEY"], model=gemini_model)
        )
    worksheet_generation_service = WorksheetRunGenerationService(
        paths.artifact_root,
        gemini_color_grid_generator=(
            GeminiColorGridGenerator(api_key=os.environ["GEMINI_API_KEY"], model=gemini_model)
            if gemini_enabled
            else None
        ),
        worksheet_image_styler=(
            GeminiWorksheetImageStylingService(api_key=os.environ["GEMINI_API_KEY"], model=gemini_image_model)
            if gemini_enabled
            else None
        ),
        styling_prompt_refiner=(
            GeminiWorksheetStylingPromptRefiner(api_key=os.environ["GEMINI_API_KEY"], model=gemini_model)
            if gemini_enabled
            else None
        ),
    )

    app.config["APP_DB_PATH"] = str(paths.database_path)
    app.config["APP_ARTIFACT_ROOT"] = str(paths.artifact_root)
    app.config["GEMINI_ENABLED"] = gemini_enabled
    app.config["GEMINI_MODEL"] = gemini_model
    app.config["GEMINI_IMAGE_MODEL"] = gemini_image_model
    app.config["APP_LOG_VERBOSITY"] = log_verbosity
    app.config["APP_DEBUG_UI"] = is_debug_ui_enabled()
    app.config["APP_JOB_WORKER_ENABLED"] = is_job_worker_enabled()
    app.config["GALLERY_PAGE_SIZE_MAXIMUM"] = 48
    app.config["WORKSHEET_GENERATION_TIMEOUT_SECONDS"] = worksheet_generation_timeout_seconds
    app.config["WORKSHEET_STYLING_TIMEOUT_SECONDS"] = worksheet_styling_timeout_seconds
    app.config["STYLING_CONFIRMATION_TIMEOUT_SECONDS"] = styling_confirmation_timeout_seconds

    log_event(
        logger,
        "app_initialized",
        database_path=str(paths.database_path),
        artifact_root=str(paths.artifact_root),
        gemini_enabled=gemini_enabled,
        gemini_model=gemini_model,
        gemini_image_model=gemini_image_model,
        log_verbosity=log_verbosity,
        debug_ui=app.config["APP_DEBUG_UI"],
        job_worker_enabled=app.config["APP_JOB_WORKER_ENABLED"],
        worksheet_generation_timeout_seconds=worksheet_generation_timeout_seconds,
        worksheet_styling_timeout_seconds=worksheet_styling_timeout_seconds,
        styling_confirmation_timeout_seconds=styling_confirmation_timeout_seconds,
    )

    worker_name = f"worksheet-worker-{os.getpid()}"
    job_wake_event = threading.Event()

    def _parse_db_timestamp(value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

    def _set_job_progress(job_id: int, message: str, *, event_name: str = "worksheet_job_progress", phase: str | None = None) -> None:
        if _job_is_terminal(job_id):
            return
        repository.update_generation_job(job_id, progress_message=message, phase=phase)
        job = repository.get_generation_job(job_id)
        workflow_token = str(job["requested_parameters"].get("workflow_token") or "").strip() or None
        _update_workflow_session(
            workflow_token,
            phase=phase or str(job["phase"]),
            generation_job_id=job_id,
            worksheet_run_id=int(job["worksheet_run_id"]) if job.get("worksheet_run_id") is not None else None,
            status="active",
        )
        log_event(logger, event_name, verbosity="normal", job_id=job_id, message=message)

    def _job_is_terminal(job_id: int) -> bool:
        return repository.get_generation_job(job_id)["status"] in {"completed", "failed", "cancelled"}

    def _job_is_cancelled(job_id: int) -> bool:
        return repository.get_generation_job(job_id)["status"] == "cancelled"

    def _workflow_token_from_payload(payload: dict[str, object]) -> str | None:
        token = str(payload.get("workflow_token", "")).strip()
        return token or None

    def _styling_job_can_be_cancelled(job: dict[str, object]) -> bool:
        if job.get("job_type") != "worksheet_style":
            return True
        if job.get("status") in {"completed", "failed", "cancelled"}:
            return False
        phase = str(job.get("phase") or "").strip() or "queued"
        return phase in STYLING_CANCELLABLE_PHASES

    def _update_workflow_session(token: str | None, **kwargs: object) -> None:
        if not token:
            return
        try:
            repository.update_workflow_session(token, **kwargs)
        except KeyError:
            log_event(logger, "workflow_session_missing", verbosity="normal", workflow_token=token)

    def _timeout_seconds_for_job(job_type: str) -> float | None:
        if job_type == "worksheet_generate":
            return worksheet_generation_timeout_seconds
        if job_type == "worksheet_style":
            return worksheet_styling_timeout_seconds
        return None

    def _apply_job_timeout(job_id: int, worksheet_run_id: int | None, *, timeout_seconds: float, job_type: str) -> None:
        try:
            job = repository.get_generation_job(job_id)
        except KeyError:
            return
        if job["status"] in {"completed", "failed", "cancelled"}:
            return
        timeout_message = (
            f"Worksheet generation timed out after {int(timeout_seconds)} seconds."
            if job_type == "worksheet_generate"
            else f"Worksheet styling timed out after {int(timeout_seconds)} seconds. Base worksheet retained."
        )
        repository.fail_generation_job(job_id, progress_message=timeout_message)
        if worksheet_run_id is not None:
            if job_type == "worksheet_generate":
                repository.fail_worksheet_run(
                    worksheet_run_id,
                    message="Worksheet generation timed out before completion. Regenerate the worksheet to try again.",
                )
            elif job_type == "worksheet_style":
                repository.update_worksheet_run_styling(
                    worksheet_run_id,
                    status="styled_failed_error",
                    verification_status="failed",
                )
        log_event(
            logger,
            "worksheet_job_timed_out",
            job_id=job_id,
            worksheet_run_id=worksheet_run_id,
            job_type=job_type,
            timeout_seconds=timeout_seconds,
            message=timeout_message,
        )

    def _run_generation_job(
        job_id: int,
        worksheet_run_id: int,
        approved_draft: dict[str, object],
        parameters: dict[str, object],
    ) -> None:
        workflow_token = str(parameters.get("workflow_token") or "").strip() or None
        try:
            repository.update_generation_job(
                job_id,
                status="running",
                phase="worksheet_generation_prepare",
                progress_message=f"Preparing worksheet run {worksheet_run_id} for background generation.",
                worksheet_run_id=worksheet_run_id,
            )
            repository.update_worksheet_run_lifecycle(
                worksheet_run_id,
                lifecycle_phase="worksheet_generation_running",
            )
            _update_workflow_session(
                workflow_token,
                phase="worksheet_generation_running",
                draft_id=int(parameters.get("draft_id") or approved_draft["id"]),
                worksheet_run_id=worksheet_run_id,
                generation_job_id=job_id,
                status="active",
            )
            result = worksheet_generation_service.generate(
                worksheet_run_id=worksheet_run_id,
                approved_draft=approved_draft,
                parameters=parameters,
                progress_callback=lambda stage: _set_job_progress(
                    job_id,
                    f"Generating worksheet run {worksheet_run_id}: {stage}.",
                    event_name="worksheet_generation_progress",
                    phase=GENERATION_STAGE_PHASES.get(stage, "worksheet_generation_running"),
                ),
                should_cancel=lambda: _job_is_cancelled(job_id),
            )
            if _job_is_terminal(job_id):
                log_event(
                    logger,
                    "worksheet_generation_late_result_discarded",
                    verbosity="normal",
                    worksheet_run_id=worksheet_run_id,
                    job_id=job_id,
                )
                return
            for artifact in result["artifacts"]:
                repository.attach_artifact(
                    worksheet_run_id=worksheet_run_id,
                    artifact_kind=str(artifact["artifact_kind"]),
                    output_format=str(artifact["output_format"]),
                    relative_path=str(Path(artifact["path"]).relative_to(paths.artifact_root)),
                    display_name=str(artifact["display_name"]),
                )
            repository.complete_worksheet_run(
                worksheet_run_id,
                artifact_group=str(result["artifact_group"]),
                thumbnail_path=str(Path(result["thumbnail_path"]).relative_to(paths.artifact_root)),
            )
            run_record = repository.get_worksheet_run(worksheet_run_id)
            if run_record["styling"]["requested"]:
                repository.update_worksheet_run_styling(
                    worksheet_run_id,
                    status=str(run_record["styling"]["status"]),
                    verification_status=str(run_record["styling"]["verification_status"]),
                    prompt_text=_styling_prompt_from_generated_worksheet(
                        worksheet=result["worksheet"],
                        parameters=parameters,
                        style_name=str(run_record["styling"]["style_name"]),
                        color_mode=str(run_record["styling"]["color_mode"]),
                        ink_saver=bool(run_record["styling"]["ink_saver"]),
                    ),
                    model=str(run_record["styling"]["model"]) if run_record["styling"]["model"] else None,
                )
            repository.complete_generation_job(
                job_id,
                progress_message=f"Worksheet run {worksheet_run_id} generated.",
            )
            _update_workflow_session(
                workflow_token,
                phase=(
                    "review_plain_run"
                    if run_record["styling"]["requested"]
                    else "plain_worksheet_ready"
                ),
                worksheet_run_id=worksheet_run_id,
                generation_job_id=job_id,
                status="active" if run_record["styling"]["requested"] else "completed",
            )
            log_event(
                logger,
                "worksheet_generation_completed",
                verbosity="normal",
                worksheet_run_id=worksheet_run_id,
                job_id=job_id,
                artifact_group=str(result["artifact_group"]),
                artifact_count=len(result["artifacts"]),
            )
        except WorksheetRunCancelledError as exc:
            repository.cancel_worksheet_run(worksheet_run_id, message=str(exc))
            _update_workflow_session(
                workflow_token,
                phase="run_cancelled",
                worksheet_run_id=worksheet_run_id,
                generation_job_id=job_id,
                status="cancelled",
            )
            log_event(
                logger,
                "worksheet_generation_cancelled",
                verbosity="normal",
                draft_id=int(parameters.get("draft_id") or 0) or None,
                worksheet_run_id=worksheet_run_id,
                job_id=job_id,
                message=str(exc),
            )
        except (WorksheetRunGenerationError, ValueError) as exc:
            repository.fail_generation_job(job_id, progress_message=str(exc))
            repository.fail_worksheet_run(worksheet_run_id, message=str(exc))
            _update_workflow_session(
                workflow_token,
                phase="run_failed",
                worksheet_run_id=worksheet_run_id,
                generation_job_id=job_id,
                status="failed",
            )
            log_event(
                logger,
                "worksheet_generation_failed",
                draft_id=int(parameters.get("draft_id") or 0) or None,
                worksheet_run_id=worksheet_run_id,
                job_id=job_id,
                error=str(exc),
            )

    def _run_styling_job(job_id: int, worksheet_run_id: int, *, retry: bool = False) -> None:
        workflow_token = str(repository.get_generation_job(job_id)["requested_parameters"].get("workflow_token") or "").strip() or None
        try:
            repository.update_generation_job(
                job_id,
                status="running",
                phase="styling_retry_prepare" if retry else "styling_prepare",
                progress_message=(
                    f"Preparing styling retry for worksheet run {worksheet_run_id}."
                    if retry
                    else f"Review confirmed. Preparing styling for worksheet run {worksheet_run_id}."
                ),
                worksheet_run_id=worksheet_run_id,
            )
            repository.update_worksheet_run_styling(
                worksheet_run_id,
                status="retry_in_progress" if retry else "styling_in_progress",
                verification_status="pending",
            )
            _update_workflow_session(
                workflow_token,
                phase="styling_running",
                worksheet_run_id=worksheet_run_id,
                generation_job_id=job_id,
                status="active",
            )
            run_record = repository.get_worksheet_run(worksheet_run_id)
            result = worksheet_generation_service.apply_image_styling(
                worksheet_run_id=worksheet_run_id,
                prompt_text=str(run_record["styling"]["prompt_text"] or ""),
                progress_callback=lambda stage: _set_job_progress(
                    job_id,
                    (
                        f"Retrying styling for worksheet run {worksheet_run_id}: {stage}."
                        if retry
                        else f"Styling worksheet run {worksheet_run_id}: {stage}."
                    ),
                    event_name="worksheet_styling_progress",
                    phase=STYLING_STAGE_PHASES.get(stage, "styling_running"),
                ),
                should_cancel=lambda: _job_is_cancelled(job_id),
            )
            if _job_is_terminal(job_id):
                log_event(
                    logger,
                    "worksheet_styling_late_result_discarded",
                    verbosity="normal",
                    worksheet_run_id=worksheet_run_id,
                    job_id=job_id,
                    retry=retry,
                )
                return
            for artifact in result["artifacts"]:
                repository.attach_artifact(
                    worksheet_run_id=worksheet_run_id,
                    artifact_kind=str(artifact["artifact_kind"]),
                    output_format=str(artifact["output_format"]),
                    relative_path=str(Path(artifact["path"]).relative_to(paths.artifact_root)),
                    display_name=str(artifact["display_name"]),
                )
            repository.update_worksheet_run_styling(
                worksheet_run_id,
                status="styled_verified" if result["verified"] else "styled_failed_verification",
                verification_status="passed" if result["verified"] else "failed",
                prompt_text=str(result["final_prompt"] or run_record["styling"]["prompt_text"] or ""),
                styled_artifact_group=str(result["artifact_group"]),
                styled_thumbnail_path=str(Path(result["thumbnail_path"]).relative_to(paths.artifact_root)),
                style_check_artifact_path=str(Path(result["style_check_artifact_path"]).relative_to(paths.artifact_root)),
            )
            if result["verified"]:
                repository.complete_generation_job(
                    job_id,
                    progress_message=(
                        f"Styled worksheet retry for run {worksheet_run_id} verified and saved."
                        if retry
                        else f"Styled worksheet run {worksheet_run_id} verified and saved."
                    ),
                )
            else:
                repository.fail_generation_job(
                    job_id,
                    progress_message=(
                        f"Styled worksheet retry for run {worksheet_run_id} failed verification. Base worksheet retained."
                        if retry
                        else f"Styled worksheet run {worksheet_run_id} failed verification after retry. Base worksheet retained."
                    ),
                )
            log_event(
                logger,
                "worksheet_styling_completed",
                verbosity="normal",
                worksheet_run_id=worksheet_run_id,
                job_id=job_id,
                retry=retry,
                verified=result["verified"],
                mismatch_count=result["verification_report"].mismatch_count,
            )
            if not result["verified"]:
                response_json = None
                attempts = result.get("attempts") or ()
                if attempts:
                    response_json = getattr(attempts[-1].styled_artifact, "raw_response_json", None)
                if response_json:
                    log_event(
                        logger,
                        "worksheet_styling_failed_gemini_response",
                        verbosity="minimal",
                        worksheet_run_id=worksheet_run_id,
                        job_id=job_id,
                        retry=retry,
                        response_json=response_json,
                    )
            _update_workflow_session(
                workflow_token,
                phase="styled_verified" if result["verified"] else "styled_failed_plain_retained",
                worksheet_run_id=worksheet_run_id,
                generation_job_id=job_id,
                status="completed" if result["verified"] else "failed",
            )
        except WorksheetRunCancelledError as exc:
            repository.update_worksheet_run_styling(
                worksheet_run_id,
                status="cancelled_during_styling",
                verification_status="not_requested",
            )
            _update_workflow_session(
                workflow_token,
                phase="styling_cancelled_plain_retained",
                worksheet_run_id=worksheet_run_id,
                generation_job_id=job_id,
                status="cancelled",
            )
            log_event(
                logger,
                "worksheet_styling_cancelled",
                worksheet_run_id=worksheet_run_id,
                job_id=job_id,
                retry=retry,
                message=str(exc),
            )
        except Exception as exc:
            repository.update_worksheet_run_styling(
                worksheet_run_id,
                status="styled_failed_error",
                verification_status="failed",
            )
            repository.fail_generation_job(job_id, progress_message=str(exc))
            _update_workflow_session(
                workflow_token,
                phase="styled_failed_plain_retained",
                worksheet_run_id=worksheet_run_id,
                generation_job_id=job_id,
                status="failed",
            )
            log_event(
                logger,
                "worksheet_styling_failed",
                worksheet_run_id=worksheet_run_id,
                job_id=job_id,
                retry=retry,
                error=str(exc),
                response_json=getattr(exc, "response_json", None),
            )

    def _dispatch_claimed_job(job: dict[str, object]) -> None:
        requested = dict(job.get("requested_parameters") or {})
        worksheet_run_id = int(job["worksheet_run_id"]) if job.get("worksheet_run_id") is not None else None
        if job["job_type"] == "worksheet_generate":
            draft_id = int(requested["draft_id"])
            draft_record = repository.get_reward_content_draft(draft_id)
            _run_generation_job(
                int(job["id"]),
                int(requested["worksheet_run_id"]),
                draft_record,
                requested,
            )
            return
        if job["job_type"] == "worksheet_style":
            _run_styling_job(
                int(job["id"]),
                int(requested["worksheet_run_id"]),
                retry=bool(requested.get("retry")),
            )
            return
        repository.fail_generation_job(int(job["id"]), progress_message=f"Unsupported queued job type: {job['job_type']}.")

    def _queue_worker_loop() -> None:
        while True:
            claimed_job: dict[str, object] | None = None
            try:
                claimed_job = repository.claim_next_generation_job(worker_name=worker_name)
                if claimed_job is None:
                    job_wake_event.wait(0.1)
                    job_wake_event.clear()
                    continue
                _dispatch_claimed_job(claimed_job)
            except Exception as exc:
                if claimed_job is not None:
                    repository.fail_generation_job(int(claimed_job["id"]), progress_message=str(exc))
                    worksheet_run_id = int(claimed_job["worksheet_run_id"]) if claimed_job.get("worksheet_run_id") is not None else None
                    if worksheet_run_id is not None:
                        if claimed_job["job_type"] == "worksheet_generate":
                            repository.fail_worksheet_run(worksheet_run_id, message=str(exc))
                        elif claimed_job["job_type"] == "worksheet_style":
                            repository.update_worksheet_run_styling(
                                worksheet_run_id,
                                status="styled_failed_error",
                                verification_status="failed",
                            )
                log_event(logger, "queue_worker_loop_error", error=str(exc))
                time.sleep(0.5)

    def _queue_watchdog_loop() -> None:
        while True:
            try:
                for job in repository.list_running_generation_jobs():
                    timeout_seconds = _timeout_seconds_for_job(str(job["job_type"]))
                    if timeout_seconds is None:
                        continue
                    started_at = _parse_db_timestamp(str(job.get("started_at") or "")) or _parse_db_timestamp(str(job.get("created_at") or ""))
                    if started_at is None:
                        continue
                    elapsed_seconds = (datetime.now(timezone.utc) - started_at).total_seconds()
                    if elapsed_seconds >= timeout_seconds:
                        worksheet_run_id = int(job["worksheet_run_id"]) if job.get("worksheet_run_id") is not None else None
                        _apply_job_timeout(
                            int(job["id"]),
                            worksheet_run_id,
                            timeout_seconds=timeout_seconds,
                            job_type=str(job["job_type"]),
                        )
                time.sleep(0.05)
            except Exception as exc:
                log_event(logger, "queue_watchdog_loop_error", error=str(exc))
                time.sleep(0.05)

    def _enqueue_styling_job(worksheet_run_id: int, *, retry: bool = False, workflow_token: str | None = None) -> int:
        job_id = repository.create_generation_job(
            job_type="worksheet_style",
            requested_parameters={
                "worksheet_run_id": worksheet_run_id,
                "retry": retry,
                **({"workflow_token": workflow_token} if workflow_token else {}),
            },
            progress_message=(
                f"Queued styling retry for worksheet run {worksheet_run_id}."
                if retry
                else f"Queued styling for worksheet run {worksheet_run_id}."
            ),
            worksheet_run_id=worksheet_run_id,
            phase="styling_retry_queued" if retry else "styling_queued",
        )
        job_wake_event.set()
        return job_id

    def _reconcile_stale_background_state() -> None:
        for job in repository.list_running_generation_jobs():
            job_type = str(job["job_type"])
            job_id = int(job["id"])
            worksheet_run_id = int(job["worksheet_run_id"]) if job.get("worksheet_run_id") is not None else None
            if job_type == "worksheet_generate":
                message = "Worksheet generation was interrupted by application restart. Regenerate the worksheet to try again."
                repository.fail_generation_job(job_id, progress_message=message)
                if worksheet_run_id is not None:
                    repository.fail_worksheet_run(worksheet_run_id, message=message)
            elif job_type == "worksheet_style":
                message = "Worksheet styling was interrupted by application restart. Base worksheet retained and styling can be retried."
                repository.fail_generation_job(job_id, progress_message=message)
                if worksheet_run_id is not None:
                    repository.update_worksheet_run_styling(
                        worksheet_run_id,
                        status="styled_failed_error",
                        verification_status="failed",
                    )
            else:
                message = f"{job_type.replace('_', ' ').title()} was interrupted by application restart."
                repository.fail_generation_job(job_id, progress_message=message)
            log_event(
                logger,
                "stale_job_reconciled",
                verbosity="normal",
                job_id=job_id,
                worksheet_run_id=worksheet_run_id,
                job_type=job_type,
                message=message,
            )

        for run in repository.list_worksheet_runs_awaiting_styling_confirmation_before(
            cutoff_seconds=styling_confirmation_timeout_seconds
        ):
            repository.update_worksheet_run_styling(
                int(run["id"]),
                status="cancelled_after_plain_review",
                verification_status="not_requested",
            )
            log_event(
                logger,
                "stale_styling_confirmation_cancelled",
                verbosity="normal",
                worksheet_run_id=int(run["id"]),
                timeout_seconds=styling_confirmation_timeout_seconds,
            )

    _reconcile_stale_background_state()
    if app.config["APP_JOB_WORKER_ENABLED"]:
        threading.Thread(target=_queue_worker_loop, daemon=True, name="worksheet-job-worker").start()
        threading.Thread(target=_queue_watchdog_loop, daemon=True, name="worksheet-job-watchdog").start()
        job_wake_event.set()

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/api/app-config")
    def app_config() -> Any:
        return jsonify(
            {
                "gemini": {
                    "enabled": app.config["GEMINI_ENABLED"],
                    "model": app.config["GEMINI_MODEL"],
                    "image_model": app.config["GEMINI_IMAGE_MODEL"],
                    "note": (
                        f"Gemini-assisted riddle generation is available via {app.config['GEMINI_MODEL']}."
                        if app.config["GEMINI_ENABLED"]
                        else "Gemini-assisted features are disabled because GEMINI_API_KEY was not detected."
                    ),
                },
                "storage": {
                    "database_path": app.config["APP_DB_PATH"],
                    "artifact_root": app.config["APP_ARTIFACT_ROOT"],
                },
                "logging": {
                    "verbosity": app.config["APP_LOG_VERBOSITY"],
                    "modes": ["minimal", "normal", "verbose"],
                },
                "ui": {
                    "debug_enabled": app.config["APP_DEBUG_UI"],
                    "mode": "debug" if app.config["APP_DEBUG_UI"] else "customer",
                },
                "maintenance": {
                    "enabled": app.config["APP_DEBUG_UI"],
                },
                "worksheet_options": {
                    "learner_bands": [
                        {
                            "value": learner_band.value,
                            "label": learner_band.value.replace("_", " ").title(),
                            **LEARNER_BAND_PRESETS[learner_band],
                        }
                        for learner_band in LearnerBand
                    ],
                    "reveal_modes": [
                        {"value": reveal_mode.value, "label": reveal_mode.value.replace("_", " ").title()}
                        for reveal_mode in RevealMode
                    ],
                    "skill_profiles": _supported_skill_profiles(problem_generation_service),
                    "skills": _supported_skills(problem_generation_service),
                    "reward_styles": REWARD_STYLES,
                    "languages": LANGUAGE_OPTIONS,
                    "difficulty_range": {"minimum": 1, "maximum": 5, "default_minimum": 1, "default_maximum": 2},
                    "color_by_number_difficulty_range": {
                        "minimum": 1,
                        "maximum": COLOR_BY_NUMBER_MAX_DIFFICULTY,
                        "note": "Color-by-number is limited to beginner difficulty so the answers fit cleanly inside the grid.",
                    },
                    "decoy_percentage": {"minimum": 0, "maximum": 300, "default": 100},
                    "max_color_options": 32,
                    "color_grid_size": {"minimum": 16, "maximum": 40},
                    "color_picture_sources": [
                        {"value": "preset", "label": "Preset Picture"},
                        {
                            "value": "gemini",
                            "label": "Gemini Picture (Experimental)",
                            "enabled": app.config["GEMINI_ENABLED"],
                        },
                    ],
                    "color_picture_presets": PRESET_PICTURE_OPTIONS,
                    "image_styling": {
                        "styles": IMAGE_STYLE_OPTIONS,
                        "color_modes": IMAGE_STYLE_COLOR_MODES,
                        "default_style": DEFAULT_IMAGE_STYLE,
                        "default_color_mode": DEFAULT_IMAGE_COLOR_MODE,
                        "default_enabled": False,
                        "default_ink_saver": False,
                        "enabled": app.config["GEMINI_ENABLED"],
                        "model": app.config["GEMINI_IMAGE_MODEL"],
                        "prompt_refinement_model": DEFAULT_IMAGE_REFINEMENT_MODEL,
                        "prompt_strategy": "worksheet_semantic_preservation",
                        "preserves_content_via_foreground_compositing": True,
                        "verification_strategy": "semantic_foreground_pixel_preservation",
                        "retry_policy": {
                            "max_attempts": 2,
                            "retry_on_verification_failure": True,
                        },
                        "timeout_policy": {
                            "worksheet_generation_seconds": app.config["WORKSHEET_GENERATION_TIMEOUT_SECONDS"],
                            "worksheet_styling_seconds": app.config["WORKSHEET_STYLING_TIMEOUT_SECONDS"],
                            "styling_confirmation_seconds": app.config["STYLING_CONFIRMATION_TIMEOUT_SECONDS"],
                        },
                        "sample_prompt": build_worksheet_styling_prompt(
                            WorksheetImageStylingPromptRequest(
                                theme="space",
                                style_name=DEFAULT_IMAGE_STYLE,
                                color_mode=DEFAULT_IMAGE_COLOR_MODE,
                                ink_saver=False,
                                additional_guidance="",
                                title="Space Worksheet",
                                prompt_text="What clue points to the night sky?",
                                learner_band_label="Upper Elementary",
                                reveal_mode_label="Letter Bank",
                            )
                        ),
                        "note": (
                            "Styling settings, prompt construction, execution, compositing, and verification/retry are available for worksheet runs."
                            if app.config["GEMINI_ENABLED"]
                            else "Image styling is disabled because GEMINI_API_KEY was not detected."
                        ),
                    },
                    "seed": {"minimum": 1, "placeholder": "Optional deterministic seed"},
                    "gallery": {
                        "page_size_default": 24,
                        "page_size_maximum": 48,
                        "sort_options": [
                            {"value": "created_desc", "label": "Newest First"},
                            {"value": "created_asc", "label": "Oldest First"},
                            {"value": "updated_desc", "label": "Recently Updated"},
                            {"value": "title_asc", "label": "Title A-Z"},
                        ],
                        "styling_status_options": [
                            {"value": "not_requested", "label": "Plain Only"},
                            {"value": "awaiting_confirmation", "label": "Awaiting Styling Confirmation"},
                            {"value": "styled_verified", "label": "Styled Verified"},
                            {"value": "styled_failed_verification", "label": "Styled Verification Failed"},
                            {"value": "styled_failed_error", "label": "Styled Error"},
                            {"value": "cancelled_after_plain_review", "label": "Styling Cancelled"},
                        ],
                    },
                },
                "job_tracking": {
                    "transport": "durable_queue_polling",
                    "worker_enabled": app.config["APP_JOB_WORKER_ENABLED"],
                    "blocking_poll_ready": True,
                    "sse_ready": False,
                    "workflow_url_tracking": True,
                    "job_phase_catalog": JOB_PHASE_LABELS,
                    "run_phase_catalog": RUN_LIFECYCLE_LABELS,
                    "note": "Worksheet generation and styling are queued in SQLite-backed jobs and processed by startup background workers.",
                    "stale_job_reconciliation": True,
                },
            }
        )

    @app.post("/api/workflow-sessions")
    def create_workflow_session() -> Any:
        payload = request.get_json(force=True, silent=False) or {}
        learner_band = LearnerBand(str(payload.get("learner_band") or LearnerBand.UPPER_ELEMENTARY.value))
        reveal_mode = str(payload.get("reveal_mode", RevealMode.LETTER_BANK.value)).strip() or RevealMode.LETTER_BANK.value
        try:
            selected_skills = _normalize_selected_skills(
                payload,
                learner_band=learner_band,
                problem_generation_service=problem_generation_service,
                reveal_mode=reveal_mode,
            )
            image_additional_guidance = _normalized_image_additional_guidance(payload.get("image_additional_guidance", ""))
        except ValueError as exc:
            return jsonify({"error": "invalid_workflow_controls", "message": str(exc)}), 400
        difficulty_minimum, difficulty_maximum = _selected_skill_difficulty_bounds(selected_skills)
        controls = {
            "theme": str(payload.get("theme", "")).strip(),
            "learner_band": learner_band.value,
            "style": str(payload.get("style", "riddle")).strip() or "riddle",
            "language": str(payload.get("language", "en")).strip() or "en",
            "reveal_mode": reveal_mode,
            "skill_profile": _derived_skill_profile(selected_skills),
            "selected_skills": selected_skills,
            "difficulty_minimum": difficulty_minimum,
            "difficulty_maximum": difficulty_maximum,
            "decoy_percentage": int(payload.get("decoy_percentage") or 0),
            "color_picture_source": str(payload.get("color_picture_source", "preset")).strip() or "preset",
            "color_picture_preset": str(payload.get("color_picture_preset", "smile")).strip() or "smile",
            "apply_image_styling": bool(payload.get("apply_image_styling")),
            "image_style_name": str(payload.get("image_style_name", DEFAULT_IMAGE_STYLE)).strip() or DEFAULT_IMAGE_STYLE,
            "image_color_mode": str(payload.get("image_color_mode", DEFAULT_IMAGE_COLOR_MODE)).strip() or DEFAULT_IMAGE_COLOR_MODE,
            "image_ink_saver": bool(payload.get("image_ink_saver")),
            "image_additional_guidance": image_additional_guidance,
            "seed": payload.get("seed"),
        }
        session = repository.create_workflow_session(controls=controls, phase="draft_generation_requested", status="active")
        return jsonify({"workflow_session": session})

    @app.get("/api/workflow-sessions/<token>")
    def get_workflow_session(token: str) -> Any:
        try:
            return jsonify({"workflow_session": repository.get_workflow_session(token)})
        except KeyError:
            return jsonify({"error": "not_found", "message": "Workflow session not found."}), 404

    def _enqueue_worksheet_run_from_draft(*, draft_record: dict[str, object], draft_id: int, parameters: dict[str, object]) -> dict[str, object]:
        workflow_token = str(parameters.get("workflow_token") or "").strip() or None
        title = _worksheet_title(parameters, draft_record)
        styling_request = _styling_request_from_parameters(
            parameters=parameters,
            draft_record=draft_record,
            gemini_enabled=bool(app.config["GEMINI_ENABLED"]),
            gemini_image_model=str(app.config["GEMINI_IMAGE_MODEL"]),
        )
        worksheet_run_id = repository.create_worksheet_run(
            title=title,
            learner_band=parameters["learner_band"],
            reveal_mode=parameters["reveal_mode"],
            skill_profile=parameters["skill_profile"],
            theme=parameters["theme"] or (str(draft_record["theme"]) if draft_record.get("theme") else None),
            prompt_text=str(draft_record["prompt_text"]),
            solution_phrase=str(draft_record["solution_phrase"]),
            parameters={"draft_id": draft_id, **parameters},
            styling=styling_request,
        )
        job_id = repository.create_generation_job(
            job_type="worksheet_generate",
            requested_parameters={"worksheet_run_id": worksheet_run_id, "draft_id": draft_id, **parameters},
            progress_message=f"Queued worksheet generation for run {worksheet_run_id}.",
            worksheet_run_id=worksheet_run_id,
            phase="worksheet_generation_queued",
        )
        _update_workflow_session(
            workflow_token,
            phase="worksheet_generation_queued",
            draft_id=draft_id,
            worksheet_run_id=worksheet_run_id,
            generation_job_id=job_id,
            status="active",
        )
        job_payload = repository.get_generation_job(job_id)
        job_wake_event.set()
        return {"worksheet_run": repository.get_worksheet_run(worksheet_run_id), "job": job_payload}

    @app.get("/api/maintenance")
    def get_maintenance() -> Any:
        if not app.config["APP_DEBUG_UI"]:
            return jsonify({"error": "not_found", "message": "Maintenance tools are only available in debug UI mode."}), 404
        return jsonify(maintenance_snapshot(repository=repository, artifact_root=paths.artifact_root))

    @app.post("/api/maintenance/prune-artifacts")
    def prune_artifacts() -> Any:
        if not app.config["APP_DEBUG_UI"]:
            return jsonify({"error": "not_found", "message": "Maintenance tools are only available in debug UI mode."}), 404
        result = prune_orphan_artifacts(repository=repository, artifact_root=paths.artifact_root)
        log_event(logger, "maintenance_artifacts_pruned", verbosity="normal", result=result)
        return jsonify(result)

    @app.post("/api/maintenance/vacuum")
    def vacuum_database() -> Any:
        if not app.config["APP_DEBUG_UI"]:
            return jsonify({"error": "not_found", "message": "Maintenance tools are only available in debug UI mode."}), 404
        repository.vacuum_and_analyze()
        snapshot = maintenance_snapshot(repository=repository, artifact_root=paths.artifact_root)
        log_event(logger, "maintenance_database_vacuumed", verbosity="normal")
        return jsonify({"status": "completed", "snapshot": snapshot})

    @app.get("/api/gallery")
    def gallery() -> Any:
        search = request.args.get("search", "").strip() or None
        learner_band = request.args.get("learner_band", "").strip() or None
        reveal_mode = request.args.get("reveal_mode", "").strip() or None
        skill_profile = request.args.get("skill_profile", "").strip() or None
        styling_status = request.args.get("styling_status", "").strip() or None
        picture_source = request.args.get("picture_source", "").strip() or None
        picture_preset = request.args.get("picture_preset", "").strip() or None
        sort = request.args.get("sort", "").strip() or "created_desc"
        difficulty_minimum = _optional_int_arg(request.args.get("difficulty_minimum"))
        difficulty_maximum = _optional_int_arg(request.args.get("difficulty_maximum"))
        seed = _optional_int_arg(request.args.get("seed"))
        styling_requested_arg = request.args.get("styling_requested", "").strip().lower()
        styling_requested = (
            True if styling_requested_arg == "true"
            else False if styling_requested_arg == "false"
            else None
        )
        limit = min(
            max(int(request.args.get("limit", "24")), 1),
            int(app.config.get("GALLERY_PAGE_SIZE_MAXIMUM", 48)),
        )
        offset = max(int(request.args.get("offset", "0")), 0)
        gallery_data = repository.list_gallery_items(
            search=search,
            learner_band=learner_band,
            reveal_mode=reveal_mode,
            skill_profile=skill_profile,
            difficulty_minimum=difficulty_minimum,
            difficulty_maximum=difficulty_maximum,
            styling_requested=styling_requested,
            styling_status=styling_status,
            picture_source=picture_source,
            picture_preset=picture_preset,
            seed=seed,
            sort=sort,
            limit=limit,
            offset=offset,
        )
        filters_active = any(
            value not in {None, "", False}
            for value in (
                search,
                learner_band,
                reveal_mode,
                skill_profile,
                styling_status,
                picture_source,
                picture_preset,
                difficulty_minimum,
                difficulty_maximum,
                seed,
                styling_requested,
            )
        )
        return jsonify(
            {
                "items": gallery_data["items"],
                "pagination": gallery_data["pagination"],
                "counts": repository.counts(),
                "empty_state": (
                    "No worksheets match the current filters. Adjust or clear the gallery filters and try again."
                    if filters_active
                    else "No worksheets have been generated yet. Configure one from the left control panel."
                ),
            }
        )

    @app.post("/api/worksheets/generate")
    def generate_worksheet() -> Any:
        payload = request.get_json(silent=True) or {}
        draft_id = int(payload["draft_id"])
        workflow_token = _workflow_token_from_payload(payload)
        learner_band = LearnerBand(str(payload["learner_band"]))
        reveal_mode = str(payload["reveal_mode"])
        try:
            selected_skills = _normalize_selected_skills(
                payload,
                learner_band=learner_band,
                problem_generation_service=problem_generation_service,
                reveal_mode=reveal_mode,
            )
            image_additional_guidance = _normalized_image_additional_guidance(payload.get("image_additional_guidance", ""))
        except ValueError as exc:
            return jsonify({"error": "worksheet_generation_failed", "message": str(exc)}), 400
        difficulty_minimum, difficulty_maximum = _selected_skill_difficulty_bounds(selected_skills)
        parameters = {
            **({"workflow_token": workflow_token} if workflow_token else {}),
            "learner_band": learner_band.value,
            "reveal_mode": reveal_mode,
            "skill_profile": _derived_skill_profile(selected_skills),
            "selected_skills": selected_skills,
            "difficulty_minimum": difficulty_minimum,
            "difficulty_maximum": difficulty_maximum,
            "decoy_percentage": max(0, int(payload.get("decoy_percentage") or 0)),
            "color_picture_source": str(payload.get("color_picture_source") or "preset"),
            "color_picture_preset": str(payload.get("color_picture_preset") or "smile"),
            "theme": str(payload.get("theme", "")).strip() or None,
            "seed": int(payload.get("seed") or 0) or None,
            "apply_image_styling": bool(payload.get("apply_image_styling")) and app.config["GEMINI_ENABLED"],
            "image_style_name": str(payload.get("image_style_name") or DEFAULT_IMAGE_STYLE),
            "image_color_mode": str(payload.get("image_color_mode") or DEFAULT_IMAGE_COLOR_MODE),
            "image_ink_saver": bool(payload.get("image_ink_saver")),
            "image_additional_guidance": image_additional_guidance,
        }
        color_mode_error = _validate_color_by_number_difficulty(
            reveal_mode=str(parameters["reveal_mode"]),
            difficulty_minimum=int(parameters["difficulty_minimum"]),
            difficulty_maximum=int(parameters["difficulty_maximum"]),
        )
        if color_mode_error:
            return jsonify({"error": "worksheet_generation_failed", "message": color_mode_error}), 400
        try:
            draft_record = repository.get_reward_content_draft(draft_id)
        except KeyError:
            return jsonify({"error": "not_found", "message": "Reward content draft not found."}), 404
        if draft_record["approval_state"] != ApprovalState.APPROVED.value:
            return jsonify({"error": "draft_not_approved", "message": "Reward content draft must be approved before worksheet generation."}), 400
        try:
            if not selected_skills:
                problem_generation_service.available_families(learner_band, str(parameters["skill_profile"]))
        except ValueError as exc:
            return jsonify({"error": "worksheet_generation_failed", "message": str(exc)}), 400
        parameters["solution_letter_count"] = _solution_slot_count(str(draft_record["solution_phrase"]))
        if parameters["reveal_mode"] == RevealMode.COLOR_BY_NUMBER.value:
            parameters["decoy_count"] = 0
            parameters["problem_count"] = difficulty_to_color_count(int(parameters["difficulty_maximum"]))
        else:
            parameters["decoy_count"] = _decoy_count(
                solution_letter_count=int(parameters["solution_letter_count"]),
                decoy_percentage=int(parameters["decoy_percentage"]),
                reveal_mode=str(parameters["reveal_mode"]),
            )
            parameters["problem_count"] = int(parameters["solution_letter_count"])
        log_event(
            logger,
            "worksheet_generation_requested",
            verbosity="normal",
            draft_id=draft_id,
            parameters=parameters,
            prompt_text=str(draft_record["prompt_text"]),
            solution_phrase=str(draft_record["solution_phrase"]),
        )

        return jsonify(_enqueue_worksheet_run_from_draft(draft_record=draft_record, draft_id=draft_id, parameters=parameters))

    @app.get("/api/worksheet-runs/<int:worksheet_run_id>")
    def get_worksheet_run(worksheet_run_id: int) -> Any:
        try:
            return jsonify({"worksheet_run": repository.get_worksheet_run(worksheet_run_id)})
        except KeyError:
            return jsonify({"error": "not_found", "message": "Worksheet run not found."}), 404

    @app.post("/api/worksheet-runs/<int:worksheet_run_id>/retry-generation")
    def retry_generation(worksheet_run_id: int) -> Any:
        try:
            worksheet_run = repository.get_worksheet_run(worksheet_run_id)
        except KeyError:
            return jsonify({"error": "not_found", "message": "Worksheet run not found."}), 404
        parameters = dict(worksheet_run["parameters"] or {})
        candidate = RewardContentCandidate(
            prompt_text=str(worksheet_run["prompt_text"]),
            solution_phrase=str(worksheet_run["solution_phrase"]),
            theme=str(worksheet_run["theme"]) if worksheet_run.get("theme") else None,
            source="run_retry",
            approval_state=ApprovalState.APPROVED,
            style="question",
            language="en",
            review_notes=[f"Cloned from worksheet run {worksheet_run_id} for generation retry."],
        )
        retry_draft = repository.create_reward_content_draft(
            candidate=candidate,
            learner_band=str(worksheet_run["learner_band"]),
            generation_parameters={"retry_source_run_id": worksheet_run_id, **parameters},
        )
        log_event(
            logger,
            "worksheet_generation_retry_requested",
            verbosity="normal",
            worksheet_run_id=worksheet_run_id,
            retry_draft_id=retry_draft["id"],
        )
        workflow_token = _workflow_token_from_payload(request.get_json(silent=True) or {})
        if workflow_token:
            _update_workflow_session(
                workflow_token,
                draft_id=int(retry_draft["id"]),
                worksheet_run_id=None,
                generation_job_id=None,
                phase="draft_review",
                status="active",
            )
        return jsonify(
            _enqueue_worksheet_run_from_draft(
                draft_record=retry_draft,
                draft_id=int(retry_draft["id"]),
                parameters={**parameters, "workflow_token": workflow_token} if workflow_token else parameters,
            )
        )

    @app.post("/api/worksheet-runs/<int:worksheet_run_id>/styling-decision")
    def decide_styling(worksheet_run_id: int) -> Any:
        payload = request.get_json(silent=True) or {}
        decision = str(payload.get("decision", "")).strip().lower()
        workflow_token = _workflow_token_from_payload(payload)
        try:
            worksheet_run = repository.get_worksheet_run(worksheet_run_id)
        except KeyError:
            return jsonify({"error": "not_found", "message": "Worksheet run not found."}), 404
        styling = worksheet_run.get("styling") or {}
        if not styling.get("requested"):
            return jsonify({"error": "styling_not_requested", "message": "This worksheet run did not request image styling."}), 400
        if str(styling.get("status")) != "awaiting_confirmation":
            return jsonify(
                {
                    "error": "styling_decision_unavailable",
                    "message": f"Styling decision is only available while awaiting confirmation. Current status: {styling.get('status')}.",
                }
            ), 400
        if decision == "confirm":
            parameters = dict(worksheet_run.get("parameters") or {})
            try:
                parameters["image_additional_guidance"] = _normalized_image_additional_guidance(
                    payload.get("image_additional_guidance", parameters.get("image_additional_guidance", ""))
                )
            except ValueError as exc:
                return jsonify({"error": "invalid_styling_guidance", "message": str(exc)}), 400
            repository.update_worksheet_run_parameters(worksheet_run_id, parameters=parameters)
            repository.update_worksheet_run_styling(
                worksheet_run_id,
                status="confirmed_pending_styling",
                verification_status="pending",
                prompt_text=_styling_prompt_from_run_record(
                    run_record={**worksheet_run, "parameters": parameters},
                    parameters=parameters,
                    style_name=str(styling.get("style_name") or DEFAULT_IMAGE_STYLE),
                    color_mode=str(styling.get("color_mode") or DEFAULT_IMAGE_COLOR_MODE),
                    ink_saver=bool(styling.get("ink_saver")),
                ),
            )
            if workflow_token:
                try:
                    session = repository.get_workflow_session(workflow_token)
                except KeyError:
                    session = None
                if session is not None:
                    _update_workflow_session(
                        workflow_token,
                        controls={**dict(session.get("controls") or {}), "image_additional_guidance": parameters["image_additional_guidance"]},
                    )
            confirmed_run = repository.get_worksheet_run(worksheet_run_id)
            log_event(logger, "worksheet_styling_confirmed", verbosity="normal", worksheet_run_id=worksheet_run_id)
            job_id = _enqueue_styling_job(worksheet_run_id, workflow_token=workflow_token)
            _update_workflow_session(
                workflow_token,
                worksheet_run_id=worksheet_run_id,
                generation_job_id=job_id,
                phase="styling_queued",
                status="active",
            )
            return jsonify(
                {
                    "worksheet_run": confirmed_run,
                    "job": repository.get_generation_job(job_id),
                }
            )
        elif decision == "cancel":
            repository.update_worksheet_run_styling(
                worksheet_run_id,
                status="cancelled_after_plain_review",
                verification_status="not_requested",
            )
            _update_workflow_session(
                workflow_token,
                worksheet_run_id=worksheet_run_id,
                generation_job_id=None,
                phase="styling_cancelled_plain_retained",
                status="completed",
            )
            log_event(logger, "worksheet_styling_cancelled", verbosity="normal", worksheet_run_id=worksheet_run_id)
        else:
            return jsonify({"error": "invalid_decision", "message": "Decision must be 'confirm' or 'cancel'."}), 400
        return jsonify({"worksheet_run": repository.get_worksheet_run(worksheet_run_id), "job": None})

    @app.post("/api/worksheet-runs/<int:worksheet_run_id>/styling-retry")
    def retry_styling(worksheet_run_id: int) -> Any:
        payload = request.get_json(silent=True) or {}
        workflow_token = _workflow_token_from_payload(payload)
        try:
            worksheet_run = repository.get_worksheet_run(worksheet_run_id)
        except KeyError:
            return jsonify({"error": "not_found", "message": "Worksheet run not found."}), 404
        styling = worksheet_run.get("styling") or {}
        current_status = str(styling.get("status") or "not_requested")
        if not styling.get("requested"):
            return jsonify({"error": "styling_not_requested", "message": "This worksheet run did not request image styling."}), 400
        if current_status not in STYLING_RETRYABLE_STATUSES:
            return jsonify(
                {
                    "error": "styling_retry_unavailable",
                    "message": f"Styling retry is only available after a retryable styling failure. Current status: {current_status}.",
                }
            ), 400
        repository.update_worksheet_run_styling(
            worksheet_run_id,
            status="retry_pending_styling",
            verification_status="pending",
        )
        log_event(logger, "worksheet_styling_retry_requested", verbosity="normal", worksheet_run_id=worksheet_run_id)
        job_id = _enqueue_styling_job(worksheet_run_id, retry=True, workflow_token=workflow_token)
        _update_workflow_session(
            workflow_token,
            worksheet_run_id=worksheet_run_id,
            generation_job_id=job_id,
            phase="styling_retry_queued",
            status="active",
        )
        return jsonify({"worksheet_run": repository.get_worksheet_run(worksheet_run_id), "job": repository.get_generation_job(job_id)})

    @app.get("/api/jobs/<int:job_id>")
    def get_generation_job(job_id: int) -> Any:
        wait_seconds = min(max(float(request.args.get("wait_seconds", "0") or 0), 0), 15)
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                job = repository.get_generation_job(job_id)
            except KeyError:
                return jsonify({"error": "not_found", "message": "Generation job not found."}), 404
            if job["status"] in {"completed", "failed", "cancelled"} or time.monotonic() >= deadline:
                return jsonify({"job": job})
            time.sleep(0.25)

    @app.post("/api/jobs/<int:job_id>/cancel")
    def cancel_generation_job(job_id: int) -> Any:
        try:
            job = repository.get_generation_job(job_id)
        except KeyError:
            return jsonify({"error": "not_found", "message": "Generation job not found."}), 404

        if job["status"] in {"completed", "failed", "cancelled"}:
            return jsonify({"job": job, "worksheet_run": repository.get_worksheet_run(job["worksheet_run_id"]) if job.get("worksheet_run_id") else None})

        if job["job_type"] == "worksheet_style" and not _styling_job_can_be_cancelled(job):
            return (
                jsonify(
                    {
                        "error": "styling_not_cancellable",
                        "message": "Styling can only be cancelled before the Gemini request is sent.",
                        "job": job,
                        "worksheet_run": repository.get_worksheet_run(job["worksheet_run_id"]) if job.get("worksheet_run_id") else None,
                    }
                ),
                409,
            )

        message = "Generation job cancelled by user."
        repository.cancel_generation_job(job_id, progress_message=message)
        workflow_token = str(job["requested_parameters"].get("workflow_token") or "").strip() or None
        run_payload = None
        worksheet_run_id = int(job["worksheet_run_id"]) if job.get("worksheet_run_id") is not None else None
        if worksheet_run_id is not None:
            if job["job_type"] == "worksheet_generate":
                repository.cancel_worksheet_run(
                    worksheet_run_id,
                    message="Worksheet generation was cancelled. Already-written artifacts were preserved on disk.",
                )
                _update_workflow_session(
                    workflow_token,
                    worksheet_run_id=worksheet_run_id,
                    generation_job_id=job_id,
                    phase="run_cancelled",
                    status="cancelled",
                )
            elif job["job_type"] == "worksheet_style":
                repository.update_worksheet_run_styling(
                    worksheet_run_id,
                    status="cancelled_during_styling",
                    verification_status="not_requested",
                )
                _update_workflow_session(
                    workflow_token,
                    worksheet_run_id=worksheet_run_id,
                    generation_job_id=job_id,
                    phase="styling_cancelled_plain_retained",
                    status="cancelled",
                )
            run_payload = repository.get_worksheet_run(worksheet_run_id)
        log_event(
            logger,
            "generation_job_cancelled",
            verbosity="normal",
            job_id=job_id,
            worksheet_run_id=worksheet_run_id,
            job_type=job["job_type"],
        )
        return jsonify({"job": repository.get_generation_job(job_id), "worksheet_run": run_payload})

    @app.post("/api/reward-content/generate")
    def generate_reward_content() -> Any:
        if not app.config["GEMINI_ENABLED"] or reward_content_service is None:
            return (
                jsonify(
                    {
                        "error": "gemini_unavailable",
                        "message": "Gemini-assisted reward content generation is disabled because GEMINI_API_KEY is not set.",
                    }
                ),
                503,
            )

        payload = request.get_json(silent=True) or {}
        workflow_token = _workflow_token_from_payload(payload)
        learner_band = LearnerBand(str(payload["learner_band"]))
        generation_request = RewardContentGenerationRequest(
            theme=str(payload["theme"]).strip(),
            learner_band=learner_band,
            preferred_style=str(payload.get("style", "riddle")).strip() or "riddle",
            language=str(payload.get("language", "en")).strip() or "en",
            reveal_mode=str(payload.get("reveal_mode", RevealMode.LETTER_BANK.value)).strip() or RevealMode.LETTER_BANK.value,
            color_picture_source=str(payload.get("color_picture_source", "preset")).strip() or "preset",
            color_picture_preset=str(payload.get("color_picture_preset", "smile")).strip() or "smile",
            solution_length_guidance=_solution_length_guidance(
                learner_band=learner_band,
                difficulty_maximum=int(payload.get("difficulty_maximum") or 2),
            ),
        )
        generation_parameters = {
            "theme": generation_request.theme,
            "learner_band": generation_request.learner_band.value,
            "style": generation_request.preferred_style,
            "language": generation_request.language,
            "reveal_mode": generation_request.reveal_mode,
            "color_picture_source": generation_request.color_picture_source,
            "color_picture_preset": generation_request.color_picture_preset,
            "difficulty_maximum": int(payload.get("difficulty_maximum") or 2),
            "solution_length_guidance": generation_request.solution_length_guidance,
        }
        log_event(logger, "reward_content_generate_requested", verbosity="normal", request=generation_parameters)
        job_id = repository.create_generation_job(
            job_type="reward_content_generate",
            requested_parameters={**generation_parameters, **({"workflow_token": workflow_token} if workflow_token else {})},
            progress_message="Generating Gemini reward content draft.",
            phase="draft_generation_requested",
        )
        try:
            repository.update_generation_job(job_id, status="running", phase="draft_generation_running")
            _update_workflow_session(
                workflow_token,
                phase="draft_generation_running",
                generation_job_id=job_id,
                status="active",
            )
            candidate = reward_content_service.generate_candidate(generation_request)
            draft = repository.create_reward_content_draft(
                candidate=candidate,
                learner_band=learner_band.value,
                generation_parameters=generation_parameters,
            )
            repository.complete_generation_job(job_id, progress_message="Reward content draft generated.")
            _update_workflow_session(
                workflow_token,
                phase="draft_review",
                draft_id=int(draft["id"]),
                generation_job_id=job_id,
                status="active",
            )
            log_event(
                logger,
                "reward_content_generate_completed",
                verbosity="normal",
                draft_id=draft["id"],
                job_id=job_id,
                prompt_text=draft["prompt_text"],
                solution_phrase=draft["solution_phrase"],
            )
            return jsonify({"draft": draft, "job": {"id": job_id, "status": "completed"}})
        except (GeminiGenerationError, RewardContentValidationError) as exc:
            repository.fail_generation_job(job_id, progress_message=str(exc))
            _update_workflow_session(
                workflow_token,
                phase="draft_generation_failed",
                generation_job_id=job_id,
                status="failed",
            )
            log_event(logger, "reward_content_generate_failed", job_id=job_id, error=str(exc))
            return jsonify({"error": "gemini_generation_failed", "message": str(exc), "job": {"id": job_id, "status": "failed"}}), 502

    @app.post("/api/reward-content/direct")
    def create_direct_reward_content() -> Any:
        if reward_content_service is None:
            return jsonify({"error": "service_unavailable", "message": "Reward content service is not configured."}), 503

        payload = request.get_json(force=True, silent=False) or {}
        workflow_token = _workflow_token_from_payload(payload)
        learner_band = LearnerBand(str(payload["learner_band"]))
        try:
            generation_parameters = _reward_generation_parameters_from_payload(payload, learner_band=learner_band)
        except ValueError as exc:
            return jsonify({"error": "reward_content_invalid", "message": str(exc)}), 400
        log_event(logger, "reward_content_direct_requested", verbosity="normal", request=generation_parameters)
        try:
            candidate = reward_content_service.create_direct_candidate(
                learner_band=learner_band,
                prompt_text=str(payload["prompt_text"]),
                solution_phrase=str(payload["solution_phrase"]),
                theme=str(payload.get("theme", "")).strip() or None,
                style=str(payload.get("style", "riddle")).strip() or "riddle",
                language=str(payload.get("language", "en")).strip() or "en",
            )
            draft = repository.create_reward_content_draft(
                candidate=candidate,
                learner_band=learner_band.value,
                generation_parameters=generation_parameters,
            )
            _update_workflow_session(
                workflow_token,
                phase="manual_review",
                draft_id=int(draft["id"]),
                generation_job_id=None,
                status="active",
            )
            log_event(
                logger,
                "reward_content_direct_completed",
                verbosity="normal",
                draft_id=draft["id"],
                prompt_text=draft["prompt_text"],
                solution_phrase=draft["solution_phrase"],
            )
            return jsonify({"draft": draft})
        except RewardContentValidationError as exc:
            log_event(logger, "reward_content_direct_failed", error=str(exc))
            return jsonify({"error": "reward_content_invalid", "message": str(exc)}), 400

    @app.post("/api/reward-content/<int:draft_id>/regenerate")
    def regenerate_reward_content(draft_id: int) -> Any:
        if not app.config["GEMINI_ENABLED"] or reward_content_service is None:
            return jsonify({"error": "gemini_unavailable", "message": "Gemini-assisted generation is disabled."}), 503
        try:
            existing = repository.get_reward_content_draft(draft_id)
        except KeyError:
            return jsonify({"error": "not_found", "message": "Reward content draft not found."}), 404

        payload = request.get_json(force=True, silent=False) or {}
        workflow_token = _workflow_token_from_payload(payload)
        try:
            params = _merged_reward_generation_parameters(existing["generation_parameters"], payload)
        except ValueError as exc:
            return jsonify({"error": "reward_content_invalid", "message": str(exc)}), 400
        log_event(logger, "reward_content_regenerate_requested", verbosity="normal", draft_id=draft_id, request=params)
        learner_band = LearnerBand(str(params["learner_band"]))
        generation_request = RewardContentGenerationRequest(
            theme=str(params["theme"]),
            learner_band=learner_band,
            preferred_style=str(params.get("style", "riddle")),
            language=str(params.get("language", "en")),
            reveal_mode=str(params.get("reveal_mode", RevealMode.LETTER_BANK.value)),
            color_picture_source=str(params.get("color_picture_source", "preset")),
            color_picture_preset=str(params.get("color_picture_preset", "smile")),
            solution_length_guidance=str(params.get("solution_length_guidance") or ""),
        )
        job_id = repository.create_generation_job(
            job_type="reward_content_regenerate",
            requested_parameters={**params, **({"workflow_token": workflow_token} if workflow_token else {})},
            progress_message=f"Regenerating reward content draft {draft_id}.",
            phase="draft_regeneration_requested",
        )
        try:
            repository.update_generation_job(job_id, status="running", phase="draft_regeneration_running")
            _update_workflow_session(
                workflow_token,
                phase="draft_regeneration_running",
                draft_id=draft_id,
                generation_job_id=job_id,
                status="active",
            )
            candidate = reward_content_service.generate_candidate(generation_request)
            candidate.review_notes.append("Regenerated from Gemini request.")
            draft = repository.update_reward_content_draft(
                draft_id,
                candidate=candidate,
                generation_parameters=params,
                learner_band=learner_band.value,
            )
            repository.complete_generation_job(job_id, progress_message=f"Reward content draft {draft_id} regenerated.")
            _update_workflow_session(
                workflow_token,
                phase="draft_review",
                draft_id=draft_id,
                generation_job_id=job_id,
                status="active",
            )
            log_event(
                logger,
                "reward_content_regenerate_completed",
                verbosity="normal",
                draft_id=draft_id,
                job_id=job_id,
                prompt_text=draft["prompt_text"],
                solution_phrase=draft["solution_phrase"],
            )
            return jsonify({"draft": draft, "job": {"id": job_id, "status": "completed"}})
        except (GeminiGenerationError, RewardContentValidationError) as exc:
            repository.fail_generation_job(job_id, progress_message=str(exc))
            _update_workflow_session(
                workflow_token,
                phase="draft_regeneration_failed",
                draft_id=draft_id,
                generation_job_id=job_id,
                status="failed",
            )
            log_event(logger, "reward_content_regenerate_failed", draft_id=draft_id, job_id=job_id, error=str(exc))
            return jsonify({"error": "gemini_generation_failed", "message": str(exc), "job": {"id": job_id, "status": "failed"}}), 502

    @app.post("/api/reward-content/<int:draft_id>/regenerate-from-solution")
    def regenerate_reward_content_from_solution(draft_id: int) -> Any:
        if not app.config["GEMINI_ENABLED"] or reward_content_service is None:
            return jsonify({"error": "gemini_unavailable", "message": "Gemini-assisted generation is disabled."}), 503
        try:
            existing = repository.get_reward_content_draft(draft_id)
        except KeyError:
            return jsonify({"error": "not_found", "message": "Reward content draft not found."}), 404

        payload = request.get_json(force=True, silent=False) or {}
        workflow_token = _workflow_token_from_payload(payload)
        try:
            params = _merged_reward_generation_parameters(existing["generation_parameters"], payload)
        except ValueError as exc:
            return jsonify({"error": "reward_content_invalid", "message": str(exc)}), 400
        log_event(
            logger,
            "reward_content_solution_regenerate_requested",
            verbosity="normal",
            draft_id=draft_id,
            request=params,
            requested_solution_phrase=str(payload.get("solution_phrase", "")),
        )
        learner_band = LearnerBand(str(params["learner_band"]))
        generation_request = RewardContentGenerationRequest(
            theme=str(params["theme"]),
            learner_band=learner_band,
            preferred_style=str(params.get("style", "riddle")),
            language=str(params.get("language", "en")),
            reveal_mode=str(params.get("reveal_mode", RevealMode.LETTER_BANK.value)),
            color_picture_source=str(params.get("color_picture_source", "preset")),
            color_picture_preset=str(params.get("color_picture_preset", "smile")),
            solution_length_guidance=str(params.get("solution_length_guidance") or ""),
        )
        candidate = _candidate_from_record(existing)
        candidate = replace(
            candidate,
            theme=str(params.get("theme", candidate.theme or "")).strip() or None,
            style=str(params.get("style", candidate.style or "riddle")).strip() or "riddle",
            language=str(params.get("language", candidate.language)).strip() or candidate.language,
        )
        try:
            normalized_candidate = reward_content_service.edit_candidate(
                candidate,
                learner_band,
                prompt_text=candidate.prompt_text,
                solution_phrase=str(payload.get("solution_phrase", "")).strip(),
            )
        except RewardContentValidationError as exc:
            return jsonify({"error": "reward_content_invalid", "message": str(exc)}), 400

        job_id = repository.create_generation_job(
            job_type="reward_content_regenerate_from_solution",
            requested_parameters={**params, "solution_phrase": normalized_candidate.solution_phrase, **({"workflow_token": workflow_token} if workflow_token else {})},
            progress_message=f"Generating a new clue for reward content draft {draft_id}.",
            phase="draft_regeneration_requested",
        )
        try:
            repository.update_generation_job(job_id, status="running", phase="draft_regeneration_running")
            _update_workflow_session(
                workflow_token,
                phase="draft_regeneration_running",
                draft_id=draft_id,
                generation_job_id=job_id,
                status="active",
            )
            regenerated_candidate = reward_content_service.generate_candidate_for_solution(
                generation_request,
                normalized_candidate.solution_phrase,
            )
            regenerated_candidate.review_notes.append("Regenerated from a user-edited solution phrase.")
            draft = repository.update_reward_content_draft(
                draft_id,
                candidate=regenerated_candidate,
                generation_parameters={**params, "solution_phrase": normalized_candidate.solution_phrase},
                learner_band=learner_band.value,
            )
            repository.complete_generation_job(job_id, progress_message=f"Reward content draft {draft_id} regenerated from the edited solution.")
            _update_workflow_session(
                workflow_token,
                phase="draft_review",
                draft_id=draft_id,
                generation_job_id=job_id,
                status="active",
            )
            log_event(
                logger,
                "reward_content_solution_regenerate_completed",
                verbosity="normal",
                draft_id=draft_id,
                job_id=job_id,
                prompt_text=draft["prompt_text"],
                solution_phrase=draft["solution_phrase"],
            )
            return jsonify({"draft": draft, "job": {"id": job_id, "status": "completed"}})
        except (GeminiGenerationError, RewardContentValidationError) as exc:
            repository.fail_generation_job(job_id, progress_message=str(exc))
            _update_workflow_session(
                workflow_token,
                phase="draft_regeneration_failed",
                draft_id=draft_id,
                generation_job_id=job_id,
                status="failed",
            )
            log_event(logger, "reward_content_solution_regenerate_failed", draft_id=draft_id, job_id=job_id, error=str(exc))
            return jsonify({"error": "gemini_generation_failed", "message": str(exc), "job": {"id": job_id, "status": "failed"}}), 502

    @app.patch("/api/reward-content/<int:draft_id>")
    def edit_reward_content(draft_id: int) -> Any:
        payload = request.get_json(force=True, silent=False) or {}
        workflow_token = _workflow_token_from_payload(payload)
        try:
            draft_record = repository.get_reward_content_draft(draft_id)
        except KeyError:
            return jsonify({"error": "not_found", "message": "Reward content draft not found."}), 404
        candidate = _candidate_from_record(draft_record)
        if reward_content_service is None:
            return jsonify({"error": "service_unavailable", "message": "Reward content service is not configured."}), 503
        learner_band = LearnerBand(str(payload.get("learner_band") or draft_record["learner_band"]))
        candidate = replace(
            candidate,
            theme=str(payload.get("theme", candidate.theme or "")).strip() or None,
            style=str(payload.get("style", candidate.style or "riddle")).strip() or "riddle",
            language=str(payload.get("language", candidate.language)).strip() or candidate.language,
        )
        try:
            updated_candidate = reward_content_service.edit_candidate(
                candidate,
                learner_band,
                prompt_text=str(payload.get("prompt_text", candidate.prompt_text)).strip(),
                solution_phrase=str(payload.get("solution_phrase", candidate.solution_phrase)).strip(),
            )
        except RewardContentValidationError as exc:
            return jsonify({"error": "reward_content_invalid", "message": str(exc)}), 400
        generation_parameters = _merged_reward_generation_parameters(draft_record["generation_parameters"], payload, learner_band=learner_band)
        draft = repository.update_reward_content_draft(
            draft_id,
            candidate=updated_candidate,
            generation_parameters=generation_parameters,
            learner_band=learner_band.value,
        )
        log_event(
            logger,
            "reward_content_edited",
            verbosity="normal",
            draft_id=draft_id,
            prompt_text=draft["prompt_text"],
            solution_phrase=draft["solution_phrase"],
        )
        _update_workflow_session(
            workflow_token,
            phase="draft_review",
            draft_id=draft_id,
            status="active",
        )
        return jsonify({"draft": draft})

    @app.post("/api/reward-content/<int:draft_id>/reject")
    def reject_reward_content(draft_id: int) -> Any:
        try:
            draft_record = repository.get_reward_content_draft(draft_id)
        except KeyError:
            return jsonify({"error": "not_found", "message": "Reward content draft not found."}), 404
        candidate = _candidate_from_record(draft_record)
        if reward_content_service is None:
            return jsonify({"error": "service_unavailable", "message": "Reward content service is not configured."}), 503
        reason = str((request.get_json(silent=True) or {}).get("reason", "")).strip() or "Rejected from review pane."
        rejected_candidate = reward_content_service.reject_candidate(candidate, reason)
        draft = repository.update_reward_content_draft(draft_id, candidate=rejected_candidate)
        log_event(logger, "reward_content_rejected", draft_id=draft_id, reason=reason)
        return jsonify({"draft": draft})

    @app.post("/api/reward-content/<int:draft_id>/approve")
    def approve_reward_content(draft_id: int) -> Any:
        try:
            draft_record = repository.get_reward_content_draft(draft_id)
        except KeyError:
            return jsonify({"error": "not_found", "message": "Reward content draft not found."}), 404
        candidate = _candidate_from_record(draft_record)
        if reward_content_service is None:
            return jsonify({"error": "service_unavailable", "message": "Reward content service is not configured."}), 503
        payload = request.get_json(silent=True) or {}
        workflow_token = _workflow_token_from_payload(payload)
        learner_band = LearnerBand(str(payload.get("learner_band") or draft_record["learner_band"]))
        try:
            approved = reward_content_service.approve_candidate(candidate, learner_band)
        except (RewardContentApprovalError, RewardContentStateError, RewardContentValidationError) as exc:
            return jsonify({"error": "approval_failed", "message": str(exc)}), 400

        approved_candidate = RewardContentCandidate(
            prompt_text=approved.prompt_text,
            solution_phrase=approved.solution_phrase,
            theme=approved.theme,
            source=approved.source,
            approval_state=ApprovalState.APPROVED,
            style=approved.style,
            language=approved.language,
            reading_level_assessment=approved.reading_level_assessment,
            review_notes=approved.review_notes,
        )
        generation_parameters = _merged_reward_generation_parameters(draft_record["generation_parameters"], payload, learner_band=learner_band)
        draft = repository.update_reward_content_draft(
            draft_id,
            candidate=approved_candidate,
            generation_parameters=generation_parameters,
            learner_band=learner_band.value,
        )
        _update_workflow_session(
            workflow_token,
            phase="draft_approved",
            draft_id=draft_id,
            status="active",
        )
        log_event(logger, "reward_content_approved", verbosity="normal", draft_id=draft_id, learner_band=learner_band.value)
        return jsonify({"draft": draft})

    @app.get("/api/reward-content/<int:draft_id>")
    def get_reward_content_draft(draft_id: int) -> Any:
        try:
            return jsonify({"draft": repository.get_reward_content_draft(draft_id)})
        except KeyError:
            return jsonify({"error": "not_found", "message": "Reward content draft not found."}), 404

    @app.get("/api/health")
    def health() -> Any:
        return jsonify(
            {
                "status": "ok",
                "database_path": app.config["APP_DB_PATH"],
                "artifact_root": app.config["APP_ARTIFACT_ROOT"],
                "gemini_enabled": app.config["GEMINI_ENABLED"],
            }
        )

    @app.get("/artifacts/<path:relative_path>")
    def artifacts(relative_path: str) -> Any:
        return send_from_directory(paths.artifact_root, relative_path, as_attachment=False)

    @app.get("/api/artifacts/<int:artifact_id>/download")
    def download_artifact(artifact_id: int) -> Any:
        try:
            artifact = repository.get_artifact(artifact_id)
        except KeyError:
            return jsonify({"error": "not_found", "message": "Artifact not found."}), 404
        relative_path = str(artifact["relative_path"])
        file_path = paths.artifact_root / relative_path
        if not file_path.exists():
            return jsonify({"error": "not_found", "message": "Artifact file not found on disk."}), 404
        parent = file_path.parent.relative_to(paths.artifact_root)
        return send_from_directory(
            paths.artifact_root / parent,
            file_path.name,
            as_attachment=True,
            download_name=_artifact_download_name(artifact),
        )

    return app


def _candidate_from_record(draft_record: dict[str, object]) -> RewardContentCandidate:
    assessment_payload = draft_record.get("reading_level_assessment")
    assessment = None
    if assessment_payload:
        from ..models import ReadingLevelAssessment

        assessment = ReadingLevelAssessment(
            learner_band=LearnerBand(str(assessment_payload["learner_band"])),
            passed=bool(assessment_payload["passed"]),
            word_count=int(assessment_payload["word_count"]),
            sentence_count=int(assessment_payload["sentence_count"]),
            long_word_count=int(assessment_payload["long_word_count"]),
            flagged_terms=list(assessment_payload.get("flagged_terms", [])),
            notes=list(assessment_payload.get("notes", [])),
        )
    return RewardContentCandidate(
        prompt_text=str(draft_record["prompt_text"]),
        solution_phrase=str(draft_record["solution_phrase"]),
        theme=str(draft_record["theme"]) if draft_record.get("theme") is not None else None,
        source=str(draft_record["source"]),
        approval_state=ApprovalState(str(draft_record["approval_state"])),
        style=str(draft_record["style"]) if draft_record.get("style") is not None else None,
        language=str(draft_record["language"]),
        reading_level_assessment=assessment,
        review_notes=list(draft_record.get("review_notes", [])),
    )


def _worksheet_title(parameters: dict[str, object], draft_record: dict[str, object]) -> str:
    return _rendered_worksheet_title(str(parameters["skill_profile"]))


def _rendered_worksheet_title(skill_profile: str) -> str:
    return f"{skill_profile_title(skill_profile)} Worksheet"


def _download_slug_part(value: str | None, fallback: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        raw = fallback
    normalized = re.sub(r"[^a-z0-9]+", "_", raw)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or fallback


def _artifact_download_name(artifact: dict[str, object]) -> str:
    learner_band = _download_slug_part(str(artifact.get("learner_band") or ""), "worksheet")
    skill_profile = _download_slug_part(str(artifact.get("skill_profile") or ""), "worksheet")
    theme = _download_slug_part(str(artifact.get("theme") or ""), "worksheet")
    extension = _download_slug_part(str(artifact.get("output_format") or ""), "bin")
    return f"{learner_band}_{skill_profile}_{theme}.{extension}"


def _solution_length_guidance(*, learner_band: LearnerBand, difficulty_maximum: int) -> str:
    if difficulty_maximum >= 3 or learner_band in {LearnerBand.PRE_ALGEBRA, LearnerBand.ALGEBRA, LearnerBand.GEOMETRY}:
        return "Prefer a multi-word answer phrase with 2 to 4 words so advanced worksheets reveal longer answers."
    return "Prefer a short single-word answer or a very short phrase."


def _solution_slot_count(solution_phrase: str) -> int:
    return solution_slot_count(solution_phrase)


def _decoy_count(*, solution_letter_count: int, decoy_percentage: int, reveal_mode: str) -> int:
    if reveal_mode != RevealMode.LETTER_BANK.value:
        return 0
    return max(0, round((solution_letter_count * decoy_percentage) / 100))


def _validate_color_by_number_difficulty(*, reveal_mode: str, difficulty_minimum: int, difficulty_maximum: int) -> str | None:
    if reveal_mode != RevealMode.COLOR_BY_NUMBER.value:
        return None
    if difficulty_minimum > COLOR_BY_NUMBER_MAX_DIFFICULTY or difficulty_maximum > COLOR_BY_NUMBER_MAX_DIFFICULTY:
        return "Color-by-number worksheets are limited to difficulty 1 or 2 so the answer values fit inside the grid."
    return None


def _supported_skill_profiles(problem_generation_service: ProblemGenerationService) -> list[dict[str, object]]:
    profiles: list[dict[str, object]] = []
    for profile in SKILL_PROFILES:
        supported_bands = [
            learner_band.value
            for learner_band in LearnerBand
            if _skill_profile_supported(problem_generation_service, learner_band, str(profile["value"]))
        ]
        profiles.append({**profile, "supported_learner_bands": supported_bands})
    return profiles


def _skill_profile_supported(
    problem_generation_service: ProblemGenerationService,
    learner_band: LearnerBand,
    skill_profile: str,
) -> bool:
    try:
        problem_generation_service.available_families(learner_band, skill_profile)
    except ValueError:
        return False
    return True


def _normalized_image_additional_guidance(value: object) -> str:
    guidance = str(value or "").strip()
    if len(guidance) > MAX_IMAGE_ADDITIONAL_GUIDANCE_LENGTH:
        raise ValueError(
            f"Additional style guidance must be {MAX_IMAGE_ADDITIONAL_GUIDANCE_LENGTH} characters or fewer."
        )
    return guidance


def _reward_generation_parameters_from_payload(payload: dict[str, object], *, learner_band: LearnerBand) -> dict[str, object]:
    difficulty_maximum = int(payload.get("difficulty_maximum") or 2)
    reveal_mode = str(payload.get("reveal_mode", RevealMode.LETTER_BANK.value)).strip() or RevealMode.LETTER_BANK.value
    if reveal_mode == RevealMode.COLOR_BY_NUMBER.value:
        difficulty_maximum = min(difficulty_maximum, COLOR_BY_NUMBER_MAX_DIFFICULTY)
    return {
        "theme": str(payload.get("theme", "")).strip(),
        "learner_band": learner_band.value,
        "style": str(payload.get("style", "riddle")).strip() or "riddle",
        "language": str(payload.get("language", "en")).strip() or "en",
        "reveal_mode": reveal_mode,
        "color_picture_source": str(payload.get("color_picture_source", "preset")).strip() or "preset",
        "color_picture_preset": str(payload.get("color_picture_preset", "smile")).strip() or "smile",
        "apply_image_styling": bool(payload.get("apply_image_styling")),
        "image_style_name": str(payload.get("image_style_name", DEFAULT_IMAGE_STYLE)).strip() or DEFAULT_IMAGE_STYLE,
        "image_color_mode": str(payload.get("image_color_mode", DEFAULT_IMAGE_COLOR_MODE)).strip() or DEFAULT_IMAGE_COLOR_MODE,
        "image_ink_saver": bool(payload.get("image_ink_saver")),
        "image_additional_guidance": _normalized_image_additional_guidance(payload.get("image_additional_guidance", "")),
        "difficulty_maximum": difficulty_maximum,
        "solution_length_guidance": _solution_length_guidance(
            learner_band=learner_band,
            difficulty_maximum=difficulty_maximum,
        ),
    }


def _merged_reward_generation_parameters(
    existing: dict[str, object],
    payload: dict[str, object],
    *,
    learner_band: LearnerBand | None = None,
) -> dict[str, object]:
    band = learner_band or LearnerBand(str(payload.get("learner_band") or existing["learner_band"]))
    merged = dict(existing)
    merged.update(_reward_generation_parameters_from_payload({**existing, **payload}, learner_band=band))
    return merged


def _styling_request_from_parameters(
    *,
    parameters: dict[str, object],
    draft_record: dict[str, object],
    gemini_enabled: bool,
    gemini_image_model: str,
) -> dict[str, object]:
    requested = bool(parameters.get("apply_image_styling")) and gemini_enabled
    return {
        "requested": requested,
        "style_name": str(parameters.get("image_style_name") or DEFAULT_IMAGE_STYLE),
        "color_mode": str(parameters.get("image_color_mode") or DEFAULT_IMAGE_COLOR_MODE),
        "ink_saver": bool(parameters.get("image_ink_saver")),
        "additional_guidance": str(parameters.get("image_additional_guidance") or "").strip(),
        "status": "awaiting_confirmation" if requested else "not_requested",
        "verification_status": "pending_confirmation" if requested else "not_requested",
        "model": gemini_image_model if requested else None,
        "prompt_text": (
            build_worksheet_styling_prompt(
                WorksheetImageStylingPromptRequest(
                    theme=str(parameters.get("theme") or "general classroom math"),
                    style_name=str(parameters.get("image_style_name") or DEFAULT_IMAGE_STYLE),
                    color_mode=str(parameters.get("image_color_mode") or DEFAULT_IMAGE_COLOR_MODE),
                    ink_saver=bool(parameters.get("image_ink_saver")),
                    additional_guidance=str(parameters.get("image_additional_guidance") or "").strip(),
                    title=_worksheet_title(parameters, draft_record),
                    prompt_text=str(draft_record.get("prompt_text") or "Decorate around the existing worksheet content without changing it."),
                    learner_band_label=str(parameters.get("learner_band") or "").replace("_", " ").title() or "Worksheet",
                    reveal_mode_label=str(parameters.get("reveal_mode") or "").replace("_", " ").title() or "Worksheet",
                )
            )
            if requested
            else None
        ),
        "styled_artifact_group": None,
        "styled_thumbnail_path": None,
        "style_check_artifact_path": None,
    }


def _styling_prompt_from_generated_worksheet(
    *,
    worksheet,
    parameters: dict[str, object],
    style_name: str,
    color_mode: str,
    ink_saver: bool,
) -> str:
    return build_worksheet_styling_prompt(
        WorksheetImageStylingPromptRequest(
            theme=str(parameters.get("theme") or worksheet.reward_content.theme or "general classroom math"),
            style_name=style_name,
            color_mode=color_mode,
            ink_saver=ink_saver,
            additional_guidance=str(parameters.get("image_additional_guidance") or "").strip(),
            title=_rendered_worksheet_title(worksheet.spec.skill_profile),
            prompt_text=worksheet.reward_content.prompt_text,
            learner_band_label=worksheet.spec.learner_band.value.replace("_", " ").title(),
            reveal_mode_label=worksheet.spec.reveal_mode.value.replace("_", " ").title(),
        )
    )


def _styling_prompt_from_run_record(
    *,
    run_record: dict[str, object],
    parameters: dict[str, object],
    style_name: str,
    color_mode: str,
    ink_saver: bool,
) -> str:
    return build_worksheet_styling_prompt(
        WorksheetImageStylingPromptRequest(
            theme=str(parameters.get("theme") or run_record.get("theme") or "general classroom math"),
            style_name=style_name,
            color_mode=color_mode,
            ink_saver=ink_saver,
            additional_guidance=str(parameters.get("image_additional_guidance") or "").strip(),
            title=str(run_record.get("title") or "Worksheet"),
            prompt_text=str(run_record.get("prompt_text") or "Decorate around the existing worksheet content without changing it."),
            learner_band_label=str(run_record.get("learner_band") or "").replace("_", " ").title() or "Worksheet",
            reveal_mode_label=str(run_record.get("reveal_mode") or "").replace("_", " ").title() or "Worksheet",
        )
    )
