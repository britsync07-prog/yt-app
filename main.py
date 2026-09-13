"""Main FastAPI server. Local: port 4444. Render: uses the $PORT env var."""
import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from auth import require_auth
from auth import router as auth_router
from debug import router as debug_router
from downloader import router as downloader_router
from health import router as health_router
from playlist import router as playlist_router
from videoinfo import router as videoinfo_router
from zipjobs import router as zipjobs_router

load_dotenv()

app = FastAPI()


def _cors_origins() -> list[str]:
    raw = os.environ.get("FRONTEND_URL", "")
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    return origins or ["*"]


# JWT travels in the Authorization header (no cookies), so credentials
# stay off and an explicit origin list can be enforced in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(health_router)  # public (uptime checks)
app.include_router(debug_router, dependencies=[Depends(require_auth)])
app.include_router(playlist_router, dependencies=[Depends(require_auth)])
app.include_router(videoinfo_router, dependencies=[Depends(require_auth)])
app.include_router(downloader_router, dependencies=[Depends(require_auth)])
app.include_router(zipjobs_router, dependencies=[Depends(require_auth)])


# Local dev only: serve the page at / when a generated config exists
# (run `node frontend/build-config.js` first). On Render there is no
# config.js in the repo, so nothing is mounted and this stays a pure API
# with a plain JSON message at /.
FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
if (FRONTEND_DIR / "config.js").exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:

    @app.get("/")
    def root():
        return {
            "message": "YT app API. GET /health for 200 OK.",
            "health": "/health",
            "docs": "/docs",
        }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "4444")))
