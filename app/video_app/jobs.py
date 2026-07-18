from __future__ import annotations

import datetime as dt
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


class JobManager:
    def __init__(self, workers: int = 2) -> None:
        self._executor = ThreadPoolExecutor(max_workers=workers)
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def submit(self, kind: str, project_id: str, operation: Callable[[], Any]) -> dict:
        job_id = uuid.uuid4().hex[:12]
        job = {
            "job_id": job_id,
            "project_id": project_id,
            "kind": kind,
            "status": "queued",
            "created_at": utc_now(),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        }
        with self._lock:
            self._jobs[job_id] = job

        def run() -> None:
            self._update(job_id, status="running", started_at=utc_now())
            try:
                result = operation()
            except Exception as exc:  # surfaced through the job API
                self._update(
                    job_id,
                    status="failed",
                    error=str(exc),
                    finished_at=utc_now(),
                )
            else:
                self._update(
                    job_id,
                    status="completed",
                    result=result,
                    finished_at=utc_now(),
                )

        self._executor.submit(run)
        return dict(job)

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(values)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(job) for job in self._jobs.values()]
