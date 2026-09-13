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
    # Shared hardening (player clients, cookies, JS runtime) + ffmpeg path.
    base: dict = base_opts()
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
