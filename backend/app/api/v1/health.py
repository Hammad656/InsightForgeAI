from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import get_engine
from app.core.queue_telemetry import observe_queue

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
            try:
                observe_queue(redis)
            except RedisError:
                pass  # Operational sampling does not decide readiness.
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="not_ready")
    return HealthResponse(status="ok")
