from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

from app.api.v1 import auth, datasets, organizations
from app.api.v1.health import router
from app.core.config import get_settings
from app.core.upload_limit import UploadBodyLimit

app = FastAPI(title="InsightForge AI API", version="0.1.0")
app.add_middleware(UploadBodyLimit)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF"],
)
app.include_router(router, prefix="/api/v1")

for m2_router in (auth.router, organizations.router, datasets.router):
    app.include_router(m2_router, prefix="/api/v1")


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        {"detail": [{"loc": error["loc"], "msg": error["msg"]} for error in exc.errors()]},
        status_code=422,
    )

@app.middleware("http")
async def protect_private_responses(request: Request, call_next: RequestResponseEndpoint) -> Response:
    response = await call_next(request)
    if request.url.path.startswith(("/api/v1/auth/", "/api/v1/organizations")):
        response.headers["Cache-Control"] = "private, no-store"
    return response
