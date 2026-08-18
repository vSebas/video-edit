from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

from .config import Settings
from .planning import (
    PlanningError,
    compile_edit_plan,
    generate_concepts,
    revise_plan,
    validate_edit_plan,
)
from .providers import ChatClient, ProviderError, make_client, resolve_provider
from .semantic import SemanticEvidenceError, validate_semantic_evidence
from .visual import VisualAnalysisError, analyze_assets, auto_review_decisions


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg"}
SUPPORTED_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS
# Writer chosen by blind video screening (bench/planner, 2026-08-17): the
# user preferred deepseek's and fable's cuts over both Qwens'; deepseek is
# the default (available on the existing workspace key).
PLANNER_DEFAULT_MODELS = {"qwen": "deepseek-v4-pro"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value[:60] or "video-project"


class ProjectError(RuntimeError):
    pass


class ProjectService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.runtime.mkdir(parents=True, exist_ok=True)
        self._semantic_review_lock = threading.Lock()

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
                "id": "otio-xmeml-opentake",
                "label": "OTIO, DaVinci XMEML, OpenTake",
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
            os.replace(staging, final_dir)
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
        "runtime", ".tmp", ".claude",
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
            normalized, raw_records = analyze_assets(
                client, assets, media_root, project_id, run_id
            )
            validate_semantic_evidence(
                normalized,
                self.settings.poc_root / "schemas" / "semantic-evidence.schema.json",
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
        self._mark_semantic_progress(project_id, "visual")
        return self.semantic_run(project_id, run_key)

    def analyze_speech(self, project_id: str, model_size: str | None = None) -> dict:
        """Run local timestamped ASR over every asset with audio and persist
        the transcript as a semantic evidence run."""
        from .speech import SpeechAnalysisError, analyze_speech

        project = self.get_project(project_id)
        assets = project.get("inventory", {}).get("assets", [])
        if not assets:
            raise ProjectError("The project has no indexed media to analyze")
        media_root = self.settings.root

        run_id = uuid.uuid4().hex[:12]
        run_key = f"asr-live-{run_id}"
        try:
            normalized, raw_records = analyze_speech(
                assets, media_root, project_id, run_id, model_size
            )
            validate_semantic_evidence(
                normalized,
                self.settings.poc_root / "schemas" / "semantic-evidence.schema.json",
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
        from .visual import AUTO_APPROVE_MIN_CONFIDENCE

        words = self._speech_words(project_id)
        if not words:
            return 0
        corroborated = 0
        for manifest in self._current_run_manifests(project_id):
            if manifest["provider"]["adapter"] != "owned-live-visual":
                continue
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
                asset_words = words.get(observation["asset_id"]) or []
                if not any(
                    word["start_seconds"] < observation["end_seconds"]
                    and word["end_seconds"] > observation["start_seconds"]
                    for word in asset_words
                ):
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
        stored = load_json(path)
        stored["updated_at"] = utc_now()
        stored["analysis"][kind] = "completed"
        pending_risky = 0
        for manifest in self._semantic_run_manifests(project_id):
            run = self.semantic_run(project_id, manifest["run_key"])
            pending_risky += run["summary"].get("pending_review_count", 0)
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

    def _current_run_manifests(self, project_id: str) -> list[dict]:
        """Newest run per provider adapter — re-analysis supersedes older
        runs so evidence never accumulates stale duplicates."""
        latest: dict[str, dict] = {}
        for manifest in self._semantic_run_manifests(project_id):
            adapter = manifest["provider"]["adapter"]
            if (
                adapter not in latest
                or manifest["imported_at"] > latest[adapter]["imported_at"]
            ):
                latest[adapter] = manifest
        return list(latest.values())

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
    ) -> dict:
        """Generate grounded creative concepts with missing-shot advice from
        the project's approved evidence. Kept concepts survive regeneration;
        guidance steers the new ones."""
        project = self.get_project(project_id)
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
            client = ChatClient(resolve_provider(
                provider, model or PLANNER_DEFAULT_MODELS.get(provider)
            ))
            document = generate_concepts(
                client,
                project,
                evidence,
                guidance=guidance,
                keep_concepts=keep_concepts,
                footage_language=self._footage_language(project_id),
            )
            self._validate_schema(
                document,
                self.settings.poc_root / "schemas" / "creative-concepts.schema.json",
                "Creative concepts",
            )
        except (ProviderError, PlanningError) as exc:
            raise ProjectError(f"Concept generation failed: {exc}") from exc

        write_json(
            self.settings.runtime / project_id / "analysis" / "concepts.json", document
        )
        path = self.settings.runtime / project_id / "project.json"
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
        approved_ranges: dict[str, list[tuple[float, float]]] = {}
        for item in self.approved_evidence(project_id):
            approved_ranges.setdefault(item["asset_id"], []).append(
                (item["start_seconds"], item["end_seconds"])
            )
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
            )
            validate_edit_plan(
                plan,
                self.settings.poc_root / "schemas" / "edit-plan.schema.json",
                project,
            )
        except PlanningError as exc:
            raise ProjectError(f"Plan compilation failed: {exc}") from exc

        plan_dir = self.settings.runtime / project_id / "plan"
        write_json(plan_dir / "edit-plan.json", plan)
        write_json(
            plan_dir / "media-inventory.json",
            {"assets": project["inventory"]["assets"]},
        )
        path = self.settings.runtime / project_id / "project.json"
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
            client = ChatClient(resolve_provider(
                provider, model or PLANNER_DEFAULT_MODELS.get(provider)
            ))
            new_plan, note = revise_plan(
                client,
                project,
                plan,
                evidence,
                instruction,
                speech_words=self._speech_words(project_id),
                footage_language=self._footage_language(project_id),
            )
            validate_edit_plan(
                new_plan,
                self.settings.poc_root / "schemas" / "edit-plan.schema.json",
                project,
            )
        except (ProviderError, PlanningError) as exc:
            raise ProjectError(f"Plan revision failed: {exc}") from exc

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

    def _footage_language(self, project_id: str) -> str | None:
        """Dominant detected speech language from the most recent ASR run,
        weighted by transcribed duration."""
        runs_dir = self.settings.runtime / project_id / "analysis" / "runs"
        candidates = sorted(runs_dir.glob("asr-live-*/raw/transcripts.json"))
        if not candidates:
            return None
        weights: dict[str, float] = {}
        for record in load_json(candidates[-1]).get("transcripts", []):
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
        has_runs = bool(self._semantic_run_manifests(project_id))
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
        runs_dir = self.settings.runtime / project_id / "analysis" / "runs"
        candidates = sorted(runs_dir.glob("asr-live-*/raw/transcripts.json"))
        if not candidates:
            raise ProjectError("Run speech analysis before exporting captions")
        segments_by_asset: dict[str, list[dict]] = {}
        for record in load_json(candidates[-1]).get("transcripts", []):
            segments_by_asset[record["asset_id"]] = record.get("segments", [])

        entries: list[tuple[float, float, str]] = []
        video_events = next(
            track["events"] for track in plan["tracks"] if track["kind"] == "video"
        )
        for event in video_events:
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
        runs_dir = self.settings.runtime / project_id / "analysis" / "runs"
        candidates = sorted(runs_dir.glob("asr-live-*/raw/transcripts.json"))
        if not candidates:
            return {}
        words: dict[str, list[dict]] = {}
        for record in load_json(candidates[-1]).get("transcripts", []):
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

    def semantic_run(self, project_id: str, run_key: str) -> dict:
        self.get_project(project_id)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,160}", run_key):
            raise ProjectError("Invalid semantic run id")
        path = self._semantic_run_path(project_id, run_key)
        result = load_json(self._require_file(path))
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

    def render(self, project_id: str) -> dict:
        project = self.get_project(project_id)
        plan = project.get("plan")
        if not plan:
            raise ProjectError("This project does not have an approved edit plan")
        selection = self._selection(project_id)
        selected = selection.get("concept_id") if selection else plan["concept_id"]
        if selected != plan["concept_id"]:
            raise ProjectError(
                "The selected concept has no compiled edit plan yet; compile it first"
            )
        output_dir = self.settings.runtime / project_id / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / "review.mp4"
        script = self.settings.poc_root / "scripts/render_reference_edit.py"
        plan_path, inventory_path, media_root = self._plan_sources(project_id)
        command = [
            sys.executable,
            str(script),
            "--plan",
            str(plan_path),
            "--output",
            str(output),
            "--inventory",
            str(inventory_path),
            "--media-root",
            str(media_root),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise ProjectError(f"Render failed: {detail[-1000:]}")
        return {
            "output": f"/api/projects/{project_id}/outputs/render",
            "path": str(output),
        }

    def prepare_exports(self, project_id: str, include_proxies: bool = False) -> dict:
        project = self.get_project(project_id)
        if not project.get("plan"):
            raise ProjectError("This project does not have a compiled editable timeline")
        script = self.settings.poc_root / "scripts/export_timelines.py"
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
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", f"{max(0.0, min(timestamp, 36000.0)):.3f}",
            "-i", str(path),
            "-frames:v", "1",
            "-vf", "scale=360:-2",
            "-f", "image2", "-c:v", "mjpeg", "pipe:1",
        ]
        result = subprocess.run(command, capture_output=True, check=False)
        if result.returncode or not result.stdout:
            raise ProjectError(f"Could not extract a frame from {asset_id}")
        return result.stdout

    def thumbnail_path(self, project_id: str, asset_id: str) -> Path:
        path = self.settings.runtime / project_id / "thumbnails" / f"{asset_id}.jpg"
        return self._require_file(path)

    def output_path(self, project_id: str, kind: str) -> Path:
        runtime_outputs = self.settings.runtime / project_id / "outputs"
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
