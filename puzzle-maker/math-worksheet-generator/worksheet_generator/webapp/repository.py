from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sqlite3
from uuid import uuid4

from ..models import RewardContentCandidate
from .db import connect


JOB_PHASE_LABELS = {
    "queued": "Queued",
    "cancelled": "Cancelled",
    "draft_generation_requested": "Draft Requested",
    "draft_generation_running": "Generating Draft",
    "draft_generation_complete": "Draft Ready",
    "draft_generation_failed": "Draft Failed",
    "draft_generation_cancelled": "Draft Cancelled",
    "draft_regeneration_requested": "Regeneration Requested",
    "draft_regeneration_running": "Regenerating Draft",
    "draft_regeneration_complete": "Draft Regenerated",
    "draft_regeneration_failed": "Draft Regeneration Failed",
    "draft_regeneration_cancelled": "Draft Regeneration Cancelled",
    "worksheet_generation_queued": "Worksheet Queued",
    "worksheet_generation_prepare": "Preparing Worksheet",
    "worksheet_generation_assemble": "Assembling Worksheet",
    "worksheet_generation_export": "Exporting Worksheet",
    "worksheet_generation_write_metadata": "Writing Metadata",
    "worksheet_generation_persist_artifacts": "Persisting Artifacts",
    "worksheet_generation_complete": "Worksheet Ready",
    "worksheet_generation_failed": "Worksheet Failed",
    "worksheet_generation_cancelled": "Worksheet Cancelled",
    "styling_queued": "Styling Queued",
    "styling_retry_queued": "Styling Retry Queued",
    "styling_prepare": "Preparing Styling",
    "styling_retry_prepare": "Preparing Styling Retry",
    "styling_render_foreground": "Rendering Foreground",
    "styling_refine_prompt": "Refining Prompt",
    "styling_apply_and_verify": "Applying And Verifying",
    "styling_write_artifacts": "Writing Styled Artifacts",
    "styling_complete": "Styling Complete",
    "styling_failed": "Styling Failed",
    "styling_cancelled": "Styling Cancelled",
}

RUN_LIFECYCLE_LABELS = {
    "draft_ready_for_review": "Draft Ready For Review",
    "draft_approved": "Draft Approved",
    "worksheet_generation_queued": "Worksheet Queued",
    "worksheet_generation_running": "Worksheet Generating",
    "plain_worksheet_ready": "Plain Worksheet Ready",
    "awaiting_styling_confirmation": "Awaiting Styling Confirmation",
    "styling_queued": "Styling Queued",
    "styling_running": "Styling Running",
    "styled_verified": "Styled Verified",
    "styled_failed_plain_retained": "Styled Failed, Plain Retained",
    "styling_cancelled_plain_retained": "Styling Cancelled, Plain Retained",
    "run_cancelled": "Run Cancelled",
    "run_failed": "Run Failed",
}

_UNSET = object()


