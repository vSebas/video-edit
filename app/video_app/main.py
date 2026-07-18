from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings
from .jobs import JobManager
from .projects import ProjectError, ProjectService


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source_directory: str = Field(min_length=1)
    prompt: str = Field(default="", max_length=4000)


class SelectConceptRequest(BaseModel):
    concept_id: str = Field(min_length=1)


def create_app(settings: Settings | None = None) -> FastAPI:
    current_settings = settings or Settings.from_environment()
    projects = ProjectService(current_settings)
    jobs = JobManager()
    application = FastAPI(title="Local Video Editing Workbench", version="0.1.0")

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

    @application.post("/api/projects/{project_id}/selection")
    def select_concept(project_id: str, request: SelectConceptRequest):
        return project_call(
            lambda: projects.select_concept(project_id, request.concept_id)
        )

    @application.post("/api/projects/{project_id}/render", status_code=202)
    def render(project_id: str):
        project_call(lambda: projects.get_project(project_id))
        return jobs.submit("render", project_id, lambda: projects.render(project_id))

    @application.post("/api/projects/{project_id}/exports", status_code=202)
    def export(project_id: str):
        project_call(lambda: projects.get_project(project_id))
        return jobs.submit(
            "editable_exports", project_id, lambda: projects.prepare_exports(project_id)
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
