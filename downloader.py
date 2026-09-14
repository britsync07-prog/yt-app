"""Download a YouTube video as video (mp4) or audio.

No API key needed (uses yt-dlp). Works without ffmpeg
(single-file mp4 / m4a fallback). If ffmpeg is installed,
video is merged to best mp4 and audio is converted to mp3.

CLI:
    python downloader.py "<video_url>" --format video
    python downloader.py "<video_url>" --format audio
    python downloader.py "<video_url>" -f audio -o downloads

API (wired into main.py):
    GET /download?url=<video_url>&format=video  -> mp4 file
    GET /download?url=<video_url>&format=audio  -> mp3/m4a file
"""
import argparse
import shutil
import sys
import tempfile
from pathlib import Path

import yt_dlp
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse

from ytcore import base_opts, friendly_error

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "downloads"

VALID_FORMATS = ("video", "audio")


def _ffmpeg_exe() -> str | None:
    """Path to an ffmpeg binary: system ffmpeg or imageio-ffmpeg bundle."""
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except ImportError:
        pass
    return None


def has_ffmpeg() -> bool:
    return _ffmpeg_exe() is not None


def build_opts(media_format: str, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(output_dir / "%(title)s [%(id)s].%(ext)s")
    # Shared hardening (player clients, PO tokens, cookies, JS runtime)
    # + ffmpeg path.
    base: dict = base_opts()
    # Small pause before each download so bursts look less bot-like.
    base["sleep_interval"] = 2
    base["max_sleep_interval"] = 5
    ffmpeg_exe = _ffmpeg_exe()
    if ffmpeg_exe:
        # Full path to binary (imageio-ffmpeg names it differently
        # than ffmpeg.exe, so pass the exe itself, not its folder).
        base["ffmpeg_location"] = ffmpeg_exe

    if media_format == "audio":
        if ffmpeg_exe:
            return {
                **base,
                "format": "bestaudio/best",
                "outtmpl": outtmpl,
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }
        # No ffmpeg: keep original audio container (usually m4a)
        return {
            **base,
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }

    # video
    if ffmpeg_exe:
        return {
            **base,
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "merge_output_format": "mp4",
        }
    # No ffmpeg: progressive mp4 only (single file with audio+video,
    # usually up to 360p/720p). Install ffmpeg for best quality.
    return {
        **base,
        "format": "best[ext=mp4][acodec!=none][vcodec!=none]/best[acodec!=none][vcodec!=none]/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }


def _safe_name(title: str, video_id: str) -> str:
    import re

    clean = re.sub(r'[\\/*?:"<>|]', "", title or "video").strip()[:80] or "video"
    return f"{clean} [{video_id}]" if video_id else clean


def _fetch_stream(url: str, dest: Path) -> Path:
    """Download a (signed) media URL the way a browser would.

    When a relay is configured, stream URLs come back IP-locked to the
    Cloudflare worker, so the actual bytes must be proxied through its
    /stream route (only it can fetch googlevideo from the bound IP).
    """
    import urllib.request

    from ytcore import worker_cfg

    relay_base, relay_key = worker_cfg()
    if relay_base and url.startswith("http"):
        import urllib.parse

        proxy_url = (
            f"{relay_base}/stream?key={relay_key}&url={urllib.parse.quote(url, safe='')}"
        )
        url = proxy_url

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
    return dest


def _run_ffmpeg(args: list[str]) -> None:
    import subprocess

    exe = _ffmpeg_exe()
    if not exe:
        raise ValueError("ffmpeg is required for this conversion.")
    proc = subprocess.run([exe, "-y", *args], capture_output=True, timeout=900)
    if proc.returncode != 0:
        raise ValueError("Media conversion failed.")


def _pick_streams(streams: list[dict], media_format: str) -> dict:
    """Choose stream URLs. Returns {"kind": "progressive"/"mux"/"audio", ...}."""
    audio = [s for s in streams if (s.get("mime") or "").startswith("audio/")]
    video = [s for s in streams if (s.get("mime") or "").startswith("video/")]

    def quality_key(s: dict) -> tuple:
        q = s.get("quality") or ""
        m = __import__("re").search(r"(\d+)", q)
        return (int(m.group(1)) if m else 0, s.get("size") or 0)

    progressive = [s for s in video if "mp4" in (s.get("mime") or "")]
    if progressive:
        progressive.sort(key=quality_key)

    if media_format == "audio":
        if audio:
            # Prefer m4a (mp3 conversion is lossless-container, no re-encode pain).
            audio.sort(key=lambda s: (s.get("mime") == "audio/mp4", s.get("size") or 0))
            return {"kind": "audio", "audio": audio[-1]}
        if progressive:
            # No audio-only URL (common now) - rip audio from the
            # progressive mp4 instead.
            return {"kind": "audio_from_video", "video": progressive[-1]}
        raise ValueError("No audio streams available.")
    # Progressive first (single file, video+audio together).
    progressive = [s for s in video if "mp4" in (s.get("mime") or "")]
    if progressive:
        progressive.sort(key=quality_key)
        return {"kind": "progressive", "video": progressive[-1]}
    mp4video = [s for s in video if "mp4" in (s.get("mime") or "")]
    if mp4video and audio:
        mp4video.sort(key=quality_key)
        audio.sort(key=lambda s: s.get("size") or 0)
        return {"kind": "mux", "video": mp4video[-1], "audio": audio[-1]}
    if video and audio:
        video.sort(key=quality_key)
        audio.sort(key=lambda s: s.get("size") or 0)
        return {"kind": "mux", "video": video[-1], "audio": audio[-1]}
    raise ValueError("No playable streams available.")


def download_via_worker_streams(
    url: str, media_format: str = "video", output_dir: Path = DEFAULT_OUTPUT_DIR
) -> Path:
    """Fallback path: stream URLs from the relay, downloaded directly.

    Used when yt-dlp itself is bot-checked. Raises ValueError otherwise.
    """
    from ytcore import worker_video_info

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    info = worker_video_info(url)
    streams = info.get("streams") or []
    if not streams:
        raise ValueError("Relay returned no playable streams.")
    pick = _pick_streams(streams, media_format)
    name = _safe_name(info.get("title", ""), info.get("id", ""))

    if pick["kind"] == "progressive":
        return _fetch_stream(pick["video"]["url"], output_dir / f"{name}.mp4")
    if pick["kind"] == "audio_from_video":
        if not _ffmpeg_exe():
            raise ValueError("Audio extraction needs ffmpeg.")
        src = _fetch_stream(pick["video"]["url"], output_dir / f"{name}.mp4")
        out = output_dir / f"{name}.mp3"
        _run_ffmpeg(["-i", str(src), "-vn", "-b:a", "192k", str(out)])
        try:
            src.unlink()
        except OSError:
            pass
        return out
    if pick["kind"] == "audio":
        src_ext = ".m4a" if "mp4" in (pick["audio"].get("mime") or "") else ".webm"
        raw = _fetch_stream(pick["audio"]["url"], output_dir / f"{name}{src_ext}")
        if _ffmpeg_exe():
            out = output_dir / f"{name}.mp3"
            _run_ffmpeg(["-i", str(raw), "-vn", "-b:a", "192k", str(out)])
            try:
                raw.unlink()
            except OSError:
                pass
            return out
        return raw
    # mux: best video + best audio, merged to mp4
    vpart = _fetch_stream(pick["video"]["url"], output_dir / f"{name}.vpart")
    apart = _fetch_stream(pick["audio"]["url"], output_dir / f"{name}.apart")
    out = output_dir / f"{name}.mp4"
    _run_ffmpeg(["-i", str(vpart), "-i", str(apart), "-c", "copy", str(out)])
    for p in (vpart, apart):
        try:
            p.unlink()
        except OSError:
            pass
    return out


def download_youtube(
    url: str, media_format: str = "video", output_dir: Path = DEFAULT_OUTPUT_DIR
) -> Path:
    """Download URL as 'video' or 'audio'. Returns the downloaded file path."""
    media_format = media_format.lower().strip()
    if media_format not in VALID_FORMATS:
        raise ValueError(f"format must be one of {VALID_FORMATS}, got '{media_format}'")

    output_dir = Path(output_dir)
    before = set(output_dir.glob("*")) if output_dir.exists() else set()

    opts = build_opts(media_format, output_dir)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as e:
        from ytcore import is_bot_check, worker_cfg

        if is_bot_check(e) and worker_cfg()[0]:
            import logging

            log = logging.getLogger("yt-app")
            log.info("download bot-checked, trying relay streams")
            print("[yt-app] download bot-checked, trying relay streams", flush=True)
            try:
                result = download_via_worker_streams(url, media_format, output_dir)
                print(f"[yt-app] download via relay OK: {result}", flush=True)
                return result
            except Exception as relay_err:
                log.error("download via relay FAILED: %s", relay_err)
                print(f"[yt-app] download via relay FAILED: {relay_err}", flush=True)
        raise friendly_error(e, "Download failed") from e

    after = set(output_dir.glob("*"))
    new_files = sorted(after - before, key=lambda p: p.stat().st_mtime)
    if not new_files:
        # Fallback: newest file in dir (same title re-downloaded)
        all_files = sorted(output_dir.glob("*"), key=lambda p: p.stat().st_mtime)
        if not all_files:
            raise ValueError("Download failed: no file produced.")
        return all_files[-1]
    # If audio with ffmpeg produced mp3 + leftover, prefer mp3
    for f in reversed(new_files):
        if media_format == "audio" and f.suffix.lower() == ".mp3":
            return f
    return new_files[-1]


@router.get("/download")
def download_endpoint(
    background: BackgroundTasks,
    url: str = Query(..., description="YouTube video URL"),
    format: str = Query("video", description="'video' or 'audio'"),
):
    """Download and return the file, e.g. /download?url=...&format=audio.

    The file is prepared in a unique temp folder which is deleted
    right after it is served - nothing stays on the server disk.
    """
    media_format = (format or "video").lower().strip()
    if media_format not in VALID_FORMATS:
        raise HTTPException(
            status_code=400, detail=f"format must be 'video' or 'audio', got '{format}'"
        )
    tmpdir = Path(tempfile.mkdtemp(prefix="dl_"))
    try:
        filepath = download_youtube(url, media_format, output_dir=tmpdir)
    except ValueError as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(e))

    background.add_task(shutil.rmtree, tmpdir, True)

    media_type = (
        "audio/mpeg" if filepath.suffix.lower() == ".mp3" else "application/octet-stream"
    )
    if media_format == "video":
        media_type = "video/mp4"
    elif filepath.suffix.lower() == ".m4a":
        media_type = "audio/mp4"

    return FileResponse(
        path=str(filepath),
        filename=filepath.name,
        media_type=media_type,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Download YouTube video or audio.")
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument(
        "-f",
        "--format",
        default="video",
        choices=list(VALID_FORMATS),
        help="'video' (mp4) or 'audio' (mp3/m4a)",
    )
    parser.add_argument("-o", "--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    try:
        filepath = download_youtube(args.url, args.format, Path(args.output_dir))
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    print(f"Saved: {filepath}")


if __name__ == "__main__":
    main()
