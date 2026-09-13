"""Health endpoint - replies 200 OK."""
from fastapi import APIRouter

from ytcore import pot_status

router = APIRouter()


@router.get("/health", status_code=200)
def health_check():
    return {"status": "ok", "pot": pot_status()}
