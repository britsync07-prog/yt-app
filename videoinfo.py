"""Single YouTube video metadata (no download).

Usage as API (wired into main.py):
    GET /video-info?url=<video_url>
"""
import logging

import yt_dlp
from fastapi import APIRouter, HTTPException, Query

from playlist import _pick_thumbnail
from ytcore import (
    base_opts,
    friendly_error,
    is_bot_check,
    worker_cfg,
    worker_video_info,
)

log = logging.getLogger("yt-app")

router = APIRouter()


def get_video_info(video_url: str) -> dict:
    """Fetch title/thumbnail/duration/etc. for one video without downloading.

    Returns:
        {"id": str, "title": str, "thumbnail": str, "duration": int|None,
         "uploader": str|None, "url": str}
    """
    log.info("video-info START url=%s relay=%s", video_url, bool(worker_cfg()[0]))
    ydl_opts = base_opts({"skip_download": True, "noplaylist": True})
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
    except Exception as e:
        err_msg = str(e)
        bot = is_bot_check(e)
        relay_url = worker_cfg()[0]
        log.warning(
            "video-info yt-dlp FAILED bot_check=%s relay_configured=%s err=%s",
            bot, bool(relay_url), err_msg[:200],
        )
        if bot and relay_url:
            log.info("video-info bot-checked, trying relay for url=%s", video_url)
            try:
                result = worker_video_info(video_url)
                log.info("video-info relay OK title=%s", result.get("title", "?"))
                return result
            except Exception as relay_err:
                log.error("video-info relay FAILED: %s", relay_err)
        raise friendly_error(e, "Could not read video") from e

    if not info:
        raise ValueError("Could not read video (empty response).")
    if info.get("_type") == "playlist":
        raise ValueError("That URL is a playlist - use the Playlist tab instead.")

    video_id = info.get("id", "")
    log.info("video-info OK via yt-dlp title=%s", info.get("title", "?"))
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
