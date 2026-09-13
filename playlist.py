"""YouTube public playlist -> video titles + thumbnails.

No API key needed (uses yt-dlp).

Usage as script:
    python playlist.py "<playlist_url>"

Usage as API (wired into main.py):
    GET /playlist?url=<playlist_url>
"""
import json
import sys

import yt_dlp
from fastapi import APIRouter, HTTPException, Query

from ytcore import (
    _playlist_id_from_url,
    base_opts,
    friendly_error,
    is_bot_check,
    worker_cfg,
    worker_playlist_videos,
)

router = APIRouter()


def _pick_thumbnail(entry: dict) -> str | None:
    # yt-dlp flat-extract usually gives direct 'thumbnail'
    if entry.get("thumbnail"):
        return entry["thumbnail"]
    # fallback: pick highest-res from 'thumbnails' list
    thumbs = entry.get("thumbnails") or []
    if thumbs:
        # last one is usually highest resolution
        return thumbs[-1].get("url")
    video_id = entry.get("id")
    if video_id:
        # final fallback: default YouTube thumbnail CDN
        return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    return None


def get_playlist_videos(playlist_url: str) -> dict:
    """Fetch all videos in a public YouTube playlist.

    Returns:
        {"playlist_title": str, "count": int,
         "videos": [{"index": int, "id": str, "title": str,
                      "thumbnail": str, "url": str, "duration": int|None}]}
    """
    ydl_opts = base_opts(
        {
            "extract_flat": True,  # fast: don't resolve each video
            "skip_download": True,
        }
    )
    # A /watch?v=...&list=... link makes yt-dlp return just the one
    # video, so always prefer the canonical playlist URL when a
    # playlist id is present.
    candidates = [playlist_url]
    playlist_id = _playlist_id_from_url(playlist_url)
    if playlist_id:
        candidates.insert(
            0, f"https://www.youtube.com/playlist?list={playlist_id}"
        )
    info = None
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(candidate, download=False)
            if info:
                break
        except Exception as e:
            last_error = e
            info = None
    if not info:
        if last_error and is_bot_check(last_error) and worker_cfg()[0]:
            try:
                return worker_playlist_videos(playlist_url)
            except Exception:
                pass
        raise friendly_error(last_error or Exception("empty response"), "Could not read playlist")

    # If a single video URL was passed instead of a playlist, wrap it.
    if info.get("_type") != "playlist" and "entries" not in info:
        entries = [info]
        playlist_title = info.get("title", "")
    else:
        entries = list(info.get("entries") or [])
        playlist_title = info.get("title", "")

    videos = []
    for i, entry in enumerate(entries, start=1):
        if entry is None:  # private/deleted video slot
            continue
        video_id = entry.get("id", "")
        videos.append(
            {
                "index": i,
                "id": video_id,
                "title": entry.get("title", ""),
                "thumbnail": _pick_thumbnail(entry),
                "url": entry.get("url")
                or (f"https://www.youtube.com/watch?v={video_id}" if video_id else ""),
                "duration": entry.get("duration"),
            }
        )

    return {"playlist_title": playlist_title, "count": len(videos), "videos": videos}


@router.get("/playlist")
def playlist_endpoint(
    url: str = Query(..., description="Public YouTube playlist URL"),
):
    """GET /playlist?url=<playlist_url> -> titles + thumbnails."""
    try:
        return get_playlist_videos(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python playlist.py "<playlist_url>"')
        sys.exit(1)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    result = get_playlist_videos(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=True))
