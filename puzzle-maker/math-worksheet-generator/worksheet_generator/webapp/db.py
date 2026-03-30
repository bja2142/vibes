from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3
from typing import Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS worksheet_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL,
    lifecycle_phase TEXT NOT NULL DEFAULT 'worksheet_generation_queued',
    title TEXT NOT NULL,
    learner_band TEXT NOT NULL,
    reveal_mode TEXT NOT NULL,
    skill_profile TEXT NOT NULL,
    theme TEXT,
    prompt_text TEXT,
    solution_phrase TEXT,
    parameters_json TEXT NOT NULL,
    difficulty_minimum INTEGER,
    difficulty_maximum INTEGER,
    seed INTEGER,
    color_picture_source TEXT,
    color_picture_preset TEXT,
    styling_requested INTEGER NOT NULL DEFAULT 0,
    styling_style_name TEXT,
    styling_color_mode TEXT,
    styling_ink_saver INTEGER NOT NULL DEFAULT 0,
    styling_status TEXT NOT NULL DEFAULT 'not_requested',
    styling_verification_status TEXT NOT NULL DEFAULT 'not_requested',
    styling_model TEXT,
    styling_prompt_text TEXT,
    styled_artifact_group TEXT,
    styled_thumbnail_path TEXT,
    style_check_artifact_path TEXT,
    artifact_group TEXT,
    thumbnail_path TEXT
);

