"""Shared yt-dlp configuration: anti-bot hardening + auth + friendly errors.

All modules (playlist, videoinfo, downloader) build their options from
base_opts() so fixes apply everywhere at once.

Layers:
  1. Fallback player clients (android/ios/web) - mobile clients are
     challenged far less often than the web client.
  2. Cookies - export youtube.com cookies (Get cookies.txt extension),
     then either set YT_COOKIES to the file CONTENTS (Render env var)
     or drop a cookies.txt next to .env (local dev, gitignored).
"""
import os
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

_COOKIE_TMP: str | None = None
_COOKIE_CHECKED = False


def cookie_file() -> str | None:
    """Resolve a cookies.txt for yt-dlp, or None when not configured."""
    global _COOKIE_TMP, _COOKIE_CHECKED
    explicit = os.environ.get("YT_COOKIES_FILE", "").strip()
    if explicit and Path(explicit).exists():
        return explicit
    if not _COOKIE_CHECKED:
        _COOKIE_CHECKED = True
        raw = os.environ.get("YT_COOKIES", "")
        if "# Netscape HTTP Cookie File" in raw and "youtube" in raw.lower():
            fd, tmp = tempfile.mkstemp(prefix="ytcookies_", suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(raw)
            _COOKIE_TMP = tmp
    if _COOKIE_TMP and Path(_COOKIE_TMP).exists():
        return _COOKIE_TMP
    local = BASE_DIR / "cookies.txt"
    if local.exists():
        return str(local)
    return None


def base_opts(extra: dict | None = None) -> dict:
    """Common yt-dlp options every module starts from."""
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": {"node": {}},
        # Try mobile API clients before web - they trip bot checks rarely.
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "web"]}},
    }
    cf = cookie_file()
    if cf:
        opts["cookiefile"] = cf
    if extra:
        opts.update(extra)
    return opts


def is_bot_check(e: Exception) -> bool:
    msg = str(e)
    return "Sign in to confirm you" in msg and "bot" in msg


def friendly_error(e: Exception, what: str) -> ValueError:
    """Map yt-dlp failures to messages safe for end users."""
    if is_bot_check(e):
        return ValueError(
            f"{what}: YouTube asked for verification (bot check). "
            "Try again in a minute."
        )
    return ValueError(f"{what}: {e}")
