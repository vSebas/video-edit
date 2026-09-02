from __future__ import annotations

import datetime as dt
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

HISTORY_LIMIT = 200


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


class JobManager:
    """Background jobs with durable history (P6).

    Every state change is persisted to jobs.json, so job history survives an
    app restart; jobs that were queued/running at shutdown reload as
    "interrupted" (their thread is gone — the work may or may not have
    finished, and the record says so). A second submit of the same
    (project_id, kind) while one is still active returns the active job
    instead of stacking a duplicate.
    """

    def __init__(self, workers: int = 2, store: Path | None = None) -> None:
        self._executor = ThreadPoolExecutor(max_workers=workers)
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._store = store
        self._load()

    def _load(self) -> None:
        if self._store is None or not self._store.is_file():
            return
        try:
            saved = json.loads(self._store.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for job in saved.get("jobs", []):
            if job.get("status") in ("queued", "running"):
                job["status"] = "interrupted"
                job["error"] = (
                    "The app restarted while this job was active; check the "
                    "project outputs to see whether the work completed"
                )
                job["finished_at"] = job.get("finished_at") or utc_now()
            self._jobs[job["job_id"]] = job

    def _persist_locked(self) -> None:
        if self._store is None:
            return
        jobs = sorted(self._jobs.values(), key=lambda j: j["created_at"])
        payload = {"jobs": jobs[-HISTORY_LIMIT:]}
        try:
            self._store.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._store.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )
            temporary.replace(self._store)
        except OSError:
            pass  # durability is best-effort; the in-memory view stays correct

    def submit(
        self,
        kind: str,
        project_id: str,
        operation: Callable[[], Any],
        fingerprint: str | None = None,
    ) -> dict:
        """fingerprint distinguishes materially different requests of the
        same kind (captioned vs plain render, different models) so dedup
        never hands back a job that will not do what was asked."""
        with self._lock:
            for job in self._jobs.values():
                if (
                    job["project_id"] == project_id
                    and job["kind"] == kind
                    and job.get("fingerprint") == fingerprint
                    and job["status"] in ("queued", "running")
                ):
                    duplicate = dict(job)
                    duplicate["already_running"] = True
                    return duplicate
            job_id = uuid.uuid4().hex[:12]
            job = {
                "job_id": job_id,
                "project_id": project_id,
                "kind": kind,
                "fingerprint": fingerprint,
                "status": "queued",
                "created_at": utc_now(),
                "started_at": None,
                "finished_at": None,
                "result": None,
                "error": None,
            }
            self._jobs[job_id] = job
            self._persist_locked()

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
            self._persist_locked()

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(job) for job in self._jobs.values()]
