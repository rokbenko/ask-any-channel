"""GET /healthz — liveness only, no DB round-trip (the api role's aac doctor checks cover
readiness at container-healthcheck level)."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
