from celery import Celery

from app.core.config import get_settings

celery_app = Celery("insightforge", broker=get_settings().redis_url,
                    include=["app.jobs.imports", "app.jobs.reports", "app.jobs.reconciliation", "app.jobs.analyses"])
celery_app.conf.update(task_serializer="json", accept_content=["json"], result_serializer="json")
