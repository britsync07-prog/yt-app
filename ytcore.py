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
PLUGIN_ZIP_NAME = "bgutil-ytdlp-pot-provider-rs.zip"
POT_VERSION = "v0.8.1"
POT_RELEASE = (
    "https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs"
    f"/releases/download/{POT_VERSION}"
)
POT_ASSETS = {
    "linux": "bgutil-pot-linux-x86_64",
    "win": "bgutil-pot-windows-x86_64.exe",
}
# Sidecar token server (bgutil-pot). Render runs it via start.sh;
# locally run bgutil-pot(.exe) server --port 4416 yourself, or skip it
# (extraction still works, just without PO tokens).
# NOTE: hostname "localhost" (not 127.0.0.1) - the server binds IPv6 [::].
POT_SERVER_URL = os.environ.get("POT_SERVER_URL", "http://localhost:4416")
POT_PORT = 4416

PLAYER_CLIENTS = ["android", "ios", "tv", "mweb", "web_embedded", "web"]

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


def _download(url: str, dest: Path, timeout: int = 300) -> bool:
    """Fetch a URL to dest (atomic-ish via .part file). False on any error."""
    import urllib.request

    part = dest.with_suffix(dest.suffix + ".part")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "yt-app"})
        with urllib.request.urlopen(req, timeout=timeout) as r, open(part, "wb") as f:
            while True:
                chunk = r.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
        part.replace(dest)
        return True
    except Exception:
        try:
            part.unlink()
        except OSError:
            pass
        return False


