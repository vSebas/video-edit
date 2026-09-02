from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Live receiver-side progress for in-flight uploads, keyed by upload id.
ACTIVE_UPLOADS: dict[str, dict] = {}


def uuid_hex(length: int) -> str:
    return uuid.uuid4().hex[:length]

from .config import Settings
from .jobs import JobManager
from .projects import ProjectError, ProjectService, slugify


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source_directory: str = Field(min_length=1)
    prompt: str = Field(default="", max_length=4000)


class SelectConceptRequest(BaseModel):
    concept_id: str = Field(min_length=1)


class AnalyzeVisualRequest(BaseModel):
    provider: str = Field(default="gemini", pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    model: str | None = Field(default=None, max_length=160)
    force: bool = False


class AnalyzeSpeechRequest(BaseModel):
    model_size: str | None = Field(default=None, pattern=r"^[a-z0-9.-]{1,40}$")
    force: bool = False


class AnalyzeContextRequest(BaseModel):
    model: str | None = Field(default=None, max_length=160)
    force: bool = False


class GenerateConceptsRequest(BaseModel):
    provider: str = Field(default="qwen", pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    model: str | None = Field(default=None, max_length=160)
    guidance: str | None = Field(default=None, max_length=2000)
    keep_concept_ids: list[str] | None = Field(default=None, max_length=10)
    use_source_context: bool = False
    style_id: str | None = Field(default=None, max_length=64)


class AnalyzeStyleReferenceRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    name: str | None = Field(default=None, max_length=80)


class CompilePlanRequest(BaseModel):
    concept_id: str | None = Field(default=None, max_length=160)
    width: int = Field(default=1080, ge=16, le=7680)
    height: int = Field(default=1920, ge=16, le=7680)
    fps: int = Field(default=30, ge=1, le=120)


class ExportsRequest(BaseModel):
    include_proxies: bool = False


class CloneProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(default="", max_length=4000)


class ResetProjectRequest(BaseModel):
    keep_analysis: bool = True


class RevisePlanRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=2000)
    provider: str = Field(default="qwen", pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    model: str | None = Field(default=None, max_length=160)


class DriveImportRequest(BaseModel):
    folder: str = Field(min_length=1, max_length=200)


class CleanupApplyRequest(BaseModel):
    indices: list[int] = Field(min_length=1, max_length=200)
    readback: dict | None = None


class PlanCommandRequest(BaseModel):
    instruction: str = Field(min_length=2, max_length=500)


class OpenTakeSyncRequest(BaseModel):
    # Host-side callers (CLI/browser) pass the get_timeline readback; when
    # omitted the server fetches it over MCP itself (host-run app only —
    # the container cannot reach OpenTake's loopback listener).
    readback: dict | None = None


class ReviewSemanticEvidenceRequest(BaseModel):
    evidence_id: str = Field(min_length=1, max_length=160)
    action: Literal["approve", "reject"]
    caption: str | None = Field(default=None, max_length=4000)
    note: str | None = Field(default=None, max_length=1000)


def create_app(settings: Settings | None = None) -> FastAPI:
    current_settings = settings or Settings.from_environment()
    projects = ProjectService(current_settings)
    jobs = JobManager(store=current_settings.runtime / "jobs.json")
    application = FastAPI(title="Local Video Editing Workbench", version="0.1.0")

    access_token = os.environ.get("VIDEO_EDITING_TOKEN", "").strip()

    @application.middleware("http")
    async def require_token(request: Request, call_next):
        """Opt-in shared-secret gate for non-local access.

        Unset (the default) leaves the app open, which is fine bound to
        localhost. Set it before binding to 0.0.0.0: everything here —
        footage, deletion, paid model calls — is otherwise reachable by
        anyone who can route to the port.
        """
        if not access_token or request.url.path == "/api/health":
            return await call_next(request)
        supplied = (
            request.headers.get("x-vlog-token")
            or request.query_params.get("token")
            or request.cookies.get("vlog_token")
            or ""
        )
        if not secrets.compare_digest(supplied, access_token):
            return Response(status_code=401, content="Unauthorized")
        response = await call_next(request)
        if request.query_params.get("token"):
            # Let a phone open one bookmarked ?token=… URL and keep browsing.
            response.set_cookie(
                "vlog_token", access_token, httponly=True, samesite="lax", max_age=31536000
            )
        return response

    @application.middleware("http")
    async def track_upload_progress(request: Request, call_next):
        # Count bytes as they truly arrive from the network (the framework
        # buffers the body before handlers run, so counting must happen at
        # the ASGI receive layer).
        if request.method == "POST" and "/uploads" in request.url.path:
            upload_id = uuid_hex(8)
            total = int(request.headers.get("content-length") or 0)
            ACTIVE_UPLOADS[upload_id] = {
                "label": "subida entrante", "received": 0, "total": total,
            }
            original_receive = request.receive

            async def counting_receive():
                message = await original_receive()
                if message.get("type") == "http.request":
                    ACTIVE_UPLOADS[upload_id]["received"] += len(
                        message.get("body", b"")
                    )
                return message

            request._receive = counting_receive
            try:
                return await call_next(request)
            finally:
                ACTIVE_UPLOADS.pop(upload_id, None)
        return await call_next(request)

    def project_call(operation):
        try:
            return operation()
        except ProjectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.get("/api/health")
    def health():
        return {"status": "ok"}

    @application.get("/api/status")
    def status():
        return {
            "application": "Local Video Editing Workbench",
            "version": "0.1.0",
            "workspace": str(current_settings.root),
            "capabilities": projects.capabilities(),
        }

    @application.get("/api/projects")
    def list_projects():
        return {"projects": projects.list_projects()}

    @application.post("/api/projects", status_code=201)
    def create_project(request: CreateProjectRequest):
        return project_call(
            lambda: projects.create_project(
                request.name, request.source_directory, request.prompt
            )
        )

    @application.get("/api/projects/{project_id}")
    def get_project(project_id: str):
        return project_call(lambda: projects.get_project(project_id))

    @application.delete("/api/projects/{project_id}")
    def delete_project(project_id: str):
        return project_call(lambda: projects.delete_project(project_id))

    @application.post("/api/projects/{project_id}/clone", status_code=201)
    def clone_project(project_id: str, request: CloneProjectRequest):
        return project_call(
            lambda: projects.clone_project(project_id, request.name, request.prompt)
        )

    @application.post("/api/projects/{project_id}/reset")
    def reset_project(project_id: str, request: ResetProjectRequest | None = None):
        options = request or ResetProjectRequest()
        return project_call(
            lambda: projects.reset_project(project_id, options.keep_analysis)
        )

    @application.get("/api/projects/{project_id}/clip-scores")
    def clip_scores(project_id: str):
        return {"clips": project_call(lambda: projects.clip_scores(project_id))}

    async def _save_uploads(
        request: Request, files: list[UploadFile], target: Path, label: str
    ) -> int:
        saved = 0
        for upload in files:
            suffix = Path(upload.filename or "clip.mp4").suffix.lower()
            if suffix not in {".mp4", ".mov", ".m4v", ".jpg", ".jpeg", ".png", ".m4a", ".wav", ".mp3"}:
                continue
            safe_name = re.sub(
                r"[^A-Za-z0-9._-]", "_", upload.filename or f"clip{saved}{suffix}"
            )
            destination = target / safe_name
            if destination.exists():
                destination = target / f"{destination.stem}-{uuid_hex(6)}{destination.suffix}"
            with destination.open("wb") as handle:
                while chunk := await upload.read(8 * 1024 * 1024):
                    handle.write(chunk)
            saved += 1
        return saved

    @application.post("/api/uploads/item", status_code=200)
    async def upload_item(
        request: Request,
        name: str = Form(min_length=1, max_length=120),
        files: list[UploadFile] = File(...),
    ):
        """One-clip-per-request flow for iOS Shortcuts (whose requests time
        out around a minute): the first item creates the project, subsequent
        items append to it. Safe to loop over an entire day."""
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "upload"
        target = current_settings.root / "footage" / slug
        target.mkdir(parents=True, exist_ok=True)
        saved = await _save_uploads(request, files, target, f"clip para «{name}»")
        if not saved:
            raise HTTPException(status_code=400, detail="Unsupported file")
        try:
            projects.get_project(slug)
            result = projects.sync_media(slug)
            return {"project_id": slug, "clips": result["total"], "created": False}
        except ProjectError:
            project = project_call(
                lambda: projects.create_project(name, f"footage/{slug}", "")
            )
            return {
                "project_id": project["project_id"],
                "clips": len(project["inventory"]["assets"]),
                "created": True,
            }

    @application.get("/api/drive/inbox")
    def drive_inbox():
        return {"folders": project_call(projects.drive_inbox)}

    @application.post("/api/drive/import", status_code=202)
    def drive_import(request: DriveImportRequest):
        return jobs.submit(
            "drive_import",
            slugify(request.folder),
            lambda: projects.import_drive_folder(request.folder),
        )

    @application.get("/api/uploads/active")
    def active_uploads():
        return {"uploads": list(ACTIVE_UPLOADS.values())}

    @application.post("/api/uploads", status_code=201)
    async def upload_project(
        request: Request,
        name: str = Form(min_length=1, max_length=120),
        prompt: str = Form(default=""),
        files: list[UploadFile] = File(...),
    ):
        """Create a project from files sent by a phone/browser: media lands
        in footage/<slug>/ and the normal indexing flow takes over."""
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "upload"
        target = current_settings.root / "footage" / slug
        if target.exists():
            raise HTTPException(status_code=400, detail=f"Folder already exists: footage/{slug}")
        target.mkdir(parents=True)
        saved = await _save_uploads(request, files, target, f"nuevo vlog «{name}»")
        if not saved:
            target.rmdir()
            raise HTTPException(status_code=400, detail="No supported media files were uploaded")
        try:
            return project_call(
                lambda: projects.create_project(name, f"footage/{slug}", prompt)
            )
        except HTTPException:
            shutil.rmtree(target, ignore_errors=True)
            raise

    @application.post("/api/projects/{project_id}/uploads", status_code=200)
    async def upload_to_project(
        request: Request,
        project_id: str,
        files: list[UploadFile] = File(...),
    ):
        """Add clips or voiceover recordings to an existing project's folder
        and reconcile the inventory."""
        project = project_call(lambda: projects.get_project(project_id))
        target = current_settings.root / project["source_directory"]
        saved = await _save_uploads(request, files, target, f"clips para «{project['name']}»")
        if not saved:
            raise HTTPException(status_code=400, detail="No supported media files were uploaded")
        return project_call(lambda: projects.sync_media(project_id))

    @application.post("/api/projects/{project_id}/sync-media")
    def sync_media(project_id: str):
        project_call(lambda: projects.get_project(project_id))
        return project_call(lambda: projects.sync_media(project_id))

    @application.delete("/api/projects/{project_id}/assets/{asset_id}")
    def remove_asset(project_id: str, asset_id: str, delete_file: bool = False):
        project_call(lambda: projects.get_project(project_id))
        return project_call(
            lambda: projects.remove_asset(project_id, asset_id, delete_file)
        )

    @application.get("/api/browse")
    def browse(path: str = ""):
        return project_call(lambda: projects.browse_directories(path))

    @application.post("/api/projects/{project_id}/selection")
    def select_concept(project_id: str, request: SelectConceptRequest):
        return project_call(
            lambda: projects.select_concept(project_id, request.concept_id)
        )

    @application.post("/api/projects/{project_id}/render", status_code=202)
    def render(project_id: str, burn_captions: bool = False):
        project_call(lambda: projects.get_project(project_id))
        return jobs.submit(
            "render", project_id,
            lambda: projects.render(project_id, burn_captions=burn_captions),
            fingerprint=f"captions={burn_captions}",
        )

    @application.get("/api/styles")
    def list_styles():
        return {"styles": projects.list_styles(include_invalid=True)}

    @application.delete("/api/styles/{style_id}")
    def delete_style(style_id: str):
        project_call(lambda: projects.delete_style(style_id))
        return {"deleted": style_id}

    @application.get("/api/styles/references")
    def list_style_references():
        return {"references": projects.list_style_references()}

    @application.post("/api/styles/analyze", status_code=202)
    def analyze_style_reference(request: AnalyzeStyleReferenceRequest):
        from .style_intelligence import STYLE_PROMPT_VERSION

        # content identity (size+mtime) so replacing the file or renaming
        # the style is a new job, not a dedup hit on the old analysis.
        # Best-effort: a same-size same-mtime swap or a swap between stat
        # and analysis start is not caught — the observation's sha256
        # records what was actually analyzed
        try:
            stat = (
                projects._references_dir() / Path(request.filename).name
            ).stat()
            identity = f"{stat.st_size}:{stat.st_mtime_ns}"
        except OSError:
            identity = "missing"
        return jobs.submit(
            "style_analysis",
            "styles",
            lambda: projects.analyze_style_reference(
                request.filename, request.name
            ),
            fingerprint=(
                f"{Path(request.filename).name}:{request.name}:"
                f"{identity}:{STYLE_PROMPT_VERSION}"
            ),
        )

    @application.post("/api/projects/{project_id}/style-matches")
    def style_matches(project_id: str):
        return project_call(lambda: projects.style_matches(project_id))

    @application.get("/api/projects/{project_id}/plan/revisions")
    def plan_revisions(project_id: str):
        project_call(lambda: projects.get_project(project_id))
        log_path = (
            current_settings.runtime / project_id / "plan" / "revisions"
            / "revision-log.json"
        )
        if not log_path.is_file():
            return {"entries": []}
        try:
            import json as json_module

            return json_module.loads(log_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"entries": []}

    @application.post("/api/projects/{project_id}/plan/revisions/{revision}/restore")
    def plan_restore_revision(project_id: str, revision: int):
        return project_call(
            lambda: projects.plan_restore_revision(project_id, revision)
        )

    @application.post("/api/projects/{project_id}/plan/command")
    def plan_command_propose(project_id: str, request: PlanCommandRequest):
        return project_call(
            lambda: projects.plan_command_propose(project_id, request.instruction)
        )

    @application.post("/api/projects/{project_id}/plan/command/apply")
    def plan_command_apply(project_id: str, proposal_id: str | None = None):
        return project_call(
            lambda: projects.plan_command_apply(project_id, proposal_id)
        )

    @application.post("/api/projects/{project_id}/opentake/place")
    def opentake_place(project_id: str):
        return project_call(lambda: projects.opentake_place(project_id))

    @application.post("/api/projects/{project_id}/opentake/cleanup")
    def opentake_cleanup_candidates(project_id: str, request: OpenTakeSyncRequest | None = None):
        payload = request or OpenTakeSyncRequest()
        return project_call(
            lambda: projects.opentake_cleanup_candidates(project_id, payload.readback)
        )

    @application.post("/api/projects/{project_id}/opentake/cleanup/apply")
    def opentake_cleanup_apply(project_id: str, request: CleanupApplyRequest):
        return project_call(
            lambda: projects.opentake_cleanup_apply(
                project_id, request.indices, request.readback
            )
        )

    @application.post("/api/projects/{project_id}/opentake/sync")
    def opentake_sync_preview(project_id: str, request: OpenTakeSyncRequest | None = None):
        payload = request or OpenTakeSyncRequest()
        return project_call(
            lambda: projects.opentake_sync_preview(project_id, payload.readback)
        )

    @application.post("/api/projects/{project_id}/opentake/sync/apply")
    def opentake_sync_apply(project_id: str):
        return project_call(lambda: projects.opentake_sync_apply(project_id))

    @application.post("/api/projects/{project_id}/exports", status_code=202)
    def export(project_id: str, request: ExportsRequest | None = None):
        project_call(lambda: projects.get_project(project_id))
        options = request or ExportsRequest()
        return jobs.submit(
            "editable_exports",
            project_id,
            lambda: projects.prepare_exports(project_id, options.include_proxies),
        )

    @application.post("/api/projects/{project_id}/analysis/visual", status_code=202)
    def analyze_visual(project_id: str, request: AnalyzeVisualRequest | None = None):
        project_call(lambda: projects.get_project(project_id))
        options = request or AnalyzeVisualRequest()
        return jobs.submit(
            "visual_analysis",
            project_id,
            lambda: projects.analyze_visual(
                project_id, options.provider, options.model,
                force=options.force,
            ),
            fingerprint=f"{options.provider}:{options.model}:{options.force}",
        )

    @application.post("/api/projects/{project_id}/analysis/speech", status_code=202)
    def analyze_speech(project_id: str, request: AnalyzeSpeechRequest | None = None):
        project_call(lambda: projects.get_project(project_id))
        options = request or AnalyzeSpeechRequest()
        return jobs.submit(
            "speech_analysis",
            project_id,
            lambda: projects.analyze_speech(
                project_id, options.model_size, force=options.force,
            ),
            fingerprint=f"{options.model_size}:{options.force}",
        )

    @application.post("/api/projects/{project_id}/analysis/context", status_code=202)
    def analyze_context(project_id: str, request: AnalyzeContextRequest | None = None):
        project_call(lambda: projects.get_project(project_id))
        options = request or AnalyzeContextRequest()
        return jobs.submit(
            "source_context_analysis",
            project_id,
            lambda: projects.analyze_context(
                project_id, options.model, force=options.force,
            ),
            fingerprint=f"{options.model}:{options.force}",
        )

    @application.post("/api/projects/{project_id}/concepts", status_code=202)
    def generate_concepts(project_id: str, request: GenerateConceptsRequest | None = None):
        project_call(lambda: projects.get_project(project_id))
        options = request or GenerateConceptsRequest()
        return jobs.submit(
            "concept_generation",
            project_id,
            lambda: projects.generate_concepts(
                project_id,
                options.provider,
                options.model,
                options.guidance,
                options.keep_concept_ids,
                options.use_source_context,
                style_id=options.style_id,
            ),
            # everything that changes the result must change the
            # fingerprint: the request options AND the project state the
            # generation will read (evidence approvals, prompt, context)
            fingerprint=hashlib.sha1(
                json.dumps(
                    [
                        options.provider, options.model, options.guidance,
                        sorted(options.keep_concept_ids or []),
                        options.use_source_context, options.style_id,
                        projects.concept_inputs_token(project_id),
                    ],
                    default=str,
                ).encode()
            ).hexdigest()[:16],
        )

    @application.post("/api/projects/{project_id}/plan", status_code=201)
    def compile_plan(project_id: str, request: CompilePlanRequest | None = None):
        options = request or CompilePlanRequest()
        return project_call(
            lambda: projects.compile_plan(
                project_id,
                options.concept_id,
                options.width,
                options.height,
                options.fps,
            )
        )

    @application.post("/api/projects/{project_id}/plan/revise", status_code=202)
    def revise_plan(project_id: str, request: RevisePlanRequest):
        project_call(lambda: projects.get_project(project_id))
        return jobs.submit(
            "plan_revision",
            project_id,
            lambda: projects.revise_plan(
                project_id, request.instruction, request.provider, request.model
            ),
        )

    @application.get("/api/projects/{project_id}/analysis/runs")
    def list_semantic_runs(project_id: str):
        return {"runs": project_call(lambda: projects.semantic_runs(project_id))}

    @application.get("/api/projects/{project_id}/analysis/telemetry")
    def get_analysis_telemetry(project_id: str):
        return project_call(lambda: projects.analysis_telemetry(project_id))

    @application.get("/api/projects/{project_id}/analysis/runs/{run_key}")
    def get_semantic_run(project_id: str, run_key: str):
        return project_call(lambda: projects.semantic_run(project_id, run_key))

    @application.post("/api/projects/{project_id}/analysis/runs/{run_key}/reviews")
    def review_semantic_evidence(
        project_id: str, run_key: str, request: ReviewSemanticEvidenceRequest
    ):
        return project_call(
            lambda: projects.review_semantic_evidence(
                project_id,
                run_key,
                request.evidence_id,
                request.action,
                request.caption,
                request.note,
            )
        )

    @application.get("/api/jobs")
    def list_jobs():
        return {"jobs": jobs.list()}

    @application.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job")
        return job

    @application.get("/api/projects/{project_id}/media/{asset_id}")
    def media(project_id: str, asset_id: str):
        path = project_call(lambda: projects.media_path(project_id, asset_id))
        return FileResponse(path)

    @application.get("/api/projects/{project_id}/frames/{asset_id}")
    def frame(project_id: str, asset_id: str, t: float = 0.0):
        data = project_call(lambda: projects.frame_at(project_id, asset_id, t))
        return Response(
            content=data,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=86400"},
        )

    @application.get("/api/projects/{project_id}/thumbnails/{asset_id}")
    def thumbnail(project_id: str, asset_id: str):
        path = project_call(lambda: projects.thumbnail_path(project_id, asset_id))
        return FileResponse(path, media_type="image/jpeg")

    @application.get("/api/projects/{project_id}/outputs/{kind}")
    def output(project_id: str, kind: str):
        path = project_call(lambda: projects.output_path(project_id, kind))
        media_type = "video/mp4" if kind == "render" else "application/octet-stream"
        return FileResponse(path, media_type=media_type, filename=path.name if kind != "render" else None)

    static_dir = Path(__file__).resolve().parent.parent / "static"
    application.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return application


app = create_app()
