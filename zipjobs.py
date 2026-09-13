"""Batch download jobs: many videos -> one zip file, with polling.

Flow:
    POST /zip-jobs  {urls: [...], format: "video"|"audio"} -> {job_id}
    GET  /zip-jobs/{job_id}        -> {state, total, done, current, filename, error}
    GET  /zip-jobs/{job_id}/file   -> the .zip download

Jobs run in background threads so big playlists don't time out the request.
"""
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from downloader import VALID_FORMATS, download_youtube

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent
ZIP_DIR = BASE_DIR / "downloads" / "_zips"

# Finished jobs (done/error/cancelled) are wiped after this long,
# so abandoned zips can never pile up on disk.
ZIP_TTL_SECONDS = 30 * 60
SWEEP_INTERVAL_SECONDS = 10 * 60
MAX_JOBS = 100

# job_id -> {"state", "total", "done", "current", "filename", "error",
#             "cancel", "created", "finished"}
JOBS: dict[str, dict] = {}


class ZipRequest(BaseModel):
    urls: list[str] = Field(..., min_length=1, description="Video URLs to download")
    format: str = Field("video", description="'video' or 'audio'")


def _run_job(job_id: str, urls: list[str], media_format: str) -> None:
    job = JOBS[job_id]
    workdir = Path(tempfile.mkdtemp(prefix=f"zipjob_{job_id}_"))
    try:
        files: list[Path] = []
        for i, url in enumerate(urls, start=1):
            if job.get("cancel"):
                job["state"] = "cancelled"
                job["current"] = "Cancelled"
                job["finished"] = time.time()
                return
            job["current"] = f"Downloading {i}/{len(urls)}"
            filepath = download_youtube(url, media_format, output_dir=workdir)
            files.append(filepath)
            job["done"] = i
            job["current"] = filepath.stem

        if job.get("cancel"):
            job["state"] = "cancelled"
            job["current"] = "Cancelled"
            job["finished"] = time.time()
            return

        ZIP_DIR.mkdir(parents=True, exist_ok=True)
        zip_name = f"videos_{job_id[:8]}.zip"
        zip_path = ZIP_DIR / zip_name
        used_names: set[str] = set()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
            for f in files:
                arcname = f.name
                n = 2
                while arcname in used_names:
                    arcname = f"{f.stem} ({n}){f.suffix}"
                    n += 1
                used_names.add(arcname)
                zf.write(f, arcname=arcname)

        if job.get("cancel"):
            try:
                zip_path.unlink()
            except OSError:
                pass
            job["state"] = "cancelled"
            job["current"] = "Cancelled"
        else:
            job["state"] = "done"
            job["filename"] = zip_name
            job["current"] = f"{len(files)} files zipped"
        job["finished"] = time.time()
    except Exception as e:
        job["state"] = "error"
        job["error"] = str(e)
        job["finished"] = time.time()
    finally:
        # Raw per-video files are inside the zip now - free the temp space.
        for f in workdir.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            workdir.rmdir()
        except OSError:
            pass


@router.post("/zip-jobs")
def create_zip_job(req: ZipRequest):
    """Start a batch zip job. Returns {"job_id": ...} - poll the status URL."""
    media_format = (req.format or "video").lower().strip()
    if media_format not in VALID_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"format must be 'video' or 'audio', got '{req.format}'",
        )
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {
        "state": "working",
        "total": len(req.urls),
        "done": 0,
        "current": "Starting...",
        "filename": None,
        "error": None,
        "cancel": False,
        "created": time.time(),
        "finished": None,
    }
    _evict_old_jobs()
    thread = threading.Thread(
        target=_run_job, args=(job_id, req.urls, media_format), daemon=True
    )
    thread.start()
    return {"job_id": job_id}


@router.get("/zip-jobs/{job_id}")
def zip_job_status(job_id: str):
    """Poll job progress."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id.")
    return {"job_id": job_id, **job}


@router.get("/zip-jobs/{job_id}/file")
def zip_job_file(job_id: str, background: BackgroundTasks):
    """Download the finished zip. The zip is deleted from the server
    right after it is served - nothing stays on disk."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id.")
    if job["state"] != "done":
        raise HTTPException(
            status_code=409, detail=f"Job is {job['state']} - not ready yet."
        )
    zip_path = ZIP_DIR / (job["filename"] or "")
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail="Zip file no longer exists.")
    background.add_task(_delete_zip, zip_path)
    return FileResponse(
        path=str(zip_path),
        filename=zip_path.name,
        media_type="application/zip",
    )


@router.post("/zip-jobs/{job_id}/cancel")
def zip_job_cancel(job_id: str):
    """Stop a running job (e.g. user left the page). The worker finishes
    its current video, then cleans up and stops - no further work runs."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id.")
    if job["state"] == "working":
        job["cancel"] = True
    return {"job_id": job_id, "state": job["state"], "cancel": job["cancel"]}


def _delete_zip(zip_path: Path) -> None:
    try:
        zip_path.unlink()
    except OSError:
        pass


def _evict_old_jobs() -> None:
    """Keep the registry bounded: drop oldest finished jobs past MAX_JOBS."""
    if len(JOBS) <= MAX_JOBS:
        return
    finished = sorted(
        ((jid, j) for jid, j in JOBS.items() if j["state"] != "working"),
        key=lambda kv: kv[1].get("finished") or kv[1].get("created", 0),
    )
    for jid, _ in finished[: len(JOBS) - MAX_JOBS]:
        del JOBS[jid]


def _sweeper_loop() -> None:
    """Safety net: every SWEEP_INTERVAL delete finished-job zips older
    than ZIP_TTL_SECONDS plus any orphan zips, so abandoned downloads
    can never pile up on disk."""
    while True:
        time.sleep(SWEEP_INTERVAL_SECONDS)
        try:
            now = time.time()
            for jid, job in list(JOBS.items()):
                if job["state"] == "working":
                    continue
                finished = job.get("finished") or job.get("created", now)
                if now - finished > ZIP_TTL_SECONDS:
                    if job.get("filename"):
                        _delete_zip(ZIP_DIR / job["filename"])
                    JOBS.pop(jid, None)
            if ZIP_DIR.exists():
                for f in ZIP_DIR.glob("*.zip"):
                    try:
                        if now - f.stat().st_mtime > ZIP_TTL_SECONDS:
                            f.unlink()
                    except OSError:
                        pass
        except Exception:
            pass


threading.Thread(target=_sweeper_loop, daemon=True).start()