CREATE TABLE IF NOT EXISTS worksheet_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    worksheet_run_id INTEGER NOT NULL REFERENCES worksheet_runs(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    artifact_kind TEXT NOT NULL,
    output_format TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    display_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS generation_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    finished_at TEXT,
    claimed_by TEXT,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'queued',
    requested_parameters_json TEXT NOT NULL,
    progress_message TEXT NOT NULL,
    worksheet_run_id INTEGER REFERENCES worksheet_runs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS workflow_sessions (
    token TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL,
    phase TEXT NOT NULL,
    controls_json TEXT NOT NULL,
    draft_id INTEGER REFERENCES reward_content_drafts(id) ON DELETE SET NULL,
    generation_job_id INTEGER REFERENCES generation_jobs(id) ON DELETE SET NULL,
    worksheet_run_id INTEGER REFERENCES worksheet_runs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS reward_content_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    learner_band TEXT NOT NULL,
    theme TEXT,
    style TEXT,
    language TEXT NOT NULL,
    source TEXT NOT NULL,
    approval_state TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    solution_phrase TEXT NOT NULL,
    reading_level_assessment_json TEXT,
    review_notes_json TEXT NOT NULL,
    generation_parameters_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_worksheet_runs_created_at ON worksheet_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_worksheet_runs_learner_band ON worksheet_runs(learner_band);
CREATE INDEX IF NOT EXISTS idx_worksheet_runs_reveal_mode ON worksheet_runs(reveal_mode);
CREATE INDEX IF NOT EXISTS idx_worksheet_runs_theme ON worksheet_runs(theme);
CREATE INDEX IF NOT EXISTS idx_generation_jobs_status ON generation_jobs(status);
CREATE INDEX IF NOT EXISTS idx_reward_content_drafts_created_at ON reward_content_drafts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reward_content_drafts_state ON reward_content_drafts(approval_state);
CREATE INDEX IF NOT EXISTS idx_workflow_sessions_updated_at ON workflow_sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_sessions_job_id ON workflow_sessions(generation_job_id);
CREATE INDEX IF NOT EXISTS idx_workflow_sessions_run_id ON workflow_sessions(worksheet_run_id);
"""

WORKSHEET_RUN_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("lifecycle_phase", "ALTER TABLE worksheet_runs ADD COLUMN lifecycle_phase TEXT NOT NULL DEFAULT 'worksheet_generation_queued'"),
    ("difficulty_minimum", "ALTER TABLE worksheet_runs ADD COLUMN difficulty_minimum INTEGER"),
    ("difficulty_maximum", "ALTER TABLE worksheet_runs ADD COLUMN difficulty_maximum INTEGER"),
    ("seed", "ALTER TABLE worksheet_runs ADD COLUMN seed INTEGER"),
    ("color_picture_source", "ALTER TABLE worksheet_runs ADD COLUMN color_picture_source TEXT"),
    ("color_picture_preset", "ALTER TABLE worksheet_runs ADD COLUMN color_picture_preset TEXT"),
    ("styling_requested", "ALTER TABLE worksheet_runs ADD COLUMN styling_requested INTEGER NOT NULL DEFAULT 0"),
    ("styling_style_name", "ALTER TABLE worksheet_runs ADD COLUMN styling_style_name TEXT"),
    ("styling_color_mode", "ALTER TABLE worksheet_runs ADD COLUMN styling_color_mode TEXT"),
    ("styling_ink_saver", "ALTER TABLE worksheet_runs ADD COLUMN styling_ink_saver INTEGER NOT NULL DEFAULT 0"),
    ("styling_status", "ALTER TABLE worksheet_runs ADD COLUMN styling_status TEXT NOT NULL DEFAULT 'not_requested'"),
    (
        "styling_verification_status",
        "ALTER TABLE worksheet_runs ADD COLUMN styling_verification_status TEXT NOT NULL DEFAULT 'not_requested'",
    ),
    ("styling_model", "ALTER TABLE worksheet_runs ADD COLUMN styling_model TEXT"),
    ("styling_prompt_text", "ALTER TABLE worksheet_runs ADD COLUMN styling_prompt_text TEXT"),
    ("styled_artifact_group", "ALTER TABLE worksheet_runs ADD COLUMN styled_artifact_group TEXT"),
    ("styled_thumbnail_path", "ALTER TABLE worksheet_runs ADD COLUMN styled_thumbnail_path TEXT"),
    ("style_check_artifact_path", "ALTER TABLE worksheet_runs ADD COLUMN style_check_artifact_path TEXT"),
)

GENERATION_JOB_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("phase", "ALTER TABLE generation_jobs ADD COLUMN phase TEXT NOT NULL DEFAULT 'queued'"),
    ("started_at", "ALTER TABLE generation_jobs ADD COLUMN started_at TEXT"),
    ("finished_at", "ALTER TABLE generation_jobs ADD COLUMN finished_at TEXT"),
    ("claimed_by", "ALTER TABLE generation_jobs ADD COLUMN claimed_by TEXT"),
)


def ensure_storage_paths(database_path: Path, artifact_root: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)


def initialize_database(database_path: Path) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(SCHEMA)
        _apply_worksheet_run_migrations(connection)
        _apply_generation_job_migrations(connection)
        _backfill_worksheet_run_search_columns(connection)
        connection.commit()


@contextmanager
def connect(database_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def _apply_worksheet_run_migrations(connection: sqlite3.Connection) -> None:
    existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(worksheet_runs)").fetchall()}
    for column_name, statement in WORKSHEET_RUN_MIGRATIONS:
        if column_name not in existing_columns:
            connection.execute(statement)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_worksheet_runs_skill_profile ON worksheet_runs(skill_profile)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_worksheet_runs_difficulty_minimum ON worksheet_runs(difficulty_minimum)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_worksheet_runs_difficulty_maximum ON worksheet_runs(difficulty_maximum)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_worksheet_runs_seed ON worksheet_runs(seed)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_worksheet_runs_color_picture_source ON worksheet_runs(color_picture_source)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_worksheet_runs_color_picture_preset ON worksheet_runs(color_picture_preset)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_worksheet_runs_styling_status ON worksheet_runs(styling_status)")


def _apply_generation_job_migrations(connection: sqlite3.Connection) -> None:
    existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(generation_jobs)").fetchall()}
    for column_name, statement in GENERATION_JOB_MIGRATIONS:
        if column_name not in existing_columns:
            connection.execute(statement)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_generation_jobs_started_at ON generation_jobs(started_at)")


def _backfill_worksheet_run_search_columns(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT id, parameters_json, difficulty_minimum, difficulty_maximum, seed, color_picture_source, color_picture_preset
        FROM worksheet_runs
        WHERE difficulty_minimum IS NULL
            OR difficulty_maximum IS NULL
            OR color_picture_source IS NULL
            OR color_picture_preset IS NULL
        """
    ).fetchall()
    for row in rows:
        parameters = json.loads(row["parameters_json"])
        connection.execute(
            """
            UPDATE worksheet_runs
            SET difficulty_minimum = COALESCE(difficulty_minimum, ?),
                difficulty_maximum = COALESCE(difficulty_maximum, ?),
                seed = COALESCE(seed, ?),
                color_picture_source = COALESCE(color_picture_source, ?),
                color_picture_preset = COALESCE(color_picture_preset, ?)
            WHERE id = ?
            """,
            (
                parameters.get("difficulty_minimum"),
                parameters.get("difficulty_maximum"),
                parameters.get("seed"),
                parameters.get("color_picture_source"),
                parameters.get("color_picture_preset"),
                row["id"],
            ),
        )
