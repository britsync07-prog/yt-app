"""Health endpoint - replies 200 OK."""
from fastapi import APIRouter

from ytcore import pot_status, worker_cfg

router = APIRouter()


@router.get("/health", status_code=200)
def health_check():
    relay_base, _ = worker_cfg()
    return {
        "status": "ok",
        "pot": pot_status(),
        "relay": bool(relay_base),
    }
