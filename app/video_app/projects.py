from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import Settings
from .context import PROMPT_VERSION as CONTEXT_PROMPT_VERSION
from .context import ContextAnalysisError, analyze_context
from .planning import (
    PlanningError,
    compile_edit_plan,
    generate_concepts,
    revise_plan,
    validate_edit_plan,
)
from .providers import ChatClient, ProviderError, make_client, resolve_provider
from .semantic import SemanticEvidenceError, validate_semantic_evidence
from .visual import (
    PROMPT_VERSION as VISUAL_PROMPT_VERSION,
    VisualAnalysisError,
    analyze_assets,
    auto_review_decisions,
)


LOGGER = logging.getLogger(__name__)

# Live analysis progress by project, for the UI's busy card. Best-effort
# in-memory state — restarts simply reset it.
ANALYSIS_PROGRESS: dict[str, dict] = {}


def _progress_setter(project_id: str, phase: str):
    token = uuid.uuid4().hex[:8]

    def update(done: int, total: int) -> None:
        ANALYSIS_PROGRESS[project_id] = {
            "phase": phase, "done": done, "total": total, "token": token,
        }

    def clear() -> None:
        # concurrent jobs must not erase each other's progress
        if ANALYSIS_PROGRESS.get(project_id, {}).get("token") == token:
            ANALYSIS_PROGRESS.pop(project_id, None)

    update.clear = clear
    return update

def concepts_doc_concepts(document: dict) -> list[dict]:
    return document.get("concepts") or []


