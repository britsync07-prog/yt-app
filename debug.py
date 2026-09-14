"""Ops self-test for the Cloudflare relay (JWT-protected, no secrets exposed)."""
from fastapi import APIRouter, Depends

from auth import require_auth
from ytcore import worker_cfg, worker_get, worker_video_info

router = APIRouter()


@router.get("/debug/relay")
def debug_relay(_auth=Depends(require_auth)):
    base, _ = worker_cfg()
    if not base:
        return {"configured": False}
    try:
        data = worker_get("/health", {}, timeout=15)
        return {"configured": True, "reachable": True, "worker": data}
    except Exception as e:
        return {"configured": True, "reachable": False, "error": str(e)}


@router.get("/debug/video-test")
def debug_video_test(
    url: str = "https://www.youtube.com/watch?v=09Urt8CSQAA",
    _auth=Depends(require_auth),
):
    """Call worker_video_info directly and return the raw result or error."""
    import logging
    import traceback

    log = logging.getLogger("yt-app")
    base, key = worker_cfg()
    result = {"relay_configured": bool(base), "relay_url": base}
    try:
        info = worker_video_info(url)
        result["status"] = "ok"
        result["title"] = info.get("title")
        result["streams"] = len(info.get("streams") or [])
        result["duration"] = info.get("duration")
        log.info("debug/video-test OK title=%s", info.get("title"))
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
        log.error("debug/video-test FAILED: %s", e)
    return result


@router.get("/debug/pot-test")
def debug_pot_test(
    vid: str = "09Urt8CSQAA",
    full: int = 0,
    _auth=Depends(require_auth),
):
    """Probe the PO-token sidecar server and return a sample token."""
    import json
    import urllib.request
    from ytcore import POT_SERVER_URL

    result = {"sidecar_url": POT_SERVER_URL}
    try:
        with urllib.request.urlopen(POT_SERVER_URL + "/ping", timeout=5) as r:
            result["ping"] = r.read().decode()[:200]
    except Exception as e:
        result["ping_error"] = str(e)
    try:
        req = urllib.request.Request(
            POT_SERVER_URL + "/get_pot",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"content_binding": vid, "bypass_cache": False}).encode(),
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode()[:4000])
            result["po_token"] = (body.get("poToken") or "")[:40] + "..."
            result["content_binding"] = body.get("contentBinding")
            result["visitor_data"] = (body.get("visitorData") or "")[:40] + "..."
            if full:
                result["po_token_full"] = body.get("poToken")
                result["visitor_data_full"] = body.get("visitorData")
                result["raw_keys"] = list(body.keys())
    except Exception as e:
        result["generate_error"] = str(e)[:300]
    return result