@dataclass(frozen=True)
class AppRepository:
    database_path: Path

    def create_workflow_session(self, *, controls: dict[str, object], phase: str = "starting", status: str = "active") -> dict[str, object]:
        token = str(uuid4())
        with connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO workflow_sessions (token, status, phase, controls_json)
                VALUES (?, ?, ?, ?)
                """,
                (token, status, phase, json.dumps(controls)),
            )
        return self.get_workflow_session(token)

    def update_workflow_session(
        self,
        token: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        controls: dict[str, object] | None = None,
        draft_id: int | None | object = _UNSET,
        generation_job_id: int | None | object = _UNSET,
        worksheet_run_id: int | None | object = _UNSET,
    ) -> None:
        existing = self.get_workflow_session(token)
        with connect(self.database_path) as connection:
            connection.execute(
                """
                UPDATE workflow_sessions
                SET status = ?,
                    phase = ?,
                    controls_json = ?,
                    draft_id = ?,
                    generation_job_id = ?,
                    worksheet_run_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE token = ?
                """,
                (
                    status if status is not None else existing["status"],
                    phase if phase is not None else existing["phase"],
                    json.dumps(controls if controls is not None else existing["controls"]),
                    existing["draft_id"] if draft_id is _UNSET else draft_id,
                    existing["generation_job_id"] if generation_job_id is _UNSET else generation_job_id,
                    existing["worksheet_run_id"] if worksheet_run_id is _UNSET else worksheet_run_id,
                    token,
                ),
            )

    def get_workflow_session(self, token: str) -> dict[str, object]:
        with connect(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT token, created_at, updated_at, status, phase, controls_json, draft_id, generation_job_id, worksheet_run_id
                FROM workflow_sessions
                WHERE token = ?
                """,
                (token,),
            ).fetchone()
        if row is None:
            raise KeyError(token)
        draft_id = int(row["draft_id"]) if row["draft_id"] is not None else None
        generation_job_id = int(row["generation_job_id"]) if row["generation_job_id"] is not None else None
        worksheet_run_id = int(row["worksheet_run_id"]) if row["worksheet_run_id"] is not None else None
        draft = self.get_reward_content_draft(draft_id) if draft_id is not None else None
        job = self.get_generation_job(generation_job_id) if generation_job_id is not None else None
        worksheet_run = self.get_worksheet_run(worksheet_run_id) if worksheet_run_id is not None else None
        effective_phase = str(row["phase"])
        if job and job["status"] not in {"completed", "failed", "cancelled"}:
            effective_phase = str(job["phase"])
        elif worksheet_run:
            lifecycle_phase = str(worksheet_run["lifecycle"]["phase"])
            effective_phase = "review_plain_run" if lifecycle_phase == "awaiting_styling_confirmation" else lifecycle_phase
        elif draft:
            effective_phase = "draft_review"
        return {
            "token": str(row["token"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "status": str(row["status"]),
            "phase": effective_phase,
            "controls": json.loads(row["controls_json"]),
            "draft_id": draft_id,
            "generation_job_id": generation_job_id,
            "worksheet_run_id": worksheet_run_id,
            "draft": draft,
            "job": job,
            "worksheet_run": worksheet_run,
        }

    def list_gallery_items(
        self,
        *,
        limit: int = 24,
        offset: int = 0,
        search: str | None = None,
        learner_band: str | None = None,
        reveal_mode: str | None = None,
        skill_profile: str | None = None,
        difficulty_minimum: int | None = None,
        difficulty_maximum: int | None = None,
        styling_requested: bool | None = None,
        styling_status: str | None = None,
        picture_source: str | None = None,
        picture_preset: str | None = None,
        seed: int | None = None,
        sort: str = "created_desc",
    ) -> dict[str, object]:
        base_query = """
        FROM worksheet_runs
        WHERE 1 = 1
        """
        clauses: list[str] = []
        params: list[object] = []
        if search:
            clauses.append(
                """
                AND (
                    title LIKE ?
                    OR COALESCE(theme, '') LIKE ?
                    OR COALESCE(prompt_text, '') LIKE ?
                    OR COALESCE(solution_phrase, '') LIKE ?
                    OR COALESCE(styling_style_name, '') LIKE ?
                    OR COALESCE(styling_color_mode, '') LIKE ?
                    OR COALESCE(color_picture_preset, '') LIKE ?
                    OR COALESCE(color_picture_source, '') LIKE ?
                )
                """
            )
            search_value = f"%{search}%"
            params.extend([search_value] * 8)
        if learner_band:
            clauses.append(" AND learner_band = ?")
            params.append(learner_band)
        if reveal_mode:
            clauses.append(" AND reveal_mode = ?")
            params.append(reveal_mode)
        if skill_profile:
            clauses.append(" AND skill_profile = ?")
            params.append(skill_profile)
        if difficulty_minimum is not None:
            clauses.append(" AND difficulty_maximum >= ?")
            params.append(int(difficulty_minimum))
        if difficulty_maximum is not None:
            clauses.append(" AND difficulty_minimum <= ?")
            params.append(int(difficulty_maximum))
        if styling_requested is not None:
            clauses.append(" AND styling_requested = ?")
            params.append(1 if styling_requested else 0)
        if styling_status:
            clauses.append(" AND styling_status = ?")
            params.append(styling_status)
        if picture_source:
            clauses.append(" AND color_picture_source = ?")
            params.append(picture_source)
        if picture_preset:
            clauses.append(" AND color_picture_preset = ?")
            params.append(picture_preset)
        if seed is not None:
            clauses.append(" AND seed = ?")
            params.append(int(seed))

        order_by = {
            "created_desc": "created_at DESC, id DESC",
            "created_asc": "created_at ASC, id ASC",
            "updated_desc": "updated_at DESC, id DESC",
            "title_asc": "title COLLATE NOCASE ASC, id DESC",
        }.get(sort, "created_at DESC, id DESC")

        query = """
        SELECT
            id,
            created_at,
            updated_at,
            status,
            lifecycle_phase,
            title,
            learner_band,
            reveal_mode,
            skill_profile,
            theme,
            prompt_text,
            solution_phrase,
            parameters_json,
            styling_requested,
            styling_style_name,
            styling_color_mode,
            styling_ink_saver,
            styling_status,
            styling_verification_status,
            styling_model,
            styling_prompt_text,
            styled_artifact_group,
            styled_thumbnail_path,
            style_check_artifact_path,
            artifact_group,
            thumbnail_path
        """
        filter_sql = base_query + "".join(clauses)
        query += filter_sql
        query += f" ORDER BY {order_by} LIMIT ? OFFSET ?"
        query_params = [*params, limit, offset]
        with connect(self.database_path) as connection:
            total_count = int(
                connection.execute(f"SELECT COUNT(*) {filter_sql}", tuple(params)).fetchone()[0]
            )
            rows = connection.execute(query, tuple(query_params)).fetchall()
            run_ids = [row["id"] for row in rows]
            artifact_rows = connection.execute(
                """
                SELECT id, worksheet_run_id, artifact_kind, output_format, relative_path, display_name
                FROM worksheet_artifacts
                WHERE worksheet_run_id IN ({placeholders})
                ORDER BY id ASC
                """.format(placeholders=",".join("?" for _ in run_ids) if run_ids else "NULL"),
                tuple(run_ids),
            ).fetchall() if run_ids else []

        artifacts_by_run: dict[int, list[dict[str, object]]] = {}
        for row in artifact_rows:
            artifacts_by_run.setdefault(int(row["worksheet_run_id"]), []).append(
                {
                    "id": int(row["id"]) if "id" in row.keys() else None,
                    "artifact_kind": row["artifact_kind"],
                    "output_format": row["output_format"],
                    "relative_path": row["relative_path"],
                    "display_name": row["display_name"],
                }
            )

        items = [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "status": row["status"],
                "lifecycle": self._run_lifecycle_payload(row),
                "title": row["title"],
                "learner_band": row["learner_band"],
                "reveal_mode": row["reveal_mode"],
                "skill_profile": row["skill_profile"],
                "theme": row["theme"],
                "prompt_text": row["prompt_text"],
                "solution_phrase": row["solution_phrase"],
                "parameters": json.loads(row["parameters_json"]),
                "styling": {
                    "requested": bool(row["styling_requested"]),
                    "style_name": row["styling_style_name"],
                    "color_mode": row["styling_color_mode"],
                    "ink_saver": bool(row["styling_ink_saver"]),
                    "status": row["styling_status"],
                    "verification_status": row["styling_verification_status"],
                    "model": row["styling_model"],
                    "prompt_text": row["styling_prompt_text"],
                    "styled_artifact_group": row["styled_artifact_group"],
                    "styled_thumbnail_path": row["styled_thumbnail_path"],
                    "style_check_artifact_path": row["style_check_artifact_path"],
                },
                "artifact_group": row["artifact_group"],
                "thumbnail_path": row["thumbnail_path"],
                "artifacts": artifacts_by_run.get(int(row["id"]), []),
            }
            for row in rows
        ]

        return {
            "items": items,
            "pagination": {
                "offset": offset,
                "limit": limit,
                "returned": len(items),
                "total": total_count,
                "has_more": offset + len(items) < total_count,
            },
        }

    def counts(self) -> dict[str, int]:
        with connect(self.database_path) as connection:
            worksheet_count = connection.execute("SELECT COUNT(*) FROM worksheet_runs").fetchone()[0]
            artifact_count = connection.execute("SELECT COUNT(*) FROM worksheet_artifacts").fetchone()[0]
            job_count = connection.execute("SELECT COUNT(*) FROM generation_jobs").fetchone()[0]
            draft_count = connection.execute("SELECT COUNT(*) FROM reward_content_drafts").fetchone()[0]

        return {
            "worksheet_runs": int(worksheet_count),
            "artifacts": int(artifact_count),
            "jobs": int(job_count),
            "reward_content_drafts": int(draft_count),
        }

    def list_worksheet_run_ids(self) -> list[int]:
        with connect(self.database_path) as connection:
            rows = connection.execute("SELECT id FROM worksheet_runs ORDER BY id ASC").fetchall()
        return [int(row["id"]) for row in rows]

    def vacuum_and_analyze(self) -> None:
        connection = sqlite3.connect(self.database_path, isolation_level=None)
        try:
            connection.execute("VACUUM")
            connection.execute("ANALYZE")
        finally:
            connection.close()

    def list_running_generation_jobs(self) -> list[dict[str, object]]:
        query = """
        SELECT id, created_at, updated_at, started_at, finished_at, claimed_by, job_type, status, phase, requested_parameters_json, progress_message, worksheet_run_id
        FROM generation_jobs
        WHERE status = ?
        ORDER BY id ASC
        """
        with connect(self.database_path) as connection:
            rows = connection.execute(query, ("running",)).fetchall()
        jobs: list[dict[str, object]] = []
        for row in rows:
            phase = row["phase"] or self._default_job_phase(row["job_type"], row["status"])
            jobs.append(
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                    "claimed_by": row["claimed_by"],
                    "job_type": row["job_type"],
                    "status": row["status"],
                    "phase": phase,
                    "phase_label": JOB_PHASE_LABELS.get(phase, "In Progress"),
                    "requested_parameters": json.loads(row["requested_parameters_json"]),
                    "progress_message": row["progress_message"],
                    "worksheet_run_id": row["worksheet_run_id"],
                }
            )
        return jobs

    def list_worksheet_runs_awaiting_styling_confirmation_before(self, *, cutoff_seconds: float) -> list[dict[str, object]]:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=cutoff_seconds)
        query = """
        SELECT id
        FROM worksheet_runs
        WHERE lifecycle_phase = ? AND updated_at <= ?
        ORDER BY id ASC
        """
        with connect(self.database_path) as connection:
            rows = connection.execute(
                query,
                ("awaiting_styling_confirmation", cutoff.strftime("%Y-%m-%d %H:%M:%S")),
            ).fetchall()
        return [self.get_worksheet_run(int(row["id"])) for row in rows]

    def create_generation_job(
        self,
        *,
        job_type: str,
        requested_parameters: dict[str, object],
        progress_message: str,
        worksheet_run_id: int | None = None,
        phase: str = "queued",
    ) -> int:
        query = """
        INSERT INTO generation_jobs (job_type, status, phase, requested_parameters_json, progress_message, worksheet_run_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """
        with connect(self.database_path) as connection:
            cursor = connection.execute(
                query,
                (
                    job_type,
                    "queued",
                    phase,
                    json.dumps(requested_parameters),
                    progress_message,
                    worksheet_run_id,
                ),
            )
            return int(cursor.lastrowid)

    def get_generation_job(self, job_id: int) -> dict[str, object]:
        query = """
        SELECT id, created_at, updated_at, started_at, finished_at, claimed_by, job_type, status, phase, requested_parameters_json, progress_message, worksheet_run_id
        FROM generation_jobs
        WHERE id = ?
        """
        with connect(self.database_path) as connection:
            row = connection.execute(query, (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        phase = row["phase"] or self._default_job_phase(row["job_type"], row["status"])
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "claimed_by": row["claimed_by"],
            "job_type": row["job_type"],
            "status": row["status"],
            "phase": phase,
            "phase_label": JOB_PHASE_LABELS.get(phase, "In Progress"),
            "requested_parameters": json.loads(row["requested_parameters_json"]),
            "progress_message": row["progress_message"],
            "worksheet_run_id": row["worksheet_run_id"],
        }

    def update_generation_job(
        self,
        job_id: int,
        *,
        status: str | None = None,
        phase: str | None = None,
        progress_message: str | None = None,
        worksheet_run_id: int | None = None,
    ) -> None:
        existing = self.get_generation_job(job_id)
        if existing["status"] in {"completed", "failed", "cancelled"}:
            return
        query = """
        UPDATE generation_jobs
        SET
            status = ?,
            phase = ?,
            progress_message = ?,
            worksheet_run_id = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """
        with connect(self.database_path) as connection:
            connection.execute(
                query,
                (
                    status if status is not None else existing["status"],
                    phase if phase is not None else existing["phase"],
                    progress_message if progress_message is not None else existing["progress_message"],
                    worksheet_run_id if worksheet_run_id is not None else existing["worksheet_run_id"],
                    job_id,
                ),
            )

    def claim_next_generation_job(self, *, worker_name: str, job_types: tuple[str, ...] = ("worksheet_generate", "worksheet_style")) -> dict[str, object] | None:
        placeholders = ",".join("?" for _ in job_types)
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"""
                SELECT id
                FROM generation_jobs
                WHERE status = ? AND job_type IN ({placeholders})
                ORDER BY id ASC
                LIMIT 1
                """,
                ("queued", *job_types),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE generation_jobs
                SET status = ?, started_at = COALESCE(started_at, CURRENT_TIMESTAMP), claimed_by = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = ?
                """,
                ("running", worker_name, int(row["id"]), "queued"),
            )
        return self.get_generation_job(int(row["id"]))

    def create_worksheet_run(
        self,
        *,
        title: str,
        learner_band: str,
        reveal_mode: str,
        skill_profile: str,
        theme: str | None,
        prompt_text: str,
        solution_phrase: str,
        parameters: dict[str, object],
        styling: dict[str, object] | None = None,
    ) -> int:
        styling = styling or {}
        query = """
        INSERT INTO worksheet_runs (
            status,
            lifecycle_phase,
            title,
            learner_band,
            reveal_mode,
            skill_profile,
            theme,
            prompt_text,
            solution_phrase,
            parameters_json,
            difficulty_minimum,
            difficulty_maximum,
            seed,
            color_picture_source,
            color_picture_preset,
            styling_requested,
            styling_style_name,
            styling_color_mode,
            styling_ink_saver,
            styling_status,
            styling_verification_status,
            styling_model,
            styling_prompt_text,
            styled_artifact_group,
            styled_thumbnail_path,
            style_check_artifact_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with connect(self.database_path) as connection:
            cursor = connection.execute(
                query,
                (
                    "generating",
                    "worksheet_generation_queued",
                    title,
                    learner_band,
                    reveal_mode,
                    skill_profile,
                    theme,
                    prompt_text,
                    solution_phrase,
                    json.dumps(parameters),
                    parameters.get("difficulty_minimum"),
                    parameters.get("difficulty_maximum"),
                    parameters.get("seed"),
                    parameters.get("color_picture_source"),
                    parameters.get("color_picture_preset"),
                    1 if styling.get("requested") else 0,
                    styling.get("style_name"),
                    styling.get("color_mode"),
                    1 if styling.get("ink_saver") else 0,
                    str(styling.get("status") or "not_requested"),
                    str(styling.get("verification_status") or "not_requested"),
                    styling.get("model"),
                    styling.get("prompt_text"),
                    styling.get("styled_artifact_group"),
                    styling.get("styled_thumbnail_path"),
                    styling.get("style_check_artifact_path"),
                ),
            )
            return int(cursor.lastrowid)

    def complete_worksheet_run(
        self,
        worksheet_run_id: int,
        *,
        artifact_group: str,
        thumbnail_path: str | None,
    ) -> None:
        query = """
        UPDATE worksheet_runs
        SET status = ?, lifecycle_phase = ?, artifact_group = ?, thumbnail_path = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """
        existing = self.get_worksheet_run(worksheet_run_id)
        lifecycle_phase = "awaiting_styling_confirmation" if existing["styling"]["requested"] else "plain_worksheet_ready"
        with connect(self.database_path) as connection:
            connection.execute(query, ("completed", lifecycle_phase, artifact_group, thumbnail_path, worksheet_run_id))

    def fail_worksheet_run(self, worksheet_run_id: int, *, message: str) -> None:
        query = """
        UPDATE worksheet_runs
        SET status = ?, lifecycle_phase = ?, updated_at = CURRENT_TIMESTAMP, prompt_text = COALESCE(prompt_text, ?)
        WHERE id = ?
        """
        with connect(self.database_path) as connection:
            connection.execute(query, ("failed", "run_failed", message, worksheet_run_id))

    def update_worksheet_run_lifecycle(self, worksheet_run_id: int, *, lifecycle_phase: str) -> None:
        query = """
        UPDATE worksheet_runs
        SET lifecycle_phase = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """
        with connect(self.database_path) as connection:
            connection.execute(query, (lifecycle_phase, worksheet_run_id))

    def update_worksheet_run_parameters(self, worksheet_run_id: int, *, parameters: dict[str, object]) -> None:
        query = """
        UPDATE worksheet_runs
        SET
            parameters_json = ?,
            difficulty_minimum = ?,
            difficulty_maximum = ?,
            seed = ?,
            color_picture_source = ?,
            color_picture_preset = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """
        with connect(self.database_path) as connection:
            connection.execute(
                query,
                (
                    json.dumps(parameters),
                    parameters.get("difficulty_minimum"),
                    parameters.get("difficulty_maximum"),
                    parameters.get("seed"),
                    parameters.get("color_picture_source"),
                    parameters.get("color_picture_preset"),
                    worksheet_run_id,
                ),
            )

    def update_worksheet_run_styling(
        self,
        worksheet_run_id: int,
        *,
        status: str,
        verification_status: str | None = None,
        prompt_text: str | None = None,
        model: str | None = None,
        styled_artifact_group: str | None = None,
        styled_thumbnail_path: str | None = None,
        style_check_artifact_path: str | None = None,
    ) -> None:
        existing = self.get_worksheet_run(worksheet_run_id)
        query = """
        UPDATE worksheet_runs
        SET
            styling_status = ?,
            styling_verification_status = ?,
            styling_prompt_text = ?,
            styling_model = ?,
            styled_artifact_group = ?,
            styled_thumbnail_path = ?,
            style_check_artifact_path = ?,
            lifecycle_phase = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """
        lifecycle_phase = self._lifecycle_phase_for_styling_status(status, existing["status"])
        with connect(self.database_path) as connection:
            connection.execute(
                query,
                (
                    status,
                    verification_status if verification_status is not None else existing["styling"]["verification_status"],
                    prompt_text if prompt_text is not None else existing["styling"]["prompt_text"],
                    model if model is not None else existing["styling"]["model"],
                    styled_artifact_group if styled_artifact_group is not None else existing["styling"]["styled_artifact_group"],
                    styled_thumbnail_path if styled_thumbnail_path is not None else existing["styling"]["styled_thumbnail_path"],
                    style_check_artifact_path if style_check_artifact_path is not None else existing["styling"]["style_check_artifact_path"],
                    lifecycle_phase,
                    worksheet_run_id,
                ),
            )

    def attach_artifact(
        self,
        *,
        worksheet_run_id: int,
        artifact_kind: str,
        output_format: str,
        relative_path: str,
        display_name: str,
    ) -> None:
        query = """
        INSERT INTO worksheet_artifacts (
            worksheet_run_id,
            artifact_kind,
            output_format,
            relative_path,
            display_name
        ) VALUES (?, ?, ?, ?, ?)
        """
        with connect(self.database_path) as connection:
            connection.execute(
                query,
                (worksheet_run_id, artifact_kind, output_format, relative_path, display_name),
            )

    def get_artifact(self, artifact_id: int) -> dict[str, object]:
        with connect(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT
                    a.id,
                    a.worksheet_run_id,
                    a.artifact_kind,
                    a.output_format,
                    a.relative_path,
                    a.display_name,
                    r.learner_band,
                    r.skill_profile,
                    r.theme
                FROM worksheet_artifacts a
                JOIN worksheet_runs r ON r.id = a.worksheet_run_id
                WHERE a.id = ?
                """,
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise KeyError(artifact_id)
        return {
            "id": int(row["id"]),
            "worksheet_run_id": int(row["worksheet_run_id"]),
            "artifact_kind": row["artifact_kind"],
            "output_format": row["output_format"],
            "relative_path": row["relative_path"],
            "display_name": row["display_name"],
            "learner_band": row["learner_band"],
            "skill_profile": row["skill_profile"],
            "theme": row["theme"],
        }

    def get_worksheet_run(self, worksheet_run_id: int) -> dict[str, object]:
        query = """
        SELECT
            id,
            created_at,
            updated_at,
            status,
            lifecycle_phase,
            title,
            learner_band,
            reveal_mode,
            skill_profile,
            theme,
            prompt_text,
            solution_phrase,
            parameters_json,
            styling_requested,
            styling_style_name,
            styling_color_mode,
            styling_ink_saver,
            styling_status,
            styling_verification_status,
            styling_model,
            styling_prompt_text,
            styled_artifact_group,
            styled_thumbnail_path,
            style_check_artifact_path,
            artifact_group,
            thumbnail_path
        FROM worksheet_runs
        WHERE id = ?
        """
        with connect(self.database_path) as connection:
            row = connection.execute(query, (worksheet_run_id,)).fetchone()
            artifacts = connection.execute(
                """
                SELECT id, artifact_kind, output_format, relative_path, display_name
                FROM worksheet_artifacts
                WHERE worksheet_run_id = ?
                ORDER BY id ASC
                """,
                (worksheet_run_id,),
            ).fetchall()
        if row is None:
            raise KeyError(worksheet_run_id)
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "status": row["status"],
            "lifecycle": self._run_lifecycle_payload(row),
            "title": row["title"],
            "learner_band": row["learner_band"],
            "reveal_mode": row["reveal_mode"],
            "skill_profile": row["skill_profile"],
            "theme": row["theme"],
            "prompt_text": row["prompt_text"],
            "solution_phrase": row["solution_phrase"],
            "parameters": json.loads(row["parameters_json"]),
            "styling": {
                "requested": bool(row["styling_requested"]),
                "style_name": row["styling_style_name"],
                "color_mode": row["styling_color_mode"],
                "ink_saver": bool(row["styling_ink_saver"]),
                "status": row["styling_status"],
                "verification_status": row["styling_verification_status"],
                "model": row["styling_model"],
                "prompt_text": row["styling_prompt_text"],
                "styled_artifact_group": row["styled_artifact_group"],
                "styled_thumbnail_path": row["styled_thumbnail_path"],
                "style_check_artifact_path": row["style_check_artifact_path"],
            },
            "artifact_group": row["artifact_group"],
            "thumbnail_path": row["thumbnail_path"],
            "artifacts": [
                {
                    "id": int(artifact["id"]),
                    "artifact_kind": artifact["artifact_kind"],
                    "output_format": artifact["output_format"],
                    "relative_path": artifact["relative_path"],
                    "display_name": artifact["display_name"],
                }
                for artifact in artifacts
            ],
        }

    def complete_generation_job(self, job_id: int, *, progress_message: str) -> None:
        query = """
        UPDATE generation_jobs
        SET status = ?, phase = ?, progress_message = ?, finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """
        existing = self.get_generation_job(job_id)
        final_phase = self._default_job_phase(existing["job_type"], "completed")
        with connect(self.database_path) as connection:
            connection.execute(query, ("completed", final_phase, progress_message, job_id))

    def cancel_generation_job(self, job_id: int, *, progress_message: str) -> None:
        query = """
        UPDATE generation_jobs
        SET status = ?, phase = ?, progress_message = ?, finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """
        existing = self.get_generation_job(job_id)
        final_phase = self._default_job_phase(existing["job_type"], "cancelled")
        with connect(self.database_path) as connection:
            connection.execute(query, ("cancelled", final_phase, progress_message, job_id))

    def fail_generation_job(self, job_id: int, *, progress_message: str) -> None:
        query = """
        UPDATE generation_jobs
        SET status = ?, phase = ?, progress_message = ?, finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """
        existing = self.get_generation_job(job_id)
        final_phase = self._default_job_phase(existing["job_type"], "failed")
        with connect(self.database_path) as connection:
            connection.execute(query, ("failed", final_phase, progress_message, job_id))

    def cancel_worksheet_run(self, worksheet_run_id: int, *, message: str) -> None:
        query = """
        UPDATE worksheet_runs
        SET status = ?, lifecycle_phase = ?, updated_at = CURRENT_TIMESTAMP, prompt_text = COALESCE(prompt_text, ?)
        WHERE id = ?
        """
        with connect(self.database_path) as connection:
            connection.execute(query, ("cancelled", "run_cancelled", message, worksheet_run_id))

    def create_reward_content_draft(
        self,
        *,
        candidate: RewardContentCandidate,
        learner_band: str,
        generation_parameters: dict[str, object],
    ) -> dict[str, object]:
        query = """
        INSERT INTO reward_content_drafts (
            learner_band,
            theme,
            style,
            language,
            source,
            approval_state,
            prompt_text,
            solution_phrase,
            reading_level_assessment_json,
            review_notes_json,
            generation_parameters_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with connect(self.database_path) as connection:
            cursor = connection.execute(
                query,
                (
                    learner_band,
                    candidate.theme,
                    candidate.style,
                    candidate.language,
                    candidate.source,
                    candidate.approval_state.value,
                    candidate.prompt_text,
                    candidate.solution_phrase,
                    json.dumps(self._assessment_to_dict(candidate.reading_level_assessment)),
                    json.dumps(candidate.review_notes),
                    json.dumps(generation_parameters),
                ),
            )
            draft_id = int(cursor.lastrowid)

        return self.get_reward_content_draft(draft_id)

    def update_reward_content_draft(
        self,
        draft_id: int,
        *,
        candidate: RewardContentCandidate,
        generation_parameters: dict[str, object] | None = None,
        learner_band: str | None = None,
    ) -> dict[str, object]:
        existing = self.get_reward_content_draft(draft_id)
        query = """
        UPDATE reward_content_drafts
        SET
            learner_band = ?,
            theme = ?,
            style = ?,
            language = ?,
            source = ?,
            approval_state = ?,
            prompt_text = ?,
            solution_phrase = ?,
            reading_level_assessment_json = ?,
            review_notes_json = ?,
            generation_parameters_json = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """
        with connect(self.database_path) as connection:
            connection.execute(
                query,
                (
                    learner_band if learner_band is not None else existing["learner_band"],
                    candidate.theme,
                    candidate.style,
                    candidate.language,
                    candidate.source,
                    candidate.approval_state.value,
                    candidate.prompt_text,
                    candidate.solution_phrase,
                    json.dumps(self._assessment_to_dict(candidate.reading_level_assessment)),
                    json.dumps(candidate.review_notes),
                    json.dumps(generation_parameters if generation_parameters is not None else existing["generation_parameters"]),
                    draft_id,
                ),
            )

        return self.get_reward_content_draft(draft_id)

    def get_reward_content_draft(self, draft_id: int) -> dict[str, object]:
        query = """
        SELECT
            id,
            created_at,
            updated_at,
            learner_band,
            theme,
            style,
            language,
            source,
            approval_state,
            prompt_text,
            solution_phrase,
            reading_level_assessment_json,
            review_notes_json,
            generation_parameters_json
        FROM reward_content_drafts
        WHERE id = ?
        """
        with connect(self.database_path) as connection:
            row = connection.execute(query, (draft_id,)).fetchone()
        if row is None:
            raise KeyError(draft_id)
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "learner_band": row["learner_band"],
            "theme": row["theme"],
            "style": row["style"],
            "language": row["language"],
            "source": row["source"],
            "approval_state": row["approval_state"],
            "prompt_text": row["prompt_text"],
            "solution_phrase": row["solution_phrase"],
            "reading_level_assessment": json.loads(row["reading_level_assessment_json"]) if row["reading_level_assessment_json"] else None,
            "review_notes": json.loads(row["review_notes_json"]),
            "generation_parameters": json.loads(row["generation_parameters_json"]),
        }

    @staticmethod
    def _assessment_to_dict(assessment: object) -> dict[str, object] | None:
        if assessment is None:
            return None
        return {
            "learner_band": assessment.learner_band.value,
            "passed": assessment.passed,
            "word_count": assessment.word_count,
            "sentence_count": assessment.sentence_count,
            "long_word_count": assessment.long_word_count,
            "flagged_terms": list(assessment.flagged_terms),
            "notes": list(assessment.notes),
        }

    @staticmethod
    def _default_job_phase(job_type: str, status: str) -> str:
        if status == "cancelled":
            if job_type == "worksheet_generate":
                return "worksheet_generation_cancelled"
            if job_type == "worksheet_style":
                return "styling_cancelled"
            if job_type == "reward_content_generate":
                return "draft_generation_cancelled"
            if job_type == "reward_content_regenerate":
                return "draft_regeneration_cancelled"
            return "cancelled"
        if status == "failed":
            if job_type == "worksheet_generate":
                return "worksheet_generation_failed"
            if job_type == "worksheet_style":
                return "styling_failed"
            if job_type == "reward_content_generate":
                return "draft_generation_failed"
            if job_type == "reward_content_regenerate":
                return "draft_regeneration_failed"
            return "queued"
        if status == "completed":
            if job_type == "worksheet_generate":
                return "worksheet_generation_complete"
            if job_type == "worksheet_style":
                return "styling_complete"
            if job_type == "reward_content_generate":
                return "draft_generation_complete"
            if job_type == "reward_content_regenerate":
                return "draft_regeneration_complete"
            return "queued"
        if job_type == "worksheet_generate":
            return "worksheet_generation_queued"
        if job_type == "worksheet_style":
            return "styling_queued"
        if job_type == "reward_content_generate":
            return "draft_generation_requested"
        if job_type == "reward_content_regenerate":
            return "draft_regeneration_requested"
        return "queued"

    def _run_lifecycle_payload(self, row: object) -> dict[str, object]:
        phase = row["lifecycle_phase"] or self._derive_run_lifecycle_phase(row)
        return {
            "phase": phase,
            "label": RUN_LIFECYCLE_LABELS.get(phase, "In Progress"),
            "is_terminal": phase in {
                "plain_worksheet_ready",
                "styled_verified",
                "styled_failed_plain_retained",
                "styling_cancelled_plain_retained",
                "run_cancelled",
                "run_failed",
            },
            "can_confirm_styling": phase == "awaiting_styling_confirmation",
            "can_cancel_styling": phase == "awaiting_styling_confirmation",
            "can_retry_styling": (row["styling_status"] or "not_requested") in {"styled_failed_verification", "styled_failed_error"},
            "can_retry_generation": phase in {
                "plain_worksheet_ready",
                "styled_verified",
                "styled_failed_plain_retained",
                "styling_cancelled_plain_retained",
                "run_cancelled",
                "run_failed",
            },
        }

    @staticmethod
    def _derive_run_lifecycle_phase(row: object) -> str:
        styling_status = row["styling_status"] or "not_requested"
        status = row["status"]
        if status == "cancelled":
            return "run_cancelled"
        if status == "failed":
            return "run_failed"
        if styling_status == "awaiting_confirmation":
            return "awaiting_styling_confirmation"
        if styling_status == "confirmed_pending_styling":
            return "styling_queued"
        if styling_status == "retry_pending_styling":
            return "styling_queued"
        if styling_status == "styling_in_progress":
            return "styling_running"
        if styling_status == "retry_in_progress":
            return "styling_running"
        if styling_status == "styled_verified":
            return "styled_verified"
        if styling_status in {"styled_failed_verification", "styled_failed_error"}:
            return "styled_failed_plain_retained"
        if styling_status == "cancelled_after_plain_review":
            return "styling_cancelled_plain_retained"
        if status == "completed":
            return "plain_worksheet_ready"
        return "worksheet_generation_running" if status == "generating" else "worksheet_generation_queued"

    @staticmethod
    def _lifecycle_phase_for_styling_status(styling_status: str, run_status: str) -> str:
        if styling_status == "awaiting_confirmation":
            return "awaiting_styling_confirmation"
        if styling_status == "confirmed_pending_styling":
            return "styling_queued"
        if styling_status == "retry_pending_styling":
            return "styling_queued"
        if styling_status == "styling_in_progress":
            return "styling_running"
        if styling_status == "retry_in_progress":
            return "styling_running"
        if styling_status == "styled_verified":
            return "styled_verified"
        if styling_status in {"styled_failed_verification", "styled_failed_error"}:
            return "styled_failed_plain_retained"
        if styling_status in {"cancelled_after_plain_review", "cancelled_during_styling"}:
            return "styling_cancelled_plain_retained"
        if run_status == "cancelled":
            return "run_cancelled"
        if run_status == "completed":
            return "plain_worksheet_ready"
        return "worksheet_generation_running"
