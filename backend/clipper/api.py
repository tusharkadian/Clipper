from __future__ import annotations

import hmac
import json
import secrets
import shutil
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import RETENTION_SECONDS, ROOT, Settings, configure_tools
from .domain import RunOptions, URLInput
from .preflight import inspect_system
from .retention import prune_expired, remove_owned_media
from .rerank import MODEL_REPOSITORY, is_ready as reranker_ready
from .storage import Store
from .worker import workspace_size


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    settings.prepare()
    configure_tools()
    store = Store(settings.data / "jobs.sqlite3")
    prune_expired(settings, store)
    token = secrets.token_urlsafe(32)
    app = FastAPI(title="Clipper Local", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_session(request: Request, call_next):
        origin = request.headers.get("origin")
        allowed_origins = {f"http://{host}:{port}" for host in ("127.0.0.1", "localhost") for port in range(8765, 8776)}
        if origin and origin not in allowed_origins:
            return JSONResponse({"detail": "Origin not allowed."}, status_code=403)
        if request.url.path.startswith("/api/") and request.url.path not in {"/api/session", "/api/health"}:
            provided = request.headers.get("x-clipper-token", "")
            if request.method == "GET" and "/files/" in request.url.path:
                provided = request.cookies.get("clipper_session", "")
            if not hmac.compare_digest(provided, token):
                return JSONResponse({"detail": "Local session required."}, status_code=403)
        if request.method == "POST":
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                return JSONResponse({"detail": "Invalid content length."}, status_code=400)
            if length > settings.max_upload_bytes + 1024**2:
                return JSONResponse({"detail": "Upload exceeds the configured limit."}, status_code=413)
            if request.url.path == "/api/uploads":
                if length <= 0:
                    return JSONResponse({"detail": "An upload content length is required."}, status_code=411)
                if shutil.disk_usage(settings.data).free - length < settings.reserve_bytes:
                    return JSONResponse({"detail": "Insufficient disk space for this upload."}, status_code=507)
                if workspace_size(settings.data) + length > settings.workspace_limit_bytes:
                    return JSONResponse({"detail": "This upload would exceed the workspace limit."}, status_code=507)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok", "cloud_enabled": False}

    @app.get("/api/session")
    def session():
        response = JSONResponse({"token": token})
        response.set_cookie("clipper_session", token, httponly=True, samesite="strict")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/system")
    def system():
        report = inspect_system(settings.data)
        report["model_ready"] = (settings.data / "models/small.en/model.bin").is_file()
        report["reranker_ready"] = reranker_ready(settings.data / "models")
        report["reranker_model"] = MODEL_REPOSITORY
        report["workspace_gb"] = round(workspace_size(settings.data) / 1024**3, 2)
        report["workspace_limit_gb"] = round(settings.workspace_limit_bytes / 1024**3)
        report["reserve_gb"] = round(settings.reserve_bytes / 1024**3)
        report["retention_days"] = RETENTION_SECONDS // 86400
        report["workspace_preallocated"] = False
        worker_file = settings.data / "worker.json"
        try:
            heartbeat = json.loads(worker_file.read_text(encoding="utf-8"))
            report["worker_ready"] = time.time() - heartbeat["updated"] < 10
        except (OSError, ValueError, KeyError):
            report["worker_ready"] = False
        return report

    @app.get("/api/jobs")
    def jobs():
        rows = store.list()
        for job in rows:
            if job["expires_at"] is not None and job["expires_at"] <= time.time():
                job["result"] = {}
        return rows

    def preflight_upload(expected: int = 0):
        if shutil.disk_usage(settings.data).free - expected < settings.reserve_bytes:
            raise HTTPException(507, "Insufficient disk space while preserving the OS reserve.")
        if workspace_size(settings.data) + expected > settings.workspace_limit_bytes:
            raise HTTPException(507, "Workspace limit reached.")

    @app.post("/api/uploads", status_code=201)
    def upload(file: UploadFile = File(...), options: str = Form("{}")):
        try:
            parsed = RunOptions.model_validate_json(options)
        except ValueError:
            raise HTTPException(422, "Invalid processing options.") from None
        suffix = Path(file.filename or "video").suffix.lower()
        if suffix not in {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts"}:
            raise HTTPException(422, "Unsupported video extension.")
        preflight_upload(file.size or 0)
        target = settings.data / "imports" / (uuid4().hex + suffix)
        temporary = target.with_suffix(".part")
        size = 0
        try:
            with temporary.open("xb") as stream:
                while chunk := file.file.read(4 * 1024**2):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(413, "Upload exceeds the configured limit.")
                    if shutil.disk_usage(settings.data).free - len(chunk) < settings.reserve_bytes:
                        raise HTTPException(507, "Disk reserve reached during upload.")
                    stream.write(chunk)
            if size == 0:
                raise HTTPException(422, "The uploaded file is empty.")
            temporary.replace(target)
            return store.create(Path(file.filename or "Recording").name, "file", str(target), parsed.model_dump())
        finally:
            temporary.unlink(missing_ok=True)
            file.file.close()

    @app.post("/api/sources", status_code=201)
    def source(body: URLInput):
        preflight_upload()
        return store.create(body.url, "url", body.url, body.options.model_dump())

    @app.post("/api/model", status_code=201)
    def model():
        if (settings.data / "models/small.en/model.bin").exists():
            raise HTTPException(409, "English model already installed.")
        if any(job["kind"] == "model" and job["status"] in {"queued", "running", "paused"} for job in store.list()):
            raise HTTPException(409, "A model installation is already queued.")
        preflight_upload(1024**3)
        return store.create("English speech model", "model", "", RunOptions().model_dump())

    @app.post("/api/jobs/{identifier}/{action}")
    def control(identifier: str, action: Literal["pause", "resume", "cancel", "retry"]):
        job = store.get(identifier)
        if job and action in {"retry", "resume"} and job["status"] in {"succeeded", "failed", "cancelled", "expired"}:
            if job["expires_at"] is not None and job["expires_at"] <= time.time():
                raise HTTPException(410, "Media retention expired. Import the recording again.")
        try:
            return store.control(identifier, action)
        except KeyError:
            raise HTTPException(404, "Job not found.") from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @app.post("/api/reranker/model", status_code=201)
    def install_reranker():
        if reranker_ready(settings.data / "models"):
            raise HTTPException(409, "Local Qwen reranker is already installed.")
        if any(job["kind"] == "model" and job["source"] == "qwen-reranker"
               and job["status"] in {"queued", "running", "paused"} for job in store.list()):
            raise HTTPException(409, "A reranker installation is already queued.")
        preflight_upload(2 * 1024**3)
        return store.create("Qwen 1.5B Q4 reranker", "model", "qwen-reranker", RunOptions().model_dump())

    @app.get("/api/jobs/{identifier}/files/{clip}/{name}")
    def artifact(identifier: str, clip: str, name: str):
        job = store.get(identifier)
        if job and job["expires_at"] is not None and job["expires_at"] <= time.time():
            raise HTTPException(410, "This clip has reached its seven-day retention limit.")
        if not job or name not in {"clip.mp4", "thumbnail.jpg", "captions.srt", "plan.json"}:
            raise HTTPException(404, "Artifact not found.")
        allowed = {item["id"] for item in job["result"].get("exports", [])}
        if clip not in allowed:
            raise HTTPException(404, "Clip not found.")
        root = (settings.data / "runs" / identifier).resolve()
        path = (root / clip / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, "Artifact not found.")
        return FileResponse(path, filename=name if name != "thumbnail.jpg" else None,
                            content_disposition_type="inline" if name in {"thumbnail.jpg", "clip.mp4"} else "attachment")

    @app.get("/api/jobs/{identifier}/report")
    def report(identifier: str):
        job = store.get(identifier)
        if not job:
            raise HTTPException(404, "Job not found.")
        return JSONResponse(job["result"], headers={"Content-Disposition": f'attachment; filename="{identifier}-report.json"'})

    @app.delete("/api/jobs/{identifier}")
    def remove(identifier: str):
        try:
            job = store.remove(identifier)
        except KeyError:
            raise HTTPException(404, "Job not found.") from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        try:
            remove_owned_media(settings, job)
        except OSError:
            raise HTTPException(409, "Media files are busy. Removal will retry during automatic cleanup.") from None
        store.forget_expired(identifier)
        return {"removed": identifier}

    frontend = ROOT / "apps/web/dist"
    if frontend.exists():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="web")
    return app