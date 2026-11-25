from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from redis import Redis
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import get_engine
from app.core.telemetry import emit

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok", "not_ready"]


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/ready", response_model=HealthResponse)
def ready(response: Response) -> HealthResponse:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        with Redis.from_url(get_settings().redis_url, socket_connect_timeout=3, socket_timeout=3) as redis:
            redis.ping()
            emit("broker.queue", default_queue_depth=redis.llen("celery"))
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="not_ready")
    return HealthResponse(status="ok")
