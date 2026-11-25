"""Loopback-only synthetic benchmark ASGI entry point."""

from time import sleep

from starlette.middleware.trustedhost import TrustedHostMiddleware
from test_m4 import MockProvider

from app.api.v1 import ai
from app.main import app


class BenchmarkProvider(MockProvider):
    def structured(self, schema, instructions, context):
        if context.get("question"):
            sleep(1.2)
        return super().structured(schema, instructions, context)


ai.get_provider = BenchmarkProvider
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
