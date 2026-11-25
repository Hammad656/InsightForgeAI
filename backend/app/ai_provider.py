"""Server-side structured model calls. Tenant results stay inside the application."""
import json
from time import perf_counter
from typing import Protocol, TypeVar
from urllib.error import URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel

from app.core.config import get_settings
from app.core.telemetry import emit

Output = TypeVar("Output", bound=BaseModel)


class ProviderUnavailable(RuntimeError):
    pass


class Provider(Protocol):
    def structured(self, schema: type[Output], instructions: str, context: dict[str, object]) -> tuple[Output, int]: ...


class OpenAIProvider:
    def __init__(self, key: str, model: str) -> None:
        self.key = key
        self.model = model

    def structured(self, schema: type[Output], instructions: str, context: dict[str, object]) -> tuple[Output, int]:
        body = json.dumps({
            "model": self.model, "store": False, "max_completion_tokens": 600,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": json.dumps(context)},
            ],
            "response_format": {
                "type": "json_schema", "json_schema": {
                    "name": schema.__name__, "strict": True, "schema": schema.model_json_schema(),
                },
            },
        }).encode()
        request = Request("https://api.openai.com/v1/chat/completions", data=body, headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
        })
        started = perf_counter()
        try:
            with urlopen(request, timeout=12) as response:
                payload = json.loads(response.read(128_000))
            result = schema.model_validate_json(payload["choices"][0]["message"]["content"])
            tokens = int(payload["usage"]["total_tokens"])
            ceiling = get_settings().evaluation_cost_ceiling_per_million_tokens
            emit("ai.provider", outcome="ok", tokens=tokens, duration_ms=round((perf_counter() - started) * 1000, 2),
                 usd_cost_upper_bound=float(tokens * ceiling / 1_000_000) if ceiling is not None else None)
            return result, tokens
        except (URLError, OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            emit("ai.provider", outcome="failed", duration_ms=round((perf_counter() - started) * 1000, 2))
            raise ProviderUnavailable("The analysis provider is temporarily unavailable") from exc


def get_provider() -> Provider:
    settings = get_settings()
    key = settings.openai_api_key.get_secret_value()
    if settings.ai_provider != "openai" or not key:
        raise ProviderUnavailable("AI analytics is disabled")
    return OpenAIProvider(key, settings.openai_model)
