from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import RequestResponseEndpoint
from starlette.concurrency import run_in_threadpool

from app.api.v1 import ai, analyses, analytics, auth, connectors, dashboards, datasets, evaluations, forecasts, observability, organizations, reconciliation, reports, schedules
from app.api.v1.health import router
from app.audit import AuditContext, audit, audit_context
from app.core.config import get_settings
from app.core.database import get_engine
from app.core.telemetry import correlation, emit
from app.core.tenant import set_org
from app.core.upload_limit import UploadBodyLimit

app = FastAPI(title="InsightForge AI API", version="0.1.0")
app.add_middleware(UploadBodyLimit)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF"],
)
app.include_router(router, prefix="/api/v1")

for application_router in (
    auth.router, organizations.router, datasets.router, analytics.router, ai.router, reports.router, reconciliation.router,
    analyses.router, dashboards.router, schedules.router, schedules.notices, evaluations.router, connectors.router, forecasts.router,
    observability.router,
):
    app.include_router(application_router, prefix="/api/v1")


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        {"detail": [{"loc": error["loc"], "msg": error["msg"]} for error in exc.errors()]},
        status_code=422,
    )

@app.middleware("http")
async def protect_private_responses(request: Request, call_next: RequestResponseEndpoint) -> Response:
    private = request.url.path.startswith((
        "/api/v1/auth/", "/api/v1/organizations", "/api/v1/ai/", "/api/v1/reports", "/api/v1/reconciliation-",
        "/api/v1/forecasts", "/api/v1/evaluations", "/api/v1/dashboards", "/api/v1/report-schedules", "/api/v1/notifications",
    ))
    context = AuditContext(uuid4())
    reset = audit_context.set(context) if private else None
    trace = correlation.set(str(context.correlation_id))
    started = perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Correlation-ID"] = str(context.correlation_id)
        if private:
            response.headers["Cache-Control"] = "private, no-store"
            if status in {401, 403, 404, 429}:
                await run_in_threadpool(record_denial, context, status)
        return response
    finally:
        route = getattr(request.scope.get("route"), "path", "unmatched")
        interactive = request.method != "OPTIONS" and request.url.path not in {
            "/api/v1/health", "/api/v1/ready", "/api/v1/telemetry/browser",
        }
        emit("api.request", route=route, status=status, interactive=int(interactive),
             server_error=int(status >= 500), duration_ms=round((perf_counter() - started) * 1000, 2))
        correlation.reset(trace)
        if reset is not None:
            audit_context.reset(reset)


def record_denial(context: AuditContext, status: int) -> None:
    try:
        with get_engine().begin() as db:
            if context.org:
                set_org(db, context.org)
            audit(db, f"authorization.denied.{status}", actor=context.actor, org=context.org,
                  correlation=context.correlation_id)
    except (SQLAlchemyError, RuntimeError):
        # Preserve the denial when the audit store is unavailable.
        return
