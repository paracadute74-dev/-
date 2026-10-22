"""API and mobile frontend for a consent-based, public-media extractor."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .core import (
    IMAGE_EXTENSIONS, MAX_TOTAL_BYTES, VIDEO_EXTENSIONS, InputProblem, Job,
    MediaProblem, build_zip, collect_media, inspect_link, media_type,
    normalize_share_url, safe_filename, store,
)

BASE = Path(__file__).resolve().parent.parent
app = FastAPI(title="拾光 Extract", version="1.0.0", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    # Only local scripts/styles. Some media covers are hotlinked to the original site.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self' https: data: blob:; "
        "media-src 'self' blob:; font-src 'self'; object-src 'none'; "
        "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )
    return response


class LinkInput(BaseModel):
    url: str = Field(min_length=4, max_length=4000)


class DownloadInput(LinkInput):
    mode: str = "auto"


_counter: dict[str, deque[float]] = defaultdict(deque)
_counter_lock = Lock()


def rate_limit(request: Request, max_per_hour=35):
    # Basic per-client throttling; deploy behind an edge WAF for stronger limits.
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with _counter_lock:
        d = _counter[key]
        while d and now - d[0] > 3600:
            d.popleft()
        if len(d) >= max_per_hour:
            raise HTTPException(status_code=429, detail="请求过于频繁，请一小时后再试。")
        d.append(now)
        if len(_counter) > 2000:
            for k in list(_counter)[:1000]:
                if not _counter[k] or now - _counter[k][-1] > 3600:
                    _counter.pop(k, None)


def require_url(text: str) -> tuple[str, str]:
    try:
        return normalize_share_url(text)
    except (InputProblem, ValueError) as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex


@app.exception_handler(HTTPException)
async def handle_http(_request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html", media_type="text/html")


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "shiguang-extract"}


@app.post("/api/inspect")
async def inspect(request: Request, data: LinkInput):
    rate_limit(request)
    url, platform = require_url(data.url)
    result = await run_in_threadpool(inspect_link, url, platform)
    return {"ok": True, "url": url, **result}


@app.post("/api/jobs", status_code=202)
def start_download(request: Request, data: DownloadInput):
    rate_limit(request)
    url, platform = require_url(data.url)
    if data.mode not in {"auto", "video", "photos", "live"}:
        raise HTTPException(status_code=400, detail="请选择有效的提取模式。")
    try:
        job = store.create(mode=data.mode, platform=platform)
    except MediaProblem as ex:
        raise HTTPException(status_code=503, detail=str(ex)) from ex
    store.schedule(job, url)
    return {"id": job.id, "status": job.status, "platform": platform}


def describe_job(job: Job):
    return {
        "id": job.id,
        "status": job.status,
        "message": job.message,
        "mode": job.mode,
        "platform": job.platform,
        "expires_in_seconds": max(0, int(35 * 60 - (time.monotonic() - job.created))),
        "archive_url": f"/api/jobs/{job.id}/download" if job.archive else None,
        "files": [{
            "id": n,
            "name": p.name,
            "size": p.stat().st_size,
            "type": media_type(p),
            "preview_url": f"/api/jobs/{job.id}/files/{n}?inline=1",
            "download_url": f"/api/jobs/{job.id}/files/{n}",
        } for n, p in enumerate(job.files) if p.exists()],
    }


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, request: Request):
    rate_limit(request, max_per_hour=400)  # polling is expected
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在或已过期，请重新提取。")
    return describe_job(job)


@app.get("/api/jobs/{job_id}/files/{file_id}")
def get_file(job_id: str, file_id: int, inline: bool = False):
    job = store.get(job_id)
    if not job or job.status != "ready" or file_id < 0 or file_id >= len(job.files):
        raise HTTPException(status_code=404, detail="素材不存在或任务已过期。")
    path = job.files[file_id]
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件已被清理。")
    # FileResponse has HTTP Range support for iPhone video previews.
    return FileResponse(path, filename=path.name, content_disposition_type="inline" if inline else "attachment")


@app.get("/api/jobs/{job_id}/download")
def get_zip(job_id: str):
    job = store.get(job_id)
    if not job or job.status != "ready":
        raise HTTPException(status_code=404, detail="下载任务不存在或已过期。")
    if job.archive and job.archive.exists():
        return FileResponse(job.archive, filename=job.archive.name, media_type="application/zip")
    if len(job.files) == 1:
        file = job.files[0]
        return FileResponse(file, filename=file.name)
    raise HTTPException(status_code=404, detail="文件不存在。")


@app.post("/api/live-pair", status_code=202)
async def live_pair(request: Request, files: list[UploadFile] = File(...)):
    rate_limit(request, max_per_hour=15)
    if not 2 <= len(files) <= 12:
        raise HTTPException(status_code=400, detail="请上传至少一张照片与一段动态视频，最多 12 个文件。")
    try:
        job = store.create(mode="live", platform="本地实况")
    except MediaProblem as ex:
        raise HTTPException(status_code=503, detail=str(ex)) from ex
    job.status = "working"
    directory = job.directory / "LivePhoto"
    directory.mkdir()
    total_bytes = 0
    photo_count = video_count = 0
    used_names: set[str] = set()
    try:
        for upload in files:
            name = safe_filename(upload.filename or "")
            ext = Path(name).suffix.lower()
            if ext not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
                raise HTTPException(status_code=400, detail="只允许 JPG、PNG、HEIC、WEBP、MOV、MP4 等媒体文件。")
            if ext in IMAGE_EXTENSIONS:
                photo_count += 1
            if ext in VIDEO_EXTENSIONS:
                video_count += 1
            candidate = name
            c = 1
            while candidate.casefold() in used_names:
                candidate = f"{Path(name).stem}_{c}{ext}"
                c += 1
            used_names.add(candidate.casefold())
            target = directory / candidate
            with target.open("wb") as dest:
                while True:
                    data = await upload.read(1024 * 1024)
                    if not data:
                        break
                    total_bytes += len(data)
                    if total_bytes > MAX_TOTAL_BYTES:
                        raise HTTPException(status_code=413, detail="文件总大小超过本站免费额度。")
                    dest.write(data)
            await upload.close()
        if not photo_count or not video_count:
            raise HTTPException(status_code=400, detail="必须同时上传静态照片和动态视频。")
        job.files = collect_media(directory)
        if len(job.files) < 2:
            raise HTTPException(status_code=400, detail="未找到有效的照片与视频。")
        # ZIP stores original bytes; it cannot recreate Apple's proprietary Live Photo pairing metadata.
        archive = job.directory / "拾光_LivePhoto_原始素材.zip"
        await run_in_threadpool(build_zip, job.files, directory, archive)
        job.archive = archive
        job.message = "原始照片和动态视频已打包（不会生成苹果相册中的 Live Photo 对象）。"
        job.status = "ready"
        return describe_job(job)
    except HTTPException:
        job.status = "error"
        job.message = "本地文件验证未通过。"
        raise
    except Exception:
        job.status = "error"
        job.message = "文件处理失败，请稍后重试。"
        raise HTTPException(status_code=500, detail=job.message)
