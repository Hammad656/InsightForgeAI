from celery import Celery

from app.core.config import get_settings

celery_app = Celery("insightforge", broker=get_settings().redis_url,
                    include=["app.jobs.imports", "app.jobs.reports", "app.jobs.reconciliation", "app.jobs.analyses",
                             "app.jobs.schedules", "app.jobs.evaluations", "app.jobs.connectors", "app.jobs.forecasts"])
celery_app.conf.update(task_serializer="json", accept_content=["json"], result_serializer="json")

celery_app.conf.beat_schedule = {
    "dispatch-executive-report-schedules": {"task": "app.jobs.schedules.dispatch_schedules", "schedule": 60.0},
}
