from __future__ import annotations

from pathlib import Path
import sqlite3
import time
from uuid import UUID

from worksheet_generator.models import ApprovalState, LearnerBand, RewardContentCandidate
from worksheet_generator.manifest import read_worksheet_manifest
from worksheet_generator.reward_content_generation import RewardContentGenerationRequest
from worksheet_generator.reward_content_service import RewardContentService
from worksheet_generator.webapp.app import create_app
from worksheet_generator.webapp.generation_service import COLOR_SEQUENCE
from worksheet_generator.webapp.generation_service import WorksheetRunCancelledError, WorksheetRunGenerationService
from worksheet_generator.webapp.repository import AppRepository


def wait_for_job_completion(client, job_id: int, *, timeout: float = 5.0) -> dict[str, object]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job_response = client.get(f"/api/jobs/{job_id}?wait_seconds=0.1")
        assert job_response.status_code == 200
        job = job_response.get_json()["job"]
        if job["status"] in {"completed", "failed", "cancelled"}:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not complete within {timeout} seconds")


def test_webapp_bootstraps_storage_and_reports_disabled_gemini(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "db" / "app.sqlite3"
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("APP_DB_PATH", str(database_path))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    app = create_app()
    client = app.test_client()

    config_response = client.get("/api/app-config")
    gallery_response = client.get("/api/gallery")
    health_response = client.get("/api/health")
    page_response = client.get("/")

    assert config_response.status_code == 200
    assert gallery_response.status_code == 200
    assert health_response.status_code == 200
    assert page_response.status_code == 200
    assert database_path.exists() is True
    assert artifact_root.exists() is True
    assert config_response.get_json()["gemini"]["enabled"] is False
    assert config_response.get_json()["worksheet_options"]["image_styling"]["enabled"] is False
    assert config_response.get_json()["ui"]["debug_enabled"] is False
    assert config_response.get_json()["ui"]["mode"] == "customer"
    assert config_response.get_json()["maintenance"]["enabled"] is False
    assert config_response.get_json()["job_tracking"]["transport"] == "durable_queue_polling"
    assert config_response.get_json()["job_tracking"]["worker_enabled"] is True
    assert "worksheet_generation_queued" in config_response.get_json()["job_tracking"]["job_phase_catalog"]
    assert "awaiting_styling_confirmation" in config_response.get_json()["job_tracking"]["run_phase_catalog"]
    assert gallery_response.get_json()["counts"]["worksheet_runs"] == 0
    page_text = page_response.get_data(as_text=True)
    assert 'id="worksheet-config-form"' in page_text
    assert 'id="workflow-modal"' in page_text
    assert 'id="apply-image-styling"' in page_text
    assert 'id="image-additional-guidance"' in page_text
    assert 'id="workflow-image-additional-guidance"' in page_text
    assert 'id="modal-image-additional-guidance"' in page_text
    assert 'id="workflow-image-guidance-preview"' in page_text
    assert 'id="workflow-image-guidance-count"' in page_text
    assert 'id="modal-image-guidance-preview"' in page_text
    assert 'id="modal-image-guidance-count"' in page_text
    assert 'id="image-additional-guidance-count"' in page_text
    assert page_text.count('maxlength="500"') >= 3
    assert 'maxlength="300"' not in page_text
    assert 'id="workflow-phase-list"' in page_text
    assert 'id="workflow-config-section"' in page_text
    assert 'id="workflow-regenerate-from-solution-button"' in page_text
    assert 'id="workflow-reject-button"' not in page_text
    assert 'id="workflow-close-button"' in page_text


def test_webapp_reports_enabled_gemini_when_environment_key_exists(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    app = create_app()
    client = app.test_client()
    response = client.get("/api/app-config")

    assert response.status_code == 200
    assert response.get_json()["gemini"]["enabled"] is True
    learner_bands = response.get_json()["worksheet_options"]["learner_bands"]
    early_band = next(band for band in learner_bands if band["value"] == LearnerBand.EARLY_ARITHMETIC.value)
    assert early_band["default_reveal_mode"] == "color_by_number"
    assert early_band["default_skill_profile"] == "subtraction_and_addition"
    assert early_band["default_decoy_percentage"] == 100
    skill_profiles = response.get_json()["worksheet_options"]["skill_profiles"]
    multiplication = next(profile for profile in skill_profiles if profile["value"] == "multiplication_focus")
    assert LearnerBand.EARLY_ARITHMETIC.value not in multiplication["supported_learner_bands"]
    assert LearnerBand.UPPER_ELEMENTARY.value in multiplication["supported_learner_bands"]
    algebra_profile = next(profile for profile in skill_profiles if profile["value"] == "algebra")
    geometry_profile = next(profile for profile in skill_profiles if profile["value"] == "geometry")
    assert LearnerBand.PRE_ALGEBRA.value in algebra_profile["supported_learner_bands"]
    assert LearnerBand.ALGEBRA.value in algebra_profile["supported_learner_bands"]
    assert LearnerBand.GEOMETRY.value in algebra_profile["supported_learner_bands"]
    assert geometry_profile["supported_learner_bands"] == [LearnerBand.GEOMETRY.value]
    pre_algebra_band = next(band for band in learner_bands if band["value"] == LearnerBand.PRE_ALGEBRA.value)
    algebra_band = next(band for band in learner_bands if band["value"] == LearnerBand.ALGEBRA.value)
    geometry_band = next(band for band in learner_bands if band["value"] == LearnerBand.GEOMETRY.value)
    assert pre_algebra_band["default_skill_profile"] == "algebra"
    assert pre_algebra_band["default_difficulty_maximum"] == 2
    assert algebra_band["default_skill_profile"] == "algebra"
    assert algebra_band["default_difficulty_minimum"] == 3
    assert geometry_band["default_skill_profile"] == "geometry"
    assert response.get_json()["worksheet_options"]["decoy_percentage"]["default"] == 100
    assert response.get_json()["worksheet_options"]["color_grid_size"]["minimum"] == 16
    assert response.get_json()["worksheet_options"]["color_grid_size"]["maximum"] == 40
    assert response.get_json()["worksheet_options"]["color_by_number_difficulty_range"]["maximum"] == 2
    assert response.get_json()["worksheet_options"]["image_styling"]["enabled"] is True
    assert response.get_json()["worksheet_options"]["image_styling"]["default_style"] == "watercolor"
    assert response.get_json()["worksheet_options"]["image_styling"]["model"] == response.get_json()["gemini"]["image_model"]
    assert response.get_json()["worksheet_options"]["image_styling"]["prompt_refinement_model"] == "gemini-2.5-flash-lite"
    assert response.get_json()["worksheet_options"]["image_styling"]["prompt_strategy"] == "worksheet_semantic_preservation"
    assert response.get_json()["worksheet_options"]["image_styling"]["preserves_content_via_foreground_compositing"] is True
    assert response.get_json()["worksheet_options"]["image_styling"]["verification_strategy"] == "semantic_foreground_pixel_preservation"
    assert response.get_json()["worksheet_options"]["image_styling"]["retry_policy"]["max_attempts"] == 2
    assert response.get_json()["worksheet_options"]["image_styling"]["retry_policy"]["retry_on_verification_failure"] is True
    assert response.get_json()["worksheet_options"]["image_styling"]["timeout_policy"]["worksheet_generation_seconds"] == 180.0
    assert response.get_json()["worksheet_options"]["gallery"]["page_size_default"] == 24
    assert response.get_json()["worksheet_options"]["gallery"]["page_size_maximum"] == 48
    assert any(
        option["value"] == "styled_verified"
        for option in response.get_json()["worksheet_options"]["gallery"]["styling_status_options"]
    )
    assert any(
        option["value"] == "title_asc"
        for option in response.get_json()["worksheet_options"]["gallery"]["sort_options"]
    )
    assert "CRITICAL PRESERVATION RULES" in response.get_json()["worksheet_options"]["image_styling"]["sample_prompt"]
    assert any(
        source["value"] == "gemini"
        for source in response.get_json()["worksheet_options"]["color_picture_sources"]
    )
    assert response.get_json()["logging"]["verbosity"] == "normal"
    assert response.get_json()["job_tracking"]["stale_job_reconciliation"] is True
    assert response.get_json()["job_tracking"]["worker_enabled"] is True
    assert response.get_json()["ui"]["debug_enabled"] is False
    assert response.get_json()["ui"]["mode"] == "customer"


def test_webapp_reports_debug_ui_when_enabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")
    monkeypatch.setenv("APP_DEBUG_UI", "true")

    app = create_app()
    client = app.test_client()
    response = client.get("/api/app-config")

    assert response.status_code == 200
    assert response.get_json()["ui"]["debug_enabled"] is True
    assert response.get_json()["ui"]["mode"] == "debug"
    assert response.get_json()["maintenance"]["enabled"] is True


def test_maintenance_endpoints_are_debug_only_and_can_prune_orphans(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "db" / "app.sqlite3"
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("APP_DB_PATH", str(database_path))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setenv("GEMINI_API_KEY", "present")
    monkeypatch.setenv("APP_DEBUG_UI", "true")
    monkeypatch.setenv("APP_JOB_WORKER_ENABLED", "false")

    app = create_app()
    client = app.test_client()
    orphan_dir = artifact_root / "run-99999"
    orphan_dir.mkdir(parents=True, exist_ok=True)
    (orphan_dir / "ghost.txt").write_text("ghost", encoding="utf-8")

    summary_response = client.get("/api/maintenance")
    assert summary_response.status_code == 200
    assert summary_response.get_json()["orphan_run_directory_count"] == 1

    prune_response = client.post("/api/maintenance/prune-artifacts", json={})
    assert prune_response.status_code == 200
    assert prune_response.get_json()["removed_run_directory_count"] == 1
    assert orphan_dir.exists() is False

    vacuum_response = client.post("/api/maintenance/vacuum", json={})
    assert vacuum_response.status_code == 200
    assert vacuum_response.get_json()["status"] == "completed"


def test_maintenance_endpoints_are_hidden_without_debug_ui(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")
    monkeypatch.delenv("APP_DEBUG_UI", raising=False)

    app = create_app()
    client = app.test_client()

    assert client.get("/api/maintenance").status_code == 404
    assert client.post("/api/maintenance/prune-artifacts", json={}).status_code == 404
    assert client.post("/api/maintenance/vacuum", json={}).status_code == 404


def test_frontend_avoids_template_innerhtml_sinks_for_dynamic_content() -> None:
    script_path = Path("worksheet_generator/webapp/static/app.js")
    source = script_path.read_text(encoding="utf-8")

    assert "card.innerHTML = `" not in source
    assert "wrapper.innerHTML = `" not in source
    assert "item.innerHTML = `" not in source
    assert "solutionCount + decoyCount" not in source


def test_frontend_preflight_validates_difficulty_range_before_workflow_starts() -> None:
    script_source = Path("worksheet_generator/webapp/static/app.js").read_text(encoding="utf-8")
    template_source = Path("worksheet_generator/webapp/templates/index.html").read_text(encoding="utf-8")

    assert 'id="form-validation-message"' in template_source
    assert "function validateControlPayloadPreflight(controls)" in script_source
    assert "Difficulty minimum must not exceed maximum." in script_source
    submit_slice_start = script_source.index("async function onWorkflowSubmit(event)")
    submit_slice_end = script_source.index("async function onWorkflowProceed()")
    submit_source = script_source[submit_slice_start:submit_slice_end]
    assert "const validationError = validateControlPayloadPreflight(controls);" in submit_source
    assert "const session = await createWorkflowSession(controls);" in submit_source
    assert submit_source.index("const validationError = validateControlPayloadPreflight(controls);") < submit_source.index(
        "const session = await createWorkflowSession(controls);"
    )


def test_solution_input_toggle_does_not_force_phase_list_rerender_on_each_keystroke() -> None:
    script_source = Path("worksheet_generator/webapp/static/app.js").read_text(encoding="utf-8")
    template_source = Path("worksheet_generator/webapp/templates/index.html").read_text(encoding="utf-8")
    css_source = Path("worksheet_generator/webapp/static/app.css").read_text(encoding="utf-8")

    assert "function updateWorkflowActionState({ renderPhaseList = true } = {})" in script_source
    assert 'updateWorkflowActionState({ renderPhaseList: false });' in script_source
    assert 'class="secondary-button workflow-ghost-button"' in template_source
    assert 'class="button-row workflow-review-button-group"' in template_source
    assert ".workflow-ghost-button" in css_source
    assert ".workflow-review-button-group" in css_source
    assert 'regenerateFromSolutionButton.classList.toggle("is-visible", canRewriteFromSolution);' in script_source
    assert "preserveSolutionRewriteAction: false" in script_source
    assert "state.workflow.preserveSolutionRewriteAction || currentSolution !== savedSolution" in script_source
    assert 'renderWorkflowDraft(response.draft, { preserveSolutionRewriteAction: true });' in script_source


def test_plain_review_includes_solution_guide_preview_in_frontend() -> None:
    script_source = Path("worksheet_generator/webapp/static/app.js").read_text(encoding="utf-8")

    assert 'function workflowReviewArtifacts(run)' in script_source
    assert '["worksheet_preview", "worksheet_solution"].includes(artifact.artifact_kind)' in script_source
    assert "Review the plain worksheet and solution guide below." in script_source


def test_styling_guidance_preview_and_limit_are_wired_in_frontend() -> None:
    script_source = Path("worksheet_generator/webapp/static/app.js").read_text(encoding="utf-8")
    template_source = Path("worksheet_generator/webapp/templates/index.html").read_text(encoding="utf-8")

    assert "function updateStyleGuidanceStatus({ countId, guidance, previewId = null })" in script_source
    assert 'countId: "image-additional-guidance-count"' in script_source
    assert 'countId: "workflow-image-guidance-count"' in script_source
    assert 'countId: "modal-image-guidance-count"' in script_source
    assert "const IMAGE_ADDITIONAL_GUIDANCE_MAX_LENGTH = 500;" in script_source
    assert "Guidance to Gemini:" in script_source
    assert "No additional guidance will be sent to Gemini." in script_source
    assert 'id="workflow-image-guidance-preview"' in template_source
    assert 'id="workflow-image-guidance-count"' in template_source
    assert 'id="modal-image-guidance-preview"' in template_source
    assert 'id="modal-image-guidance-count"' in template_source
    assert 'id="image-additional-guidance-count"' in template_source
    assert template_source.count('maxlength="500"') >= 3


class FakeGenerator:
    def generate(self, request: RewardContentGenerationRequest) -> RewardContentCandidate:
        return RewardContentCandidate(
            prompt_text=f"What classroom clue matches {request.theme}?",
            solution_phrase="Number Sense",
            theme=request.theme,
            source="fake_generator",
            approval_state=ApprovalState.PENDING,
            style=request.preferred_style or "riddle",
            language=request.language,
            review_notes=["Generated by fake generator for test coverage."],
        )

    def generate_for_solution(self, request: RewardContentGenerationRequest, solution_phrase: str) -> RewardContentCandidate:
        return RewardContentCandidate(
            prompt_text=f"What clue fits {request.theme} without saying the answer?",
            solution_phrase=solution_phrase,
            theme=request.theme,
            source="fake_generator",
            approval_state=ApprovalState.PENDING,
            style=request.preferred_style or "riddle",
            language=request.language,
            review_notes=["Generated from edited solution by fake generator for test coverage."],
        )


def test_reward_content_generation_edit_and_approval_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    generate_response = client.post(
        "/api/reward-content/generate",
        json={
            "theme": "fractions",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "riddle",
            "language": "en",
        },
    )
    assert generate_response.status_code == 200
    draft = generate_response.get_json()["draft"]
    assert draft["approval_state"] == ApprovalState.PENDING.value
    assert draft["theme"] == "fractions"

    edit_response = client.patch(
        f"/api/reward-content/{draft['id']}",
        json={
            "prompt_text": "What math idea helps you compare equal parts?",
            "solution_phrase": "Fractions",
        },
    )
    assert edit_response.status_code == 200
    edited = edit_response.get_json()["draft"]
    assert edited["approval_state"] == ApprovalState.EDITED.value
    assert edited["solution_phrase"] == "Fractions"

    approve_response = client.post(f"/api/reward-content/{draft['id']}/approve")
    assert approve_response.status_code == 200
    approved = approve_response.get_json()["draft"]
    assert approved["approval_state"] == ApprovalState.APPROVED.value
    assert "single-word" in approved["generation_parameters"]["solution_length_guidance"]


def test_reward_content_generation_endpoint_is_disabled_without_api_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    app = create_app()
    client = app.test_client()
    response = client.post(
        "/api/reward-content/generate",
        json={
            "theme": "patterns",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "riddle",
            "language": "en",
        },
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "gemini_unavailable"


def test_manual_reward_content_flow_works_without_gemini(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    direct_response = client.post(
        "/api/reward-content/direct",
        json={
            "theme": "patterns",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "question",
            "language": "en",
            "difficulty_maximum": 2,
            "prompt_text": "What helps you spot repeating shapes?",
            "solution_phrase": "Pattern Play",
        },
    )

    assert direct_response.status_code == 200
    draft = direct_response.get_json()["draft"]
    assert draft["source"] == "direct_input"
    assert draft["approval_state"] == ApprovalState.PENDING.value

    approve_response = client.post(
        f"/api/reward-content/{draft['id']}/approve",
        json={"learner_band": LearnerBand.UPPER_ELEMENTARY.value},
    )
    assert approve_response.status_code == 200
    assert approve_response.get_json()["draft"]["approval_state"] == ApprovalState.APPROVED.value


def test_workflow_session_persists_and_recovers_draft_and_run_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    session_response = client.post(
        "/api/workflow-sessions",
        json={
            "theme": "patterns",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "question",
            "language": "en",
            "reveal_mode": "letter_bank",
            "skill_profile": "mixed_operations",
            "difficulty_minimum": 1,
            "difficulty_maximum": 2,
            "decoy_percentage": 100,
            "color_picture_source": "preset",
            "color_picture_preset": "smile",
            "apply_image_styling": False,
            "image_style_name": "watercolor",
            "image_color_mode": "color",
            "image_ink_saver": False,
            "image_additional_guidance": "Keep the border geometric and understated.",
            "seed": 11,
        },
    )
    assert session_response.status_code == 200
    workflow_session = session_response.get_json()["workflow_session"]
    token = workflow_session["token"]
    assert str(UUID(token)) == token
    assert workflow_session["phase"] == "draft_generation_requested"

    direct_response = client.post(
        "/api/reward-content/direct",
        json={
            "workflow_token": token,
            "theme": "patterns",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "question",
            "language": "en",
            "difficulty_maximum": 2,
            "prompt_text": "What helps you spot repeating shapes?",
            "solution_phrase": "Pattern Play",
        },
    )
    draft = direct_response.get_json()["draft"]
    restored_after_draft = client.get(f"/api/workflow-sessions/{token}").get_json()["workflow_session"]
    assert restored_after_draft["draft"]["id"] == draft["id"]
    assert restored_after_draft["phase"] == "draft_review"
    assert restored_after_draft["controls"]["image_additional_guidance"] == "Keep the border geometric and understated."

    approve_response = client.post(
        f"/api/reward-content/{draft['id']}/approve",
        json={"learner_band": LearnerBand.UPPER_ELEMENTARY.value, "workflow_token": token},
    )
    approved = approve_response.get_json()["draft"]

    run_response = client.post(
        "/api/worksheets/generate",
        json={
            "workflow_token": token,
            "draft_id": approved["id"],
            "theme": "patterns",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "reveal_mode": "letter_bank",
            "skill_profile": "mixed_operations",
            "difficulty_minimum": 1,
            "difficulty_maximum": 2,
            "decoy_percentage": 50,
            "apply_image_styling": False,
            "seed": 11,
        },
    )
    assert run_response.status_code == 200
    initial_job = run_response.get_json()["job"]
    initial_run = run_response.get_json()["worksheet_run"]
    restored_queued = client.get(f"/api/workflow-sessions/{token}").get_json()["workflow_session"]
    assert restored_queued["generation_job_id"] == initial_job["id"]
    assert restored_queued["worksheet_run_id"] == initial_run["id"]

    wait_for_job_completion(client, initial_job["id"])
    restored_completed = client.get(f"/api/workflow-sessions/{token}").get_json()["workflow_session"]
    assert restored_completed["worksheet_run"]["id"] == initial_run["id"]
    assert restored_completed["phase"] == "plain_worksheet_ready"


def test_generate_worksheet_run_and_persist_gallery_record(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    draft_response = client.post(
        "/api/reward-content/generate",
        json={
            "theme": "geometry",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "riddle",
            "language": "en",
            "difficulty_maximum": 4,
        },
    )
    draft = draft_response.get_json()["draft"]
    assert "multi-word" in draft["generation_parameters"]["solution_length_guidance"]
    client.patch(
        f"/api/reward-content/{draft['id']}",
        json={
            "prompt_text": "What subject trains you to spot patterns and solve carefully?",
            "solution_phrase": "MATH TEAM",
        },
    )
    approve_response = client.post(f"/api/reward-content/{draft['id']}/approve")
    approved = approve_response.get_json()["draft"]

    run_response = client.post(
        "/api/worksheets/generate",
        json={
            "draft_id": approved["id"],
            "theme": "geometry",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "reveal_mode": "letter_bank",
            "skill_profile": "subtraction_and_addition",
            "difficulty_minimum": 1,
            "difficulty_maximum": 3,
            "decoy_percentage": 0,
            "apply_image_styling": True,
            "image_style_name": "watercolor",
            "image_color_mode": "color",
            "image_ink_saver": True,
            "image_additional_guidance": "Use constellation motifs in the empty margin space only.",
            "seed": 77,
        },
    )

    assert run_response.status_code == 200
    initial_run = run_response.get_json()["worksheet_run"]
    assert initial_run["status"] == "generating"
    assert initial_run["lifecycle"]["phase"] in {"worksheet_generation_queued", "worksheet_generation_running"}
    assert initial_run["artifacts"] == []
    job = wait_for_job_completion(client, run_response.get_json()["job"]["id"])
    assert job["status"] == "completed"
    assert job["phase"] == "worksheet_generation_complete"

    worksheet_run = client.get(f"/api/worksheet-runs/{initial_run['id']}").get_json()["worksheet_run"]
    assert worksheet_run["status"] == "completed"
    assert worksheet_run["lifecycle"]["phase"] == "awaiting_styling_confirmation"
    assert worksheet_run["title"] == "Addition and Subtraction Worksheet"
    preview_artifact = next(
        artifact for artifact in worksheet_run["artifacts"]
        if artifact["artifact_kind"] == "worksheet_preview" and artifact["output_format"] == "png"
    )
    download_response = client.get(f"/api/artifacts/{preview_artifact['id']}/download")
    assert download_response.status_code == 200
    assert "attachment;" in download_response.headers["Content-Disposition"]
    assert "upper_elementary_subtraction_and_addition_geometry.png" in download_response.headers["Content-Disposition"]
    assert worksheet_run["lifecycle"]["can_confirm_styling"] is True
    assert worksheet_run["artifacts"]
    assert worksheet_run["parameters"]["problem_count"] == 8
    assert worksheet_run["parameters"]["seed"] == 77
    assert worksheet_run["parameters"]["apply_image_styling"] is True
    assert worksheet_run["parameters"]["image_additional_guidance"] == "Use constellation motifs in the empty margin space only."
    assert worksheet_run["styling"]["requested"] is True
    assert worksheet_run["styling"]["style_name"] == "watercolor"
    assert worksheet_run["styling"]["color_mode"] == "color"
    assert worksheet_run["styling"]["ink_saver"] is True
    assert worksheet_run["styling"]["status"] == "awaiting_confirmation"
    assert worksheet_run["styling"]["verification_status"] == "pending_confirmation"
    assert "CRITICAL PRESERVATION RULES" in worksheet_run["styling"]["prompt_text"]
    assert "Addition and Subtraction Worksheet" in worksheet_run["styling"]["prompt_text"]
    assert "What subject trains you to spot patterns and solve carefully?" in worksheet_run["styling"]["prompt_text"]
    assert "Use constellation motifs in the empty margin space only." in worksheet_run["styling"]["prompt_text"]
    assert "Generated Worksheet" not in worksheet_run["styling"]["prompt_text"]
    assert any(artifact["output_format"] == "png" for artifact in worksheet_run["artifacts"])

    gallery_response = client.get("/api/gallery?search=geometry")
    assert gallery_response.status_code == 200
    items = gallery_response.get_json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == worksheet_run["id"]
    assert items[0]["lifecycle"]["phase"] == "awaiting_styling_confirmation"
    assert items[0]["styling"]["requested"] is True
    assert items[0]["styling"]["style_name"] == "watercolor"
    assert items[0]["styling"]["status"] == "awaiting_confirmation"

    assert job["status"] == "completed"

    manifest = read_worksheet_manifest(tmp_path / "artifacts" / "run-00001" / "worksheet-manifest.json")
    assert len(manifest.problems) == 8


def test_retry_generation_clones_a_new_run_from_existing_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    direct_response = client.post(
        "/api/reward-content/direct",
        json={
            "theme": "fractions",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "question",
            "language": "en",
            "difficulty_maximum": 3,
            "prompt_text": "What helps you compare equal parts?",
            "solution_phrase": "FRACTION FUN",
        },
    )
    draft = direct_response.get_json()["draft"]
    client.post(f"/api/reward-content/{draft['id']}/approve")

    initial_run_response = client.post(
        "/api/worksheets/generate",
        json={
            "draft_id": draft["id"],
            "theme": "fractions",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "reveal_mode": "letter_bank",
            "skill_profile": "subtraction_and_addition",
            "difficulty_minimum": 1,
            "difficulty_maximum": 3,
            "decoy_percentage": 50,
        },
    )
    initial_payload = initial_run_response.get_json()
    wait_for_job_completion(client, initial_payload["job"]["id"])

    retry_response = client.post(
        f"/api/worksheet-runs/{initial_payload['worksheet_run']['id']}/retry-generation",
        json={},
    )
    assert retry_response.status_code == 200
    retry_payload = retry_response.get_json()
    assert retry_payload["worksheet_run"]["id"] != initial_payload["worksheet_run"]["id"]
    assert retry_payload["job"]["status"] == "queued"
    assert retry_payload["worksheet_run"]["prompt_text"] == "What helps you compare equal parts?"
    assert retry_payload["worksheet_run"]["solution_phrase"] == "FRACTION FUN"

    final_job = wait_for_job_completion(client, retry_payload["job"]["id"])
    final_run = client.get(f"/api/worksheet-runs/{retry_payload['worksheet_run']['id']}").get_json()["worksheet_run"]

    assert final_job["status"] == "completed"
    assert final_run["status"] == "completed"
    assert final_run["lifecycle"]["phase"] == "plain_worksheet_ready"


def test_gallery_supports_parameter_filters_sort_and_pagination(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "db" / "app.sqlite3"
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("APP_DB_PATH", str(database_path))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    app = create_app()
    client = app.test_client()
    repository = AppRepository(database_path)

    alpha_id = repository.create_worksheet_run(
        title="Alpha Geometry",
        learner_band=LearnerBand.GEOMETRY.value,
        reveal_mode="color_by_number",
        skill_profile="geometry",
        theme="moon mission",
        prompt_text="Which shape is hidden in the sky?",
        solution_phrase="MOON",
        parameters={
            "difficulty_minimum": 4,
            "difficulty_maximum": 5,
            "seed": 11,
            "color_picture_source": "preset",
            "color_picture_preset": "moon",
        },
        styling={"requested": True, "status": "styled_verified", "verification_status": "passed"},
    )
    repository.complete_worksheet_run(alpha_id, artifact_group="run-00001", thumbnail_path=None)
    repository.update_worksheet_run_styling(alpha_id, status="styled_verified", verification_status="passed")

    beta_id = repository.create_worksheet_run(
        title="Beta Arithmetic",
        learner_band=LearnerBand.UPPER_ELEMENTARY.value,
        reveal_mode="letter_bank",
        skill_profile="mixed_operations",
        theme="number camp",
        prompt_text="Which team solves the puzzle?",
        solution_phrase="MATH TEAM",
        parameters={
            "difficulty_minimum": 1,
            "difficulty_maximum": 2,
            "seed": 22,
            "color_picture_source": "preset",
            "color_picture_preset": "smile",
        },
        styling={"requested": False, "status": "not_requested", "verification_status": "not_requested"},
    )
    repository.complete_worksheet_run(beta_id, artifact_group="run-00002", thumbnail_path=None)

    gamma_id = repository.create_worksheet_run(
        title="Gamma Algebra",
        learner_band=LearnerBand.ALGEBRA.value,
        reveal_mode="letter_bank",
        skill_profile="algebra",
        theme="equation lab",
        prompt_text="Which subject uses variables?",
        solution_phrase="ALGEBRA",
        parameters={
            "difficulty_minimum": 3,
            "difficulty_maximum": 4,
            "seed": 33,
            "color_picture_source": "gemini",
            "color_picture_preset": "rocket",
        },
        styling={"requested": True, "status": "awaiting_confirmation", "verification_status": "pending_confirmation"},
    )
    repository.complete_worksheet_run(gamma_id, artifact_group="run-00003", thumbnail_path=None)

    filtered = client.get(
        "/api/gallery?skill_profile=geometry&styling_status=styled_verified&picture_preset=moon"
    ).get_json()
    assert filtered["pagination"]["total"] == 1
    assert [item["id"] for item in filtered["items"]] == [alpha_id]

    ranged = client.get("/api/gallery?difficulty_minimum=3&difficulty_maximum=4&sort=title_asc").get_json()
    assert ranged["pagination"]["total"] == 2
    assert [item["title"] for item in ranged["items"]] == ["Alpha Geometry", "Gamma Algebra"]

    paged_first = client.get("/api/gallery?sort=title_asc&limit=1&offset=0").get_json()
    paged_second = client.get("/api/gallery?sort=title_asc&limit=1&offset=1").get_json()
    assert paged_first["pagination"]["has_more"] is True
    assert paged_first["items"][0]["title"] == "Alpha Geometry"
    assert paged_second["items"][0]["title"] == "Beta Arithmetic"

    gemini_picture = client.get("/api/gallery?picture_source=gemini&seed=33").get_json()
    assert gemini_picture["pagination"]["total"] == 1
    assert gemini_picture["items"][0]["id"] == gamma_id


def test_generate_worksheet_returns_before_background_job_finishes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    original_generate = WorksheetRunGenerationService.generate

    def slow_generate(self, *, worksheet_run_id: int, approved_draft: dict[str, object], parameters: dict[str, object], progress_callback=None, should_cancel=None):  # noqa: ANN001
        time.sleep(0.3)
        return original_generate(
            self,
            worksheet_run_id=worksheet_run_id,
            approved_draft=approved_draft,
            parameters=parameters,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
        )

    monkeypatch.setattr(WorksheetRunGenerationService, "generate", slow_generate)

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    direct_response = client.post(
        "/api/reward-content/direct",
        json={
            "theme": "space",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "question",
            "language": "en",
            "difficulty_maximum": 3,
            "prompt_text": "What helps you solve carefully?",
            "solution_phrase": "MATH",
        },
    )
    draft = direct_response.get_json()["draft"]
    client.post(f"/api/reward-content/{draft['id']}/approve")

    started = time.perf_counter()
    run_response = client.post(
        "/api/worksheets/generate",
        json={
            "draft_id": draft["id"],
            "theme": "space",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "reveal_mode": "letter_bank",
            "skill_profile": "subtraction_and_addition",
            "difficulty_minimum": 1,
            "difficulty_maximum": 3,
            "decoy_percentage": 0,
        },
    )
    elapsed = time.perf_counter() - started

    assert run_response.status_code == 200
    assert elapsed < 0.25
    payload = run_response.get_json()
    assert payload["worksheet_run"]["status"] == "generating"
    assert payload["worksheet_run"]["lifecycle"]["phase"] in {
        "worksheet_generation_queued",
        "worksheet_generation_running",
    }
    assert payload["job"]["status"] == "queued"
    assert payload["job"]["phase"] == "worksheet_generation_queued"
    final_job = wait_for_job_completion(client, payload["job"]["id"])
    assert final_job["status"] == "completed"


def test_startup_reconciles_stale_generation_job(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "db" / "app.sqlite3"
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("APP_DB_PATH", str(database_path))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setenv("GEMINI_API_KEY", "present")
    monkeypatch.setenv("APP_JOB_WORKER_ENABLED", "false")

    first_app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    first_repository = AppRepository(database_path)
    run_id = first_repository.create_worksheet_run(
        title="Generated Worksheet",
        learner_band=LearnerBand.UPPER_ELEMENTARY.value,
        reveal_mode="letter_bank",
        skill_profile="subtraction_and_addition",
        theme="restart",
        prompt_text="What helps you solve carefully?",
        solution_phrase="MATH",
        parameters={"theme": "restart"},
        styling={"requested": False, "status": "not_requested", "verification_status": "not_requested"},
    )
    job_id = first_repository.create_generation_job(
        job_type="worksheet_generate",
        requested_parameters={"worksheet_run_id": run_id},
        progress_message="Generating worksheet in the background.",
        worksheet_run_id=run_id,
        phase="worksheet_generation_assemble",
    )
    first_repository.update_generation_job(job_id, status="running", phase="worksheet_generation_assemble")
    assert first_app is not None

    second_app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = second_app.test_client()
    job = client.get(f"/api/jobs/{job_id}").get_json()["job"]
    run = client.get(f"/api/worksheet-runs/{run_id}").get_json()["worksheet_run"]

    assert job["status"] == "failed"
    assert job["phase"] == "worksheet_generation_failed"
    assert "interrupted by application restart" in job["progress_message"]
    assert run["status"] == "failed"
    assert run["lifecycle"]["phase"] == "run_failed"


def test_startup_reconciles_stale_styling_job_and_confirmation_timeout(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "db" / "app.sqlite3"
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("APP_DB_PATH", str(database_path))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setenv("GEMINI_API_KEY", "present")
    monkeypatch.setenv("APP_STYLING_CONFIRMATION_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("APP_JOB_WORKER_ENABLED", "false")

    create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    repository = AppRepository(database_path)

    styling_run_id = repository.create_worksheet_run(
        title="Generated Worksheet",
        learner_band=LearnerBand.UPPER_ELEMENTARY.value,
        reveal_mode="letter_bank",
        skill_profile="subtraction_and_addition",
        theme="restart",
        prompt_text="What helps you solve carefully?",
        solution_phrase="MATH",
        parameters={"theme": "restart"},
        styling={"requested": True, "status": "styling_in_progress", "verification_status": "pending"},
    )
    repository.update_worksheet_run_lifecycle(styling_run_id, lifecycle_phase="styling_running")
    styling_job_id = repository.create_generation_job(
        job_type="worksheet_style",
        requested_parameters={"worksheet_run_id": styling_run_id},
        progress_message="Styling worksheet in the background.",
        worksheet_run_id=styling_run_id,
        phase="styling_apply_and_verify",
    )
    repository.update_generation_job(styling_job_id, status="running", phase="styling_apply_and_verify")

    confirmation_run_id = repository.create_worksheet_run(
        title="Generated Worksheet",
        learner_band=LearnerBand.UPPER_ELEMENTARY.value,
        reveal_mode="letter_bank",
        skill_profile="subtraction_and_addition",
        theme="late review",
        prompt_text="What helps you solve carefully?",
        solution_phrase="MATH",
        parameters={"theme": "late review"},
        styling={"requested": True, "status": "awaiting_confirmation", "verification_status": "pending_confirmation"},
    )
    repository.complete_worksheet_run(
        confirmation_run_id,
        artifact_group=f"run-{confirmation_run_id:05d}",
        thumbnail_path=f"run-{confirmation_run_id:05d}/worksheet-preview.png",
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE worksheet_runs SET updated_at = datetime('now', '-2 minutes') WHERE id = ?",
            (confirmation_run_id,),
        )
        connection.commit()

    second_app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = second_app.test_client()
    styling_job = client.get(f"/api/jobs/{styling_job_id}").get_json()["job"]
    styling_run = client.get(f"/api/worksheet-runs/{styling_run_id}").get_json()["worksheet_run"]
    confirmation_run = client.get(f"/api/worksheet-runs/{confirmation_run_id}").get_json()["worksheet_run"]

    assert styling_job["status"] == "failed"
    assert styling_job["phase"] == "styling_failed"
    assert styling_run["styling"]["status"] == "styled_failed_error"
    assert styling_run["lifecycle"]["phase"] == "styled_failed_plain_retained"
    assert confirmation_run["styling"]["status"] == "cancelled_after_plain_review"
    assert confirmation_run["lifecycle"]["phase"] == "styling_cancelled_plain_retained"


def test_generation_job_timeout_marks_run_failed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")
    monkeypatch.setenv("APP_WORKSHEET_GENERATION_TIMEOUT_SECONDS", "0.1")

    original_generate = WorksheetRunGenerationService.generate

    def slow_generate(self, *, worksheet_run_id: int, approved_draft: dict[str, object], parameters: dict[str, object], progress_callback=None, should_cancel=None):  # noqa: ANN001
        time.sleep(0.3)
        return original_generate(
            self,
            worksheet_run_id=worksheet_run_id,
            approved_draft=approved_draft,
            parameters=parameters,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
        )

    monkeypatch.setattr(WorksheetRunGenerationService, "generate", slow_generate)

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    direct_response = client.post(
        "/api/reward-content/direct",
        json={
            "theme": "space",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "question",
            "language": "en",
            "difficulty_maximum": 3,
            "prompt_text": "What helps you solve carefully?",
            "solution_phrase": "MATH",
        },
    )
    draft = direct_response.get_json()["draft"]
    client.post(f"/api/reward-content/{draft['id']}/approve")

    run_response = client.post(
        "/api/worksheets/generate",
        json={
            "draft_id": draft["id"],
            "theme": "space",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "reveal_mode": "letter_bank",
            "skill_profile": "subtraction_and_addition",
            "difficulty_minimum": 1,
            "difficulty_maximum": 3,
            "decoy_percentage": 0,
        },
    )
    payload = run_response.get_json()
    job = wait_for_job_completion(client, payload["job"]["id"], timeout=2.0)
    time.sleep(0.35)
    run = client.get(f"/api/worksheet-runs/{payload['worksheet_run']['id']}").get_json()["worksheet_run"]
    job_after_wait = client.get(f"/api/jobs/{payload['job']['id']}").get_json()["job"]

    assert job["status"] == "failed"
    assert "timed out" in job["progress_message"]
    assert run["status"] == "failed"
    assert run["lifecycle"]["phase"] == "run_failed"
    assert job_after_wait["status"] == "failed"
    assert run["artifacts"] == []


def test_styling_job_timeout_marks_run_failed_plain_retained(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")
    monkeypatch.setenv("APP_WORKSHEET_STYLING_TIMEOUT_SECONDS", "0.1")

    def slow_apply_image_styling(self, *, worksheet_run_id: int, prompt_text: str, progress_callback=None, should_cancel=None):  # noqa: ANN001
        time.sleep(0.3)
        run_dir = tmp_path / "artifacts" / f"run-{worksheet_run_id:05d}" / "styled"
        run_dir.mkdir(parents=True, exist_ok=True)
        styled_png = run_dir / "worksheet-preview-styled.png"
        styled_png.write_bytes(b"png")
        if progress_callback:
            progress_callback("apply Gemini styling and verify")
        return {
            "verified": True,
            "artifact_group": f"run-{worksheet_run_id:05d}/styled",
            "thumbnail_path": styled_png,
            "artifacts": [
                {"artifact_kind": "worksheet_styled_preview", "output_format": "png", "path": styled_png, "display_name": "Styled Preview PNG"},
            ],
            "final_prompt": prompt_text,
            "verification_report": type("Report", (), {"mismatch_count": 0})(),
            "style_check_artifact_path": styled_png,
        }

    monkeypatch.setattr(WorksheetRunGenerationService, "apply_image_styling", slow_apply_image_styling)

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    direct_response = client.post(
        "/api/reward-content/direct",
        json={
            "theme": "space",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "question",
            "language": "en",
            "difficulty_maximum": 3,
            "prompt_text": "What helps you solve carefully?",
            "solution_phrase": "MATH",
        },
    )
    draft = direct_response.get_json()["draft"]
    client.post(f"/api/reward-content/{draft['id']}/approve")

    run_response = client.post(
        "/api/worksheets/generate",
        json={
            "draft_id": draft["id"],
            "theme": "space",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "reveal_mode": "letter_bank",
            "skill_profile": "subtraction_and_addition",
            "difficulty_minimum": 1,
            "difficulty_maximum": 3,
            "decoy_percentage": 0,
            "apply_image_styling": True,
            "image_style_name": "watercolor",
            "image_color_mode": "color",
            "image_ink_saver": False,
        },
    )
    worksheet_run = run_response.get_json()["worksheet_run"]
    wait_for_job_completion(client, run_response.get_json()["job"]["id"])
    worksheet_run = client.get(f"/api/worksheet-runs/{worksheet_run['id']}").get_json()["worksheet_run"]

    decision_response = client.post(
        f"/api/worksheet-runs/{worksheet_run['id']}/styling-decision",
        json={"decision": "confirm"},
    )
    styling_job = wait_for_job_completion(client, decision_response.get_json()["job"]["id"], timeout=2.0)
    time.sleep(0.35)
    final_run = client.get(f"/api/worksheet-runs/{worksheet_run['id']}").get_json()["worksheet_run"]
    styling_job_after_wait = client.get(f"/api/jobs/{decision_response.get_json()['job']['id']}").get_json()["job"]

    assert styling_job["status"] == "failed"
    assert "timed out" in styling_job["progress_message"]
    assert final_run["styling"]["status"] == "styled_failed_error"
    assert final_run["lifecycle"]["phase"] == "styled_failed_plain_retained"
    assert styling_job_after_wait["status"] == "failed"
    assert all(artifact["artifact_kind"] != "worksheet_styled_preview" for artifact in final_run["artifacts"])


def test_cancel_generation_job_marks_run_cancelled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    def slow_generate(self, *, worksheet_run_id: int, approved_draft, parameters, progress_callback=None, should_cancel=None):  # noqa: ANN001
        if progress_callback:
            progress_callback("assemble worksheet content")
        for _ in range(40):
            if should_cancel and should_cancel():
                raise WorksheetRunCancelledError("cancelled in fake generator")
            time.sleep(0.05)
        raise AssertionError("generation should have been cancelled before completion")

    monkeypatch.setattr(WorksheetRunGenerationService, "generate", slow_generate)

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    direct_response = client.post(
        "/api/reward-content/direct",
        json={
            "theme": "patterns",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "question",
            "language": "en",
            "difficulty_maximum": 2,
            "prompt_text": "What helps you solve carefully?",
            "solution_phrase": "MATH",
        },
    )
    draft = direct_response.get_json()["draft"]
    client.post(f"/api/reward-content/{draft['id']}/approve")

    run_response = client.post(
        "/api/worksheets/generate",
        json={
            "draft_id": draft["id"],
            "theme": "patterns",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "reveal_mode": "letter_bank",
            "skill_profile": "subtraction_and_addition",
            "difficulty_minimum": 1,
            "difficulty_maximum": 2,
            "decoy_percentage": 100,
            "apply_image_styling": False,
        },
    )

    worksheet_run = run_response.get_json()["worksheet_run"]
    job_id = run_response.get_json()["job"]["id"]
    cancel_response = client.post(f"/api/jobs/{job_id}/cancel")

    assert cancel_response.status_code == 200
    cancelled_job = cancel_response.get_json()["job"]
    assert cancelled_job["status"] == "cancelled"
    assert cancelled_job["phase"] == "worksheet_generation_cancelled"

    final_job = client.get(f"/api/jobs/{job_id}").get_json()["job"]
    final_run = client.get(f"/api/worksheet-runs/{worksheet_run['id']}").get_json()["worksheet_run"]
    assert final_job["status"] == "cancelled"
    assert final_run["status"] == "cancelled"
    assert final_run["lifecycle"]["phase"] == "run_cancelled"


def test_cancel_styling_job_preserves_plain_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    def slow_apply_image_styling(self, *, worksheet_run_id: int, prompt_text: str, progress_callback=None, should_cancel=None):  # noqa: ANN001
        if progress_callback:
            progress_callback("render semantic foreground")
        for _ in range(40):
            if should_cancel and should_cancel():
                raise WorksheetRunCancelledError("cancelled in fake styler")
            time.sleep(0.05)
        raise AssertionError("styling should have been cancelled before completion")

    monkeypatch.setattr(WorksheetRunGenerationService, "apply_image_styling", slow_apply_image_styling)

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    direct_response = client.post(
        "/api/reward-content/direct",
        json={
            "theme": "space",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "question",
            "language": "en",
            "difficulty_maximum": 3,
            "prompt_text": "What helps you solve carefully?",
            "solution_phrase": "MATH",
        },
    )
    draft = direct_response.get_json()["draft"]
    client.post(f"/api/reward-content/{draft['id']}/approve")

    run_response = client.post(
        "/api/worksheets/generate",
        json={
            "draft_id": draft["id"],
            "theme": "space",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "reveal_mode": "letter_bank",
            "skill_profile": "subtraction_and_addition",
            "difficulty_minimum": 1,
            "difficulty_maximum": 3,
            "decoy_percentage": 0,
            "apply_image_styling": True,
            "image_style_name": "watercolor",
            "image_color_mode": "color",
            "image_ink_saver": False,
        },
    )
    worksheet_run = run_response.get_json()["worksheet_run"]
    wait_for_job_completion(client, run_response.get_json()["job"]["id"])

    decision_response = client.post(
        f"/api/worksheet-runs/{worksheet_run['id']}/styling-decision",
        json={"decision": "confirm"},
    )
    style_job_id = decision_response.get_json()["job"]["id"]

    cancel_response = client.post(f"/api/jobs/{style_job_id}/cancel")
    assert cancel_response.status_code == 200
    cancelled_job = wait_for_job_completion(client, style_job_id)
    run_detail_response = client.get(f"/api/worksheet-runs/{worksheet_run['id']}")
    updated_run = run_detail_response.get_json()["worksheet_run"]
    assert cancelled_job["status"] == "cancelled"
    assert cancelled_job["phase"] == "styling_cancelled"
    assert updated_run["styling"]["status"] == "cancelled_during_styling"
    assert updated_run["lifecycle"]["phase"] == "styling_cancelled_plain_retained"

    final_job = client.get(f"/api/jobs/{style_job_id}").get_json()["job"]
    final_run = client.get(f"/api/worksheet-runs/{worksheet_run['id']}").get_json()["worksheet_run"]
    assert final_job["status"] == "cancelled"
    assert final_run["styling"]["status"] == "cancelled_during_styling"
    assert any(artifact["artifact_kind"] == "worksheet_preview" for artifact in final_run["artifacts"])


def test_cannot_cancel_styling_after_gemini_request_started(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    def slow_apply_image_styling(self, *, worksheet_run_id: int, prompt_text: str, progress_callback=None, should_cancel=None):  # noqa: ANN001
        if progress_callback:
            progress_callback("apply Gemini styling")
        for _ in range(30):
            time.sleep(0.05)
        raise WorksheetRunGenerationError("stop after simulated Gemini request window")

    monkeypatch.setattr(WorksheetRunGenerationService, "apply_image_styling", slow_apply_image_styling)

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    direct_response = client.post(
        "/api/reward-content/direct",
        json={
            "theme": "space",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "question",
            "language": "en",
            "difficulty_maximum": 3,
            "prompt_text": "What helps you solve carefully?",
            "solution_phrase": "MATH",
        },
    )
    draft = direct_response.get_json()["draft"]
    client.post(f"/api/reward-content/{draft['id']}/approve")

    run_response = client.post(
        "/api/worksheets/generate",
        json={
            "draft_id": draft["id"],
            "theme": "space",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "reveal_mode": "letter_bank",
            "skill_profile": "subtraction_and_addition",
            "difficulty_minimum": 1,
            "difficulty_maximum": 3,
            "decoy_percentage": 0,
            "apply_image_styling": True,
            "image_style_name": "watercolor",
            "image_color_mode": "color",
            "image_ink_saver": False,
        },
    )
    worksheet_run = run_response.get_json()["worksheet_run"]
    wait_for_job_completion(client, run_response.get_json()["job"]["id"])

    decision_response = client.post(
        f"/api/worksheet-runs/{worksheet_run['id']}/styling-decision",
        json={"decision": "confirm"},
    )
    style_job_id = decision_response.get_json()["job"]["id"]

    deadline = time.time() + 5
    style_job = None
    while time.time() < deadline:
        style_job = client.get(f"/api/jobs/{style_job_id}").get_json()["job"]
        if style_job["phase"] == "styling_apply_and_verify":
            break
        time.sleep(0.05)
    assert style_job is not None
    assert style_job["phase"] == "styling_apply_and_verify"

    cancel_response = client.post(f"/api/jobs/{style_job_id}/cancel")
    assert cancel_response.status_code == 409
    payload = cancel_response.get_json()
    assert payload["error"] == "styling_not_cancellable"
    assert "before the Gemini request is sent" in payload["message"]

    final_job = wait_for_job_completion(client, style_job_id)
    final_run = client.get(f"/api/worksheet-runs/{worksheet_run['id']}").get_json()["worksheet_run"]
    assert final_job["status"] == "failed"
    assert final_run["styling"]["status"] == "styled_failed_error"


def test_letter_bank_decoys_do_not_increase_question_count(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    draft_response = client.post(
        "/api/reward-content/generate",
        json={
            "theme": "algebra",
            "learner_band": LearnerBand.ALGEBRA.value,
            "style": "riddle",
            "language": "en",
            "difficulty_maximum": 4,
        },
    )
    draft = draft_response.get_json()["draft"]
    approve_response = client.post(f"/api/reward-content/{draft['id']}/approve")
    approved = approve_response.get_json()["draft"]

    run_response = client.post(
        "/api/worksheets/generate",
        json={
            "draft_id": approved["id"],
            "theme": "algebra",
            "learner_band": LearnerBand.ALGEBRA.value,
            "reveal_mode": "letter_bank",
            "skill_profile": "algebra",
            "difficulty_minimum": 3,
            "difficulty_maximum": 4,
            "decoy_percentage": 100,
            "seed": 91,
        },
    )

    assert run_response.status_code == 200
    worksheet_run = run_response.get_json()["worksheet_run"]
    assert worksheet_run["status"] == "generating"
    wait_for_job_completion(client, run_response.get_json()["job"]["id"])
    worksheet_run = client.get(f"/api/worksheet-runs/{worksheet_run['id']}").get_json()["worksheet_run"]
    assert worksheet_run["parameters"]["solution_letter_count"] == 11
    assert worksheet_run["parameters"]["decoy_count"] == 11
    assert worksheet_run["parameters"]["problem_count"] == 11

    manifest = read_worksheet_manifest(tmp_path / "artifacts" / "run-00001" / "worksheet-manifest.json")
    assert len(manifest.problems) == 11
    assert len(manifest.slot_assignments()) == 11
    assert len([assignment for assignment in manifest.letter_assignments if assignment.is_distractor]) == 11


def test_regenerate_honors_updated_generation_parameters(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    generate_response = client.post(
        "/api/reward-content/generate",
        json={
            "theme": "fractions",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "riddle",
            "language": "en",
            "difficulty_maximum": 2,
        },
    )
    draft = generate_response.get_json()["draft"]

    regenerate_response = client.post(
        f"/api/reward-content/{draft['id']}/regenerate",
        json={
            "theme": "counting",
            "learner_band": LearnerBand.EARLY_ARITHMETIC.value,
            "style": "question",
            "language": "en",
            "difficulty_maximum": 1,
        },
    )

    assert regenerate_response.status_code == 200
    regenerated = regenerate_response.get_json()["draft"]
    assert regenerated["theme"] == "counting"
    assert regenerated["style"] == "question"
    assert regenerated["learner_band"] == LearnerBand.EARLY_ARITHMETIC.value
    assert regenerated["generation_parameters"]["theme"] == "counting"
    assert regenerated["generation_parameters"]["learner_band"] == LearnerBand.EARLY_ARITHMETIC.value


def test_regenerate_from_solution_updates_solution_and_prompt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    generate_response = client.post(
        "/api/reward-content/generate",
        json={
            "theme": "ocean",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "riddle",
            "language": "en",
            "difficulty_maximum": 2,
        },
    )
    draft = generate_response.get_json()["draft"]

    regenerate_response = client.post(
        f"/api/reward-content/{draft['id']}/regenerate-from-solution",
        json={
            "theme": "sea life",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "question",
            "language": "en",
            "difficulty_maximum": 2,
            "solution_phrase": "Blue Tang",
        },
    )

    assert regenerate_response.status_code == 200
    regenerated = regenerate_response.get_json()["draft"]
    assert regenerated["theme"] == "sea life"
    assert regenerated["style"] == "question"
    assert regenerated["solution_phrase"] == "Blue Tang"
    assert regenerated["prompt_text"] == "What clue fits sea life without saying the answer?"
    assert regenerated["generation_parameters"]["solution_phrase"] == "Blue Tang"


def test_regenerate_from_solution_rejects_invalid_solution_phrase(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    generate_response = client.post(
        "/api/reward-content/generate",
        json={
            "theme": "ocean",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "riddle",
            "language": "en",
        },
    )
    draft = generate_response.get_json()["draft"]

    regenerate_response = client.post(
        f"/api/reward-content/{draft['id']}/regenerate-from-solution",
        json={"solution_phrase": "Blue Tang 2"},
    )

    assert regenerate_response.status_code == 400
    assert regenerate_response.get_json()["error"] == "reward_content_invalid"


def test_reject_endpoint_sets_rejected_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    generate_response = client.post(
        "/api/reward-content/generate",
        json={
            "theme": "space",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "riddle",
            "language": "en",
        },
    )
    draft = generate_response.get_json()["draft"]

    reject_response = client.post(
        f"/api/reward-content/{draft['id']}/reject",
        json={"reason": "Needs a different tone."},
    )

    assert reject_response.status_code == 200
    rejected = reject_response.get_json()["draft"]
    assert rejected["approval_state"] == ApprovalState.REJECTED.value
    assert any("Needs a different tone." in note for note in rejected["review_notes"])


def test_solution_phrase_validation_blocks_non_letter_content(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    response = client.post(
        "/api/reward-content/direct",
        json={
            "theme": "geometry",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "question",
            "language": "en",
            "difficulty_maximum": 2,
            "prompt_text": "What shape lesson uses models?",
            "solution_phrase": "3D SHAPES!",
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "reward_content_invalid"


def test_color_pool_matches_32_color_contract() -> None:
    assert len(COLOR_SEQUENCE) == 32


def test_color_by_number_rejects_difficulty_above_two(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    direct_response = client.post(
        "/api/reward-content/direct",
        json={
            "theme": "space",
            "learner_band": LearnerBand.EARLY_ARITHMETIC.value,
            "style": "question",
            "language": "en",
            "difficulty_maximum": 5,
            "reveal_mode": "color_by_number",
            "color_picture_source": "preset",
            "color_picture_preset": "rocket",
            "prompt_text": "What vehicle flies into space?",
            "solution_phrase": "ROCKET",
        },
    )
    draft = direct_response.get_json()["draft"]
    client.post(f"/api/reward-content/{draft['id']}/approve")

    run_response = client.post(
        "/api/worksheets/generate",
        json={
            "draft_id": draft["id"],
            "theme": "space",
            "learner_band": LearnerBand.EARLY_ARITHMETIC.value,
            "reveal_mode": "color_by_number",
            "skill_profile": "subtraction_and_addition",
            "difficulty_minimum": 1,
            "difficulty_maximum": 5,
            "decoy_percentage": 0,
            "color_picture_source": "preset",
            "color_picture_preset": "rocket",
        },
    )

    assert run_response.status_code == 400
    assert "difficulty 1 or 2" in run_response.get_json()["message"]


def test_invalid_skill_profile_for_learner_band_returns_400(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    draft_response = client.post(
        "/api/reward-content/generate",
        json={
            "theme": "counting",
            "learner_band": LearnerBand.EARLY_ARITHMETIC.value,
            "style": "riddle",
            "language": "en",
        },
    )
    draft = draft_response.get_json()["draft"]
    client.patch(
        f"/api/reward-content/{draft['id']}",
        json={
            "prompt_text": "What helps you count carefully?",
            "solution_phrase": "MATH",
        },
    )
    client.post(f"/api/reward-content/{draft['id']}/approve")

    run_response = client.post(
        "/api/worksheets/generate",
        json={
            "draft_id": draft["id"],
            "theme": "counting",
            "learner_band": LearnerBand.EARLY_ARITHMETIC.value,
            "reveal_mode": "letter_bank",
            "skill_profile": "multiplication_focus",
            "difficulty_minimum": 1,
            "difficulty_maximum": 2,
        },
    )

    assert run_response.status_code == 400
    assert run_response.get_json()["error"] == "worksheet_generation_failed"


def test_worksheet_problem_count_ignores_non_letter_characters_in_legacy_solution_phrase(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "db" / "app.sqlite3"
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("APP_DB_PATH", str(database_path))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()
    repository = AppRepository(database_path)
    draft = repository.create_reward_content_draft(
        candidate=RewardContentCandidate(
            prompt_text="What phrase describes strong number sense?",
            solution_phrase="NUMBER SENSE!!",
            theme="legacy import",
            source="legacy_import",
            approval_state=ApprovalState.APPROVED,
            style="question",
            language="en",
        ),
        learner_band=LearnerBand.UPPER_ELEMENTARY.value,
        generation_parameters={
            "theme": "legacy import",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "question",
            "language": "en",
            "difficulty_maximum": 3,
            "solution_length_guidance": "Prefer a multi-word answer phrase with 2 to 4 words so advanced worksheets reveal longer answers.",
        },
    )

    run_response = client.post(
        "/api/worksheets/generate",
        json={
            "draft_id": draft["id"],
            "theme": "legacy import",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "reveal_mode": "letter_bank",
            "skill_profile": "subtraction_and_addition",
            "difficulty_minimum": 1,
            "difficulty_maximum": 3,
            "decoy_percentage": 0,
            "seed": 91,
        },
    )

    assert run_response.status_code == 200
    worksheet_run = run_response.get_json()["worksheet_run"]
    assert worksheet_run["status"] == "generating"
    wait_for_job_completion(client, run_response.get_json()["job"]["id"])
    worksheet_run = client.get(f"/api/worksheet-runs/{worksheet_run['id']}").get_json()["worksheet_run"]
    assert worksheet_run["parameters"]["problem_count"] == 11

    manifest = read_worksheet_manifest(artifact_root / "run-00001" / "worksheet-manifest.json")
    assert len(manifest.problems) == 11
    assert len(manifest.slot_assignments()) == 11
    assert all(not assignment.is_distractor for assignment in manifest.letter_assignments)


def test_generate_worksheet_with_decoys_adds_extra_lookup_entries(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    direct_response = client.post(
        "/api/reward-content/direct",
        json={
            "theme": "space",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "question",
            "language": "en",
            "difficulty_maximum": 3,
            "prompt_text": "What helps you solve carefully?",
            "solution_phrase": "MATH",
        },
    )
    draft = direct_response.get_json()["draft"]
    client.post(f"/api/reward-content/{draft['id']}/approve")

    run_response = client.post(
        "/api/worksheets/generate",
        json={
            "draft_id": draft["id"],
            "theme": "space",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "reveal_mode": "letter_bank",
            "skill_profile": "subtraction_and_addition",
            "difficulty_minimum": 1,
            "difficulty_maximum": 3,
            "decoy_percentage": 100,
        },
    )

    assert run_response.status_code == 200
    worksheet_run = run_response.get_json()["worksheet_run"]
    assert worksheet_run["status"] == "generating"
    wait_for_job_completion(client, run_response.get_json()["job"]["id"])
    worksheet_run = client.get(f"/api/worksheet-runs/{worksheet_run['id']}").get_json()["worksheet_run"]
    assert worksheet_run["parameters"]["solution_letter_count"] == 4
    assert worksheet_run["parameters"]["decoy_count"] == 4
    assert worksheet_run["parameters"]["problem_count"] == 4

    manifest = read_worksheet_manifest(tmp_path / "artifacts" / "run-00001" / "worksheet-manifest.json")
    distractors = [assignment for assignment in manifest.letter_assignments if assignment.is_distractor]
    assert len(manifest.problems) == 4
    assert len(manifest.slot_assignments()) == 4
    assert len(distractors) == 4
    assert all(assignment.reveal_token != "DISTRACTOR" for assignment in distractors)


def test_generate_color_by_number_uses_palette_size_for_problem_count(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    direct_response = client.post(
        "/api/reward-content/direct",
        json={
            "theme": "nature",
            "learner_band": LearnerBand.EARLY_ARITHMETIC.value,
            "style": "question",
            "language": "en",
            "difficulty_maximum": 2,
            "reveal_mode": "color_by_number",
            "color_picture_source": "preset",
            "color_picture_preset": "heart",
            "prompt_text": "What shape is often used to show love?",
            "solution_phrase": "Heart",
        },
    )
    draft = direct_response.get_json()["draft"]
    client.post(f"/api/reward-content/{draft['id']}/approve")

    run_response = client.post(
        "/api/worksheets/generate",
        json={
            "draft_id": draft["id"],
            "theme": "nature",
            "learner_band": LearnerBand.EARLY_ARITHMETIC.value,
            "reveal_mode": "color_by_number",
            "skill_profile": "subtraction_and_addition",
            "difficulty_minimum": 1,
            "difficulty_maximum": 2,
            "decoy_percentage": 100,
            "color_picture_source": "preset",
            "color_picture_preset": "heart",
        },
    )

    assert run_response.status_code == 200
    worksheet_run = run_response.get_json()["worksheet_run"]
    assert worksheet_run["status"] == "generating"
    wait_for_job_completion(client, run_response.get_json()["job"]["id"])
    worksheet_run = client.get(f"/api/worksheet-runs/{worksheet_run['id']}").get_json()["worksheet_run"]
    assert worksheet_run["parameters"]["solution_letter_count"] == 5
    assert worksheet_run["parameters"]["problem_count"] == 6
    assert worksheet_run["parameters"]["decoy_count"] == 0

    manifest = read_worksheet_manifest(tmp_path / "artifacts" / "run-00001" / "worksheet-manifest.json")
    assert len(manifest.problems) == 6
    assert len(manifest.slot_assignments()) == 0
    assert len(manifest.active_assignments()) == 6
    grid_labels = {cell for row in manifest.spec.layout.color_grid_cells for cell in row}
    assignment_labels = {assignment.reveal_token for assignment in manifest.active_assignments()}
    assert grid_labels == assignment_labels


def test_styling_decision_endpoint_confirms_requested_styling(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    direct_response = client.post(
        "/api/reward-content/direct",
        json={
            "theme": "space",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "question",
            "language": "en",
            "difficulty_maximum": 3,
            "prompt_text": "What helps you solve carefully?",
            "solution_phrase": "MATH",
        },
    )
    draft = direct_response.get_json()["draft"]
    client.post(f"/api/reward-content/{draft['id']}/approve")

    run_response = client.post(
        "/api/worksheets/generate",
        json={
            "draft_id": draft["id"],
            "theme": "space",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "reveal_mode": "letter_bank",
            "skill_profile": "subtraction_and_addition",
            "difficulty_minimum": 1,
            "difficulty_maximum": 3,
            "decoy_percentage": 0,
            "apply_image_styling": True,
            "image_style_name": "watercolor",
            "image_color_mode": "color",
            "image_ink_saver": False,
        },
    )

    worksheet_run = run_response.get_json()["worksheet_run"]
    assert worksheet_run["status"] == "generating"
    wait_for_job_completion(client, run_response.get_json()["job"]["id"])
    worksheet_run = client.get(f"/api/worksheet-runs/{worksheet_run['id']}").get_json()["worksheet_run"]
    assert worksheet_run["styling"]["status"] == "awaiting_confirmation"
    assert worksheet_run["lifecycle"]["phase"] == "awaiting_styling_confirmation"

    def fake_apply_image_styling(self, *, worksheet_run_id: int, prompt_text: str, progress_callback=None, should_cancel=None):  # noqa: ANN001
        run_dir = tmp_path / "artifacts" / f"run-{worksheet_run_id:05d}" / "styled"
        run_dir.mkdir(parents=True, exist_ok=True)
        styled_png = run_dir / "worksheet-preview-styled.png"
        styled_pdf = run_dir / "worksheet-preview-styled.pdf"
        styled_background = run_dir / "worksheet-preview-styled-background.png"
        verification_png = run_dir / "styled-verification-overlay.png"
        verification_json = run_dir / "styled-verification-report.json"
        for path in (styled_png, styled_background, verification_png):
            path.write_bytes(b"png")
        styled_pdf.write_bytes(b"%PDF-1.4\n")
        verification_json.write_text('{"passed": true}', encoding="utf-8")
        if progress_callback:
            progress_callback("apply Gemini styling and verify")
        return {
            "verified": True,
            "artifact_group": f"run-{worksheet_run_id:05d}/styled",
            "thumbnail_path": styled_background,
            "artifacts": [
                {"artifact_kind": "worksheet_styled_preview", "output_format": "png", "path": styled_png, "display_name": "Styled Preview PNG"},
                {"artifact_kind": "worksheet_styled_preview", "output_format": "pdf", "path": styled_pdf, "display_name": "Styled Preview PDF"},
                {"artifact_kind": "worksheet_styled_background", "output_format": "png", "path": styled_background, "display_name": "Styled Background PNG"},
                {"artifact_kind": "worksheet_styling_verification", "output_format": "json", "path": verification_json, "display_name": "Worksheet Styling Verification"},
                {"artifact_kind": "worksheet_styling_verification", "output_format": "png", "path": verification_png, "display_name": "Worksheet Styling Verification Overlay"},
            ],
                "final_prompt": prompt_text,
                "verification_report": type("Report", (), {"mismatch_count": 0})(),
            "style_check_artifact_path": verification_png,
        }

    monkeypatch.setattr(WorksheetRunGenerationService, "apply_image_styling", fake_apply_image_styling)

    decision_response = client.post(
        f"/api/worksheet-runs/{worksheet_run['id']}/styling-decision",
        json={"decision": "confirm", "image_additional_guidance": "Add a light star border only in the empty outer frame."},
    )

    assert decision_response.status_code == 200
    updated = decision_response.get_json()["worksheet_run"]
    assert updated["styling"]["status"] == "confirmed_pending_styling"
    assert updated["styling"]["verification_status"] == "pending"
    assert updated["parameters"]["image_additional_guidance"] == "Add a light star border only in the empty outer frame."
    assert "Add a light star border only in the empty outer frame." in updated["styling"]["prompt_text"]
    assert updated["lifecycle"]["phase"] == "styling_queued"
    assert decision_response.get_json()["job"]["job_type"] == "worksheet_style"
    assert decision_response.get_json()["job"]["phase"] in {"styling_queued", "styling_prepare", "styling_apply_and_verify", "styling_complete"}

    deadline = time.time() + 5
    while time.time() < deadline:
        job_response = client.get(f"/api/jobs/{decision_response.get_json()['job']['id']}")
        job = job_response.get_json()["job"]
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)
    assert job["status"] == "completed"
    final_run = client.get(f"/api/worksheet-runs/{worksheet_run['id']}").get_json()["worksheet_run"]
    assert final_run["styling"]["status"] == "styled_verified"
    assert final_run["styling"]["verification_status"] == "passed"
    assert final_run["parameters"]["image_additional_guidance"] == "Add a light star border only in the empty outer frame."
    assert "Add a light star border only in the empty outer frame." in final_run["styling"]["prompt_text"]
    assert final_run["lifecycle"]["phase"] == "styled_verified"
    assert final_run["styling"]["styled_artifact_group"] == f"run-{worksheet_run['id']:05d}/styled"
    assert final_run["styling"]["styled_thumbnail_path"] == f"run-{worksheet_run['id']:05d}/styled/worksheet-preview-styled-background.png"
    assert any(artifact["artifact_kind"] == "worksheet_styled_preview" for artifact in final_run["artifacts"])
    assert any(artifact["artifact_kind"] == "worksheet_styled_background" for artifact in final_run["artifacts"])
    gallery_items = client.get("/api/gallery").get_json()["items"]
    assert gallery_items[0]["styling"]["status"] == "styled_verified"
    assert gallery_items[0]["styling"]["verification_status"] == "passed"
    assert gallery_items[0]["styling"]["styled_thumbnail_path"] == f"run-{worksheet_run['id']:05d}/styled/worksheet-preview-styled-background.png"


def test_styling_decision_rejects_guidance_over_500_characters(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    direct_response = client.post(
        "/api/reward-content/direct",
        json={
            "theme": "space",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "question",
            "language": "en",
            "difficulty_maximum": 3,
            "prompt_text": "What helps you solve carefully?",
            "solution_phrase": "MATH",
        },
    )
    draft = direct_response.get_json()["draft"]
    client.post(f"/api/reward-content/{draft['id']}/approve")

    run_response = client.post(
        "/api/worksheets/generate",
        json={
            "draft_id": draft["id"],
            "theme": "space",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "reveal_mode": "letter_bank",
            "skill_profile": "subtraction_and_addition",
            "difficulty_minimum": 1,
            "difficulty_maximum": 3,
            "decoy_percentage": 0,
            "apply_image_styling": True,
            "image_style_name": "watercolor",
            "image_color_mode": "color",
            "image_ink_saver": False,
        },
    )
    worksheet_run = run_response.get_json()["worksheet_run"]
    wait_for_job_completion(client, run_response.get_json()["job"]["id"])

    decision_response = client.post(
        f"/api/worksheet-runs/{worksheet_run['id']}/styling-decision",
        json={"decision": "confirm", "image_additional_guidance": "x" * 501},
    )

    assert decision_response.status_code == 400
    assert decision_response.get_json()["error"] == "invalid_styling_guidance"
    assert "500 characters or fewer" in decision_response.get_json()["message"]


def test_styling_decision_endpoint_can_cancel_after_plain_review(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    direct_response = client.post(
        "/api/reward-content/direct",
        json={
            "theme": "nature",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "question",
            "language": "en",
            "difficulty_maximum": 3,
            "prompt_text": "What helps you solve carefully?",
            "solution_phrase": "MATH",
        },
    )
    draft = direct_response.get_json()["draft"]
    client.post(f"/api/reward-content/{draft['id']}/approve")

    run_response = client.post(
        "/api/worksheets/generate",
        json={
            "draft_id": draft["id"],
            "theme": "nature",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "reveal_mode": "letter_bank",
            "skill_profile": "subtraction_and_addition",
            "difficulty_minimum": 1,
            "difficulty_maximum": 3,
            "decoy_percentage": 0,
            "apply_image_styling": True,
            "image_style_name": "chalkboard",
            "image_color_mode": "black_and_white",
            "image_ink_saver": True,
        },
    )

    worksheet_run = run_response.get_json()["worksheet_run"]
    assert worksheet_run["status"] == "generating"
    wait_for_job_completion(client, run_response.get_json()["job"]["id"])
    worksheet_run = client.get(f"/api/worksheet-runs/{worksheet_run['id']}").get_json()["worksheet_run"]
    decision_response = client.post(
        f"/api/worksheet-runs/{worksheet_run['id']}/styling-decision",
        json={"decision": "cancel"},
    )

    assert decision_response.status_code == 200
    updated = decision_response.get_json()["worksheet_run"]
    assert updated["styling"]["status"] == "cancelled_after_plain_review"
    assert updated["styling"]["verification_status"] == "not_requested"
    assert updated["lifecycle"]["phase"] == "styling_cancelled_plain_retained"
    assert decision_response.get_json()["job"] is None


def test_styling_retry_endpoint_retries_failed_styling_without_overwriting_prior_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "db" / "app.sqlite3"))
    monkeypatch.setenv("APP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GEMINI_API_KEY", "present")

    app = create_app(reward_content_service=RewardContentService(generator=FakeGenerator()))
    client = app.test_client()

    direct_response = client.post(
        "/api/reward-content/direct",
        json={
            "theme": "space",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "style": "question",
            "language": "en",
            "difficulty_maximum": 3,
            "prompt_text": "What helps you solve carefully?",
            "solution_phrase": "MATH",
        },
    )
    draft = direct_response.get_json()["draft"]
    client.post(f"/api/reward-content/{draft['id']}/approve")

    run_response = client.post(
        "/api/worksheets/generate",
        json={
            "draft_id": draft["id"],
            "theme": "space",
            "learner_band": LearnerBand.UPPER_ELEMENTARY.value,
            "reveal_mode": "letter_bank",
            "skill_profile": "subtraction_and_addition",
            "difficulty_minimum": 1,
            "difficulty_maximum": 3,
            "decoy_percentage": 0,
            "apply_image_styling": True,
            "image_style_name": "watercolor",
            "image_color_mode": "color",
            "image_ink_saver": False,
        },
    )

    worksheet_run = run_response.get_json()["worksheet_run"]
    wait_for_job_completion(client, run_response.get_json()["job"]["id"])
    worksheet_run = client.get(f"/api/worksheet-runs/{worksheet_run['id']}").get_json()["worksheet_run"]

    attempt_counter = {"count": 0}

    def fake_apply_image_styling(self, *, worksheet_run_id: int, prompt_text: str, progress_callback=None, should_cancel=None):  # noqa: ANN001
        attempt_counter["count"] += 1
        if attempt_counter["count"] == 1:
            run_dir = tmp_path / "artifacts" / f"run-{worksheet_run_id:05d}" / "styled"
            suffix = ""
            artifact_group = f"run-{worksheet_run_id:05d}/styled"
        else:
            run_dir = tmp_path / "artifacts" / f"run-{worksheet_run_id:05d}" / "styled" / "retry-01"
            suffix = " (Retry 1)"
            artifact_group = f"run-{worksheet_run_id:05d}/styled/retry-01"
        run_dir.mkdir(parents=True, exist_ok=True)
        styled_png = run_dir / "worksheet-preview-styled.png"
        styled_pdf = run_dir / "worksheet-preview-styled.pdf"
        styled_background = run_dir / "worksheet-preview-styled-background.png"
        verification_png = run_dir / "styled-verification-overlay.png"
        verification_json = run_dir / "styled-verification-report.json"
        debug_json = run_dir / "worksheet-styling-debug.json"
        semantic_foreground = run_dir / "worksheet-preview-semantic-foreground.png"
        for path in (styled_png, styled_background, verification_png, semantic_foreground):
            path.write_bytes(b"png")
        styled_pdf.write_bytes(b"%PDF-1.4\n")
        verification_json.write_text('{"passed": true}', encoding="utf-8")
        debug_json.write_text('{"debug": true}', encoding="utf-8")
        if progress_callback:
            progress_callback("apply Gemini styling and verify")
            return {
                "verified": attempt_counter["count"] > 1,
                "artifact_group": artifact_group,
                "thumbnail_path": styled_background,
                "artifacts": [
                    {"artifact_kind": "worksheet_styled_preview", "output_format": "png", "path": styled_png, "display_name": f"Styled Preview PNG{suffix}"},
                    {"artifact_kind": "worksheet_styled_preview", "output_format": "pdf", "path": styled_pdf, "display_name": f"Styled Preview PDF{suffix}"},
                    {"artifact_kind": "worksheet_styled_background", "output_format": "png", "path": styled_background, "display_name": f"Styled Background PNG{suffix}"},
                {"artifact_kind": "worksheet_styling_debug", "output_format": "json", "path": debug_json, "display_name": f"Worksheet Styling Debug{suffix}"},
                {"artifact_kind": "worksheet_styling_verification", "output_format": "json", "path": verification_json, "display_name": f"Worksheet Styling Verification{suffix}"},
                {"artifact_kind": "worksheet_styling_verification", "output_format": "png", "path": verification_png, "display_name": f"Worksheet Styling Verification Overlay{suffix}"},
                {"artifact_kind": "worksheet_semantic_foreground", "output_format": "png", "path": semantic_foreground, "display_name": f"Semantic Foreground PNG{suffix}"},
            ],
            "final_prompt": prompt_text,
            "verification_report": type("Report", (), {"mismatch_count": 3 if attempt_counter["count"] == 1 else 0})(),
            "style_check_artifact_path": verification_png,
        }

    monkeypatch.setattr(WorksheetRunGenerationService, "apply_image_styling", fake_apply_image_styling)

    decision_response = client.post(
        f"/api/worksheet-runs/{worksheet_run['id']}/styling-decision",
        json={"decision": "confirm"},
    )
    assert decision_response.status_code == 200
    first_styling_job = wait_for_job_completion(client, decision_response.get_json()["job"]["id"])
    assert first_styling_job["status"] == "failed"

    failed_run = client.get(f"/api/worksheet-runs/{worksheet_run['id']}").get_json()["worksheet_run"]
    assert failed_run["styling"]["status"] == "styled_failed_verification"
    assert failed_run["lifecycle"]["phase"] == "styled_failed_plain_retained"
    assert failed_run["lifecycle"]["can_retry_styling"] is True
    assert any(artifact["relative_path"] == f"run-{worksheet_run['id']:05d}/styled/worksheet-preview-styled.png" for artifact in failed_run["artifacts"])

    retry_response = client.post(f"/api/worksheet-runs/{worksheet_run['id']}/styling-retry", json={})
    assert retry_response.status_code == 200
    retried_run = retry_response.get_json()["worksheet_run"]
    assert retried_run["styling"]["status"] in {"retry_pending_styling", "styled_verified"}
    assert retried_run["lifecycle"]["phase"] in {"styling_queued", "styled_verified"}
    assert retry_response.get_json()["job"]["job_type"] == "worksheet_style"
    assert retry_response.get_json()["job"]["phase"] in {
        "styling_retry_queued",
        "styling_retry_prepare",
        "styling_apply_and_verify",
        "styling_complete",
    }

    retry_job = wait_for_job_completion(client, retry_response.get_json()["job"]["id"])
    assert retry_job["status"] == "completed"

    final_run = client.get(f"/api/worksheet-runs/{worksheet_run['id']}").get_json()["worksheet_run"]
    assert final_run["styling"]["status"] == "styled_verified"
    assert final_run["styling"]["verification_status"] == "passed"
    assert final_run["lifecycle"]["phase"] == "styled_verified"
    assert final_run["styling"]["styled_artifact_group"] == f"run-{worksheet_run['id']:05d}/styled/retry-01"
    assert final_run["styling"]["styled_thumbnail_path"] == f"run-{worksheet_run['id']:05d}/styled/retry-01/worksheet-preview-styled-background.png"
    artifact_paths = {artifact["relative_path"] for artifact in final_run["artifacts"]}
    assert f"run-{worksheet_run['id']:05d}/styled/worksheet-preview-styled.png" in artifact_paths
    assert f"run-{worksheet_run['id']:05d}/styled/retry-01/worksheet-preview-styled.png" in artifact_paths
