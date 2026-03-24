from __future__ import annotations

import threading
import time
from typing import Any

from .errors import StructuredToolError
from .transport import RequestContext, get_request_context, load_http_transport_config, request_context
from .utils import json_clone, paginate, utc_now


class JobManager:
    def __init__(self, store, parser_sandbox) -> None:
        self.store = store
        self.parser_sandbox = parser_sandbox
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancel_events: dict[str, threading.Event] = {}

    def start_artifact_analysis(self, session_id: str, artifact_id: str, hints: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._start_job(
            job_type="artifact_analysis",
            session_id=session_id,
            artifact_id=artifact_id,
            hints=hints or {},
            worker_target=self._run_artifact_analysis,
        )

    def start_artifact_reanalysis(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        return self._start_job(
            job_type="artifact_reanalysis",
            session_id=session_id,
            artifact_id=artifact_id,
            hints={},
            worker_target=self._run_artifact_reanalysis,
        )

    def _start_job(self, *, job_type: str, session_id: str, artifact_id: str, hints: dict[str, Any], worker_target) -> dict[str, Any]:
        job_id = f"job_{time.time_ns():x}"
        now = utc_now()
        context = get_request_context()
        job = {
            "job_id": job_id,
            "job_type": job_type,
            "status": "queued",
            "session_id": session_id,
            "artifact_id": artifact_id,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            "progress": {"percent": 0, "message": "Queued"},
            "partial_result": None,
            "result": None,
            "error": None,
            "parameters": {"hints": json_clone(hints)},
            "ownership": {
                "transport": context.transport,
                "authenticated": context.authenticated,
                "tenant_id": context.tenant_id,
                "agent_id": context.agent_id,
            },
        }
        cancel_event = threading.Event()
        with self._lock:
            self._enforce_job_quota(context)
            self._jobs[job_id] = job
            self._cancel_events[job_id] = cancel_event
        worker = threading.Thread(
            target=self._run_with_context,
            args=(job["ownership"], worker_target, job_id, session_id, artifact_id, json_clone(hints), cancel_event),
            daemon=True,
        )
        worker.start()
        return json_clone(job)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise StructuredToolError("not_found", "job_not_found", f"Unknown job_id '{job_id}'.", details={"job_id": job_id})
            self._enforce_job_access(job)
            return json_clone(job)

    def list_jobs(self, session_id: str | None = None, status: str | None = None, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
        with self._lock:
            if session_id:
                self.store.load_session(session_id=session_id)
            jobs = [job for job in self._jobs.values() if self._job_visible(job)]
            if session_id:
                jobs = [job for job in jobs if job["session_id"] == session_id]
            if status:
                jobs = [job for job in jobs if job["status"] == status]
            jobs.sort(key=lambda item: (item["created_at"], item["job_id"]))
            return paginate([json_clone(item) for item in jobs], cursor, limit)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise StructuredToolError("not_found", "job_not_found", f"Unknown job_id '{job_id}'.", details={"job_id": job_id})
            self._enforce_job_access(job)
            if job["status"] in {"completed", "failed", "cancelled"}:
                return json_clone(job)
            job["status"] = "cancelling"
            job["updated_at"] = utc_now()
            job["progress"] = {"percent": job["progress"]["percent"], "message": "Cancellation requested"}
            self._cancel_events[job_id].set()
            return json_clone(job)

    def _run_artifact_analysis(
        self,
        job_id: str,
        session_id: str,
        artifact_id: str,
        hints: dict[str, Any],
        cancel_event: threading.Event,
    ) -> None:
        try:
            self._update_job(job_id, status="running", percent=0, message="Starting artifact analysis")
            self.store.mark_artifact_analysis_status(session_id, artifact_id, status="running", job_id=job_id)
            if cancel_event.is_set():
                self.store.mark_artifact_analysis_status(session_id, artifact_id, status="cancelled", job_id=job_id)
                self._mark_cancelled(job_id, {"phase": "cancelled_before_load"})
                return
            artifact = self.store.get_artifact_record(session_id=session_id, artifact_id=artifact_id)
            self._update_job(job_id, status="running", percent=10, message="Loading backend and recovering symbols", partial_result={"phase": "backend_load"})
            analysis = self.parser_sandbox.analyze_program(artifact["canonical_path"], hints=hints)
            self._update_job(job_id, status="running", percent=75, message="Persisting recovered analysis objects", partial_result={"phase": "persist"})
            if cancel_event.is_set():
                self.store.mark_artifact_analysis_status(session_id, artifact_id, status="cancelled", job_id=job_id)
                self._mark_cancelled(job_id, {"phase": "cancelled_before_commit"})
                return
            result = self.store.persist_artifact_analysis(
                session_id=session_id,
                artifact_id=artifact_id,
                analysis_payload=analysis["result"],
                job_id=job_id,
            )
            self._update_job(
                job_id,
                status="completed",
                percent=100,
                message="Artifact analysis completed",
                partial_result={"phase": "complete"},
                result=result,
                completed=True,
            )
        except StructuredToolError as exc:
            self.store.mark_artifact_analysis_status(session_id, artifact_id, status="failed", job_id=job_id, error=exc.to_dict())
            self._update_job(
                job_id,
                status="failed",
                percent=100,
                message=exc.message,
                error=exc.to_dict(),
                completed=True,
            )
        except Exception as exc:  # pragma: no cover - defensive normalization
            normalized = {
                "category": "backend_failure",
                "code": "job_internal_error",
                "message": str(exc),
                "details": {"job_id": job_id},
                "retryable": False,
                "partial": False,
            }
            self.store.mark_artifact_analysis_status(session_id, artifact_id, status="failed", job_id=job_id, error=normalized)
            self._update_job(
                job_id,
                status="failed",
                percent=100,
                message=str(exc),
                error=normalized,
                completed=True,
            )

    def _run_artifact_reanalysis(
        self,
        job_id: str,
        session_id: str,
        artifact_id: str,
        hints: dict[str, Any],
        cancel_event: threading.Event,
    ) -> None:
        del hints
        stages = [
            (10, "Validating artifact reference", {"phase": "validate"}),
            (45, "Invalidating existing analysis object IDs", {"phase": "invalidate"}),
            (80, "Committing new analysis generation", {"phase": "commit"}),
        ]
        try:
            self._update_job(job_id, status="running", percent=0, message="Starting artifact re-analysis")
            for percent, message, partial_result in stages:
                if cancel_event.is_set():
                    self._mark_cancelled(job_id, partial_result)
                    return
                time.sleep(0.05)
                self._update_job(job_id, status="running", percent=percent, message=message, partial_result=partial_result)
            if cancel_event.is_set():
                self._mark_cancelled(job_id, {"phase": "cancelled_before_commit"})
                return
            result = self.store.advance_artifact_generation(session_id=session_id, artifact_id=artifact_id)
            self._update_job(
                job_id,
                status="completed",
                percent=100,
                message="Artifact re-analysis completed",
                partial_result={"phase": "complete"},
                result=result,
                completed=True,
            )
        except StructuredToolError as exc:
            self._update_job(
                job_id,
                status="failed",
                percent=100,
                message=exc.message,
                error=exc.to_dict(),
                completed=True,
            )
        except Exception as exc:  # pragma: no cover - defensive normalization
            self._update_job(
                job_id,
                status="failed",
                percent=100,
                message=str(exc),
                error={
                    "category": "backend_failure",
                    "code": "job_internal_error",
                    "message": str(exc),
                    "details": {"job_id": job_id},
                    "retryable": False,
                    "partial": False,
                },
                completed=True,
            )

    def _run_with_context(
        self,
        ownership: dict[str, Any],
        worker_target,
        job_id: str,
        session_id: str,
        artifact_id: str,
        hints: dict[str, Any],
        cancel_event: threading.Event,
    ) -> None:
        with request_context(
            RequestContext(
                transport=ownership.get("transport", "stdio"),
                authenticated=bool(ownership.get("authenticated")),
                tenant_id=ownership.get("tenant_id"),
                agent_id=ownership.get("agent_id"),
            )
        ):
            worker_target(job_id, session_id, artifact_id, hints, cancel_event)

    def _mark_cancelled(self, job_id: str, partial_result: dict[str, Any] | None) -> None:
        self._update_job(
            job_id,
            status="cancelled",
            percent=100,
            message="Job cancelled",
            partial_result=partial_result,
            completed=True,
        )

    def _update_job(
        self,
        job_id: str,
        *,
        status: str,
        percent: int,
        message: str,
        partial_result: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        completed: bool = False,
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = status
            job["updated_at"] = utc_now()
            job["progress"] = {"percent": percent, "message": message}
            if partial_result is not None:
                job["partial_result"] = json_clone(partial_result)
            if result is not None:
                job["result"] = json_clone(result)
            if error is not None:
                job["error"] = json_clone(error)
            if completed:
                job["completed_at"] = utc_now()

    def _job_visible(self, job: dict[str, Any]) -> bool:
        context = get_request_context()
        if context.transport != "http":
            return True
        ownership = job.get("ownership") or {}
        owner_transport = ownership.get("transport")
        if owner_transport not in {None, "legacy", "http"}:
            return False
        if owner_transport == "http" and ownership.get("tenant_id") != context.tenant_id:
            return False
        if owner_transport == "http" and ownership.get("agent_id") and ownership.get("agent_id") != context.agent_id:
            return False
        return True

    def _enforce_job_access(self, job: dict[str, Any]) -> None:
        if not self._job_visible(job):
            ownership = job.get("ownership") or {}
            if ownership.get("tenant_id") != get_request_context().tenant_id:
                raise StructuredToolError(
                    "authorization_failed",
                    "job_tenant_forbidden",
                    "The requested job belongs to a different tenant.",
                    details={"job_id": job["job_id"]},
                )
            raise StructuredToolError(
                "conflict",
                "job_agent_conflict",
                "The requested job belongs to a different agent lease.",
                details={"job_id": job["job_id"]},
            )

    def _enforce_job_quota(self, context) -> None:
        if context.transport != "http":
            return
        config = load_http_transport_config()
        with self._lock:
            active = sum(
                1
                for job in self._jobs.values()
                if job.get("ownership", {}).get("transport") == "http"
                and job.get("ownership", {}).get("tenant_id") == context.tenant_id
                and job["status"] not in {"completed", "failed", "cancelled"}
            )
        if active >= config.max_active_jobs_per_tenant:
            raise StructuredToolError(
                "timeout_or_resource_limit",
                "tenant_job_quota_exceeded",
                "The current tenant reached the configured active-job quota.",
                details={"tenant_id": context.tenant_id, "limit": config.max_active_jobs_per_tenant},
            )
