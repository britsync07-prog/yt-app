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
PLUGIN_DIR = BASE_DIR / "yt_plugins"
# Sidecar token server (bgutil-pot). Render runs it via start.sh;
# locally run bgutil-pot(.exe) server --port 4416 yourself, or skip it
# (extraction still works, just without PO tokens).
# NOTE: hostname "localhost" (not 127.0.0.1) - the server binds IPv6 [::].
POT_SERVER_URL = os.environ.get("POT_SERVER_URL", "http://localhost:4416")

_COOKIE_TMP: str | None = None
_COOKIE_CHECKED = False
_PLUGINS_DONE = False


def pot_binary() -> str | None:
    """Absolute path to the bgutil-pot sidecar binary, if shipped
    next to the code (bgutil-pot.exe on Windows, bgutil-pot on Linux)."""
    names = ("bgutil-pot.exe", "bgutil-pot") if os.name == "nt" else ("bgutil-pot", "bgutil-pot.exe")
    for name in names:
        p = BASE_DIR / name
        if p.is_file():
            return str(p)
    return None


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


def _ensure_plugins() -> None:
    """Register our yt_plugins dir. NOTE: this yt-dlp version reads
    plugin dirs from a GLOBAL (yt_dlp.globals.plugin_dirs), not from
    YoutubeDL params - passing plugin_dirs to YoutubeDL is silently
    ignored, so this step is mandatory."""
    global _PLUGINS_DONE
    if _PLUGINS_DONE:
        return
    _PLUGINS_DONE = True
    if not PLUGIN_DIR.is_dir():
        return
    try:
        from yt_dlp.globals import plugin_dirs

        current = list(plugin_dirs.value or [])
        if "default" not in current:
            current.append("default")
        if str(PLUGIN_DIR) not in current:
            current.append(str(PLUGIN_DIR))
        plugin_dirs.value = current
    except Exception:
        pass


def base_opts(extra: dict | None = None) -> dict:
    """Common yt-dlp options every module starts from."""
    _ensure_plugins()
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": {"node": {}},
        # Try mobile API clients before web - they trip bot checks rarely.
        # youtubepot-bgutilhttp feeds PO tokens from the sidecar server
        # when it is running; youtubepot-bgutilcli uses the binary
        # directly as fallback. Both are harmless when unavailable.
        # NOTE: these must be TOP-LEVEL extractor_args keys,
        # not nested under "youtube".
        "extractor_args": {
            "youtube": {"player_client": ["android", "ios", "web"]},
            "youtubepot-bgutilhttp": {"base_url": POT_SERVER_URL},
            **(
                {"youtubepot-bgutilcli": {"cli_path": pot_binary()}}
                if pot_binary()
                else {}
            ),
        },
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
