import math
from time import perf_counter, time
from typing import Any
from uuid import UUID, uuid4

from celery import Celery
from celery.signals import before_task_publish, task_postrun, task_prerun

from app.core.config import get_settings
from app.core.telemetry import correlation, emit

celery_app = Celery("insightforge", broker=get_settings().redis_url,
                    include=["app.jobs.imports", "app.jobs.reports", "app.jobs.reconciliation", "app.jobs.analyses",
                             "app.jobs.schedules", "app.jobs.evaluations", "app.jobs.connectors", "app.jobs.forecasts"])
celery_app.conf.update(task_serializer="json", accept_content=["json"], result_serializer="json")


@before_task_publish.connect
def propagate_trace(headers: dict[str, Any] | None = None, **kwargs: Any) -> None:
    if headers is not None:
        headers["insightforge_trace"] = correlation.get() or str(uuid4())
        headers["insightforge_published_at"] = time()


@task_prerun.connect
def start_job(task: Any, **kwargs: Any) -> None:
    request = task.request
    try:
        trace = str(UUID((request.headers or {}).get("insightforge_trace", "")))
    except (TypeError, ValueError, AttributeError):
        trace = str(uuid4())
    request.insightforge_trace_token = correlation.set(trace)
    request.insightforge_started = perf_counter()
    published = (request.headers or {}).get("insightforge_published_at")
    if isinstance(published, (int, float)) and 0 < published <= time() and math.isfinite(published):
        emit("job.delivery", task=task.name, wait_ms=round((time() - published) * 1000, 2))


@task_postrun.connect
def finish_job(task: Any, state: str | None = None, **kwargs: Any) -> None:
    request = task.request
    if hasattr(request, "insightforge_started"):
        try:
            emit("job.execution", task=task.name, state=state, retries=request.retries,
                 duration_ms=round((perf_counter() - request.insightforge_started) * 1000, 2))
        finally:
            correlation.reset(request.insightforge_trace_token)

celery_app.conf.beat_schedule = {
    "dispatch-executive-report-schedules": {"task": "app.jobs.schedules.dispatch_schedules", "schedule": 60.0},
}
