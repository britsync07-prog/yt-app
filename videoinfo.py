"""Single YouTube video metadata (no download).

Usage as API (wired into main.py):
    GET /video-info?url=<video_url>
"""
import yt_dlp
from fastapi import APIRouter, HTTPException, Query

from playlist import _pick_thumbnail

router = APIRouter()


def get_video_info(video_url: str) -> dict:
    """Fetch title/thumbnail/duration/etc. for one video without downloading.

    Returns:
        {"id": str, "title": str, "thumbnail": str, "duration": int|None,
         "uploader": str|None, "url": str}
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "js_runtimes": {"node": {}},
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
    except Exception as e:
        raise ValueError(f"Could not read video: {e}") from e

    if not info:
        raise ValueError("Could not read video (empty response).")
    if info.get("_type") == "playlist":
        raise ValueError("That URL is a playlist - use the Playlist tab instead.")

    video_id = info.get("id", "")
    return {
        "id": video_id,
        "title": info.get("title", ""),
        "thumbnail": _pick_thumbnail(info),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel"),
        "url": info.get("webpage_url") or video_url,
    }


@router.get("/video-info")
def video_info_endpoint(
    url: str = Query(..., description="YouTube video URL"),
):
    """GET /video-info?url=<video_url> -> title + thumbnail + meta."""
    try:
        return get_video_info(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
