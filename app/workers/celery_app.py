# app/workers/celery_app.py
import os
from celery import Celery

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "cobrax",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    timezone="America/Sao_Paulo",
    enable_utc=True,
)

celery_app.conf.beat_schedule = {
    "run-due-campaigns-every-30s": {
        "task": "campaigns.run_due_campaigns",
        "schedule": 30.0,
    },
    "sync-cakto-automations-every-1min": {
        "task": "cakto.sync_all_companies",
        "schedule": 60.0,
    },
}