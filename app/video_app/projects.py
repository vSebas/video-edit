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
import tomllib
from pathlib import Path
from typing import Any

from .config import Settings


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg"}
SUPPORTED_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS
FIXTURE_ID = "morning-routine"


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

    def capabilities(self) -> dict:
        openstoryline_config = self.settings.root / "repos/FireRed-OpenStoryline/config.toml"
        openstoryline = {
            "id": "openstoryline",
            "label": "OpenStoryline visual planner",
            "ready": False,
            "detail": "A real LLM/VLM configuration is required.",
        }
        if openstoryline_config.is_file():
            try:
                data = tomllib.loads(openstoryline_config.read_text(encoding="utf-8"))
                configured = all(
                    bool(data.get(section, {}).get(field))
                    for section in ("llm", "vlm")
                    for field in ("model", "base_url", "api_key")
                )
                openstoryline["ready"] = configured
                openstoryline["detail"] = (
                    "LLM/VLM configuration is present."
                    if configured
                    else "Source and model resources are present; LLM/VLM credentials are not configured."
                )
            except (OSError, tomllib.TOMLDecodeError):
                pass

        faster_whisper = importlib.util.find_spec("faster_whisper") is not None
        return {
            "visual": [
                {
                    "id": "reviewed-fixture",
                    "label": "Reviewed fixture observations",
                    "ready": True,
                    "detail": "Available only for the completed morning-routine benchmark.",
                },
                openstoryline,
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
                {
                    "id": "cutscript-reference",
                    "label": "CutScript transcript workflow",
                    "ready": (self.settings.root / "repos/CutScript").is_dir(),
                    "detail": "Pinned source is available as a component and UI reference.",
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
        projects = [self._fixture_summary()]
        for path in sorted(self.settings.runtime.glob("*/project.json")):
            data = load_json(path)
            projects.append(self._summary(data))
        return projects

    def get_project(self, project_id: str) -> dict:
        if project_id == FIXTURE_ID:
            return self._fixture()
        path = self.settings.runtime / project_id / "project.json"
        if not path.is_file():
            raise ProjectError(f"Unknown project: {project_id}")
        data = load_json(path)
        return self._decorate_runtime_project(data)

    def create_project(self, name: str, source_directory: str, prompt: str) -> dict:
        project_id = slugify(name)
        if project_id == FIXTURE_ID:
            raise ProjectError(f"The project id '{FIXTURE_ID}' is reserved")
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
            "plan_available": project.get("plan", {}).get("concept_id") == concept_id,
        }
        write_json(self.settings.runtime / project_id / "selection.json", selection)
        return selection

    def render(self, project_id: str) -> dict:
        project = self.get_project(project_id)
        plan = project.get("plan")
        if not plan:
            raise ProjectError("This project does not have an approved edit plan")
        selection = self._selection(project_id)
        selected = selection.get("concept_id") if selection else plan["concept_id"]
        if selected != plan["concept_id"]:
            raise ProjectError(
                "The selected concept has no compiled edit plan yet; select the chronological concept"
            )
        if project_id != FIXTURE_ID:
            raise ProjectError("Generic project rendering will be enabled after semantic planning")

        output_dir = self.settings.runtime / project_id / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / "review.mp4"
        script = self.settings.poc_root / "scripts/render_reference_edit.py"
        plan_path = self.settings.poc_root / "artifacts/edit-plan.json"
        command = [
            sys.executable,
            str(script),
            "--plan",
            str(plan_path),
            "--output",
            str(output),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise ProjectError(f"Render failed: {detail[-1000:]}")
        return {
            "output": f"/api/projects/{project_id}/outputs/render",
            "path": str(output),
        }

    def prepare_exports(self, project_id: str) -> dict:
        if project_id != FIXTURE_ID:
            raise ProjectError("This project does not have a compiled editable timeline")
        script = self.settings.poc_root / "scripts/export_timelines.py"
        result = subprocess.run(
            [sys.executable, str(script)], capture_output=True, text=True, check=False
        )
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise ProjectError(f"Timeline export failed: {detail[-1000:]}")
        source_dir = self.settings.poc_root / "artifacts/timelines"
        output_dir = self.settings.runtime / project_id / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        outputs = {
            "otio": (source_dir / "morning-routine.otio", output_dir / "timeline.otio"),
            "xmeml": (
                source_dir / "morning-routine-davinci.xml",
                output_dir / "timeline-davinci.xml",
            ),
        }
        for source, destination in outputs.values():
            shutil.copy2(source, destination)
        return {
            key: f"/api/projects/{project_id}/outputs/{key}" for key in outputs
        }

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
        if project_id == FIXTURE_ID:
            path = (self.settings.poc_root / asset["source_path"]).resolve()
        else:
            path = (self.settings.root / asset["source_path"]).resolve()
        return self._require_file(path)

    def thumbnail_path(self, project_id: str, asset_id: str) -> Path:
        if project_id == FIXTURE_ID:
            analysis = self.settings.poc_root / "artifacts/assets" / asset_id / "analysis.json"
            data = load_json(analysis)
            if not data.get("keyframes"):
                raise ProjectError(f"No thumbnail for asset: {asset_id}")
            path = (self.settings.poc_root / data["keyframes"][0]["path"]).resolve()
        else:
            path = self.settings.runtime / project_id / "thumbnails" / f"{asset_id}.jpg"
        return self._require_file(path)

    def output_path(self, project_id: str, kind: str) -> Path:
        runtime_outputs = self.settings.runtime / project_id / "outputs"
        mapping = {
            "render": runtime_outputs / "review.mp4",
            "otio": runtime_outputs / "timeline.otio",
            "xmeml": runtime_outputs / "timeline-davinci.xml",
        }
        path = mapping.get(kind)
        if kind == "render" and (path is None or not path.is_file()) and project_id == FIXTURE_ID:
            docker_render = (
                self.settings.poc_root
                / "artifacts/reference-edit/morning-routine-review-docker.mp4"
            )
            original_render = (
                self.settings.poc_root / "artifacts/reference-edit/morning-routine-review.mp4"
            )
            path = docker_render if docker_render.is_file() else original_render
        if path is None:
            raise ProjectError(f"Unknown output type: {kind}")
        return self._require_file(path)

    def _fixture_summary(self) -> dict:
        data = self._fixture()
        return self._summary(data)

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

    def _fixture(self) -> dict:
        inventory = load_json(self.settings.poc_root / "artifacts/media-inventory.json")
        concepts_data = load_json(self.settings.poc_root / "artifacts/creative-concepts.json")
        plan = load_json(self.settings.poc_root / "artifacts/edit-plan.json")
        analyses = {}
        for asset in inventory["assets"]:
            path = (
                self.settings.poc_root
                / "artifacts/assets"
                / asset["asset_id"]
                / "analysis.json"
            )
            if path.is_file():
                analyses[asset["asset_id"]] = load_json(path)
        assets = []
        for asset in inventory["assets"]:
            item = dict(asset)
            item["media_url"] = f"/api/projects/{FIXTURE_ID}/media/{asset['asset_id']}"
            analysis = analyses.get(asset["asset_id"], {})
            if analysis.get("keyframes"):
                item["thumbnail_url"] = (
                    f"/api/projects/{FIXTURE_ID}/thumbnails/{asset['asset_id']}"
                )
            item["semantic_observations"] = analysis.get("semantic_observations", [])
            item["transcript"] = analysis.get("transcript")
            assets.append(item)
        selection = self._selection(FIXTURE_ID)
        selected = selection.get("concept_id") if selection else plan["concept_id"]
        outputs = self._output_manifest(FIXTURE_ID)
        return {
            "schema_version": "video-app-project.v1",
            "project_id": FIXTURE_ID,
            "name": "Morning Routine POC",
            "created_at": concepts_data["generated_at"],
            "updated_at": concepts_data["generated_at"],
            "source_directory": "Crayotter/crayotter-data/user_temp",
            "prompt": "Create a concise Instagram Reel or TikTok from the supplied morning-routine footage.",
            "status": "ready",
            "footage_summary": concepts_data["footage_summary"],
            "analysis": {
                "technical": "completed",
                "visual": "reviewed",
                "speech": "unavailable",
                "warning": "No spoken-content claims are used; visual observations were independently checked.",
            },
            "inventory": {"assets": assets},
            "concepts": concepts_data["concepts"],
            "selected_concept_id": selected,
            "plan": plan,
            "plan_summary": {
                "concept_id": plan["concept_id"],
                "duration_seconds": plan["project"]["duration_seconds"],
                "format": f"{plan['project']['width']}x{plan['project']['height']} @ {plan['project']['fps']}fps",
                "tracks": {
                    track["kind"]: len(track["events"]) for track in plan["tracks"]
                },
            },
            "outputs": outputs,
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
        return result

    def _selection(self, project_id: str) -> dict | None:
        path = self.settings.runtime / project_id / "selection.json"
        return load_json(path) if path.is_file() else None

    def _output_manifest(self, project_id: str) -> dict:
        result = {}
        for kind in ("render", "otio", "xmeml"):
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