def _sidecar_up() -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen(POT_SERVER_URL + "/ping", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_pot() -> None:
    """Self-heal the PO-token stack at runtime (Render-safe).

    - Fetches the plugin zip (~6KB) and the sidecar binary (~45MB, once)
      when missing, so plain `pip install + uvicorn` deploys work with
      zero dashboard/build changes.
    - Starts the sidecar when its binary exists but nothing listens.
    Best-effort: any failure just means "no PO tokens", never an error.
    """
    try:
        PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        lock = PLUGIN_DIR / ".pot-fetch.lock"
        try:
            lock.touch(exist_ok=False)
        except FileExistsError:
            return  # another worker/process is already fetching
        try:
            if not (PLUGIN_DIR / PLUGIN_ZIP_NAME).is_file():
                _download(f"{POT_RELEASE}/{PLUGIN_ZIP_NAME}", PLUGIN_DIR / PLUGIN_ZIP_NAME, timeout=60)
            if pot_binary() is None:
                asset = POT_ASSETS["win"] if os.name == "nt" else POT_ASSETS["linux"]
                target = BASE_DIR / asset
                # Canonical name so pot_binary() finds it without rename.
                dest = BASE_DIR / ("bgutil-pot.exe" if os.name == "nt" else "bgutil-pot")
                if _download(f"{POT_RELEASE}/{asset}", target):
                    try:
                        if not dest.exists():
                            target.replace(dest)
                    except OSError:
                        pass
                    if os.name != "nt":
                        try:
                            os.chmod(str(dest), 0o755)
                        except OSError:
                            pass
            if os.environ.get("POT_AUTOSTART", "1") != "0":
                binary = pot_binary()
                if binary and not _sidecar_up():
                    import subprocess

                    subprocess.Popen(
                        [binary, "server", "--port", str(POT_PORT)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
        finally:
            try:
                lock.unlink()
            except OSError:
                pass
    except Exception:
        pass


def _ensure_pot_background() -> None:
    import threading

    threading.Thread(target=ensure_pot, daemon=True).start()


_ensure_pot_background()


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
            "youtube": {"player_client": PLAYER_CLIENTS},
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


def pot_status() -> dict:
    """Sidecar/plugin presence for /health. Short timeouts - never slow."""
    plugin = PLUGIN_DIR.is_dir() and any(PLUGIN_DIR.iterdir())
    server = False
    try:
        import json
        import urllib.request

        with urllib.request.urlopen(POT_SERVER_URL + "/ping", timeout=3) as r:
            server = r.status == 200 and "server_uptime" in r.read().decode()
    except Exception:
        server = False
    return {"plugin": bool(plugin), "server": server}


def is_bot_check(e: Exception) -> bool:
    msg = str(e)
    return "Sign in to confirm you" in msg and "bot" in msg


def _playlist_id_from_url(url: str) -> str | None:
    """Pull the `list=` playlist id out of any YouTube URL."""
    try:
        from urllib.parse import parse_qs, urlparse

        ids = parse_qs(urlparse(url or "").query).get("list") or []
        return ids[0] if ids else None
    except Exception:
        return None


def _video_id_from_url(url: str) -> str | None:
    import re

    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else None


def worker_cfg() -> tuple[str | None, str | None]:
    """(base_url, key) for the Cloudflare relay, or (None, None)."""
    base = os.environ.get("WORKER_URL", "").rstrip("/")
    key = os.environ.get("WORKER_KEY", "")
    return (base, key) if base and key else (None, None)


def worker_get(path: str, params: dict, timeout: int = 45, retries: int = 2) -> dict:
    """GET a JSON route from the relay. Raises ValueError on any failure."""
    import json
    import logging
    import time
    import urllib.parse
    import urllib.request

    log = logging.getLogger("yt-app")
    base, key = worker_cfg()
    if not base:
        log.warning("worker_get: relay NOT configured (base=None)")
        raise ValueError("relay not configured")
    qs = urllib.parse.urlencode({"key": key, **params})
    url = f"{base}{path}?{qs}"
    log.info("worker_get: calling %s (timeout=%d, retries=%d)", path, timeout, retries)
    last_exc = None
    last_detail = ""
    for attempt in range(1 + retries):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "yt-app"}),
                timeout=timeout,
            ) as r:
                data = json.load(r)
            log.info("worker_get: %s OK (attempt %d)", path, attempt + 1)
            return data
        except Exception as e:
            last_exc = e
            detail = ""
            http_code = None
            try:
                import urllib.error

                if isinstance(e, urllib.error.HTTPError):
                    http_code = e.code
                    body = e.read().decode("utf-8", errors="replace")
                    try:
                        detail = json.loads(body).get("detail", "")
                    except Exception:
                        detail = body[:200]
                    last_detail = detail
            except Exception:
                pass
            log.warning(
                "worker_get: %s attempt %d FAILED http=%s detail=%s",
                path, attempt + 1, http_code, detail[:200] if detail else type(e).__name__,
            )
            if http_code == 502 and attempt < retries:
                log.info("worker_get: %s 502, retrying in 1.5s...", path)
                time.sleep(1.5)
                continue
            break
    log.warning("worker_get: %s ALL ATTEMPTS FAILED: %s", path, last_detail or type(last_exc).__name__)
    raise ValueError(last_detail or f"relay error: {type(last_exc).__name__}")


def worker_video_info(video_url: str) -> dict:
    """Same shape as videoinfo.get_video_info, via the relay."""
    vid = _video_id_from_url(video_url)
    if not vid:
        raise ValueError("Could not read video id from URL.")
    return worker_get("/video", {"id": vid})


def worker_playlist_videos(playlist_url: str) -> dict:
    """Same shape as playlist.get_playlist_videos, via the relay."""
    pid = _playlist_id_from_url(playlist_url)
    if not pid:
        raise ValueError("Could not read playlist id from URL.")
    return worker_get("/playlist", {"id": pid})


def friendly_error(e: Exception, what: str) -> ValueError:
    """Map yt-dlp failures to messages safe for end users."""
    if is_bot_check(e):
        return ValueError(
            f"{what}: YouTube asked for verification (bot check). "
            "Try again in a minute."
        )
    return ValueError(f"{what}: {e}")
