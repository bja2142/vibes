from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable


class Job:
    def __init__(self, job_id: str, job_type: str, session_id: str) -> None:
        self.job_id = job_id
        self.job_type = job_type
        self.session_id = session_id
        self.status = "running"          # running | completed | failed | cancelled
        self.created_at = time.time()
        self.updated_at = time.time()
        self.completed_at: float | None = None
        self.progress: dict[str, Any] = {"percent": 0, "message": "Starting..."}
        self.result: dict[str, Any] | None = None
        self.error: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def update_progress(self, percent: int, message: str) -> None:
        with self._lock:
            self.progress = {"percent": percent, "message": message}
            self.updated_at = time.time()

    def complete(self, result: dict[str, Any]) -> None:
        with self._lock:
            self.status = "completed"
            self.result = result
            self.progress = {"percent": 100, "message": "Completed"}
            self.completed_at = time.time()
            self.updated_at = self.completed_at

    def fail(self, error: dict[str, Any]) -> None:
        with self._lock:
            self.status = "failed"
            self.error = error
            self.completed_at = time.time()
            self.updated_at = self.completed_at

    def cancel(self) -> None:
        with self._lock:
            if self.status == "running":
                self.status = "cancelled"
                self.completed_at = time.time()
                self.updated_at = self.completed_at

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "job_id": self.job_id,
                "job_type": self.job_type,
                "session_id": self.session_id,
                "status": self.status,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "completed_at": self.completed_at,
                "progress": dict(self.progress),
                "result": self.result,
                "error": self.error,
            }


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, job_type: str, session_id: str) -> Job:
        job_id = f"job_{uuid.uuid4().hex[:16]}"
        job = Job(job_id, job_type, session_id)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_for_session(self, session_id: str) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.session_id == session_id]

    def run_async(self, job: Job, fn: Callable[[Job], dict[str, Any]]) -> None:
        """Run fn in a background thread, updating job on completion/failure."""
        def _worker() -> None:
            try:
                result = fn(job)
                job.complete(result)
            except Exception as exc:
                job.fail({"message": str(exc), "type": type(exc).__name__})

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