# Schemas and the deterministic render/export scripts ship with the app.
APP_DIR = Path(__file__).resolve().parent.parent
SCHEMA_DIR = APP_DIR / "schemas"
PIPELINE_DIR = APP_DIR / "pipeline"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg"}
SUPPORTED_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS
# Writer chosen by blind video screening (bench/planner, 2026-08-17): the
# user preferred deepseek's and fable's cuts over both Qwens'; deepseek is
# the default (available on the existing workspace key).
PLANNER_DEFAULT_MODELS = {"qwen": "deepseek-v4-pro"}
EVIDENCE_ADAPTERS = {"openstoryline", "owned-live-visual", "local-asr"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique temp name: two threads writing the same file would otherwise
    # share one .tmp path and publish each other's half-written bytes.
    temporary = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value[:60] or "video-project"


class ProjectError(RuntimeError):
    pass


class ProjectService:
    def __init__(self, settings: Settings) -> None:
        self._render_locks: dict[str, threading.Lock] = {}
        self._render_locks_guard = threading.Lock()
        self._drive_imports: dict[str, subprocess.Popen] = {}
        self._drive_cancelled: set[str] = set()
        self._drive_imports_guard = threading.Lock()
        self.settings = settings
        self.settings.runtime.mkdir(parents=True, exist_ok=True)
        self._semantic_review_lock = threading.Lock()
        self._project_locks: dict[str, threading.Lock] = {}
        self._project_locks_guard = threading.Lock()
        # Placement rewrites the open OpenTake timeline; two concurrent
        # clicks interleaving remove/add would shred it.
        self._opentake_place_lock = threading.Lock()

    @contextmanager
    def _project_write(self, project_id: str):
        """Serialize read-modify-write of one project's project.json.

        Background jobs and request handlers run as threads in one process,
        so an unguarded load -> mutate -> write pair silently drops whatever
        the other thread wrote in between.
        """
        with self._project_locks_guard:
            lock = self._project_locks.setdefault(project_id, threading.Lock())
        with lock:
            yield

    def capabilities(self) -> dict:
        faster_whisper = importlib.util.find_spec("faster_whisper") is not None
        live_visual_ready = bool(os.environ.get("DASHSCOPE_API_KEY")) or bool(
            os.environ.get("GEMINI_API_KEY")
        )
        return {
            "visual": [
                {
                    "id": "owned-live-visual",
                    "label": "Owned live visual adapter (Qwen/Gemini)",
                    "ready": live_visual_ready,
                    "detail": (
                        "Deterministic shots and keyframes are described by the "
                        "configured hosted VLM."
                        if live_visual_ready
                        else "Set DASHSCOPE_API_KEY or GEMINI_API_KEY to enable."
                    ),
                },
            ],
            "speech": [
                {
                    "id": "faster-whisper",
                    "label": "Local timestamped ASR",
                    "ready": faster_whisper,
                    "detail": (
                        "faster-whisper is installed."
                        if faster_whisper
                        else "Adapter boundary is defined; runtime/model are not installed in the app image yet."
                    ),
                },
            ],
            "render": {
                "id": "ffmpeg",
                "label": "Deterministic FFmpeg render",
                "ready": shutil.which("ffmpeg") is not None,
            },
            "editable_exports": {
                "id": "otio-xmeml",
                "label": "OTIO, DaVinci XMEML",
                "ready": True,
            },
        }

    def list_projects(self) -> list[dict]:
        projects = []
        for path in sorted(self.settings.runtime.glob("*/project.json")):
            data = load_json(path)
            projects.append(self._summary(data))
        return projects

    def get_project(self, project_id: str) -> dict:
        path = self.settings.runtime / project_id / "project.json"
        if not path.is_file():
            raise ProjectError(f"Unknown project: {project_id}")
        data = load_json(path)
        return self._decorate_runtime_project(data)

    def create_project(self, name: str, source_directory: str, prompt: str) -> dict:
        project_id = slugify(name)
        final_dir = self.settings.runtime / project_id
        if final_dir.exists():
            raise ProjectError(f"Project already exists: {project_id}")

        source_dir = self._resolve_source_directory(source_directory)
        media_paths = sorted(
            path
            for path in source_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        )
        if not media_paths:
            raise ProjectError("No supported video, image, or audio files were found")

        self.settings.runtime.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{project_id}-", dir=self.settings.runtime))
        try:
            thumbnails = staging / "thumbnails"
            thumbnails.mkdir()
            assets = []
            used_ids: set[str] = set()
            for path in media_paths:
                asset_id = slugify(path.stem).replace("-", "_")
                base_id = asset_id
                suffix = 2
                while asset_id in used_ids:
                    asset_id = f"{base_id}_{suffix}"
                    suffix += 1
                used_ids.add(asset_id)
                asset = self._probe_asset(asset_id, path)
                thumbnail = thumbnails / f"{asset_id}.jpg"
                if self._make_thumbnail(path, asset, thumbnail):
                    asset["thumbnail_available"] = True
                assets.append(asset)

            project = {
                "schema_version": "video-app-project.v1",
                "project_id": project_id,
                "name": name.strip(),
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "source_directory": str(source_dir.relative_to(self.settings.root)),
                "prompt": prompt.strip(),
                "status": "awaiting_semantic_analysis",
                "footage_summary": (
                    f"Indexed {len(assets)} media file(s). Technical facts are ready; "
                    "visual semantics and speech content have not been analyzed yet."
                ),
                "analysis": {
                    "technical": "completed",
                    "visual": "unavailable",
                    "speech": "unavailable",
                    "warning": "No creative claims or automatic edit will be invented without semantic evidence.",
                },
                "inventory": {"assets": assets},
                "concepts": [],
                "selected_concept_id": None,
                "plan": None,
                "outputs": {},
            }
            write_json(staging / "project.json", project)
            try:
                os.replace(staging, final_dir)
            except OSError as exc:  # concurrent create of the same id
                raise ProjectError(f"Project already exists: {project_id}") from exc
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return self.get_project(project_id)

    def delete_project(self, project_id: str) -> dict:
        """Delete a runtime project's derived state. Source media is never
        touched; the archived benchmark fixture cannot be deleted."""
        target = (self.settings.runtime / project_id).resolve()
        try:
            target.relative_to(self.settings.runtime)
        except ValueError as exc:
            raise ProjectError("Invalid project id") from exc
        if not (target / "project.json").is_file():
            raise ProjectError(f"Unknown project: {project_id}")
        shutil.rmtree(target)
        return {"deleted": project_id}

    BROWSE_SKIP = {
        ".git", "node_modules", "__pycache__", ".venv", "repos",
        "runtime", ".tmp", ".claude", "app", "bench",
    }

    def browse_directories(self, relative: str = "") -> dict:
        """List subdirectories and media counts under the workspace so the
        UI can offer a folder picker without exposing the whole filesystem."""
        base = self.settings.root
        current = (base / relative).resolve() if relative else base
        try:
            current.relative_to(base)
        except ValueError as exc:
            raise ProjectError("Folder must be inside the workspace") from exc
        if not current.is_dir():
            raise ProjectError("Folder does not exist")
        directories = []
        media_count = 0
        for entry in sorted(current.iterdir()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                if current == base and entry.name in self.BROWSE_SKIP:
                    continue
                child_media = sum(
                    1
                    for item in entry.iterdir()
                    if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS
                )
                directories.append(
                    {
                        "name": entry.name,
                        "path": str(entry.relative_to(base)),
                        "media_count": child_media,
                    }
                )
            elif entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTENSIONS:
                media_count += 1
        return {
            "path": "" if current == base else str(current.relative_to(base)),
            "parent": (
                None
                if current == base
                else str(current.parent.relative_to(base)) if current.parent != base else ""
            ),
            "media_count": media_count,
            "directories": directories,
        }

    def select_concept(self, project_id: str, concept_id: str) -> dict:
        project = self.get_project(project_id)
        concept = next(
            (item for item in project.get("concepts", []) if item["concept_id"] == concept_id),
            None,
        )
        if concept is None:
            raise ProjectError(f"Unknown concept: {concept_id}")
        selection = {
            "project_id": project_id,
            "concept_id": concept_id,
            "selected_at": utc_now(),
            "plan_available": (project.get("plan") or {}).get("concept_id") == concept_id,
        }
        write_json(self.settings.runtime / project_id / "selection.json", selection)
        return selection

    def analyze_visual(
        self,
        project_id: str,
        provider: str = "gemini",
        model: str | None = None,
        force: bool = False,
    ) -> dict:
        """Run the owned live visual adapter over the project's media and
        persist the result as a reviewed-by-policy semantic evidence run."""
        project = self.get_project(project_id)
        assets = project.get("inventory", {}).get("assets", [])
        if not assets:
            raise ProjectError("The project has no indexed media to analyze")
        media_root = self.settings.root

        run_id = uuid.uuid4().hex[:12]
        run_key = f"{provider}-live-{run_id}"
        try:
            client = make_client(provider, model)
            content_key = self._analysis_content_key(
                assets,
                adapter="owned-live-visual",
                model=getattr(client.config, "model", model),
                prompt_version=VISUAL_PROMPT_VERSION,
            )
            if not force:
                cached = self._existing_run_for(project_id, content_key)
                if cached is not None:
                    # rotation detection is incremental and pre-dates some
                    # runs — a cache hit must not skip unchecked assets
                    self._detect_rotations(project_id, client)
                    return cached
            ANALYSIS_PROGRESS.pop(project_id, None)
            visual_progress = _progress_setter(project_id, "visual")
            try:
                normalized, raw_records, telemetry = analyze_assets(
                    client, assets, media_root, project_id, run_id,
                    progress=visual_progress,
                )
            finally:
                visual_progress.clear()
            validate_semantic_evidence(
                normalized,
                SCHEMA_DIR / "semantic-evidence.schema.json",
            )
        except (ProviderError, VisualAnalysisError, SemanticEvidenceError) as exc:
            raise ProjectError(f"Live visual analysis failed: {exc}") from exc

        if not normalized["observations"]:
            raise ProjectError(
                "Live visual analysis produced no observations; "
                "provider warnings: " + "; ".join(normalized["warnings"][:3])
            )

        reviews = auto_review_decisions(normalized)
        reviews["run_key"] = run_key
        runs_dir = self.settings.runtime / project_id / "analysis" / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{run_key}-", dir=runs_dir))
        try:
            raw_dir = staging / "raw"
            raw_dir.mkdir()
            write_json(raw_dir / "live-responses.json", {"responses": raw_records})
            write_json(staging / "normalized.json", normalized)
            write_json(staging / "reviews.json", reviews)
            manifest = {
                "schema_version": "semantic-run-manifest.v1",
                "run_key": run_key,
                "run_id": run_id,
                "project_id": project_id,
                "content_key": content_key,
                "provider": normalized["provider"],
                "review_status": normalized["review_status"],
                "safe_for_edit_plan": normalized["safe_for_edit_plan"],
                "summary": normalized["summary"],
                "warnings": normalized["warnings"],
                "telemetry": telemetry,
                "imported_at": normalized["generated_at"],
                "detail_url": f"/api/projects/{project_id}/analysis/runs/{run_key}",
            }
            write_json(staging / "manifest.json", manifest)
            os.replace(staging, runs_dir / run_key)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        self._corroborate_speech_claims(project_id)
        self._mark_semantic_progress(project_id, "visual")
        self._detect_rotations(project_id, client)
        return self.semantic_run(project_id, run_key)

    def _analysis_content_key(
        self, assets: list[dict], *, adapter: str, model: str | None,
        prompt_version: str,
    ) -> str:
        """Artifact identity v1: the same media through the same adapter,
        model, and prompt version is the same computation — key it so a
        repeat run returns the existing artifact instead of paying again."""
        identity = {
            "media": sorted(
                [a["asset_id"], a.get("sha256", "")] for a in assets
            ),
            "adapter": adapter,
            "model": model or "",
            "prompt_version": prompt_version,
        }
        return hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode()
        ).hexdigest()[:24]

    def _existing_run_for(self, project_id: str, content_key: str) -> dict | None:
        runs_dir = self.settings.runtime / project_id / "analysis" / "runs"
        for manifest_path in sorted(runs_dir.glob("*/manifest.json"), reverse=True):
            try:
                manifest = load_json(manifest_path)
            except (OSError, ValueError):
                continue
            if manifest.get("content_key") == content_key:
                result = self.semantic_run(project_id, manifest["run_key"])
                result["cached"] = True
                return result
        return None

    def _detect_rotations(self, project_id: str, client) -> None:
        """Best-effort sideways-clip detection: one frame per video asset
        that has not been checked yet; a confident non-zero answer lands in
        the inventory as suggested_rotation_degrees for the compiler."""
        from .visual import detect_orientation

        project = self.get_project(project_id)
        pending = [
            asset for asset in project.get("inventory", {}).get("assets", [])
            if asset.get("media_type") == "video"
            and "suggested_rotation_degrees" not in asset
        ]
        if not pending:
            return
        found: dict[str, int] = {}
        for asset in pending:
            try:
                degrees, confidence = detect_orientation(
                    client,
                    (self.settings.root / asset["source_path"]).resolve(),
                    float(asset.get("duration_seconds") or 1.0),
                )
            except Exception:
                continue  # best-effort: an unreadable clip stays unchecked
            found[asset["asset_id"]] = (
                degrees if degrees and confidence >= 0.8 else 0
            )
        if not found:
            return
        with self._project_write(project_id):
            path = self.settings.runtime / project_id / "project.json"
            state = load_json(path)
            for asset in state.get("inventory", {}).get("assets", []):
                if asset["asset_id"] in found:
                    asset["suggested_rotation_degrees"] = found[asset["asset_id"]]
            state["updated_at"] = utc_now()
            write_json(path, state)

    def analyze_context(
        self, project_id: str, model: str | None = None, force: bool = False,
    ) -> dict:
        """Build derived source/event/relationship context without promoting
        any of it into the semantic evidence or review pipelines."""
        project = self.get_project(project_id)
        assets = project.get("inventory", {}).get("assets", [])
        if not assets:
            raise ProjectError("The project has no indexed media to analyze")

        run_id = uuid.uuid4().hex[:12]
        run_key = f"ctx-live-{run_id}"
        try:
            client = make_client("gemini", model)
            anchors = sorted(
                str(o.get("evidence_id") or o.get("id") or "")
                for o in (self._fine_observations(project_id) or [])
            )
            content_key = self._analysis_content_key(
                assets, adapter="owned-source-context",
                model=getattr(client.config, "model", model),
                prompt_version=CONTEXT_PROMPT_VERSION
                + ":" + hashlib.sha256(
                    json.dumps(anchors).encode()
                ).hexdigest()[:12],
            )
            if not force:
                cached = self._existing_run_for(project_id, content_key)
                if cached is not None:
                    # rotation detection is incremental and pre-dates some
                    # runs — a cache hit must not skip unchecked assets
                    self._detect_rotations(project_id, client)
                    return cached
            normalized, raw_records, telemetry = analyze_context(
                client,
                assets,
                self.settings.root,
                project_id,
                run_id,
                self._fine_observations(project_id),
            )
            self._validate_schema(
                normalized,
                SCHEMA_DIR / "source-context.schema.json",
                "Source context",
            )
        except (ProviderError, ContextAnalysisError) as exc:
            raise ProjectError(f"Source context analysis failed: {exc}") from exc

        runs_dir = self.settings.runtime / project_id / "analysis" / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{run_key}-", dir=runs_dir))
        try:
            raw_dir = staging / "raw"
            raw_dir.mkdir()
            write_json(raw_dir / "source-responses.json", {"responses": raw_records})
            write_json(staging / "normalized.json", normalized)
            manifest = {
                "schema_version": "semantic-run-manifest.v1",
                "run_key": run_key,
                "run_id": run_id,
                "project_id": project_id,
                "content_key": content_key,
                "provider": normalized["provider"],
                "review_status": "not_applicable",
                "safe_for_edit_plan": False,
                "summary": normalized["summary"],
                "warnings": normalized["warnings"],
                "telemetry": telemetry,
                "imported_at": normalized["generated_at"],
                "detail_url": f"/api/projects/{project_id}/analysis/runs/{run_key}",
            }
            write_json(staging / "manifest.json", manifest)
            os.replace(staging, runs_dir / run_key)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return self.semantic_run(project_id, run_key)

    def analyze_speech(
        self, project_id: str, model_size: str | None = None,
        force: bool = False,
    ) -> dict:
        """Run local timestamped ASR over every asset with audio and persist
        the transcript as a semantic evidence run."""
        from .speech import PROMPT_VERSION as SPEECH_PROMPT_VERSION
        from .speech import SpeechAnalysisError, analyze_speech

        project = self.get_project(project_id)
        assets = project.get("inventory", {}).get("assets", [])
        if not assets:
            raise ProjectError("The project has no indexed media to analyze")
        media_root = self.settings.root

        content_key = self._analysis_content_key(
            assets, adapter="local-asr", model=model_size or "auto",
            prompt_version=SPEECH_PROMPT_VERSION,
        )
        if not force:
            cached = self._existing_run_for(project_id, content_key)
            if cached is not None:
                return cached
        run_id = uuid.uuid4().hex[:12]
        run_key = f"asr-live-{run_id}"
        try:
            speech_progress = _progress_setter(project_id, "speech")
            try:
                normalized, raw_records = analyze_speech(
                    assets, media_root, project_id, run_id, model_size,
                    progress=speech_progress,
                )
            finally:
                speech_progress.clear()
            validate_semantic_evidence(
                normalized,
                SCHEMA_DIR / "semantic-evidence.schema.json",
            )
        except (SpeechAnalysisError, SemanticEvidenceError) as exc:
            raise ProjectError(f"Speech analysis failed: {exc}") from exc

        reviews = auto_review_decisions(normalized)
        reviews["run_key"] = run_key
        runs_dir = self.settings.runtime / project_id / "analysis" / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{run_key}-", dir=runs_dir))
        try:
            raw_dir = staging / "raw"
            raw_dir.mkdir()
            write_json(raw_dir / "transcripts.json", {"transcripts": raw_records})
            write_json(staging / "normalized.json", normalized)
            write_json(staging / "reviews.json", reviews)
            manifest = {
                "schema_version": "semantic-run-manifest.v1",
                "run_key": run_key,
                "run_id": run_id,
                "project_id": project_id,
                "content_key": content_key,
                "provider": normalized["provider"],
                "review_status": normalized["review_status"],
                "safe_for_edit_plan": normalized["safe_for_edit_plan"],
                "summary": normalized["summary"],
                "warnings": normalized["warnings"],
                "imported_at": normalized["generated_at"],
                "detail_url": f"/api/projects/{project_id}/analysis/runs/{run_key}",
            }
            write_json(staging / "manifest.json", manifest)
            os.replace(staging, runs_dir / run_key)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        self._corroborate_speech_claims(project_id)
        self._mark_semantic_progress(project_id, "speech")
        return self.semantic_run(project_id, run_key)

    def _corroborate_speech_claims(self, project_id: str) -> int:
        """Auto-approve visual observations held back ONLY for mentioning
        speech when the ASR transcript independently confirms speech in the
        same range. Two agreeing senses need no human referee; the decision
        is recorded in the audit trail like any other."""
        words = self._speech_words(project_id)
        if not words:
            return 0
        corroborated = 0
        for manifest in self._current_run_manifests(project_id):
            if manifest["provider"]["adapter"] != "owned-live-visual":
                continue
            # The same lock human reviews take: this read-modify-write of
            # reviews.json runs from a background job thread.
            with self._semantic_review_lock:
                corroborated += self._corroborate_run(project_id, manifest, words)
        return corroborated

    def _corroborate_run(
        self, project_id: str, manifest: dict, words: dict[str, list[dict]]
    ) -> int:
        """One visual run's corroboration pass, under _semantic_review_lock."""
        from .visual import AUTO_APPROVE_MIN_CONFIDENCE

        corroborated = 0
        run_dir = (
            self.settings.runtime / project_id / "analysis" / "runs"
            / manifest["run_key"]
        )
        normalized = load_json(run_dir / "normalized.json")
        reviews_path = run_dir / "reviews.json"
        reviews = (
            load_json(reviews_path)
            if reviews_path.is_file()
            else {
                "schema_version": "semantic-reviews.v1",
                "project_id": project_id,
                "run_key": manifest["run_key"],
                "updated_at": utc_now(),
                "decisions": {},
                "events": [],
            }
        )
        changed = False
        for observation in normalized["observations"]:
            if observation["normalization_status"] != "accepted":
                continue
            if observation["evidence_id"] in reviews["decisions"]:
                continue
            if observation["risk_flags"] != ["unverified_speech_claim"]:
                continue
            confidence = observation.get("model_confidence") or 0.0
            if confidence < AUTO_APPROVE_MIN_CONFIDENCE:
                continue
            # ASR overlap establishes that speech HAPPENS — never what was
            # said. A caption reporting speech CONTENT ("explica que
            # renunció…") must stay with a human even if words overlap.
            caption = (observation.get("caption") or "").lower()
            if re.search(
                r"(dice(?:n)? que|explica(?:n)? que|comenta(?:n)? que|"
                r"cuenta(?:n)? que|pregunta(?:n)? (?:si|por)|responde(?:n)? que|"
                r"afirma(?:n)? que|anuncia(?:n)?|menciona(?:n)?|"
                r"habla(?:n)? (?:sobre|de)|contando|relata(?:n)?|"
                r"says? that|explains? that|tells? .* that|mentions?|"
                r"announc(?:es?|ing)|talks? about|"
                "[\"\u00ab\u00bb\u201c\u201d])",
                caption,
            ):
                continue
            asset_words = words.get(observation["asset_id"]) or []
            overlapping = sum(
                1 for word in asset_words
                if word["start_seconds"] < observation["end_seconds"]
                and word["end_seconds"] > observation["start_seconds"]
            )
            # one stray recognized word is noise, not corroboration
            if overlapping < 3:
                continue
            event = {
                "event_id": uuid.uuid4().hex[:12],
                "evidence_id": observation["evidence_id"],
                "action": "approve",
                "caption": observation["caption"],
                "note": (
                    "auto-approved (policy corroborate-v1): the speech "
                    "mention is confirmed by the transcript in this range"
                ),
                "reviewed_at": utc_now(),
            }
            reviews["decisions"][observation["evidence_id"]] = event
            reviews["events"].append(event)
            changed = True
            corroborated += 1
        if changed:
            reviews["updated_at"] = utc_now()
            write_json(reviews_path, reviews)
        return corroborated

    def _mark_semantic_progress(self, project_id: str, kind: str) -> None:
        path = self.settings.runtime / project_id / "project.json"
        pending_risky = 0
        for manifest in self._semantic_run_manifests(project_id):
            if manifest["provider"]["adapter"] not in EVIDENCE_ADAPTERS:
                continue
            run = self.semantic_run(project_id, manifest["run_key"])
            pending_risky += run["summary"].get("pending_review_count", 0)
        # Visual and speech analysis complete on different job threads and
        # both flip a flag in this file.
        with self._project_write(project_id):
            stored = load_json(path)
            stored["updated_at"] = utc_now()
            stored["analysis"][kind] = "completed"
            stored["status"] = (
                "semantic_ready" if pending_risky == 0 else "semantic_review_recommended"
            )
            stored["analysis"]["warning"] = (
                "Routine evidence was auto-approved by policy; "
                f"{pending_risky} risk-flagged claim(s) await optional review."
                if pending_risky
                else "Evidence is ready for concept generation."
            )
            write_json(path, stored)

    def _approved_ranges(self, project_id: str) -> dict[str, list[tuple[float, float]]]:
        """Confirmed observation ranges per asset — the grounding gate that
        compilation and revision both apply to proposed cuts."""
        ranges: dict[str, list[tuple[float, float]]] = {}
        for item in self.approved_evidence(project_id):
            ranges.setdefault(item["asset_id"], []).append(
                (item["start_seconds"], item["end_seconds"])
            )
        return ranges

    def _verify_plan_lineage(self, project_id: str, plan: dict) -> None:
        """For contract plans: every lineage-bearing event's ids must be
        CURRENTLY approved — an approval revoked after compilation
        invalidates the scene at the next exit (render/restore)."""
        if not plan.get("lineage_contract"):
            return
        sets = self._evidence_review_sets(project_id)
        approved = sets["approved"]
        rejected = sets["rejected"]
        envelopes = sets.get("envelopes") or {}
        from .planning import envelopes_cover

        for track in plan.get("tracks", []):
            if track.get("kind") != "video":
                continue
            for event in track.get("events", []):
                qualifying: list[tuple] = []
                for eid in event.get("evidence_ids") or []:
                    if eid in rejected:
                        raise ProjectError(
                            f"La escena {event.get('event_id')} usa la "
                            f"afirmación {eid}, que fue RECHAZADA después "
                            "de compilar — quita esa escena o revisa la "
                            "decisión en Diagnóstico"
                        )
                    if eid not in approved:
                        raise ProjectError(
                            f"La escena {event.get('event_id')} depende de "
                            f"la afirmación {eid}, aún sin confirmar — "
                            "confírmala en «Afirmaciones sin verificar» y "
                            "vuelve a intentarlo"
                        )
                    envelope = envelopes.get(eid)
                    if envelope is not None:
                        qualifying.append(envelope)
                if not envelopes_cover(
                    qualifying,
                    event.get("asset_id") or "",
                    event["source_start_seconds"],
                    event["source_end_seconds"],
                ):
                    # the invariant: a contract event must be supported by
                    # at least one currently approved id whose OWN
                    # envelope covers it — an id-less or mis-attributed
                    # event never passes vacuously
                    raise ProjectError(
                        f"La escena {event.get('event_id')} no tiene "
                        "ninguna afirmación aprobada que cubra su rango — "
                        "regenera las ideas o quita esa escena"
                    )

    def _evidence_review_sets(self, project_id: str) -> dict:
        """The trust primitive: per-evidence-id review state. Approval
        rides on identity, not on time overlap or text similarity."""
        approved: dict[str, str] = {}
        pending: set[str] = set()
        rejected: set[str] = set()
        envelopes: dict[str, tuple] = {}
        for manifest in self._current_run_manifests(project_id):
            run = self.semantic_run(project_id, manifest["run_key"])
            for observation in run["observations"]:
                if observation["normalization_status"] != "accepted":
                    continue
                status = observation.get("review_status")
                eid = observation["evidence_id"]
                if status == "reviewed":
                    approved[eid] = (
                        observation.get("reviewed_caption")
                        or observation["caption"]
                    )
                elif status == "rejected":
                    rejected.add(eid)
                else:
                    pending.add(eid)
                envelopes[eid] = (
                    observation["asset_id"],
                    observation["start_seconds"],
                    observation["end_seconds"],
                )
        return {"approved": approved, "pending": pending,
                "rejected": rejected, "envelopes": envelopes}

    def _approved_captions(
        self, project_id: str
    ) -> dict[str, list[tuple[float, float, str]]]:
        """Approved observation CAPTIONS per asset — the semantic half of
        the grounding gate. Time overlap alone must never authorize a
        claim: the design-review blocker showed a pending 'ganó la
        carrera' compiling into a title because approved speech merely
        overlapped its seconds."""
        captions: dict[str, list[tuple[float, float, str]]] = {}
        for item in self.approved_evidence(project_id):
            captions.setdefault(item["asset_id"], []).append(
                (item["start_seconds"], item["end_seconds"],
                 str(item.get("caption") or ""))
            )
        return captions

    def _latest_asr_transcripts(self, project_id: str) -> dict | None:
        """Transcripts of the newest ASR run by import time. Run directories
        carry random ids, so recency comes from the manifest, never the name."""
        runs_dir = self.settings.runtime / project_id / "analysis" / "runs"
        newest: tuple[str, Path] | None = None
        for manifest in self._semantic_run_manifests(project_id):
            run_key = manifest.get("run_key", "")
            if not run_key.startswith("asr-live-"):
                continue
            transcripts = runs_dir / run_key / "raw" / "transcripts.json"
            if not transcripts.is_file():
                continue
            imported_at = manifest.get("imported_at", "")
            if newest is None or imported_at > newest[0]:
                newest = (imported_at, transcripts)
        return load_json(newest[1]) if newest else None

    def _current_run_manifests(self, project_id: str) -> list[dict]:
        """Newest run per provider adapter — re-analysis supersedes older
        runs so evidence never accumulates stale duplicates."""
        latest: dict[str, dict] = {}
        for manifest in self._semantic_run_manifests(project_id):
            adapter = manifest["provider"]["adapter"]
            if adapter not in EVIDENCE_ADAPTERS:
                continue
            if (
                adapter not in latest
                or manifest["imported_at"] > latest[adapter]["imported_at"]
            ):
                latest[adapter] = manifest
        return list(latest.values())

    def _fine_observations(self, project_id: str) -> list[dict]:
        observations = []
        for manifest in self._current_run_manifests(project_id):
            run = self.semantic_run(project_id, manifest["run_key"])
            observations.extend(run.get("observations") or [])
        return observations

    def _latest_source_context(self, project_id: str) -> dict | None:
        newest = None
        for manifest in self._semantic_run_manifests(project_id):
            if manifest.get("provider", {}).get("adapter") != "owned-source-context":
                continue
            if newest is None or manifest.get("imported_at", "") > newest.get(
                "imported_at", ""
            ):
                newest = manifest
        return self.semantic_run(project_id, newest["run_key"]) if newest else None

    def approved_evidence(self, project_id: str) -> list[dict]:
        """Approved observations from the current evidence runs, with
        reviewed wording taking precedence over the provider caption."""
        items: list[dict] = []
        for manifest in self._current_run_manifests(project_id):
            run = self.semantic_run(project_id, manifest["run_key"])
            for observation in run["observations"]:
                if observation["normalization_status"] != "accepted":
                    continue
                if observation.get("review_status") != "reviewed":
                    continue
                items.append(
                    {
                        "evidence_id": observation["evidence_id"],
                        "asset_id": observation["asset_id"],
                        "start_seconds": observation["start_seconds"],
                        "end_seconds": observation["end_seconds"],
                        "caption": observation.get("reviewed_caption")
                        or observation["caption"],
                        "evidence_type": observation.get("evidence_type") or "visual",
                        "confidence": observation.get("model_confidence") or 0.8,
                        "verified": True,
                    }
                )
        return items

    def pending_evidence(self, project_id: str) -> list[dict]:
        """Flagged-but-unreviewed observations. The planner may cite them,
        but the compiler will not use a range supported only by unverified
        claims unless the user confirms it."""
        items: list[dict] = []
        for manifest in self._current_run_manifests(project_id):
            run = self.semantic_run(project_id, manifest["run_key"])
            for observation in run["observations"]:
                if observation["normalization_status"] != "accepted":
                    continue
                if observation.get("review_status") != "pending":
                    continue
                items.append(
                    {
                        "evidence_id": observation["evidence_id"],
                        "asset_id": observation["asset_id"],
                        "start_seconds": observation["start_seconds"],
                        "end_seconds": observation["end_seconds"],
                        "caption": observation["caption"],
                        "evidence_type": observation.get("evidence_type") or "visual",
                        "confidence": observation.get("model_confidence") or 0.5,
                        "verified": False,
                    }
                )
        return items

    def generate_concepts(
        self,
        project_id: str,
        provider: str = "qwen",
        model: str | None = None,
        guidance: str | None = None,
        keep_concept_ids: list[str] | None = None,
        use_source_context: bool = False,
        style_id: str | None = None,
    ) -> dict:
        """Generate grounded creative concepts with missing-shot advice from
        the project's approved evidence. Kept concepts survive regeneration;
        guidance steers the new ones. A style_id conditions HOW stories are
        told (pacing, shape, tone) — grounding still owns the content."""
        project = self.get_project(project_id)
        if style_id:
            from .style_intelligence import style_guidance, style_targets

            template = next(
                (t for t in self.list_styles() if t["style_id"] == style_id),
                None,
            )
            if template is None:
                raise ProjectError(f"Estilo desconocido: {style_id}")
            block = style_guidance(template)
            guidance = f"{guidance.strip()}\n\n{block}" if guidance else block
            # style-application.v1 (v2 handoff §9.6, light form): record on
            # the concepts document WHICH style shaped this generation, at
            # what trust, with what guidance — so a styled run is
            # distinguishable from a baseline months later
            style_application = {
                "schema_version": "style-application.v1",
                "style_id": template["style_id"],
                "template_confidence": template.get("confidence"),
                "analyzers": template.get("analyzers") or [],
                "guidance_block": block,
                # the resolved contract: measured targets the COMPILER
                # binds to, owners per property, and what is unsupported
                "application": style_targets(template),
            }
        evidence = self.approved_evidence(project_id) + self.pending_evidence(project_id)
        keep_concepts: list[dict] = []
        if keep_concept_ids:
            concepts_path = (
                self.settings.runtime / project_id / "analysis" / "concepts.json"
            )
            if concepts_path.is_file():
                existing = load_json(concepts_path).get("concepts", [])
                keep_concepts = [
                    item for item in existing if item["concept_id"] in keep_concept_ids
                ]
        try:
            client = ChatClient(
                resolve_provider(
                    provider, model or PLANNER_DEFAULT_MODELS.get(provider)
                ),
                # big evidence packs put deepseek generations near the old
                # 360s edge; on slow provider days every attempt clipped
                timeout_seconds=600.0,
            )
            document = generate_concepts(
                client,
                project,
                evidence,
                guidance=guidance,
                keep_concepts=keep_concepts,
                footage_language=self._footage_language(project_id),
                source_context=(
                    self._latest_source_context(project_id)
                    if use_source_context
                    else None
                ),
            )
        except (ProviderError, PlanningError) as exc:
            raise ProjectError(f"Concept generation failed: {exc}") from exc

        usage = document.pop("generation_usage", None)
        if usage:
            self._log_cost(project_id, "concepts", usage)
        kept_ids = {c["concept_id"] for c in keep_concepts}
        for concept in document.get("concepts") or []:
            if concept["concept_id"] in kept_ids:
                continue  # kept concepts keep their original provenance
            concept["style_provenance"] = style_id or None
        if style_id:
            document["style_application"] = style_application
        # validate the document AS PERSISTED — validating before attaching
        # provenance/application protects nothing this path writes
        self._validate_schema(
            document,
            SCHEMA_DIR / "creative-concepts.schema.json",
            "Creative concepts",
        )
        write_json(
            self.settings.runtime / project_id / "analysis" / "concepts.json", document
        )
        path = self.settings.runtime / project_id / "project.json"
        with self._project_write(project_id):
            stored = load_json(path)
            stored["updated_at"] = utc_now()
            stored["footage_summary"] = document["footage_summary"]
            stored["concepts"] = document["concepts"]
            stored["status"] = "concepts_ready"
            write_json(path, stored)
        return document

    def compile_plan(
        self,
        project_id: str,
        concept_id: str | None = None,
        width: int = 1080,
        height: int = 1920,
        fps: int = 30,
        style_id: str | None = None,
        style_mode: str = "inherited",
        allow_conditioned: bool = False,
    ) -> dict:
        """Deterministically compile the selected concept into a validated
        edit-plan.v1 and persist it with a matching media inventory."""
        project = self.get_project(project_id)
        concepts_path = self.settings.runtime / project_id / "analysis" / "concepts.json"
        if not concepts_path.is_file():
            raise ProjectError("Generate concepts before compiling an edit plan")
        document = load_json(concepts_path)
        selection = self._selection(project_id)
        selected = concept_id or (selection or {}).get("concept_id")
        if not selected:
            raise ProjectError("Select a concept before compiling the edit plan")
        approved_ranges = self._approved_ranges(project_id)
        # Style modes are explicit, not implicit: "none" is a guaranteed
        # baseline (inheriting nothing), an explicit style_id compiles a
        # FIXED concept with that style's contract — the unconfounded arm
        # of the application-validity A/B — and the default inherits the
        # contract embedded by a styled concept generation, if any.
        selected_concept = next(
            (c for c in concepts_doc_concepts(document)
             if c.get("concept_id") == selected), {}
        )
        doc_style = (document.get("style_application") or {}).get("style_id")
        # lineage comes from the SELECTED concept, not the document: kept
        # and regenerated concepts in one document can differ. A legacy
        # concept (no provenance field) in a styled document is treated as
        # conditioned — fail closed, never assume it is a clean baseline.
        if "style_provenance" in selected_concept:
            concept_style = selected_concept["style_provenance"]
        else:
            concept_style = doc_style  # legacy: inherit the doc's claim

        def resolve_template(wanted: str) -> dict:
            template = next(
                (t for t in self.list_styles() if t["style_id"] == wanted),
                None,
            )
            if template is None:
                raise ProjectError(
                    f"Estilo desconocido o borrado: {wanted} — regenera las "
                    "ideas o elige otro estilo"
                )
            return template

        if style_mode == "none":
            compile_application = None
        elif style_id:
            from .style_intelligence import style_targets as resolve_targets

            template = resolve_template(style_id)
            # the A/B arm is only unconfounded over an UNCONDITIONED story
            if concept_style and not allow_conditioned:
                raise ProjectError(
                    "Esta historia se generó con el estilo "
                    f"{concept_style} — compilarla con un estilo explícito "
                    "confunde el experimento. Regenera ideas sin estilo, o "
                    "pasa allow_conditioned=true a sabiendas."
                )
            compile_application = resolve_targets(template)
        elif concept_style:
            from .style_intelligence import style_targets as resolve_targets

            # inherited: THIS concept's style — which may not be the
            # document's newest one
            compile_application = resolve_targets(resolve_template(concept_style))
        else:
            compile_application = None
        try:
            plan = compile_edit_plan(
                project,
                document,
                selected,
                width,
                height,
                fps,
                speech_words=self._speech_words(project_id),
                approved_ranges=approved_ranges,
                style_application=compile_application,
                approved_captions=self._approved_captions(project_id),
                review_sets=self._evidence_review_sets(project_id),
            )
            validate_edit_plan(
                plan,
                SCHEMA_DIR / "edit-plan.schema.json",
                project,
            )
        except PlanningError as exc:
            raise ProjectError(f"Plan compilation failed: {exc}") from exc

        plan_dir = self.settings.runtime / project_id / "plan"
        plan_path = plan_dir / "edit-plan.json"
        if plan_path.is_file():
            # Choosing a new story must not rewind history: archive the
            # current cut and continue the monotonic revision counter
            # (cross-review UX finding 4).
            previous = load_json(plan_path)
            previous_revision = int(previous.get("revision", 1))
            plan["revision"] = previous_revision + 1
            write_json(
                plan_dir / "revisions" / f"edit-plan.rev{previous_revision:03d}.json",
                previous,
            )
            log_path = plan_dir / "revisions" / "revision-log.json"
            log = load_json(log_path) if log_path.is_file() else {"entries": []}
            log["entries"].append(
                {
                    "revision": plan["revision"],
                    "instruction": f"new story: {selected}",
                    "note": "Nueva historia elegida",
                    "revised_at": utc_now(),
                    "provider": "story-change",
                }
            )
            write_json(log_path, log)
        write_json(plan_path, plan)
        write_json(
            plan_dir / "media-inventory.json",
            {"assets": project["inventory"]["assets"]},
        )
        path = self.settings.runtime / project_id / "project.json"
        with self._project_write(project_id):
            stored = load_json(path)
            stored["updated_at"] = utc_now()
            stored["plan"] = plan
            stored["selected_concept_id"] = selected
            stored["status"] = "plan_ready"
            write_json(path, stored)
        write_json(
            self.settings.runtime / project_id / "selection.json",
            {
                "project_id": project_id,
                "concept_id": selected,
                "selected_at": utc_now(),
                "plan_available": True,
            },
        )
        return plan

    def revise_plan(
        self,
        project_id: str,
        instruction: str,
        provider: str = "qwen",
        model: str | None = None,
    ) -> dict:
        """Revise the compiled plan from a natural-language instruction
        without re-running media analysis. Prior revisions are retained."""
        project = self.get_project(project_id)
        plan_dir = self.settings.runtime / project_id / "plan"
        plan_path = plan_dir / "edit-plan.json"
        if not plan_path.is_file():
            raise ProjectError("Compile an edit plan before revising it")
        plan = load_json(plan_path)
        evidence = self.approved_evidence(project_id)
        try:
            client = ChatClient(
                resolve_provider(
                    provider, model or PLANNER_DEFAULT_MODELS.get(provider)
                ),
                # big evidence packs put deepseek generations near the old
                # 360s edge; on slow provider days every attempt clipped
                timeout_seconds=600.0,
            )
            new_plan, note = revise_plan(
                client,
                project,
                plan,
                evidence,
                instruction,
                speech_words=self._speech_words(project_id),
                footage_language=self._footage_language(project_id),
                approved_ranges=self._approved_ranges(project_id),
                approved_captions=self._approved_captions(project_id),
                review_sets=self._evidence_review_sets(project_id),
            )
            validate_edit_plan(
                new_plan,
                SCHEMA_DIR / "edit-plan.schema.json",
                project,
            )
        except (ProviderError, PlanningError) as exc:
            raise ProjectError(f"Plan revision failed: {exc}") from exc

        # The whole install is one transaction under the project lock, with
        # a revision check against what was loaded — a concurrent
        # command/sync apply can no longer be silently overwritten
        # (cross-review 21).
        with self._project_write(project_id):
            current = load_json(plan_path)
            if int(current.get("revision", 1)) != int(plan.get("revision", 1)):
                raise ProjectError(
                    "The plan changed while this revision was being "
                    "generated — review the new state and revise again"
                )
            previous_revision = int(plan.get("revision", 1))
            write_json(
                plan_dir / "revisions" / f"edit-plan.rev{previous_revision:03d}.json",
                plan,
            )
            write_json(plan_path, new_plan)
            log_path = plan_dir / "revisions" / "revision-log.json"
            log = load_json(log_path) if log_path.is_file() else {"entries": []}
            log["entries"].append(
                {
                    "revision": new_plan["revision"],
                    "instruction": instruction.strip(),
                    "note": note,
                    "revised_at": new_plan["generated_at"],
                    "provider": provider,
                }
            )
            write_json(log_path, log)
            path = self.settings.runtime / project_id / "project.json"
            stored = load_json(path)
            stored["updated_at"] = utc_now()
            stored["plan"] = new_plan
            stored["status"] = "plan_ready"
            write_json(path, stored)
        return {"plan": new_plan, "revision_note": note}

    def opentake_place(self, project_id: str) -> dict:
        """Place the compiled plan into the open OpenTake project and persist
        the bridge for later sync. Destructive to that timeline by contract."""
        from .opentake_bridge import BridgeError, place_plan

        project = self.get_project(project_id)
        plan_dir = self.settings.runtime / project_id / "plan"
        plan_path = plan_dir / "edit-plan.json"
        if not plan_path.is_file():
            raise ProjectError("Compile a plan before sending it to OpenTake")
        plan = load_json(plan_path)
        inventory_path = plan_dir / "media-inventory.json"
        inventory = (
            load_json(inventory_path)
            if inventory_path.is_file()
            else project["inventory"]
        )
        if not self._opentake_place_lock.acquire(blocking=False):
            raise ProjectError("A placement is already running — wait for it")
        try:
            _pp, _ip, media_root = self._plan_sources(project_id)
            summary, bridge = place_plan(
                plan, inventory, project_id, media_root=str(media_root)
            )
        except BridgeError as exc:
            raise ProjectError(str(exc)) from exc
        finally:
            self._opentake_place_lock.release()
        write_json(plan_dir.parent / "opentake-bridge.json", bridge)
        return summary

    def opentake_cleanup_candidates(
        self, project_id: str, readback: dict | None = None
    ) -> dict:
        """Conservative Spanish filler/dead-air candidates for the live
        OpenTake timeline, revision-bound for a safe later apply."""
        from .cleanup import (
            CleanupError, candidates_for, clip_layout, timeline_fingerprint,
            transcript_words,
        )

        project = self.get_project(project_id)
        bridge_path = self.settings.runtime / project_id / "opentake-bridge.json"
        if not bridge_path.is_file():
            raise ProjectError("Place the plan into OpenTake first")
        bridge = load_json(bridge_path)
        if readback is None:
            from .opentake_mcp import OpenTakeMcp, OpenTakeMcpError

            try:
                readback = OpenTakeMcp().get_timeline()
            except OpenTakeMcpError as exc:
                raise ProjectError(str(exc)) from exc
        try:
            words = transcript_words(
                self.settings.runtime / project_id / "analysis" / "runs"
            )
            clips = clip_layout(readback, bridge, project["inventory"])
            found = candidates_for(words, clips, bridge["fps"])
        except CleanupError as exc:
            raise ProjectError(str(exc)) from exc
        fingerprint = timeline_fingerprint(readback)
        write_json(
            self.settings.runtime / project_id / "opentake-cleanup.json",
            {"fingerprint": fingerprint, "candidates": found},
        )
        total = sum(b - a for a, b in (c["frames"] for c in found))
        return {
            "candidates": found,
            "total_frames": total,
            "total_seconds": round(total / bridge["fps"], 2),
            "fingerprint": fingerprint,
        }

    def opentake_cleanup_apply(
        self, project_id: str, indices: list[int], readback: dict | None = None
    ) -> dict:
        """Apply the approved candidate subset in one atomic ripple.

        The fingerprint is ALWAYS checked against a live readback fetched on
        the same MCP session that performs the ripple — a caller-supplied
        readback is never trusted for a mutation (cross-review BLOCKER 1:
        a stale caller document could otherwise delete the wrong frames).
        The shared OpenTake mutation lock serializes concurrent applies."""
        from .cleanup import timeline_fingerprint
        from .opentake_mcp import OpenTakeMcp, OpenTakeMcpError

        stored_path = self.settings.runtime / project_id / "opentake-cleanup.json"
        if not stored_path.is_file():
            raise ProjectError("List cleanup candidates before applying")
        stored = load_json(stored_path)
        try:
            chosen = [stored["candidates"][i] for i in indices]
        except (IndexError, TypeError) as exc:
            raise ProjectError("Unknown candidate selection") from exc
        if not chosen:
            raise ProjectError("Nothing selected")
        if not self._opentake_place_lock.acquire(blocking=False):
            raise ProjectError(
                "Another OpenTake operation is in progress — retry in a moment"
            )
        try:
            client = OpenTakeMcp()
            live = client.get_timeline()
            if timeline_fingerprint(live) != stored["fingerprint"]:
                stored_path.unlink(missing_ok=True)
                raise ProjectError(
                    "The OpenTake timeline changed since the candidates were "
                    "computed — list them again"
                )
            result = client.tool("ripple_delete_ranges", {
                "trackIndex": 0,
                "units": "frames",
                "ranges": [c["frames"] for c in chosen],
            })
        except OpenTakeMcpError as exc:
            raise ProjectError(str(exc)) from exc
        finally:
            self._opentake_place_lock.release()
        stored_path.unlink(missing_ok=True)
        removed = sum(b - a for a, b in (c["frames"] for c in chosen))
        return {
            "applied": len(chosen),
            "removed_frames": removed,
            "detail": result.get("text", ""),
        }

    def opentake_sync_preview(
        self, project_id: str, readback: dict | None = None
    ) -> dict:
        """Translate the OpenTake timeline back into a candidate plan.

        Pure translation + validation; nothing is installed until
        opentake_sync_apply. The candidate and diff are persisted so apply
        can verify nothing moved in between."""
        from .opentake_sync import SyncError, timeline_to_candidate_plan

        project = self.get_project(project_id)
        plan_dir = self.settings.runtime / project_id / "plan"
        plan_path = plan_dir / "edit-plan.json"
        bridge_path = plan_dir.parent / "opentake-bridge.json"
        if not plan_path.is_file():
            raise ProjectError("Compile a plan before syncing from OpenTake")
        if not bridge_path.is_file():
            raise ProjectError(
                "No OpenTake bridge found — place the plan into OpenTake first"
            )
        plan = load_json(plan_path)
        bridge = load_json(bridge_path)
        if readback is None:
            from .opentake_mcp import OpenTakeMcp, OpenTakeMcpError

            try:
                readback = OpenTakeMcp().get_timeline()
            except OpenTakeMcpError as exc:
                raise ProjectError(str(exc)) from exc
        from .opentake_bridge import saved_bundle_state, staleness_warning

        warning = staleness_warning(readback, saved_bundle_state())
        try:
            candidate, diff = timeline_to_candidate_plan(plan, bridge, readback)
        except SyncError as exc:
            raise ProjectError(f"Sync rejected: {exc}") from exc
        # GUI-added B-roll of a known sideways asset must not lose its
        # detected rotation (cross-review 16): the sync module has no
        # inventory access, so enrichment happens here.
        rotations = {
            a["asset_id"]: int(a.get("suggested_rotation_degrees") or 0)
            for a in project.get("inventory", {}).get("assets", [])
        }
        for track in candidate.get("tracks", []):
            if track.get("role") != "broll":
                continue
            for event in track["events"]:
                degrees = rotations.get(event.get("asset_id"), 0)
                if degrees and event.get("reframe") is None:
                    event["reframe"] = {
                        "mode": "fit", "center_x": 0.5, "center_y": 0.5,
                        "scale": 1.0, "rotation_degrees": degrees,
                        "manual_review": True,
                    }
        try:
            validate_edit_plan(
                candidate, SCHEMA_DIR / "edit-plan.schema.json", project
            )
        except PlanningError as exc:
            raise ProjectError(f"Synced plan failed validation: {exc}") from exc
        from .cleanup import timeline_fingerprint

        write_json(
            plan_dir / "opentake-candidate.json",
            {
                "base_revision": int(plan.get("revision", 1)),
                "timeline_fingerprint": timeline_fingerprint(readback),
                "stale": warning is not None,
                "candidate": candidate,
                "diff": diff,
            },
        )
        changed = [d for d in diff if d.get("kind") != "unchanged"]
        return {
            "base_revision": int(plan.get("revision", 1)),
            "candidate_revision": candidate.get("revision"),
            "changes": changed,
            "unchanged_count": len(diff) - len(changed),
            "duration_seconds": candidate["project"]["duration_seconds"],
            "staleness": warning,
        }

    def opentake_sync_apply(self, project_id: str) -> dict:
        """Install the previewed candidate as the current plan revision.

        The candidate is bound to the exact timeline it was previewed from:
        apply re-reads the live timeline and refuses on ANY drift, and a
        preview that carried a staleness warning cannot be applied at all
        (cross-review UX blocker 2)."""
        from .cleanup import timeline_fingerprint
        from .opentake_mcp import OpenTakeMcp, OpenTakeMcpError

        plan_dir = self.settings.runtime / project_id / "plan"
        plan_path = plan_dir / "edit-plan.json"
        candidate_path = plan_dir / "opentake-candidate.json"
        if not candidate_path.is_file():
            raise ProjectError("Run a sync preview before applying")
        stored = load_json(candidate_path)
        if stored.get("stale"):
            candidate_path.unlink(missing_ok=True)
            raise ProjectError(
                "La vista previa tenía una advertencia de desincronización — "
                "guarda el proyecto en OpenTake (o reinícialo) y vuelve a "
                "previsualizar"
            )
        if stored.get("timeline_fingerprint"):
            try:
                live = OpenTakeMcp().get_timeline()
            except OpenTakeMcpError as exc:
                raise ProjectError(
                    f"No se pudo verificar la línea de tiempo: {exc}"
                ) from exc
            if timeline_fingerprint(live) != stored["timeline_fingerprint"]:
                candidate_path.unlink(missing_ok=True)
                raise ProjectError(
                    "La línea de tiempo de OpenTake cambió después de la "
                    "vista previa — vuelve a previsualizar"
                )
        with self._project_write(project_id):
            plan = load_json(plan_path)
            if int(plan.get("revision", 1)) != stored["base_revision"]:
                candidate_path.unlink(missing_ok=True)
                raise ProjectError(
                    "The plan changed since the preview — sync again"
                )
            new_plan = stored["candidate"]
            previous_revision = int(plan.get("revision", 1))
            write_json(
                plan_dir / "revisions" / f"edit-plan.rev{previous_revision:03d}.json",
                plan,
            )
            write_json(plan_path, new_plan)
            log_path = plan_dir / "revisions" / "revision-log.json"
            log = load_json(log_path) if log_path.is_file() else {"entries": []}
            log["entries"].append(
                {
                    "revision": new_plan["revision"],
                    "instruction": "opentake-sync",
                    "note": f"{len([d for d in stored['diff'] if d.get('kind') != 'unchanged'])} timeline change(s) pulled from OpenTake",
                    "revised_at": new_plan.get("generated_at"),
                    "provider": "opentake",
                }
            )
            write_json(log_path, log)
            path = self.settings.runtime / project_id / "project.json"
            project_state = load_json(path)
            project_state["updated_at"] = utc_now()
            project_state["plan"] = new_plan
            project_state["status"] = "plan_ready"
            write_json(path, project_state)
        candidate_path.unlink(missing_ok=True)
        return {"revision": new_plan["revision"], "status": "plan_ready"}

    def plan_command_propose(
        self,
        project_id: str,
        instruction: str,
        provider: str = "qwen",
        model: str | None = None,
    ) -> dict:
        """One NL instruction → one closed-set op, applied to a COPY and
        persisted for a revision-guarded apply. The LLM only picks the op;
        plan_ops computes and bounds-checks the mutation."""
        from .plan_ops import PlanOpError, apply_op, instruction_to_op

        project = self.get_project(project_id)
        plan = project.get("plan")
        if not plan:
            raise ProjectError("This project does not have an approved edit plan")
        client = ChatClient(resolve_provider(
            provider, model or PLANNER_DEFAULT_MODELS.get(provider)
        ))
        # Content hints let instructions name footage by WHAT IT SHOWS
        # ("la comida del comedor") instead of by asset id.
        hints: dict[str, str] = {}
        for item in self.approved_evidence(project_id):
            hints.setdefault(item["asset_id"], item.get("caption") or "")
        try:
            op = instruction_to_op(
                client, plan, instruction,
                project.get("inventory") or {}, hints,
            )
        except ProviderError as exc:
            raise ProjectError(f"The instruction model failed: {exc}") from exc
        except PlanOpError as exc:
            raise ProjectError(str(exc)) from exc
        if op.get("op") == "reject":
            return {
                "status": "rejected",
                "reason": str(op.get("reason") or "instrucción ambigua"),
            }
        try:
            candidate, summary = apply_op(
                plan, op, project.get("inventory") or {}
            )
            validate_edit_plan(
                candidate, SCHEMA_DIR / "edit-plan.schema.json", project
            )
        except (PlanOpError, PlanningError) as exc:
            raise ProjectError(str(exc)) from exc
        proposal_id = uuid.uuid4().hex[:12]
        write_json(
            self.settings.runtime / project_id / "plan-command.json",
            {
                "proposal_id": proposal_id,
                "base_revision": int(plan.get("revision", 1)),
                "instruction": instruction,
                "op": op,
                "summary": summary,
                "candidate": candidate,
            },
        )
        return {
            "status": "proposed",
            "proposal_id": proposal_id,
            "op": op,
            "summary": summary,
            "revision_preview": candidate["revision"],
        }

    def plan_restore_revision(self, project_id: str, revision: int) -> dict:
        """Install an archived revision as a NEW revision (roll forward,
        never rewind: the current plan is archived like any other change,
        so nothing in the history is ever lost)."""
        plan_dir = self.settings.runtime / project_id / "plan"
        plan_path = plan_dir / "edit-plan.json"
        archived_path = plan_dir / "revisions" / f"edit-plan.rev{revision:03d}.json"
        if not plan_path.is_file():
            raise ProjectError("This project does not have a compiled plan")
        if not archived_path.is_file():
            raise ProjectError(f"No archived revision {revision}")
        with self._project_write(project_id):
            plan = load_json(plan_path)
            current = int(plan.get("revision", 1))
            if current == revision:
                raise ProjectError("That is already the current cut")
            restored = load_json(archived_path)
            restored["revision"] = current + 1
            # rendered-language gate applies to RESTORED plans too — a
            # pre-gate revision must not smuggle a risky model title back
            # (review finding 4); user-typed titles stay exempt
            from .planning import title_blocked

            captions_map = self._approved_captions(project_id)
            plan_video_events = [
                event
                for track in restored.get("tracks", [])
                if track.get("kind") == "video"
                for event in track.get("events", [])
            ]
            supporting = " ".join(
                caption
                for event in plan_video_events
                for _s, _e, caption in captions_map.get(event["asset_id"], [])
                if _s < event["source_end_seconds"]
                and _e > event["source_start_seconds"]
            )
            for track in restored.get("tracks", []):
                if track.get("kind") != "title":
                    continue
                for event in track.get("events", []):
                    if title_blocked(
                        str(event.get("text") or ""), supporting,
                        user_authored=bool(event.get("user_authored")),
                    ):
                        raise ProjectError(
                            f"La revisión archivada tiene el título "
                            f"«{event.get('text')}», que afirma algo sin "
                            "respaldo aprobado — confirma la evidencia o "
                            "elige otra revisión"
                        )
            self._verify_plan_lineage(project_id, restored)
            try:
                validate_edit_plan(
                    restored, SCHEMA_DIR / "edit-plan.schema.json",
                    load_json(self.settings.runtime / project_id / "project.json"),
                )
            except PlanningError as exc:
                raise ProjectError(
                    f"El corte {revision} ya no es válido con el metraje "
                    f"actual: {exc}"
                ) from exc
            write_json(
                plan_dir / "revisions" / f"edit-plan.rev{current:03d}.json",
                plan,
            )
            write_json(plan_path, restored)
            log_path = plan_dir / "revisions" / "revision-log.json"
            log = load_json(log_path) if log_path.is_file() else {"entries": []}
            log["entries"].append(
                {
                    "revision": restored["revision"],
                    "instruction": f"restore revision {revision}",
                    "note": f"Corte {revision} restaurado",
                    "revised_at": utc_now(),
                    "provider": "restore",
                }
            )
            write_json(log_path, log)
            path = self.settings.runtime / project_id / "project.json"
            state = load_json(path)
            state["updated_at"] = utc_now()
            state["plan"] = restored
            state["status"] = "plan_ready"
            write_json(path, state)
        return {"revision": restored["revision"], "restored_from": revision}

    def plan_command_apply(
        self, project_id: str, proposal_id: str | None = None
    ) -> dict:
        """Install the last proposed instruction edit as a plan revision.
        A proposal token pins the apply to the exact proposal the caller
        reviewed — two browsers cannot install each other's edits
        (cross-review 21)."""
        plan_dir = self.settings.runtime / project_id / "plan"
        plan_path = plan_dir / "edit-plan.json"
        stored_path = self.settings.runtime / project_id / "plan-command.json"
        if not stored_path.is_file():
            raise ProjectError("No proposed edit — send an instruction first")
        stored = load_json(stored_path)
        if proposal_id is not None and stored.get("proposal_id") != proposal_id:
            raise ProjectError(
                "A different proposal replaced the one you reviewed — "
                "send the instruction again"
            )
        with self._project_write(project_id):
            plan = load_json(plan_path)
            if int(plan.get("revision", 1)) != stored["base_revision"]:
                stored_path.unlink(missing_ok=True)
                raise ProjectError(
                    "The plan changed since the proposal — send the "
                    "instruction again"
                )
            new_plan = stored["candidate"]
            previous_revision = int(plan.get("revision", 1))
            write_json(
                plan_dir / "revisions" / f"edit-plan.rev{previous_revision:03d}.json",
                plan,
            )
            write_json(plan_path, new_plan)
            log_path = plan_dir / "revisions" / "revision-log.json"
            log = load_json(log_path) if log_path.is_file() else {"entries": []}
            log["entries"].append(
                {
                    "revision": new_plan["revision"],
                    "instruction": stored["instruction"],
                    "note": stored["summary"],
                    "revised_at": utc_now(),
                    "provider": "plan-command",
                }
            )
            write_json(log_path, log)
            path = self.settings.runtime / project_id / "project.json"
            project_state = load_json(path)
            project_state["updated_at"] = utc_now()
            project_state["plan"] = new_plan
            project_state["status"] = "plan_ready"
            write_json(path, project_state)
        stored_path.unlink(missing_ok=True)
        return {
            "revision": new_plan["revision"],
            "summary": stored["summary"],
            "status": "plan_ready",
        }

    # ---------------- Reference Style Intelligence ---------------- #

    def _styles_dir(self) -> Path:
        directory = self.settings.runtime / "styles"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _references_dir(self) -> Path:
        directory = self.settings.root / "references"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def list_style_references(self) -> list[dict]:
        """Reference videos the user dropped into references/ (gitignored),
        with analyzed-state resolved from stored observation SOURCE LABELS
        — style names are truncated/renamed, so name matching lies."""
        supported = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
        analyzed_labels: set[str] = set()
        for style_path in self._styles_dir().glob("style-*.json"):
            try:
                for obs in load_json(style_path).get("observations") or []:
                    label = (obs.get("source") or {}).get("label")
                    if label:
                        analyzed_labels.add(label)
            except (OSError, ValueError):
                continue
        return [
            {
                "filename": path.name,
                "size_bytes": path.stat().st_size,
                "analyzed": path.name in analyzed_labels,
            }
            for path in sorted(self._references_dir().iterdir())
            if path.is_file() and path.suffix.lower() in supported
        ]

    def analyze_style_reference(self, filename: str, name: str | None = None) -> dict:
        """One reference video → observation → single-source template."""
        from .style_intelligence import (
            StyleError,
            aggregate_template,
            build_observation,
            deterministic_observation,
            resolve_reference_path,
            semantic_observation,
        )

        try:
            path = resolve_reference_path(self._references_dir(), filename)
        except StyleError as exc:
            raise ProjectError(str(exc)) from exc
        if not path.is_file():
            raise ProjectError(
                f"No existe references/{Path(filename).name} — deja ahí el "
                "video de referencia primero"
            )
        try:
            deterministic, source = deterministic_observation(path)
            client = make_client("gemini", None)
            semantic = semantic_observation(
                client, path, source["duration_seconds"]
            )
            observation = build_observation(deterministic, source, semantic)
            template = aggregate_template(
                name or Path(filename).stem, [observation]
            )
        except (StyleError, ProviderError) as exc:
            raise ProjectError(f"Análisis de referencia falló: {exc}") from exc
        # the schemas are enforcement points, not documentation
        self._validate_schema(
            observation, SCHEMA_DIR / "style-observation.schema.json",
            "style observation",
        )
        self._validate_schema(
            template, SCHEMA_DIR / "style-template.schema.json",
            "style template",
        )
        directory = self._styles_dir()
        write_json(
            directory / f"{template['style_id']}.json",
            {"template": template, "observations": [observation]},
        )
        return {"style_id": template["style_id"], "template": template}

    def list_styles(self, include_invalid: bool = False) -> list[dict]:
        """Valid style templates; with include_invalid, incompatible stored
        files appear as stubs so the UI can say WHY a style vanished instead
        of silently hiding it (schema tightening must not read as data loss).
        """
        styles = []
        for path in sorted(self._styles_dir().glob("style-*.json")):
            try:
                template = load_json(path)["template"]
                self._validate_schema(
                    template, SCHEMA_DIR / "style-template.schema.json",
                    "style template",
                )
                styles.append(template)
            except (OSError, ValueError, KeyError, ProjectError) as exc:
                LOGGER.warning("invalid style file %s: %s", path.name, exc)
                if include_invalid:
                    styles.append({
                        "style_id": path.stem,
                        "name": path.stem,
                        "invalid": True,
                        "reason": str(exc)[:200],
                    })
                continue
        return styles

    def combine_styles(self, style_ids: list[str], name: str) -> dict:
        """Aggregate the stored observations of several single-reference
        styles into ONE multi-reference template — the design's actual
        reusable-grammar unit (3-5 references; single references are
        confidence-capped hints). Source styles are kept."""
        from .style_intelligence import StyleError, aggregate_template

        unique_ids = sorted(set(style_ids))
        if len(unique_ids) < 2:
            raise ProjectError("Combina al menos dos estilos DISTINTOS")
        observations = []
        seen_sources: set[str] = set()
        excluded: list[dict] = []
        for style_id in unique_ids:
            if not re.fullmatch(r"style-[a-f0-9]{8}", style_id):
                raise ProjectError(f"Identificador inválido: {style_id}")
            path = self._styles_dir() / f"{style_id}.json"
            if not path.is_file():
                raise ProjectError(f"No existe el estilo {style_id}")
            stored = load_json(path)
            recorded = set(
                (stored.get("template") or {}).get("source_observations") or []
            )
            for obs in stored.get("observations") or []:
                # stored observations are data from disk — validate before
                # they can poison an aggregation, and only accept the ones
                # the template itself recorded as sources
                self._validate_schema(
                    obs, SCHEMA_DIR / "style-observation.schema.json",
                    "style observation",
                )
                if obs.get("observation_id") not in recorded:
                    excluded.append({
                        "style_id": style_id,
                        "reason": "observación no registrada por su template",
                    })
                    continue
                # confidence rises with INDEPENDENT references: the same
                # source (by content hash) must never count twice, even
                # via a previously combined style
                source = (obs.get("source") or {}).get("sha256")
                if not source:
                    excluded.append({
                        "style_id": style_id,
                        "reason": "sin identidad de contenido (sha256)",
                    })
                    continue
                if source in seen_sources:
                    excluded.append({
                        "style_id": style_id,
                        "reason": "misma referencia ya incluida",
                    })
                    continue
                seen_sources.add(source)
                observations.append(obs)
        if len(observations) < 2:
            raise ProjectError(
                "Se necesitan al menos dos referencias INDEPENDIENTES "
                "(mismos videos no cuentan doble)"
            )
        try:
            template = aggregate_template(name, observations)
        except StyleError as exc:
            raise ProjectError(str(exc)) from exc
        self._validate_schema(
            template, SCHEMA_DIR / "style-template.schema.json",
            "style template",
        )
        write_json(
            self._styles_dir() / f"{template['style_id']}.json",
            {"template": template, "observations": observations},
        )
        return {
            "style_id": template["style_id"],
            "template": template,
            "included_references": len(observations),
            "excluded": excluded,
        }

    def delete_style(self, style_id: str) -> None:
        if not re.fullmatch(r"style-[a-f0-9]{8}", style_id):
            raise ProjectError("Identificador de estilo inválido")
        path = self._styles_dir() / f"{style_id}.json"
        if not path.is_file():
            raise ProjectError(f"No existe el estilo {style_id}")
        path.unlink()

    def _log_cost(self, project_id: str, kind: str, usage: dict) -> None:
        """Append one metered model call to the project's cost ledger."""
        path = self.settings.runtime / project_id / "costs.jsonl"
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "at": utc_now(), "kind": kind,
                    "model": usage.get("model"),
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                }) + "\n")
        except OSError as exc:
            LOGGER.warning("cost log failed: %s", exc)

    def project_costs(self, project_id: str) -> dict:
        """Per-project token spend, aggregated from run manifests (visual)
        and the cost ledger (planner calls). Dollar estimates appear only
        for models priced in app/pricing.json — no invented prices."""
        self.get_project(project_id)
        pricing = {}
        pricing_path = APP_DIR / "pricing.json"
        if pricing_path.is_file():
            try:
                pricing = {
                    k: v for k, v in load_json(pricing_path).items()
                    if isinstance(v, dict)
                }
            except ValueError:
                pass

        def estimate(model, prompt, completion):
            rates = pricing.get(model or "")
            if not rates or rates.get("input_per_m") is None:
                return None
            return round(
                (prompt or 0) / 1e6 * rates["input_per_m"]
                + (completion or 0) / 1e6 * (rates.get("output_per_m") or 0),
                4,
            )

        rows = []
        runs_dir = self.settings.runtime / project_id / "analysis" / "runs"
        for manifest_path in sorted(runs_dir.glob("*/manifest.json")):
            try:
                manifest = load_json(manifest_path)
            except (OSError, ValueError):
                continue
            telemetry = manifest.get("telemetry") or {}
            provider = manifest.get("provider") or {}
            model = provider.get("model") if isinstance(provider, dict) else None
            if manifest.get("run_key", "").startswith("asr-"):
                rows.append({
                    "kind": "transcripción (local)", "model": model or "faster-whisper",
                    "calls": None, "prompt_tokens": 0, "completion_tokens": 0,
                    "est_usd": 0.0,
                })
                continue
            rows.append({
                "kind": "análisis visual", "model": model,
                "calls": telemetry.get("calls"),
                "prompt_tokens": telemetry.get("prompt_tokens") or 0,
                "completion_tokens": telemetry.get("completion_tokens") or 0,
                "est_usd": estimate(
                    model, telemetry.get("prompt_tokens"),
                    telemetry.get("completion_tokens"),
                ),
            })
        ledger = self.settings.runtime / project_id / "costs.jsonl"
        if ledger.is_file():
            for line in ledger.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                rows.append({
                    "kind": {"concepts": "historias"}.get(
                        entry.get("kind"), entry.get("kind")
                    ),
                    "model": entry.get("model"),
                    "calls": 1,
                    "prompt_tokens": entry.get("prompt_tokens") or 0,
                    "completion_tokens": entry.get("completion_tokens") or 0,
                    "est_usd": estimate(
                        entry.get("model"), entry.get("prompt_tokens"),
                        entry.get("completion_tokens"),
                    ),
                })
        priced = [r["est_usd"] for r in rows if r["est_usd"] is not None]
        # visible completion marks: how long each finished stage took,
        # from the durable jobs store (latest completed job per kind)
        step_times: list[dict] = []
        jobs_path = self.settings.runtime / "jobs.json"
        if jobs_path.is_file():
            try:
                stored = load_json(jobs_path)
                jobs = stored.get("jobs") or stored if isinstance(stored, dict) else stored
                if isinstance(jobs, dict):
                    jobs = list(jobs.values())
                latest: dict[str, dict] = {}
                for job in jobs:
                    if not isinstance(job, dict):
                        continue
                    if job.get("project_id") != project_id:
                        continue
                    if job.get("status") != "completed":
                        continue
                    if not (job.get("started_at") and job.get("finished_at")):
                        continue
                    kind = job.get("kind") or "?"
                    if (
                        kind not in latest
                        or job["finished_at"] > latest[kind]["finished_at"]
                    ):
                        latest[kind] = job
                for kind, job in sorted(
                    latest.items(), key=lambda kv: kv[1]["finished_at"]
                ):
                    start = dt.datetime.fromisoformat(
                        job["started_at"].replace("Z", "+00:00")
                    )
                    finish = dt.datetime.fromisoformat(
                        job["finished_at"].replace("Z", "+00:00")
                    )
                    step_times.append({
                        "kind": kind,
                        "seconds": round((finish - start).total_seconds(), 1),
                        "finished_at": job["finished_at"],
                    })
            except (OSError, ValueError, KeyError, TypeError):
                pass
        return {
            "rows": rows,
            "step_times": step_times,
            "total_est_usd": round(sum(priced), 4) if priced else None,
            "all_priced": all(r["est_usd"] is not None for r in rows),
            "unmetered": [
                "revisiones y comandos de edición (aún sin registrar)",
                "análisis de estilos (biblioteca global, no por proyecto)",
            ],
        }

    def concept_inputs_token(self, project_id: str) -> str:
        """Fingerprint of the PROJECT STATE a concept generation would read
        (prompt, evidence set with approval states, latest source context).
        Job dedup needs this besides the request options — otherwise a
        submit after approving evidence gets an already-running stale job."""
        project = self.get_project(project_id)
        evidence = [
            (e.get("evidence_id") or f"{e.get('asset_id')}:{e.get('start_seconds')}",
             e.get("status"))
            for e in self.approved_evidence(project_id)
            + self.pending_evidence(project_id)
        ]
        context = self._latest_source_context(project_id) or {}
        return hashlib.sha1(
            json.dumps(
                [project.get("prompt"), sorted(evidence),
                 context.get("run_id") or context.get("generated_at")],
                default=str,
            ).encode()
        ).hexdigest()[:16]

    def style_matches(self, project_id: str) -> dict:
        """Every style × every grounded concept, scored deterministically."""
        from .style_intelligence import match_concept

        project = self.get_project(project_id)
        concepts = project.get("concepts") or []
        if not concepts:
            raise ProjectError("Genera historias antes de comparar estilos")
        styles = self.list_styles()
        if not styles:
            raise ProjectError(
                "No hay estilos todavía — deja un video de referencia en "
                "references/ y analízalo"
            )
        inventory = project.get("inventory") or {}
        matches = [
            match_concept(template, concept, inventory)
            for template in styles
            for concept in concepts
        ]
        for match in matches:
            self._validate_schema(
                match, SCHEMA_DIR / "style-match.schema.json", "style match"
            )
        matches.sort(key=lambda m: -m["score"])
        # design-review reservation: concepts generated WITH a style are
        # not unbiased evidence of fit to that style — the writer was told
        # to echo its grammar, so a high score measures obedience. Surface
        # which style (if any) conditioned the stored concepts.
        # lineage is per concept (kept/mixed sets exist); the document
        # flag alone misreports both directions
        conditioned = sorted({
            c.get("style_provenance")
            for c in concepts if c.get("style_provenance")
        })
        legacy_flag = None
        if not conditioned:
            concepts_path = (
                self.settings.runtime / project_id / "analysis" / "concepts.json"
            )
            if concepts_path.is_file():
                try:
                    legacy_flag = (
                        (load_json(concepts_path).get("style_application") or {})
                        .get("style_id")
                    )
                except ValueError:
                    pass
        return {
            "matches": matches,
            "concepts_conditioned_by": conditioned[0] if conditioned else legacy_flag,
            "conditioned_styles": conditioned,
        }

    def _footage_language(self, project_id: str) -> str | None:
        """Dominant detected speech language from the most recent ASR run,
        weighted by transcribed duration."""
        latest = self._latest_asr_transcripts(project_id)
        if latest is None:
            return None
        weights: dict[str, float] = {}
        for record in latest.get("transcripts", []):
            language = (record.get("detection") or {}).get("language")
            if not language:
                continue
            spoken = sum(
                segment["end_seconds"] - segment["start_seconds"]
                for segment in record.get("segments", [])
            )
            weights[language] = weights.get(language, 0.0) + spoken
        if not weights:
            return None
        return max(weights, key=weights.get)

    def sync_media(self, project_id: str) -> dict:
        """Reconcile the inventory with the source folder: newly added files
        are probed and thumbnailed in; vanished files drop out. Existing
        asset ids and their analysis stay stable."""
        # Held across probing: a job finishing mid-sync would
        # otherwise write a stale inventory back over the new one.
        with self._project_write(project_id):
            stored = load_json(self.settings.runtime / project_id / "project.json")
            source_dir = (self.settings.root / stored["source_directory"]).resolve()
            on_disk = {
                path.name: path
                for path in sorted(source_dir.rglob("*"))
                if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
            }
            assets = stored["inventory"]["assets"]
            kept = [asset for asset in assets if asset["filename"] in on_disk]
            removed = [a["filename"] for a in assets if a["filename"] not in on_disk]
            known = {asset["filename"] for asset in kept}
            # A filename is not an identity: a clip re-recorded or trimmed under
            # the same name would keep the old evidence attached to new bytes.
            # Size is the cheap tell; re-probing re-hashes and re-reads duration.
            thumbs = self.settings.runtime / project_id / "thumbnails"
            replaced = []
            for index, asset in enumerate(kept):
                path = on_disk[asset["filename"]]
                if path.stat().st_size == asset.get("size_bytes"):
                    continue
                refreshed = self._probe_asset(asset["asset_id"], path)
                thumbs.mkdir(parents=True, exist_ok=True)
                if self._make_thumbnail(path, refreshed, thumbs / f"{asset['asset_id']}.jpg"):
                    refreshed["thumbnail_available"] = True
                refreshed["analysis_status"] = "technical_only"
                kept[index] = refreshed
                replaced.append(asset["filename"])
            used_ids = {asset["asset_id"] for asset in kept}
            thumbnails = self.settings.runtime / project_id / "thumbnails"
            thumbnails.mkdir(parents=True, exist_ok=True)
            added = []
            for filename, path in on_disk.items():
                if filename in known:
                    continue
                asset_id = slugify(path.stem).replace("-", "_")
                base_id, suffix = asset_id, 2
                while asset_id in used_ids:
                    asset_id = f"{base_id}_{suffix}"
                    suffix += 1
                used_ids.add(asset_id)
                asset = self._probe_asset(asset_id, path)
                if self._make_thumbnail(path, asset, thumbnails / f"{asset_id}.jpg"):
                    asset["thumbnail_available"] = True
                kept.append(asset)
                added.append(filename)
            stored["inventory"]["assets"] = kept
            stored["updated_at"] = utc_now()
            if added or replaced:
                changed = len(added) + len(replaced)
                stored["footage_summary"] = (
                    f"{len(kept)} media file(s); {changed} new or changed since the "
                    "last analysis — re-analyze to include them in stories."
                )
            if replaced:
                stored["analysis"]["warning"] = (
                    f"{len(replaced)} clip(s) changed on disk after they were "
                    "analyzed; their evidence describes the old footage. "
                    "Re-analyze before compiling a cut."
                )
            write_json(self.settings.runtime / project_id / "project.json", stored)
            return {
                "added": added,
                "removed": removed,
                "replaced": replaced,
                "total": len(kept),
            }

    def remove_asset(
        self, project_id: str, asset_id: str, delete_file: bool = False
    ) -> dict:
        """Remove a clip from the project's inventory. The file itself is
        kept on disk unless delete_file is explicitly requested. Evidence
        citing the clip becomes inert (grounding checks drop it)."""
        path = self.settings.runtime / project_id / "project.json"
        with self._project_write(project_id):
            stored = load_json(path)
            assets = stored["inventory"]["assets"]
            asset = next((a for a in assets if a["asset_id"] == asset_id), None)
            if asset is None:
                raise ProjectError(f"Unknown asset: {asset_id}")
            used = {
                event.get("asset_id")
                for track in (stored.get("plan") or {}).get("tracks", [])
                for event in track.get("events", [])
            }
            if asset_id in used:
                raise ProjectError(
                    "Este clip está en el corte actual — quítalo del corte "
                    "primero (pide el cambio en Edición) y luego bórralo"
                )
            stored["inventory"]["assets"] = [
                a for a in assets if a["asset_id"] != asset_id
            ]
            stored["updated_at"] = utc_now()
            write_json(path, stored)
        (self.settings.runtime / project_id / "thumbnails" / f"{asset_id}.jpg").unlink(
            missing_ok=True
        )
        if delete_file:
            source = (self.settings.root / asset["source_path"]).resolve()
            try:
                source.relative_to(self.settings.root)
                source.unlink(missing_ok=True)
            except ValueError:
                pass
        return {"removed": asset_id, "file_deleted": delete_file}

    DRIVE_INBOX = "gdrive:VlogInbox"

    def drive_inbox(self) -> list[dict]:
        """Folders waiting in the Drive VlogInbox, with import status,
        content size, and a receiving/ready signal so the phone can watch
        its own Drive upload arrive."""
        result = subprocess.run(
            ["rclone", "lsjson", "-R", "--files-only", self.DRIVE_INBOX],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode:
            raise ProjectError(f"Drive inbox unavailable: {result.stderr.strip()[-200:]}")
        existing = {p["project_id"] for p in self.list_projects()}
        grouped: dict[str, dict] = {}
        for entry in json.loads(result.stdout or "[]"):
            top, _, _ = entry["Path"].partition("/")
            if not top or "/" not in entry["Path"]:
                # files loose in the inbox root belong to no vlog folder
                continue
            folder = grouped.setdefault(
                top, {"files": 0, "bytes": 0, "modified": ""}
            )
            folder["files"] += 1
            folder["bytes"] += int(entry.get("Size") or 0)
            folder["modified"] = max(folder["modified"], entry.get("ModTime") or "")
        now = dt.datetime.now(dt.timezone.utc)
        folders = []
        for name, info in grouped.items():
            receiving = False
            if info["modified"]:
                try:
                    last = dt.datetime.fromisoformat(
                        info["modified"].replace("Z", "+00:00")
                    )
                    # the Drive app writes files as it uploads them: a file
                    # newer than 2 minutes usually means more are coming
                    receiving = (now - last).total_seconds() < 120
                except ValueError:
                    pass
            imported = slugify(name) in existing
            local = self.settings.root / "footage" / slugify(name)
            local_bytes = 0
            if not imported and local.is_dir():
                for p in local.rglob("*"):
                    # exclude rclone temps and our generated JPGs so the
                    # percentage compares like bytes with the remote total
                    if p.suffix in (".partial",) or (
                        p.suffix.lower() == ".jpg"
                        and p.with_suffix(".HEIC").exists()
                    ):
                        continue
                    try:
                        if p.is_file():
                            local_bytes += p.stat().st_size
                    except OSError:
                        continue  # racing an in-flight rclone rename
            folders.append(
                {
                    "name": name,
                    "slug": slugify(name),
                    "modified": info["modified"] or None,
                    "imported": imported,
                    "file_count": info["files"],
                    "total_bytes": info["bytes"],
                    "local_bytes": local_bytes,
                    "receiving": receiving,
                }
            )
        return sorted(folders, key=lambda item: item["modified"] or "", reverse=True)

    @staticmethod
    def _convert_heic(heic: Path, jpg: Path) -> bool:
        """HEIC → JPG via pillow-heif (bundled modern libheif — the
        distro's is too old for iOS 17+ files). Orientation is baked in
        so downstream ffmpeg sees the photo upright."""
        try:
            from PIL import Image, ImageOps
            from pillow_heif import register_heif_opener

            register_heif_opener()
            partial = jpg.with_suffix(".jpg.tmp")
            with Image.open(heic) as image:
                upright = ImageOps.exif_transpose(image)
                upright.convert("RGB").save(partial, "JPEG", quality=92)
            partial.replace(jpg)  # atomic: no truncated JPEG survives
            return True
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("HEIC conversion failed for %s: %s", heic.name, exc)
            return False

    def cancel_drive_import(self, folder: str) -> dict:
        slug = slugify(folder)
        with self._drive_imports_guard:
            process = self._drive_imports.get(slug)
            if process is None:
                raise ProjectError(
                    "No hay ninguna importación activa de esa carpeta"
                )
            # the flag covers the gaps between subprocess phases
            self._drive_cancelled.add(slug)
        process.terminate()
        return {"cancelled": slug}

    def _import_cancelled(self, slug: str) -> bool:
        with self._drive_imports_guard:
            return slug in self._drive_cancelled

    def drive_local_progress(self, folder: str) -> dict:
        """Bytes already copied to the laptop for an in-flight import —
        the UI polls this to show real progress instead of a spinner."""
        if "/" in folder or folder.startswith("."):
            raise ProjectError("Invalid inbox folder name")
        target = self.settings.root / "footage" / slugify(folder)
        if not target.is_dir():
            return {"copied_bytes": 0, "file_count": 0}
        files = [p for p in target.rglob("*") if p.is_file()]
        return {
            "copied_bytes": sum(p.stat().st_size for p in files),
            "file_count": len(files),
        }

    def import_drive_folder(self, folder: str) -> dict:
        """Sync a VlogInbox folder down and create the project: folder name
        becomes the title, a nota*/note* text file becomes the prompt.
        Strictly read-only toward Drive — this tool never deletes there."""
        if "/" in folder or folder.startswith("."):
            raise ProjectError("Invalid inbox folder name")
        slug = slugify(folder)
        target = self.settings.root / "footage" / slug
        target.mkdir(parents=True, exist_ok=True)
        with self._drive_imports_guard:
            self._drive_cancelled.discard(slug)
        process = subprocess.Popen(
            [
                "rclone", "copy", f"{self.DRIVE_INBOX}/{folder}", str(target),
                "--drive-export-formats", "txt", "--transfers", "4",
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        with self._drive_imports_guard:
            self._drive_imports[slug] = process
        try:
            _, stderr = process.communicate(timeout=7200)
        except subprocess.TimeoutExpired:
            # never leak the child: kill, reap, then report
            process.kill()
            process.communicate()
            raise ProjectError(
                "La importación superó el tiempo máximo (2h) — reanuda con "
                "Importar"
            ) from None
        if process.returncode:
            # rclone resumes cleanly: completed clips are kept and skipped
            # on the next Importar
            if process.returncode in (-15, -9):
                raise ProjectError(
                    "Importación cancelada — los clips ya copiados se "
                    "conservan; vuelve a pulsar Importar para reanudar"
                )
            raise ProjectError(f"Drive sync failed: {(stderr or '').strip()[-300:]}")
        # Integrity: verify every local file against Drive's checksums —
        # a silent partial/corrupt copy must fail the import, not become
        # a project missing footage
        check = subprocess.Popen(
            ["rclone", "check", "--one-way",
             f"{self.DRIVE_INBOX}/{folder}", str(target)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        with self._drive_imports_guard:
            self._drive_imports[slug] = check
        try:
            _, check_err = check.communicate(timeout=600)
        except subprocess.TimeoutExpired:
            check.kill()
            check.communicate()
            raise ProjectError(
                "La verificación de integridad tardó demasiado — reintenta"
            ) from None
        if self._import_cancelled(slug):
            raise ProjectError(
                "Importación cancelada — los clips ya copiados se conservan"
            )
        if check.returncode:
            raise ProjectError(
                "La verificación de integridad falló — algunos archivos no "
                "coinciden con Drive. Vuelve a pulsar Importar para "
                f"reintentar: {(check_err or '').strip()[-300:]}"
            )
        # iPhone HEIC photos: ffmpeg cannot decode them, so convert to JPG
        # at ingest (originals kept) — otherwise photos silently never
        # become B-roll assets
        try:
            for heic in sorted(target.rglob("*")):
                if self._import_cancelled(slug):
                    raise ProjectError(
                        "Importación cancelada — los clips ya copiados se "
                        "conservan"
                    )
                if heic.suffix.lower() != ".heic" or not heic.is_file():
                    continue
                jpg = heic.with_suffix(".jpg")
                if jpg.exists():
                    continue
                self._convert_heic(heic, jpg)
        finally:
            with self._drive_imports_guard:
                self._drive_imports.pop(slug, None)
                self._drive_cancelled.discard(slug)
        prompt = ""
        for note in sorted(target.glob("*.txt")):
            if note.stem.lower().startswith(("nota", "note")):
                prompt = note.read_text(encoding="utf-8", errors="replace").strip()[:4000]
                break
        return self.create_project(folder, f"footage/{slug}", prompt)

    def clone_project(self, project_id: str, name: str, prompt: str = "") -> dict:
        """New independent project over the same source folder, reusing the
        original's analysis cache (same folder means identical asset ids, so
        evidence runs transfer verbatim). Creative state starts empty."""
        clone_id = slugify(name)
        source_root = self.settings.runtime / project_id
        clone_root = self.settings.runtime / clone_id
        if clone_root.exists():
            raise ProjectError(f"Project already exists: {clone_id}")
        source_stored = load_json(source_root / "project.json")

        staging = Path(
            tempfile.mkdtemp(prefix=f".{clone_id}-", dir=self.settings.runtime)
        )
        try:
            # Instant clone: inventory, thumbnails, and analysis are copied
            # from the source instead of re-probing and re-analyzing.
            if (source_root / "thumbnails").is_dir():
                shutil.copytree(source_root / "thumbnails", staging / "thumbnails")
            runs_src = source_root / "analysis" / "runs"
            run_manifests = (
                [
                    load_json(path)
                    for path in runs_src.glob("*/manifest.json")
                    if path.is_file()
                ]
                if runs_src.is_dir()
                else []
            )
            has_runs = bool(run_manifests)
            has_evidence = any(
                item.get("provider", {}).get("adapter") in EVIDENCE_ADAPTERS
                for item in run_manifests
            )
            if has_runs:
                shutil.copytree(runs_src, staging / "analysis" / "runs")
            stored = json.loads(json.dumps(source_stored))
            stored.update(
                {
                    "project_id": clone_id,
                    "name": name.strip(),
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                    "prompt": (prompt or source_stored.get("prompt", "")).strip(),
                    "status": (
                        "semantic_ready" if has_evidence else "awaiting_semantic_analysis"
                    ),
                    "concepts": [],
                    "selected_concept_id": None,
                    "plan": None,
                    "outputs": {},
                    "footage_summary": (
                        f"Indexed {len(stored['inventory']['assets'])} media file(s); "
                        f"analysis shared from '{source_stored['name']}'."
                    ),
                }
            )
            stored["analysis"]["visual"] = (
                "completed" if has_evidence else "unavailable"
            )
            stored["analysis"]["speech"] = (
                "completed" if has_evidence else "unavailable"
            )
            write_json(staging / "project.json", stored)
            try:
                os.replace(staging, clone_root)
            except OSError as exc:  # concurrent create of the same id
                raise ProjectError(f"Project already exists: {clone_id}") from exc
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return self.get_project(clone_id)

    def reset_project(self, project_id: str, keep_analysis: bool = True) -> dict:
        """Return the project to step 1. Derived creative state (concepts,
        plan, outputs, selection) is always cleared; the analysis cache is
        kept unless keep_analysis is False. Source media is never touched."""
        self.get_project(project_id)
        root = self.settings.runtime / project_id
        for target in ("plan", "outputs"):
            shutil.rmtree(root / target, ignore_errors=True)
        (root / "analysis" / "concepts.json").unlink(missing_ok=True)
        (root / "selection.json").unlink(missing_ok=True)
        if not keep_analysis:
            shutil.rmtree(root / "analysis", ignore_errors=True)

        stored = load_json(root / "project.json")
        has_runs = bool(self._current_run_manifests(project_id))
        stored.update(
            {
                "updated_at": utc_now(),
                "concepts": [],
                "selected_concept_id": None,
                "plan": None,
                "status": "semantic_ready" if has_runs else "awaiting_semantic_analysis",
                "footage_summary": (
                    f"Indexed {len(stored.get('inventory', {}).get('assets', []))} "
                    "media file(s). "
                    + (
                        "Analysis is cached and ready for story ideas."
                        if has_runs
                        else "Footage has not been analyzed yet."
                    )
                ),
            }
        )
        stored["analysis"]["visual"] = "completed" if has_runs else "unavailable"
        stored["analysis"]["speech"] = "completed" if has_runs else "unavailable"
        write_json(root / "project.json", stored)
        return self.get_project(project_id)

    def clip_scores(self, project_id: str) -> list[dict]:
        """Deterministic per-clip value for the intended vlog, computed from
        existing artifacts: seconds used in the cut, citations across story
        ideas, strong moments found by perception, and transcribed speech.
        Fully explainable — every point traces to evidence."""
        project = self.get_project(project_id)
        plan = project.get("plan")
        used: dict[str, float] = {}
        if plan:
            for track in plan["tracks"]:
                if track["kind"] != "video":
                    continue
                for event in track["events"]:
                    if event.get("asset_id"):
                        used[event["asset_id"]] = used.get(event["asset_id"], 0.0) + (
                            event["source_end_seconds"] - event["source_start_seconds"]
                        )
        cited: dict[str, float] = {}
        for concept in project.get("concepts", []):
            for beat in concept.get("structure", []):
                for evidence in beat.get("evidence", []):
                    cited[evidence["asset_id"]] = cited.get(evidence["asset_id"], 0.0) + (
                        evidence["end_seconds"] - evidence["start_seconds"]
                    )
        moments: dict[str, int] = {}
        speech: dict[str, float] = {}
        for manifest in self._current_run_manifests(project_id):
            run = self.semantic_run(project_id, manifest["run_key"])
            for observation in run["observations"]:
                if observation.get("review_status") != "reviewed":
                    continue
                asset_id = observation.get("asset_id")
                if not asset_id:
                    continue
                if observation.get("evidence_type") == "speech":
                    speech[asset_id] = speech.get(asset_id, 0.0) + (
                        observation["end_seconds"] - observation["start_seconds"]
                    )
                elif "_m" in (observation.get("clip_id") or ""):
                    moments[asset_id] = moments.get(asset_id, 0) + 1

        results = []
        for asset in project.get("inventory", {}).get("assets", []):
            asset_id = asset["asset_id"]
            duration = float(asset.get("duration_seconds") or 0.0) or 1.0
            used_s = round(used.get(asset_id, 0.0), 1)
            cited_s = round(cited.get(asset_id, 0.0), 1)
            moment_count = moments.get(asset_id, 0)
            speech_s = round(speech.get(asset_id, 0.0), 1)
            score = min(50.0, used_s * 10.0)
            score += min(20.0, cited_s * 2.0)
            score += min(15.0, moment_count * 3.0)
            score += min(15.0, speech_s * 1.5)
            score = round(min(score, 100.0))
            if used_s > 0:
                verdict = "esencial" if score >= 60 else "en uso"
            elif cited_s > 0 or score >= 25:
                verdict = "reserva"
            else:
                verdict = "descartable"
            reasons = []
            if used_s:
                reasons.append(f"{used_s}s en el corte")
            if cited_s:
                reasons.append(f"{cited_s}s citados en historias")
            if moment_count:
                reasons.append(f"{moment_count} momentos destacados")
            if speech_s:
                reasons.append(f"{speech_s}s de tu voz")
            if not reasons:
                reasons.append("sin evidencia aprovechada")
            results.append(
                {
                    "asset_id": asset_id,
                    "filename": asset["filename"],
                    "duration_seconds": round(duration, 1),
                    "score": score,
                    "verdict": verdict,
                    "reason": ", ".join(reasons),
                    "used_seconds": used_s,
                }
            )
        return sorted(results, key=lambda item: -item["score"])

    @staticmethod
    def _srt_time(seconds: float) -> str:
        millis = int(round(seconds * 1000))
        hours, rem = divmod(millis, 3_600_000)
        minutes, rem = divmod(rem, 60_000)
        secs, ms = divmod(rem, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"

    def export_captions(self, project_id: str) -> Path:
        """Timeline-aligned SRT subtitles built from the ASR transcript:
        each spoken segment that survives into the cut appears at its
        position in the final video, verbatim in its original language."""
        plan_path, _, _ = self._plan_sources(project_id)
        plan = load_json(plan_path)
        latest = self._latest_asr_transcripts(project_id)
        if latest is None:
            raise ProjectError("Run speech analysis before exporting captions")
        segments_by_asset: dict[str, list[dict]] = {}
        for record in latest.get("transcripts", []):
            segments_by_asset[record["asset_id"]] = record.get("segments", [])

        entries: list[tuple[float, float, str]] = []
        # Captions follow SPOKEN audio, which diverges from the picture on
        # J/L cuts (cross-review 11): the primary audio track is the truth.
        caption_events = next(
            track["events"] for track in plan["tracks"] if track["kind"] == "audio"
        )
        for event in caption_events:
            for segment in segments_by_asset.get(event["asset_id"], []):
                start = max(segment["start_seconds"], event["source_start_seconds"])
                end = min(segment["end_seconds"], event["source_end_seconds"])
                if end - start < 0.2:
                    continue
                offset = event["timeline_start_seconds"] - event["source_start_seconds"]
                entries.append((start + offset, end + offset, segment["text"].strip()))
        entries.sort(key=lambda item: item[0])

        output = self.settings.runtime / project_id / "outputs" / "captions.srt"
        lines = []
        for index, (start, end, text) in enumerate(entries, start=1):
            lines.append(str(index))
            lines.append(f"{self._srt_time(start)} --> {self._srt_time(end)}")
            lines.append(text)
            lines.append("")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("\n".join(lines), encoding="utf-8")
        return output

    def _speech_words(self, project_id: str) -> dict[str, list[dict]]:
        """Word timings from the most recent ASR run, keyed by asset, for
        snapping cut boundaries to speech."""
        latest = self._latest_asr_transcripts(project_id)
        if latest is None:
            return {}
        words: dict[str, list[dict]] = {}
        for record in latest.get("transcripts", []):
            asset_words = words.setdefault(record["asset_id"], [])
            for segment in record.get("segments", []):
                for word in segment.get("words", []):
                    asset_words.append(
                        {
                            "start_seconds": word["start_seconds"],
                            "end_seconds": word["end_seconds"],
                        }
                    )
        return words

    @staticmethod
    def _validate_schema(document: dict, schema_path: Path, label: str) -> None:
        from jsonschema import Draft202012Validator

        schema = load_json(schema_path)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(document),
            key=lambda err: list(err.path),
        )
        if errors:
            first = errors[0]
            location = "/".join(str(part) for part in first.path)
            raise ProjectError(f"{label} failed validation at {location}: {first.message}")

    def semantic_runs(self, project_id: str) -> list[dict]:
        self.get_project(project_id)
        runs_dir = self.settings.runtime / project_id / "analysis" / "runs"
        manifests = []
        for path in sorted(runs_dir.glob("*/manifest.json")):
            manifests.append(load_json(path))
        return manifests

    def analysis_telemetry(self, project_id: str) -> dict:
        self.get_project(project_id)
        runs = []
        for manifest in self._semantic_run_manifests(project_id):
            telemetry = manifest.get("telemetry")
            if not isinstance(telemetry, dict):
                continue
            runs.append(
                {
                    "run_key": manifest["run_key"],
                    "imported_at": manifest.get("imported_at"),
                    "provider": manifest.get("provider"),
                    "telemetry": telemetry,
                }
            )
        runs.sort(key=lambda item: item.get("imported_at") or "")
        return {"project_id": project_id, "runs": runs}

    def semantic_run(self, project_id: str, run_key: str) -> dict:
        self.get_project(project_id)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,160}", run_key):
            raise ProjectError("Invalid semantic run id")
        path = self._semantic_run_path(project_id, run_key)
        result = load_json(self._require_file(path))
        if result.get("schema_version") == "source-context.v1":
            return result
        reviews_path = path.parent / "reviews.json"
        reviews = load_json(reviews_path) if reviews_path.is_file() else {"decisions": {}}
        decisions = reviews.get("decisions") or {}
        accepted = [
            item
            for item in result["observations"]
            if item["normalization_status"] == "accepted"
        ]
        approved_count = 0
        rejected_review_count = 0
        for observation in accepted:
            decision = decisions.get(observation["evidence_id"])
            if not decision:
                continue
            observation["review_status"] = (
                "reviewed" if decision["action"] == "approve" else "rejected"
            )
            observation["reviewed_caption"] = decision.get("caption")
            observation["review_note"] = decision.get("note")
            observation["reviewed_at"] = decision.get("reviewed_at")
            if decision["action"] == "approve":
                approved_count += 1
            else:
                rejected_review_count += 1
        pending_count = len(accepted) - approved_count - rejected_review_count
        result["summary"].update(
            {
                "pending_review_count": pending_count,
                "approved_count": approved_count,
                "rejected_review_count": rejected_review_count,
            }
        )
        result["review_status"] = "reviewed" if pending_count == 0 else "pending"
        result["safe_for_edit_plan"] = pending_count == 0 and approved_count > 0
        return result

    def review_semantic_evidence(
        self,
        project_id: str,
        run_key: str,
        evidence_id: str,
        action: str,
        caption: str | None = None,
        note: str | None = None,
    ) -> dict:
        if action not in {"approve", "reject"}:
            raise ProjectError("Review action must be approve or reject")
        normalized_path = self._semantic_run_path(project_id, run_key)
        normalized = load_json(self._require_file(normalized_path))
        if normalized.get("schema_version") == "source-context.v1":
            raise ProjectError("Source context is derived and cannot be reviewed as evidence")
        observation = next(
            (
                item
                for item in normalized["observations"]
                if item["evidence_id"] == evidence_id
            ),
            None,
        )
        if observation is None:
            raise ProjectError(f"Unknown semantic evidence: {evidence_id}")
        if observation["normalization_status"] != "accepted":
            raise ProjectError("Structurally rejected evidence cannot be approved")

        reviewed_caption = (caption or observation["caption"]).strip()
        if action == "approve" and not reviewed_caption:
            raise ProjectError("Approved evidence requires a non-empty caption")
        event = {
            "event_id": uuid.uuid4().hex[:12],
            "evidence_id": evidence_id,
            "action": action,
            "caption": reviewed_caption if action == "approve" else None,
            "note": note.strip() if note else None,
            "reviewed_at": utc_now(),
        }
        reviews_path = normalized_path.parent / "reviews.json"
        with self._semantic_review_lock:
            reviews = (
                load_json(reviews_path)
                if reviews_path.is_file()
                else {
                    "schema_version": "semantic-reviews.v1",
                    "project_id": project_id,
                    "run_key": run_key,
                    "updated_at": event["reviewed_at"],
                    "decisions": {},
                    "events": [],
                }
            )
            reviews["updated_at"] = event["reviewed_at"]
            reviews["decisions"][evidence_id] = event
            reviews["events"].append(event)
            write_json(reviews_path, reviews)
        return self.semantic_run(project_id, run_key)

    def render(self, project_id: str, burn_captions: bool = False) -> dict:
        # Captioned and uncaptioned renders share review.mp4 and its
        # state file. The expensive ffmpeg work runs UNLOCKED to a
        # per-invocation temp file (concurrent renders stay productive
        # instead of one starving a job worker on the lock); only the
        # cheap cache check and the atomic artifact+state promotion hold
        # the per-project lock.
        return self._render_impl(project_id, burn_captions)

    def _render_lock(self, project_id: str):
        with self._render_locks_guard:
            return self._render_locks.setdefault(project_id, threading.Lock())

    def _render_cache_hit(
        self, project_id: str, output: Path, state_path: Path,
        render_key: dict, measure_version: str, artifact_key: str,
    ) -> dict | None:
        if not (output.is_file() and state_path.is_file()):
            return None
        prior = load_json(state_path)
        if {k: prior.get(k) for k in render_key} != render_key:
            return None
        # a cached RENDER can still need fresh MEASUREMENT: the analyzer
        # versions independently of the renderer. The version is only
        # stamped on SUCCESS — a failure logs, keeps no stale claim, and
        # retries on the next hit.
        if prior.get("measure_version") != measure_version:
            measured = None
            try:
                from .style_intelligence import measure_rendered_grammar

                measured = measure_rendered_grammar(output)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("render re-measurement failed: %s", exc)
            if measured is not None:
                prior["achieved_render_grammar"] = measured
                prior["measure_version"] = measure_version
                prior.pop("measurement_error", None)
            else:
                prior.pop("achieved_render_grammar", None)
                prior["measurement_error"] = utc_now()
            write_json(state_path, prior)
        keyed = output.parent / f"review.{artifact_key}.mp4"
        return {
            "output": (
                f"/api/projects/{project_id}/outputs/render"
                f"?artifact={artifact_key}"
                if keyed.is_file()
                else f"/api/projects/{project_id}/outputs/render"
            ),
            "path": str(output),
            "cached": True,
            **(
                {"achieved_render_grammar": prior["achieved_render_grammar"]}
                if prior.get("achieved_render_grammar") else {}
            ),
        }

    def _render_impl(self, project_id: str, burn_captions: bool = False) -> dict:
        project = self.get_project(project_id)
        plan = project.get("plan")
        if not plan:
            raise ProjectError("This project does not have an approved edit plan")
        # rendered-language gate at the LAST exit: even a plan compiled
        # before the claim gates cannot burn a risky unsupported model
        # title into pixels (user-typed titles exempt)
        from .planning import title_blocked

        captions_map = self._approved_captions(project_id)
        plan_video_events = [
            event
            for track in plan.get("tracks", [])
            if track.get("kind") == "video"
            for event in track.get("events", [])
        ]
        supporting = " ".join(
            caption
            for event in plan_video_events
            for _s, _e, caption in captions_map.get(event["asset_id"], [])
            if _s < event["source_end_seconds"]
            and _e > event["source_start_seconds"]
        )
        for track in plan.get("tracks", []):
            if track.get("kind") != "title":
                continue
            for event in track.get("events", []):
                if title_blocked(
                    str(event.get("text") or ""), supporting,
                    user_authored=bool(event.get("user_authored")),
                ):
                    raise ProjectError(
                        f"El título «{event.get('text')}» afirma algo sin "
                        "respaldo aprobado — cámbialo (di, p. ej., «cambia "
                        "el título a …») o confirma la evidencia antes de "
                        "renderizar"
                    )
        self._verify_plan_lineage(project_id, plan)
        captions_path = None
        if burn_captions:
            captions_path = self.export_captions(project_id)
        selection = self._selection(project_id)
        selected = selection.get("concept_id") if selection else plan["concept_id"]
        if selected != plan["concept_id"]:
            raise ProjectError(
                "The selected concept has no compiled edit plan yet; compile it first"
            )
        output_dir = self.settings.runtime / project_id / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / "review.mp4"
        # Artifact cache: an identical plan (and captions choice) with an
        # existing file is a no-op — repeat renders of an unchanged cut are
        # the most common wasted minutes in a session.
        renderer_sha = hashlib.sha256(
            (PIPELINE_DIR / "render_edit.py").read_bytes()
        ).hexdigest()[:12]
        render_key = {
            "plan_sha": hashlib.sha256(
                json.dumps(plan, sort_keys=True).encode()
            ).hexdigest()[:16],
            "renderer": renderer_sha,
            "captions_sha": (
                hashlib.sha256(captions_path.read_bytes()).hexdigest()[:12]
                if captions_path is not None else None
            ),
        }
        state_path = output_dir / "review.render-state.json"
        artifact_key = hashlib.sha1(
            json.dumps(render_key, sort_keys=True).encode()
        ).hexdigest()[:8]
        from .style_intelligence import STYLE_MEASURE_VERSION as MEASURE_VERSION
        with self._render_lock(project_id):
            cached = self._render_cache_hit(
                project_id, output, state_path, render_key, MEASURE_VERSION,
                artifact_key,
            )
        if cached is not None:
            return cached
        script = PIPELINE_DIR / "render_edit.py"
        plan_path, inventory_path, media_root = self._plan_sources(project_id)
        keyed_output = output_dir / f"review.{artifact_key}.mp4"
        temp_output = output_dir / f"review.{uuid.uuid4().hex[:8]}.tmp.mp4"
        command = [
            sys.executable,
            str(script),
            "--plan",
            str(plan_path),
            "--output",
            str(temp_output),
            "--inventory",
            str(inventory_path),
            "--media-root",
            str(media_root),
        ]
        if captions_path is not None:
            command.extend(["--captions", str(captions_path)])
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=False
            )
            if result.returncode:
                detail = (result.stderr or result.stdout).strip()
                raise ProjectError(f"Render failed: {detail[-1000:]}")
            # close the loop at the pixels for EVERY fresh render: the
            # styled arm of an A/B is only meaningful against a measured
            # baseline. Measured on the temp artifact, still unlocked —
            # diagnostics must never fail a good render.
            try:
                from .style_intelligence import measure_rendered_grammar

                achieved_render = measure_rendered_grammar(temp_output)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("render measurement failed: %s", exc)
                achieved_render = None
            # atomic promotion: the immutable keyed artifact is the job's
            # durable result; review.mp4 is just the LATEST via hardlink,
            # so a concurrent job can never swap this job's pixels
            with self._render_lock(project_id):
                os.replace(temp_output, keyed_output)
                latest_link = output_dir / f".latest.{uuid.uuid4().hex[:6]}"
                os.link(keyed_output, latest_link)
                os.replace(latest_link, output)
                # prune old keyed artifacts, keeping the newest few
                keyed = sorted(
                    output_dir.glob("review.????????.mp4"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                for stale in keyed[4:]:
                    stale.unlink(missing_ok=True)
                write_json(
                    state_path,
                    {
                        **render_key,
                        "rendered_at": utc_now(),
                        **(
                            {"achieved_render_grammar": achieved_render,
                             "measure_version": MEASURE_VERSION}
                            if achieved_render is not None
                            else {"measurement_error": utc_now()}
                        ),
                        **(
                            {"style_targets":
                             plan["style_application"].get("targets")}
                            if plan.get("style_application") else {}
                        ),
                    },
                )
        finally:
            temp_output.unlink(missing_ok=True)
        return {
            "output": (
                f"/api/projects/{project_id}/outputs/render"
                f"?artifact={artifact_key}"
            ),
            "latest": f"/api/projects/{project_id}/outputs/render",
            "path": str(output),
            **(
                {"achieved_render_grammar": achieved_render}
                if achieved_render is not None else {}
            ),
        }

    def prepare_exports(self, project_id: str, include_proxies: bool = False) -> dict:
        project = self.get_project(project_id)
        if not project.get("plan"):
            raise ProjectError("This project does not have a compiled editable timeline")
        script = PIPELINE_DIR / "export_timelines.py"
        output_dir = self.settings.runtime / project_id / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        plan_path, inventory_path, media_root = self._plan_sources(project_id)
        command = [
            sys.executable,
            str(script),
            "--plan",
            str(plan_path),
            "--inventory",
            str(inventory_path),
            "--media-root",
            str(media_root),
            "--output-dir",
            str(output_dir),
            "--basename",
            "timeline",
            "--name",
            project.get("name") or project_id,
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise ProjectError(f"Timeline export failed: {detail[-1000:]}")
        outputs = {
            kind: f"/api/projects/{project_id}/outputs/{kind}"
            for kind in ("otio", "xmeml")
        }
        if include_proxies:
            self._build_resolve_proxies(project_id)
            outputs["xmeml_proxies"] = (
                f"/api/projects/{project_id}/outputs/xmeml_proxies"
            )
        try:
            self.export_captions(project_id)
            outputs["captions"] = f"/api/projects/{project_id}/outputs/captions"
        except ProjectError:
            pass  # no transcript yet; captions are optional
        return outputs

    def _build_resolve_proxies(self, project_id: str) -> None:
        """DNxHR LB proxies plus a proxy-referencing XMEML. The free Linux
        DaVinci Resolve does not decode H.264 video, so original phone
        footage imports audio-only; these proxies import fully (verified
        against Resolve 21)."""
        plan_path, inventory_path, media_root = self._plan_sources(project_id)
        plan = load_json(plan_path)
        inventory = {
            asset["asset_id"]: asset
            for asset in load_json(inventory_path)["assets"]
        }
        used_ids = {
            event["asset_id"]
            for track in plan["tracks"]
            if track["kind"] == "video"
            for event in track["events"]
            if event.get("asset_id")
        }
        output_dir = self.settings.runtime / project_id / "outputs"
        proxies_dir = output_dir / "proxies"
        proxies_dir.mkdir(parents=True, exist_ok=True)
        replacements: list[tuple[str, str]] = []
        for asset_id in sorted(used_ids):
            asset = inventory[asset_id]
            source = (media_root / asset["source_path"]).resolve()
            proxy = proxies_dir / f"{Path(asset['filename']).stem}.mov"
            if not proxy.is_file() or proxy.stat().st_mtime < source.stat().st_mtime:
                command = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(source),
                    "-c:v", "dnxhd", "-profile:v", "dnxhr_lb",
                    "-pix_fmt", "yuv422p",
                    "-c:a", "pcm_s16le", "-ar", "48000",
                    str(proxy),
                ]
                result = subprocess.run(
                    command, capture_output=True, text=True, check=False
                )
                if result.returncode:
                    raise ProjectError(
                        f"Proxy transcode failed for {asset['filename']}: "
                        f"{result.stderr.strip()[-400:]}"
                    )
            replacements.append((source.as_uri(), proxy.as_uri()))
            replacements.append(
                (f">{asset['filename']}<", f">{proxy.name}<")
            )
        # The proxies are transcoded to 48 kHz PCM; keep the declared audio
        # characteristics in sync or Resolve leaves audio items unlinked.
        replacements.append(
            ("<samplerate>44100</samplerate>", "<samplerate>48000</samplerate>")
        )
        xml_text = (output_dir / "timeline-davinci.xml").read_text(encoding="utf-8")
        for old, new in replacements:
            xml_text = xml_text.replace(old, new)
        (output_dir / "timeline-davinci-proxies.xml").write_text(
            xml_text, encoding="utf-8"
        )

    def _plan_sources(self, project_id: str) -> tuple[Path, Path, Path]:
        """Plan, inventory, and media-root paths for render/export scripts."""
        plan_dir = self.settings.runtime / project_id / "plan"
        plan_path = plan_dir / "edit-plan.json"
        if not plan_path.is_file():
            raise ProjectError("Compile an edit plan before rendering or exporting")
        return plan_path, plan_dir / "media-inventory.json", self.settings.root

    def media_path(self, project_id: str, asset_id: str) -> Path:
        project = self.get_project(project_id)
        asset = next(
            (
                item
                for item in project.get("inventory", {}).get("assets", [])
                if item["asset_id"] == asset_id
            ),
            None,
        )
        if asset is None:
            raise ProjectError(f"Unknown asset: {asset_id}")
        path = (self.settings.root / asset["source_path"]).resolve()
        return self._require_file(path)

    def frame_at(self, project_id: str, asset_id: str, timestamp: float) -> bytes:
        """A small JPEG of the asset at a given second, so review decisions
        can be made visually."""
        path = self.media_path(project_id, asset_id)
        seek = ["-ss", f"{max(0.0, min(timestamp, 36000.0)):.3f}"]
        for attempt_seek in (seek, []):  # still images reject seeking
            command = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                *attempt_seek,
                "-i", str(path),
                "-frames:v", "1",
                "-vf", "scale=360:-2",
                "-f", "image2", "-c:v", "mjpeg", "pipe:1",
            ]
            result = subprocess.run(command, capture_output=True, check=False)
            if result.returncode == 0 and result.stdout:
                return result.stdout
        raise ProjectError(f"Could not extract a frame from {asset_id}")

    def thumbnail_path(self, project_id: str, asset_id: str) -> Path:
        path = self.settings.runtime / project_id / "thumbnails" / f"{asset_id}.jpg"
        return self._require_file(path)

    def output_path(
        self, project_id: str, kind: str, artifact: str | None = None
    ) -> Path:
        runtime_outputs = self.settings.runtime / project_id / "outputs"
        if artifact is not None:
            # immutable keyed render artifact — a job's recorded result
            # must resolve to ITS render, not whatever replaced review.mp4
            if kind != "render" or not re.fullmatch(r"[a-f0-9]{8}", artifact):
                raise ProjectError("Unknown output artifact")
            return self._require_file(
                runtime_outputs / f"review.{artifact}.mp4"
            )
        mapping = {
            "render": runtime_outputs / "review.mp4",
            "otio": runtime_outputs / "timeline.otio",
            "xmeml": runtime_outputs / "timeline-davinci.xml",
            "xmeml_proxies": runtime_outputs / "timeline-davinci-proxies.xml",
            "captions": runtime_outputs / "captions.srt",
        }
        path = mapping.get(kind)
        if path is None:
            raise ProjectError(f"Unknown output type: {kind}")
        return self._require_file(path)

    def _summary(self, data: dict) -> dict:
        return {
            "project_id": data["project_id"],
            "name": data["name"],
            "status": data["status"],
            "created_at": data["created_at"],
            "asset_count": len(data.get("inventory", {}).get("assets", [])),
            "concept_count": len(data.get("concepts", [])),
            "has_plan": bool(data.get("plan")),
        }

    def _decorate_runtime_project(self, data: dict) -> dict:
        result = json.loads(json.dumps(data))
        project_id = result["project_id"]
        for asset in result.get("inventory", {}).get("assets", []):
            asset["media_url"] = f"/api/projects/{project_id}/media/{asset['asset_id']}"
            if asset.get("thumbnail_available"):
                asset["thumbnail_url"] = (
                    f"/api/projects/{project_id}/thumbnails/{asset['asset_id']}"
                )
        result["outputs"] = self._output_manifest(project_id)
        result["provider_runs"] = self._semantic_run_manifests(project_id)
        return result

    def _semantic_run_manifests(self, project_id: str) -> list[dict]:
        runs_dir = self.settings.runtime / project_id / "analysis" / "runs"
        return [
            load_json(path)
            for path in sorted(runs_dir.glob("*/manifest.json"))
            if path.is_file()
        ]

    def _semantic_run_path(self, project_id: str, run_key: str) -> Path:
        self.get_project(project_id)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,160}", run_key):
            raise ProjectError("Invalid semantic run id")
        return (
            self.settings.runtime
            / project_id
            / "analysis"
            / "runs"
            / run_key
            / "normalized.json"
        )

    def _selection(self, project_id: str) -> dict | None:
        path = self.settings.runtime / project_id / "selection.json"
        return load_json(path) if path.is_file() else None

    def _output_manifest(self, project_id: str) -> dict:
        result = {}
        for kind in ("render", "otio", "xmeml", "xmeml_proxies"):
            try:
                path = self.output_path(project_id, kind)
            except ProjectError:
                continue
            result[kind] = {
                "url": f"/api/projects/{project_id}/outputs/{kind}",
                "filename": path.name,
                "size_bytes": path.stat().st_size,
            }
        # Freshness: a render can be older than the plan (apply/restore
        # commit the plan before rendering; a failed render leaves the old
        # file). The UI must be able to say "corte anterior" honestly
        # (cross-review UX finding 8).
        if "render" in result:
            fresh = None
            state_path = (
                self.settings.runtime / project_id / "outputs"
                / "review.render-state.json"
            )
            plan_path = self.settings.runtime / project_id / "plan" / "edit-plan.json"
            if state_path.is_file() and plan_path.is_file():
                try:
                    plan_sha = hashlib.sha256(
                        json.dumps(load_json(plan_path), sort_keys=True).encode()
                    ).hexdigest()[:16]
                    fresh = load_json(state_path).get("plan_sha") == plan_sha
                except (OSError, ValueError):
                    fresh = None
            result["render"]["fresh"] = fresh
        return result

    def _resolve_source_directory(self, value: str) -> Path:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = self.settings.root / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(self.settings.root)
        except ValueError as exc:
            raise ProjectError("Source directory must be inside the video-editing workspace") from exc
        if not candidate.is_dir():
            raise ProjectError(f"Source directory does not exist: {value}")
        return candidate

    def _probe_asset(self, asset_id: str, path: Path) -> dict:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise ProjectError(f"ffprobe failed for {path.name}: {result.stderr.strip()}")
        probe = json.loads(result.stdout)
        tags = {
            key.lower(): value
            for key, value in (probe.get("format", {}).get("tags") or {}).items()
        }
        recorded_at = (
            tags.get("com.apple.quicktime.creationdate")  # local timezone
            or tags.get("creation_time")
        )
        location = None
        iso6709 = tags.get("com.apple.quicktime.location.iso6709")
        if iso6709:
            match = re.match(r"([+-]\d+\.?\d*)([+-]\d+\.?\d*)", iso6709)
            if match:
                location = {
                    "latitude": float(match.group(1)),
                    "longitude": float(match.group(2)),
                }
        device = tags.get("com.apple.quicktime.model") or tags.get("model")
        streams = probe.get("streams", [])
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        duration_values = [
            probe.get("format", {}).get("duration"),
            video.get("duration") if video else None,
            audio.get("duration") if audio else None,
        ]
        duration = next((float(value) for value in duration_values if value not in (None, "N/A")), 0.0)
        media_type = (
            "image"
            if path.suffix.lower() in IMAGE_EXTENSIONS
            else "video"
            if video is not None
            else "audio"
        )
        return {
            "asset_id": asset_id,
            "filename": path.name,
            "source_path": str(path.relative_to(self.settings.root)),
            "media_type": media_type,
            "size_bytes": path.stat().st_size,
            "sha256": self._sha256(path),
            "duration_seconds": round(duration, 6),
            "recorded_at": recorded_at,
            "location": location,
            "device": device,
            "video": (
                {
                    "codec": video.get("codec_name"),
                    "width": video.get("width"),
                    "height": video.get("height"),
                    "pixel_format": video.get("pix_fmt"),
                    "average_frame_rate": video.get("avg_frame_rate"),
                }
                if video
                else None
            ),
            "audio": (
                {
                    "codec": audio.get("codec_name"),
                    "sample_rate": int(audio["sample_rate"]) if audio.get("sample_rate") else None,
                    "channels": audio.get("channels"),
                    "channel_layout": audio.get("channel_layout"),
                }
                if audio
                else None
            ),
            "analysis_status": "technical_only",
            "thumbnail_available": False,
        }

    def _make_thumbnail(self, path: Path, asset: dict, output: Path) -> bool:
        if asset["media_type"] == "audio":
            return False
        seek = max(asset.get("duration_seconds", 0) / 2, 0)
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        if asset["media_type"] == "video":
            command.extend(["-ss", str(seek)])
        command.extend(
            [
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                "scale=480:480:force_original_aspect_ratio=decrease,pad=480:480:(ow-iw)/2:(oh-ih)/2:color=111318",
                str(output),
            ]
        )
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        return result.returncode == 0 and output.is_file()

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _require_file(path: Path) -> Path:
        if not path.is_file():
            raise ProjectError(f"File is not available: {path.name}")
        return path
