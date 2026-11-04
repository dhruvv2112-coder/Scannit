"""Health check endpoint."""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
@router.get("/api/health")
async def healthz() -> dict:
    return {"status": "ok"}
