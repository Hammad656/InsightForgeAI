from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.health import router
from app.core.config import get_settings

app = FastAPI(title="InsightForge AI API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["Content-Type"],
)
app.include_router(router, prefix="/api/v1")
