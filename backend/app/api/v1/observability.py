from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.auth import csrf, digest, limit_key
from app.core.telemetry import emit

router = APIRouter()


class BrowserEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    name: Literal["runtime_error", "unhandled_rejection", "FCP", "LCP", "INP", "TTFB"]
    value: Annotated[float, Field(ge=0, le=120_000)]


@router.post("/telemetry/browser", status_code=204, dependencies=[Depends(csrf)])
def browser_telemetry(event: BrowserEvent, request: Request) -> None:
    address = request.client.host if request.client else "unknown"
    limit_key(f"if:telemetry:{digest(address)}", 180)
    if event.name in {"runtime_error", "unhandled_rejection"}:
        emit("browser.error", kind=event.name)
    else:
        emit("browser.performance", metric=event.name, duration_ms=round(event.value, 1))
