"""Ops self-test for the Cloudflare relay (JWT-protected, no secrets exposed)."""
from fastapi import APIRouter, Depends

from auth import require_auth
from ytcore import worker_cfg, worker_get

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
