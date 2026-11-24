from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from shopflow.core.db import get_engine

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def liveness():
    """Process is up. Kubernetes livenessProbe."""
    return {"status": "ok"}


@router.get("/readyz")
async def readiness():
    """Dependencies reachable. Kubernetes readinessProbe."""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"status": "unavailable", "db": str(exc)[:200]}, status_code=503)
    return {"status": "ok", "db": "ok"}
