from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from threading import RLock
from typing import Any

from .config import SESSION_SCHEMA_VERSION, STATE_DIRNAME, get_workspace_root
from .errors import StructuredToolError
from .security import WorkspaceSecurity
from .transport import get_request_context, load_http_transport_config
from .utils import ensure_dir, json_clone, paginate, short_token, stable_json_dumps, utc_now

FUNCTION_ID_RE = re.compile(r"^(fn)_(art_[a-z0-9]+_\d{4})_g(\d+)_(\d{4})$")
STRING_ID_RE = re.compile(r"^(str)_(art_[a-z0-9]+_\d{4})_g(\d+)_(\d{4})$")


class SessionStore:
    def __init__(self, workspace_root: Path | None = None, security: WorkspaceSecurity | None = None) -> None:
        self.workspace_root = (workspace_root or get_workspace_root()).resolve()
        self.security = security or WorkspaceSecurity(self.workspace_root)
        self.state_root = ensure_dir(self.workspace_root / STATE_DIRNAME)
        self.sessions_root = ensure_dir(self.state_root / "sessions")
        self._lock = RLock()

    def list_sessions(self, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
        with self._lock:
            sessions = sorted(
                (
                    item
                    for item in (self._load_session(path.parent.name) for path in self.sessions_root.glob("*/session.json"))
                    if self._session_visible(item)
                ),
                key=lambda item: (item["created_at"], item["session_id"]),
            )
            summaries = [self._session_summary(item) for item in sessions]
            return paginate(summaries, cursor, limit)

    def create_session(self, name: str, description: str | None = None, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized_name = name.strip()
        if not normalized_name:
            raise StructuredToolError("invalid_request", "session_name_required", "Session name cannot be empty.")
        with self._lock:
            if self._find_session_by_name(normalized_name) is not None:
                raise StructuredToolError(
                    "conflict",
                    "session_name_conflict",
                    f"Session name '{normalized_name}' already exists.",
                    details={"name": normalized_name},
                )
            self._enforce_session_quota()
            created_at = utc_now()
            session_id = f"sess_{os.urandom(8).hex()}"
            ownership = self._session_ownership_payload()
            session = {
                "schema_version": SESSION_SCHEMA_VERSION,
                "session_id": session_id,
                "name": normalized_name,
                "description": description or "",
                "created_at": created_at,
                "updated_at": created_at,
                "ownership": ownership,
                "settings": json_clone(settings or {}),
                "artifacts": {},
                "annotations": {},
                "snapshots": {},
                "operation_log": [],
                "counters": {
                    "artifact": 0,
                    "annotation": 0,
                    "snapshot": 0,
                    "operation": 0,
                },
            }
            self._write_session(session)
            return self._session_detail(session)

    def load_session(self, session_id: str | None = None, name: str | None = None) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id=session_id, name=name)
            return self._session_detail(session)

    def destroy_session(self, session_id: str | None = None, name: str | None = None) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id=session_id, name=name)
            session_dir = self._session_dir(session["session_id"])
            if session_dir.exists():
                shutil.rmtree(session_dir)
            return self._session_summary(session)

    def update_settings(self, session_id: str, settings_patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(settings_patch, dict):
            raise StructuredToolError("invalid_request", "settings_patch_invalid", "settings_patch must be a JSON object.")
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            session["settings"] = self._merge_dicts(session["settings"], json_clone(settings_patch))
            session["updated_at"] = utc_now()
            self._write_session(session)
            return {"session": self._session_summary(session), "settings": json_clone(session["settings"])}

    def add_artifact(
        self,
        session_id: str,
        path: str,
        display_name: str | None = None,
        relationship: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not path.strip():
            raise StructuredToolError("invalid_request", "artifact_path_required", "Artifact path cannot be empty.")
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            self.security.validate_artifact_capacity(len(session["artifacts"]))
            resolved_path = self.security.resolve_input_file(path, purpose="Artifact")
            display_name_info = self.security.sanitize_filename(display_name or resolved_path.name, default_stem="artifact")
            session["counters"]["artifact"] += 1
            artifact_id = f"art_{short_token(session['session_id'])}_{session['counters']['artifact']:04d}"
            created_at = utc_now()
            artifact = {
                "artifact_id": artifact_id,
                "display_name": (display_name or resolved_path.name or artifact_id).strip() or artifact_id,
                "safe_display_name": display_name_info["safe_name"],
                "name_provenance": display_name_info,
                "relationship": json_clone(relationship) if relationship is not None else None,
                "canonical_path": str(resolved_path),
                "relative_path": self._maybe_relative(resolved_path),
                "size_bytes": resolved_path.stat().st_size,
                "created_at": created_at,
                "updated_at": created_at,
                "analysis_generation": 1,
                "counters": {"function": 0, "string": 0},
                "functions": {},
                "strings": {},
                "feature07": {
                    "patch_history": [],
                    "edits": {
                        "function_names": {},
                        "function_types": {},
                        "variable_names": {},
                        "variable_types": {},
                        "global_names": {},
                        "global_types": {},
                        "named_types": {
                            "structs": {},
                            "enums": {},
                            "typedefs": {},
                        },
                        "calling_conventions": {},
                        "type_imports": [],
                    },
                },
                "analysis": {
                    "status": "not_started",
                    "backend": None,
                    "summary": None,
                    "capabilities": None,
                    "cache_path": None,
                    "completed_at": None,
                    "last_job_id": None,
                    "error": None,
                    "instruction_set_mode_override": None,
                },
            }
            session["artifacts"][artifact_id] = artifact
            session["updated_at"] = utc_now()
            self._write_session(session)
            return self._artifact_summary(artifact)

    def list_artifacts(self, session_id: str, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            artifacts = [self._artifact_summary(item) for item in sorted(session["artifacts"].values(), key=lambda value: value["artifact_id"])]
            return paginate(artifacts, cursor, limit)

    def get_artifact_record(self, session_id: str, artifact_id: str | None = None, display_name: str | None = None) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            artifact = self._resolve_artifact(session, artifact_id=artifact_id, display_name=display_name)
            return json_clone(artifact)

    def remove_artifact(self, session_id: str, artifact_id: str | None = None, display_name: str | None = None) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            artifact = self._resolve_artifact(session, artifact_id=artifact_id, display_name=display_name)
            removed = self._artifact_summary(artifact)
            del session["artifacts"][artifact["artifact_id"]]
            session["annotations"] = {
                key: value
                for key, value in session["annotations"].items()
                if value["target"].get("artifact_id") != artifact["artifact_id"]
            }
            session["updated_at"] = utc_now()
            self._write_session(session)
            return removed

    def register_provisional_function(self, session_id: str, artifact_id: str, name: str, address: int | str | None = None) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            artifact = self._resolve_artifact(session, artifact_id=artifact_id)
            artifact["counters"]["function"] += 1
            function_id = f"fn_{artifact['artifact_id']}_g{artifact['analysis_generation']}_{artifact['counters']['function']:04d}"
            function = {
                "function_id": function_id,
                "artifact_id": artifact["artifact_id"],
                "analysis_generation": artifact["analysis_generation"],
                "name": name.strip() or function_id,
                "address": self._normalize_address(address),
                "created_at": utc_now(),
            }
            artifact["functions"][function_id] = function
            artifact["updated_at"] = utc_now()
            session["updated_at"] = utc_now()
            self._write_session(session)
            return json_clone(function)

    def register_provisional_string(self, session_id: str, artifact_id: str, value: str, address: int | str | None = None) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            artifact = self._resolve_artifact(session, artifact_id=artifact_id)
            artifact["counters"]["string"] += 1
            string_id = f"str_{artifact['artifact_id']}_g{artifact['analysis_generation']}_{artifact['counters']['string']:04d}"
            string_record = {
                "string_id": string_id,
                "artifact_id": artifact["artifact_id"],
                "analysis_generation": artifact["analysis_generation"],
                "value": value,
                "address": self._normalize_address(address),
                "created_at": utc_now(),
            }
            artifact["strings"][string_id] = string_record
            artifact["updated_at"] = utc_now()
            session["updated_at"] = utc_now()
            self._write_session(session)
            return json_clone(string_record)

    def get_object_reference(self, session_id: str, object_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            object_type, artifact_id, analysis_generation = self._parse_object_id(object_id)
            artifact = session["artifacts"].get(artifact_id)
            if artifact is None:
                raise StructuredToolError(
                    "invalid_id",
                    "object_id_expired",
                    f"Object ID '{object_id}' is invalid because artifact '{artifact_id}' is no longer present.",
                    details={"object_id": object_id, "artifact_id": artifact_id},
                )
            if artifact["analysis_generation"] != analysis_generation:
                raise StructuredToolError(
                    "invalid_id",
                    "object_id_expired",
                    f"Object ID '{object_id}' expired when artifact '{artifact_id}' was re-analyzed.",
                    details={"object_id": object_id, "artifact_id": artifact_id, "analysis_generation": artifact["analysis_generation"]},
                )
            collection = artifact["functions"] if object_type == "function" else artifact["strings"]
            record = collection.get(object_id)
            if record is None:
                raise StructuredToolError(
                    "invalid_id",
                    "object_id_not_found",
                    f"Object ID '{object_id}' is not present in the current artifact mapping.",
                    details={"object_id": object_id},
                )
            return json_clone(record)

    def advance_artifact_generation(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            artifact = self._resolve_artifact(session, artifact_id=artifact_id)
            invalidated = {
                "function_ids": sorted(artifact["functions"].keys()),
                "string_ids": sorted(artifact["strings"].keys()),
            }
            artifact["analysis_generation"] += 1
            artifact["functions"] = {}
            artifact["strings"] = {}
            artifact["updated_at"] = utc_now()
            session["updated_at"] = utc_now()
            self._write_session(session)
            return {
                "artifact": self._artifact_summary(artifact),
                "invalidated_ids": invalidated,
            }

    def mark_artifact_analysis_status(
        self,
        session_id: str,
        artifact_id: str,
        *,
        status: str,
        job_id: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            artifact = self._resolve_artifact(session, artifact_id=artifact_id)
            artifact["analysis"]["status"] = status
            if job_id:
                artifact["analysis"]["last_job_id"] = job_id
            artifact["analysis"]["error"] = json_clone(error) if error is not None else None
            artifact["updated_at"] = utc_now()
            session["updated_at"] = utc_now()
            self._write_session(session)
            return self._artifact_summary(artifact)

    def persist_artifact_analysis(
        self,
        session_id: str,
        artifact_id: str,
        analysis_payload: dict[str, Any],
        *,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            artifact = self._resolve_artifact(session, artifact_id=artifact_id)
            invalidated = {
                "function_ids": sorted(artifact["functions"].keys()),
                "string_ids": sorted(artifact["strings"].keys()),
            }
            if artifact["analysis"]["status"] == "completed" and (artifact["functions"] or artifact["strings"]):
                artifact["analysis_generation"] += 1

            transformed = json_clone(analysis_payload)
            artifact["functions"] = {}
            artifact["strings"] = {}
            artifact["counters"]["function"] = 0
            artifact["counters"]["string"] = 0

            function_address_to_id: dict[int, str] = {}
            transformed_functions = []
            for function in sorted(transformed.get("functions", []), key=lambda item: (item["address"], item["name"])):
                artifact["counters"]["function"] += 1
                function_id = f"fn_{artifact['artifact_id']}_g{artifact['analysis_generation']}_{artifact['counters']['function']:04d}"
                record = {
                    **function,
                    "function_id": function_id,
                    "artifact_id": artifact["artifact_id"],
                    "analysis_generation": artifact["analysis_generation"],
                }
                artifact["functions"][function_id] = record
                function_address_to_id[int(record["address"])] = function_id
                transformed_functions.append(record)

            string_address_to_id: dict[int, str] = {}
            transformed_strings = []
            for string_item in sorted(
                transformed.get("strings", []),
                key=lambda item: (item.get("file_offset", -1), item.get("address", -1) if item.get("address") is not None else -1, item["value"]),
            ):
                artifact["counters"]["string"] += 1
                string_id = f"str_{artifact['artifact_id']}_g{artifact['analysis_generation']}_{artifact['counters']['string']:04d}"
                record = {
                    **string_item,
                    "string_id": string_id,
                    "artifact_id": artifact["artifact_id"],
                    "analysis_generation": artifact["analysis_generation"],
                }
                artifact["strings"][string_id] = record
                if record.get("address") is not None and int(record["address"]) not in string_address_to_id:
                    string_address_to_id[int(record["address"])] = string_id
                transformed_strings.append(record)

            transformed_xrefs = []
            for xref in transformed.get("xrefs", []):
                record = dict(xref)
                source_function_id = function_address_to_id.get(int(record["source_function_address"]))
                if source_function_id:
                    record["source_function_id"] = source_function_id
                if record.get("target_kind") == "function" and record.get("target_address") is not None:
                    target_function_id = function_address_to_id.get(int(record["target_address"]))
                    if target_function_id:
                        record["target_function_id"] = target_function_id
                if record.get("target_kind") == "string" and record.get("target_address") is not None:
                    target_string_id = string_address_to_id.get(int(record["target_address"]))
                    if target_string_id:
                        record["target_string_id"] = target_string_id
                transformed_xrefs.append(record)

            transformed["functions"] = transformed_functions
            transformed["strings"] = transformed_strings
            transformed["xrefs"] = transformed_xrefs
            transformed["summary"]["function_count"] = len(transformed_functions)
            transformed["summary"]["string_count"] = len(transformed_strings)
            transformed["summary"]["xref_count"] = len(transformed_xrefs)

            analysis_file = self._analysis_file(session["session_id"], artifact["artifact_id"], artifact["analysis_generation"])
            self._write_json(analysis_file, transformed)

            completed_at = utc_now()
            artifact["analysis"] = {
                "status": "completed",
                "backend": json_clone(transformed.get("backend")),
                "summary": json_clone(transformed.get("summary")),
                "capabilities": json_clone(transformed.get("capabilities")),
                "cache_path": str(analysis_file),
                "completed_at": completed_at,
                "last_job_id": job_id,
                "error": None,
                "instruction_set_mode_override": artifact["analysis"].get("instruction_set_mode_override"),
            }
            artifact["updated_at"] = completed_at
            session["updated_at"] = completed_at
            self._write_session(session)
            return {
                "artifact": self._artifact_summary(artifact),
                "invalidated_ids": invalidated,
            }

    def load_artifact_analysis(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            artifact = self._resolve_artifact(session, artifact_id=artifact_id)
            if artifact["analysis"]["status"] != "completed" or not artifact["analysis"].get("cache_path"):
                raise StructuredToolError(
                    "missing_prerequisite",
                    "analysis_not_completed",
                    f"Artifact '{artifact_id}' does not have a completed analysis cache yet.",
                    details={"artifact_id": artifact_id, "analysis_status": artifact["analysis"]["status"]},
                )
            analysis = self._read_json(Path(artifact["analysis"]["cache_path"]))
            return {
                "artifact": json_clone(artifact),
                "analysis": analysis,
            }

    def set_artifact_instruction_mode_override(self, session_id: str, artifact_id: str, mode: str | None) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            artifact = self._resolve_artifact(session, artifact_id=artifact_id)
            artifact["analysis"]["instruction_set_mode_override"] = mode
            artifact["updated_at"] = utc_now()
            session["updated_at"] = utc_now()
            self._write_session(session)
            return self._artifact_summary(artifact)

    def put_annotation(
        self,
        session_id: str,
        target: dict[str, Any],
        annotation_type: str,
        value: Any,
        annotation_id: str | None = None,
    ) -> dict[str, Any]:
        if not annotation_type.strip():
            raise StructuredToolError("invalid_request", "annotation_type_required", "annotation_type cannot be empty.")
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            canonical_target = self._validate_target(session, target)
            if annotation_id:
                annotation = session["annotations"].get(annotation_id)
                if annotation is None:
                    raise StructuredToolError(
                        "not_found",
                        "annotation_not_found",
                        f"Unknown annotation_id '{annotation_id}'.",
                        details={"annotation_id": annotation_id},
                    )
                if annotation["annotation_type"] != annotation_type or annotation["target"] != canonical_target:
                    raise StructuredToolError(
                        "invalid_request",
                        "annotation_target_mismatch",
                        "Existing annotations may only be updated in place for the same target and annotation_type.",
                        details={"annotation_id": annotation_id},
                    )
            else:
                session["counters"]["annotation"] += 1
                annotation_id = f"ann_{short_token(session['session_id'])}_{session['counters']['annotation']:04d}"
                annotation = {
                    "annotation_id": annotation_id,
                    "annotation_type": annotation_type,
                    "target": canonical_target,
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                    "history": [],
                }
                session["annotations"][annotation_id] = annotation

            revision = self._new_annotation_revision(annotation, value, action="set")
            annotation["history"].append(revision)
            annotation["current_revision_id"] = revision["revision_id"]
            annotation["updated_at"] = revision["created_at"]
            session["updated_at"] = utc_now()
            self._write_session(session)
            return self._annotation_summary(annotation)

    def list_annotations(
        self,
        session_id: str,
        artifact_id: str | None = None,
        target_kind: str | None = None,
        annotation_type: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            annotations = [self._annotation_summary(item) for item in session["annotations"].values()]
            if artifact_id:
                annotations = [item for item in annotations if item["target"].get("artifact_id") == artifact_id]
            if target_kind:
                annotations = [item for item in annotations if item["target"]["kind"] == target_kind]
            if annotation_type:
                annotations = [item for item in annotations if item["annotation_type"] == annotation_type]
            annotations.sort(key=lambda item: (item["updated_at"], item["annotation_id"]))
            return paginate(annotations, cursor, limit)

    def get_annotation_history(self, session_id: str, annotation_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            annotation = session["annotations"].get(annotation_id)
            if annotation is None:
                raise StructuredToolError("not_found", "annotation_not_found", f"Unknown annotation_id '{annotation_id}'.", details={"annotation_id": annotation_id})
            return {
                "annotation": self._annotation_summary(annotation),
                "history": json_clone(annotation["history"]),
            }

    def revert_annotation(self, session_id: str, annotation_id: str, revision_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            annotation = session["annotations"].get(annotation_id)
            if annotation is None:
                raise StructuredToolError("not_found", "annotation_not_found", f"Unknown annotation_id '{annotation_id}'.", details={"annotation_id": annotation_id})
            history = annotation["history"]
            if len(history) < 2 and revision_id is None:
                raise StructuredToolError(
                    "missing_prerequisite",
                    "annotation_no_prior_revision",
                    f"Annotation '{annotation_id}' has no earlier revision to revert to.",
                    details={"annotation_id": annotation_id},
                )
            target_revision = None
            if revision_id is None:
                target_revision = history[-2]
            else:
                for candidate in history:
                    if candidate["revision_id"] == revision_id:
                        target_revision = candidate
                        break
            if target_revision is None:
                raise StructuredToolError(
                    "not_found",
                    "annotation_revision_not_found",
                    f"Unknown revision_id '{revision_id}' for annotation '{annotation_id}'.",
                    details={"annotation_id": annotation_id, "revision_id": revision_id},
                )
            revision = self._new_annotation_revision(annotation, target_revision["value"], action="revert", source_revision_id=target_revision["revision_id"])
            annotation["history"].append(revision)
            annotation["current_revision_id"] = revision["revision_id"]
            annotation["updated_at"] = revision["created_at"]
            session["updated_at"] = utc_now()
            self._write_session(session)
            return self._annotation_summary(annotation)

    def create_snapshot(self, session_id: str, name: str, description: str | None = None) -> dict[str, Any]:
        normalized_name = name.strip()
        if not normalized_name:
            raise StructuredToolError("invalid_request", "snapshot_name_required", "Snapshot name cannot be empty.")
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            if any(item["name"] == normalized_name for item in session["snapshots"].values()):
                raise StructuredToolError(
                    "conflict",
                    "snapshot_name_conflict",
                    f"Snapshot name '{normalized_name}' already exists in session '{session_id}'.",
                    details={"session_id": session_id, "name": normalized_name},
                )
            session["counters"]["snapshot"] += 1
            snapshot_id = f"snap_{short_token(session['session_id'])}_{session['counters']['snapshot']:04d}"
            created_at = utc_now()
            snapshot_meta = {
                "snapshot_id": snapshot_id,
                "name": normalized_name,
                "description": description or "",
                "created_at": created_at,
            }
            session["snapshots"][snapshot_id] = snapshot_meta
            snapshot_payload = {
                "name": session["name"],
                "description": session["description"],
                "settings": json_clone(session["settings"]),
                "artifacts": json_clone(session["artifacts"]),
                "annotations": json_clone(session["annotations"]),
                "operation_log": json_clone(session.get("operation_log", [])),
            }
            self._write_json(self._snapshot_file(session["session_id"], snapshot_id), snapshot_payload)
            session["updated_at"] = utc_now()
            self._write_session(session)
            return json_clone(snapshot_meta)

    def list_snapshots(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            snapshots = sorted(session["snapshots"].values(), key=lambda item: (item["created_at"], item["snapshot_id"]))
            return {"items": json_clone(snapshots), "total": len(snapshots)}

    def restore_snapshot(self, session_id: str, snapshot_id: str | None = None, name: str | None = None) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            snapshot = self._resolve_snapshot(session, snapshot_id=snapshot_id, name=name)
            snapshot_payload = self._read_json(self._snapshot_file(session["session_id"], snapshot["snapshot_id"]))
            session["name"] = snapshot_payload["name"]
            session["description"] = snapshot_payload["description"]
            session["settings"] = snapshot_payload["settings"]
            session["artifacts"] = snapshot_payload["artifacts"]
            session["annotations"] = snapshot_payload["annotations"]
            session["operation_log"] = snapshot_payload.get("operation_log", [])
            session["updated_at"] = utc_now()
            self._write_session(session)
            return {
                "snapshot": json_clone(snapshot),
                "session": self._session_detail(session),
            }

    def export_session_state(self, session_id: str, output_path: str | None = None) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            export_payload = {
                "session": self._session_detail(session),
                "snapshots": sorted(session["snapshots"].values(), key=lambda item: (item["created_at"], item["snapshot_id"])),
                "operation_log": json_clone(session.get("operation_log", [])),
            }
            if output_path:
                export_file = self.security.resolve_output_file(output_path, purpose="Session export")
                self._write_json(export_file, export_payload)
                return {"path": str(export_file), "relative_path": self._maybe_relative(export_file), "session": self._session_summary(session)}
            return export_payload

    def append_operation_log(
        self,
        session_id: str,
        *,
        tool_name: str,
        artifact_id: str | None = None,
        action: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            session["counters"]["operation"] = int(session["counters"].get("operation", 0)) + 1
            entry = {
                "operation_id": f"op_{short_token(session['session_id'])}_{session['counters']['operation']:04d}",
                "tool_name": tool_name,
                "action": action,
                "artifact_id": artifact_id,
                "created_at": utc_now(),
                "details": json_clone(details or {}),
            }
            session.setdefault("operation_log", []).append(entry)
            session["updated_at"] = utc_now()
            self._write_session(session)
            return json_clone(entry)

    def list_operation_log(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            return json_clone(session.get("operation_log", []))

    def update_artifact_feature07(self, session_id: str, artifact_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise StructuredToolError("invalid_request", "feature07_patch_invalid", "Feature 07 artifact updates must be a JSON object.")
        with self._lock:
            session = self._resolve_session(session_id=session_id)
            artifact = self._resolve_artifact(session, artifact_id=artifact_id)
            artifact["feature07"] = self._merge_dicts(artifact.get("feature07", {}), json_clone(patch))
            artifact["updated_at"] = utc_now()
            session["updated_at"] = utc_now()
            self._write_session(session)
            return self._artifact_summary(artifact)

    def _resolve_session(self, *, session_id: str | None = None, name: str | None = None) -> dict[str, Any]:
        if session_id:
            session = self._load_session(session_id)
            self._enforce_session_access(session)
            return session
        if name:
            session = self._find_session_by_name(name.strip())
            if session is not None:
                self._enforce_session_access(session)
                return session
            raise StructuredToolError("not_found", "session_not_found", f"Unknown session name '{name}'.", details={"name": name})
        raise StructuredToolError(
            "missing_prerequisite",
            "session_reference_required",
            "A session reference is required. Provide session_id or name.",
        )

    def _resolve_artifact(self, session: dict[str, Any], artifact_id: str | None = None, display_name: str | None = None) -> dict[str, Any]:
        if artifact_id:
            artifact = session["artifacts"].get(artifact_id)
            if artifact is not None:
                return artifact
        if display_name:
            matches = [item for item in session["artifacts"].values() if item["display_name"] == display_name]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise StructuredToolError("conflict", "artifact_name_ambiguous", f"Artifact display_name '{display_name}' is ambiguous.", details={"display_name": display_name})
        raise StructuredToolError(
            "missing_prerequisite" if not artifact_id and not display_name else "not_found",
            "artifact_not_found",
            "Artifact reference did not match any artifact in the session.",
            details={"artifact_id": artifact_id, "display_name": display_name},
        )

    def _resolve_snapshot(self, session: dict[str, Any], snapshot_id: str | None = None, name: str | None = None) -> dict[str, Any]:
        if snapshot_id:
            snapshot = session["snapshots"].get(snapshot_id)
            if snapshot is not None:
                return snapshot
        if name:
            matches = [item for item in session["snapshots"].values() if item["name"] == name]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise StructuredToolError("conflict", "snapshot_name_ambiguous", f"Snapshot name '{name}' is ambiguous.", details={"name": name})
        raise StructuredToolError("not_found", "snapshot_not_found", "Snapshot reference did not match any snapshot.", details={"snapshot_id": snapshot_id, "name": name})

    def _validate_target(self, session: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(target, dict):
            raise StructuredToolError("invalid_request", "annotation_target_invalid", "Annotation target must be a JSON object.")
        kind = target.get("kind")
        if kind not in {"session", "artifact", "function", "string", "address"}:
            raise StructuredToolError("invalid_request", "annotation_target_kind_invalid", f"Unsupported annotation target kind '{kind}'.")
        if kind == "session":
            return {"kind": "session", "session_id": session["session_id"]}
        if kind == "artifact":
            artifact = self._resolve_artifact(session, artifact_id=target.get("artifact_id"), display_name=target.get("display_name"))
            return {"kind": "artifact", "session_id": session["session_id"], "artifact_id": artifact["artifact_id"]}
        if kind in {"function", "string"}:
            object_id = target.get("object_id")
            if not object_id:
                raise StructuredToolError("invalid_request", "annotation_target_object_required", f"Target kind '{kind}' requires object_id.")
            record = self.get_object_reference(session["session_id"], object_id)
            record_key = "function_id" if kind == "function" else "string_id"
            return {"kind": kind, "session_id": session["session_id"], "artifact_id": record["artifact_id"], "object_id": record[record_key]}
        artifact = self._resolve_artifact(session, artifact_id=target.get("artifact_id"), display_name=target.get("display_name"))
        return {
            "kind": "address",
            "session_id": session["session_id"],
            "artifact_id": artifact["artifact_id"],
            "address": self._normalize_address(target.get("address")),
        }

    def _new_annotation_revision(
        self,
        annotation: dict[str, Any],
        value: Any,
        *,
        action: str,
        source_revision_id: str | None = None,
    ) -> dict[str, Any]:
        revision_number = len(annotation["history"]) + 1
        revision_id = f"rev_{annotation['annotation_id']}_{revision_number:04d}"
        revision = {
            "revision_id": revision_id,
            "created_at": utc_now(),
            "action": action,
            "value": json_clone(value),
        }
        if source_revision_id:
            revision["source_revision_id"] = source_revision_id
        return revision

    def _session_summary(self, session: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": session["session_id"],
            "name": session["name"],
            "description": session["description"],
            "created_at": session["created_at"],
            "updated_at": session["updated_at"],
            "artifact_count": len(session["artifacts"]),
            "annotation_count": len(session["annotations"]),
            "snapshot_count": len(session["snapshots"]),
            "operation_count": len(session.get("operation_log", [])),
            "ownership": json_clone(session.get("ownership")),
        }

    def _session_detail(self, session: dict[str, Any]) -> dict[str, Any]:
        return {
            "session": self._session_summary(session),
            "settings": json_clone(session["settings"]),
            "artifacts": [self._artifact_summary(item) for item in sorted(session["artifacts"].values(), key=lambda value: value["artifact_id"])],
            "snapshots": [json_clone(item) for item in sorted(session["snapshots"].values(), key=lambda value: value["snapshot_id"])],
            "operation_log": json_clone(session.get("operation_log", [])),
        }

    def _artifact_summary(self, artifact: dict[str, Any]) -> dict[str, Any]:
        return {
            "artifact_id": artifact["artifact_id"],
            "display_name": artifact["display_name"],
            "safe_display_name": artifact["safe_display_name"],
            "name_provenance": json_clone(artifact["name_provenance"]),
            "relationship": json_clone(artifact.get("relationship")),
            "canonical_path": artifact["canonical_path"],
            "relative_path": artifact["relative_path"],
            "size_bytes": artifact["size_bytes"],
            "analysis_generation": artifact["analysis_generation"],
            "created_at": artifact["created_at"],
            "updated_at": artifact["updated_at"],
            "function_count": len(artifact["functions"]),
            "string_count": len(artifact["strings"]),
            "analysis_status": artifact["analysis"]["status"],
            "analysis_backend": json_clone(artifact["analysis"]["backend"]),
            "analysis_summary": json_clone(artifact["analysis"]["summary"]),
            "analysis_completed_at": artifact["analysis"].get("completed_at"),
            "patch_count": len(artifact.get("feature07", {}).get("patch_history", [])),
        }

    def _annotation_summary(self, annotation: dict[str, Any]) -> dict[str, Any]:
        return {
            "annotation_id": annotation["annotation_id"],
            "annotation_type": annotation["annotation_type"],
            "target": json_clone(annotation["target"]),
            "created_at": annotation["created_at"],
            "updated_at": annotation["updated_at"],
            "current_revision_id": annotation["current_revision_id"],
            "revision_count": len(annotation["history"]),
            "value": json_clone(annotation["history"][-1]["value"]),
        }

    def _parse_object_id(self, object_id: str) -> tuple[str, str, int]:
        match = FUNCTION_ID_RE.match(object_id)
        if match:
            return "function", match.group(2), int(match.group(3))
        match = STRING_ID_RE.match(object_id)
        if match:
            return "string", match.group(2), int(match.group(3))
        raise StructuredToolError("invalid_id", "object_id_format_invalid", f"Object ID '{object_id}' does not match a supported ID format.", details={"object_id": object_id})

    def _maybe_relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.workspace_root))
        except ValueError:
            return str(path)

    def _normalize_address(self, value: int | str | None) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, int):
            return value
        raw = value.strip().lower()
        base = 16 if raw.startswith("0x") else 10
        try:
            return int(raw, base)
        except ValueError as exc:
            raise StructuredToolError("invalid_request", "address_invalid", f"Address '{value}' is not a valid integer.") from exc

    def _merge_dicts(self, existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        merged = json_clone(existing)
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge_dicts(merged[key], value)
            else:
                merged[key] = value
        return merged

    def _find_session_by_name(self, name: str) -> dict[str, Any] | None:
        for session_file in self.sessions_root.glob("*/session.json"):
            session = self._read_json(session_file)
            normalized = self._normalize_session_record(session)
            if normalized["name"] == name and self._session_visible(normalized):
                return normalized
        return None

    def _session_dir(self, session_id: str) -> Path:
        return self.sessions_root / session_id

    def _session_file(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "session.json"

    def _snapshot_dir(self, session_id: str) -> Path:
        return ensure_dir(self._session_dir(session_id) / "snapshots")

    def _snapshot_file(self, session_id: str, snapshot_id: str) -> Path:
        return self._snapshot_dir(session_id) / f"{snapshot_id}.json"

    def _analysis_dir(self, session_id: str) -> Path:
        return ensure_dir(self._session_dir(session_id) / "analysis")

    def _analysis_file(self, session_id: str, artifact_id: str, generation: int) -> Path:
        return self._analysis_dir(session_id) / f"{artifact_id}_g{generation}.json"

    def _load_session(self, session_id: str) -> dict[str, Any]:
        session_file = self._session_file(session_id)
        if not session_file.exists():
            raise StructuredToolError("not_found", "session_not_found", f"Unknown session_id '{session_id}'.", details={"session_id": session_id})
        return self._normalize_session_record(self._read_json(session_file))

    def _write_session(self, session: dict[str, Any]) -> None:
        session_dir = ensure_dir(self._session_dir(session["session_id"]))
        ensure_dir(session_dir / "snapshots")
        self._write_json(self._session_file(session["session_id"]), self._normalize_session_record(session))

    def _write_json(self, path: Path, payload: Any) -> None:
        ensure_dir(path.parent)
        tmp_path = path.with_name(f".{path.name}.{os.urandom(4).hex()}.tmp")
        tmp_path.write_text(stable_json_dumps(payload), encoding="utf-8")
        os.replace(tmp_path, path)

    def _read_json(self, path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise StructuredToolError("not_found", "state_file_not_found", f"State file '{path}' does not exist.", details={"path": str(path)}) from exc
        except Exception as exc:
            raise StructuredToolError("backend_failure", "state_file_invalid", f"State file '{path}' could not be parsed.", details={"path": str(path)}) from exc

    def _normalize_session_record(self, session: dict[str, Any]) -> dict[str, Any]:
        normalized = json_clone(session)
        normalized.setdefault("artifacts", {})
        normalized.setdefault("annotations", {})
        normalized.setdefault("snapshots", {})
        normalized.setdefault("settings", {})
        normalized.setdefault("operation_log", [])
        normalized.setdefault("ownership", self._legacy_ownership_payload())
        normalized.setdefault("counters", {})
        normalized["counters"].setdefault("artifact", 0)
        normalized["counters"].setdefault("annotation", 0)
        normalized["counters"].setdefault("snapshot", 0)
        normalized["counters"].setdefault("operation", len(normalized["operation_log"]))
        for artifact_id, artifact in list(normalized["artifacts"].items()):
            normalized["artifacts"][artifact_id] = self._normalize_artifact_record(artifact)
        return normalized

    def _normalize_artifact_record(self, artifact: dict[str, Any]) -> dict[str, Any]:
        normalized = json_clone(artifact)
        normalized.setdefault("functions", {})
        normalized.setdefault("strings", {})
        normalized.setdefault("counters", {})
        normalized["counters"].setdefault("function", 0)
        normalized["counters"].setdefault("string", 0)
        normalized.setdefault("analysis", {})
        normalized["analysis"].setdefault("instruction_set_mode_override", None)
        normalized.setdefault("feature07", {})
        normalized["feature07"].setdefault("patch_history", [])
        normalized["feature07"].setdefault("edits", {})
        edits = normalized["feature07"]["edits"]
        edits.setdefault("function_names", {})
        edits.setdefault("function_types", {})
        edits.setdefault("variable_names", {})
        edits.setdefault("variable_types", {})
        edits.setdefault("global_names", {})
        edits.setdefault("global_types", {})
        edits.setdefault("calling_conventions", {})
        edits.setdefault("type_imports", [])
        edits.setdefault("named_types", {})
        edits["named_types"].setdefault("structs", {})
        edits["named_types"].setdefault("enums", {})
        edits["named_types"].setdefault("typedefs", {})
        return normalized

    def _session_ownership_payload(self) -> dict[str, Any]:
        context = get_request_context()
        if context.is_http:
            return {
                "transport": context.transport,
                "authenticated": context.authenticated,
                "tenant_id": context.tenant_id,
                "agent_id": context.agent_id,
                "single_agent": True,
            }
        return {
            "transport": context.transport,
            "authenticated": False,
            "tenant_id": None,
            "agent_id": context.agent_id,
            "single_agent": False,
        }

    def _legacy_ownership_payload(self) -> dict[str, Any]:
        return {
            "transport": "legacy",
            "authenticated": False,
            "tenant_id": None,
            "agent_id": None,
            "single_agent": False,
        }

    def _session_visible(self, session: dict[str, Any]) -> bool:
        context = get_request_context()
        if not context.is_http:
            return True
        ownership = session.get("ownership") or self._legacy_ownership_payload()
        owner_transport = ownership.get("transport")
        owner_tenant = ownership.get("tenant_id")
        owner_agent = ownership.get("agent_id")
        if owner_transport in {None, "legacy"}:
            return True
        if owner_transport != "http":
            return False
        if owner_tenant != context.tenant_id:
            return False
        if ownership.get("single_agent", True) and owner_agent and owner_agent != context.agent_id:
            return False
        return True

    def _enforce_session_access(self, session: dict[str, Any]) -> None:
        context = get_request_context()
        if not context.is_http:
            return
        ownership = session.get("ownership") or self._legacy_ownership_payload()
        owner_transport = ownership.get("transport")
        owner_tenant = ownership.get("tenant_id")
        owner_agent = ownership.get("agent_id")
        if owner_transport not in {None, "legacy", "http"}:
            raise StructuredToolError(
                "authorization_failed",
                "session_transport_isolated",
                "HTTP access is not permitted for sessions created under a different transport scope.",
                details={"session_id": session["session_id"], "owner_transport": owner_transport},
            )
        if owner_transport == "http" and owner_tenant != context.tenant_id:
            raise StructuredToolError(
                "authorization_failed",
                "session_tenant_forbidden",
                "The requested session belongs to a different tenant.",
                details={"session_id": session["session_id"], "tenant_id": context.tenant_id},
            )
        if owner_transport == "http" and ownership.get("single_agent", True) and owner_agent and owner_agent != context.agent_id:
            raise StructuredToolError(
                "conflict",
                "session_agent_conflict",
                "The requested session is already leased to a different agent.",
                details={"session_id": session["session_id"], "agent_id": context.agent_id},
            )

    def _enforce_session_quota(self) -> None:
        context = get_request_context()
        if not context.is_http:
            return
        config = load_http_transport_config()
        count = 0
        for session_file in self.sessions_root.glob("*/session.json"):
            session = self._normalize_session_record(self._read_json(session_file))
            ownership = session.get("ownership") or self._legacy_ownership_payload()
            if ownership.get("transport") == "http" and ownership.get("tenant_id") == context.tenant_id:
                count += 1
        if count >= config.max_sessions_per_tenant:
            raise StructuredToolError(
                "timeout_or_resource_limit",
                "tenant_session_quota_exceeded",
                "The current tenant reached the configured session quota.",
                details={"tenant_id": context.tenant_id, "limit": config.max_sessions_per_tenant},
            )
