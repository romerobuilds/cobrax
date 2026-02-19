# app/workers/celery_app.py
import os
from celery import Celery

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "cobrax",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.workers.tasks"],  # garante registro da task
)

celery_app.conf.update(
    task_track_started=True,          # Celery marca STARTED
    task_acks_late=True,              # só confirma depois de rodar
    worker_prefetch_multiplier=1,     # mais “justo” com filas
    timezone="America/Sao_Paulo",
    enable_utc=True,
)

celery_app.conf.beat_schedule = {
    "check-scheduled-campaigns-every-minute": {
        "task": "app.workers.scheduler.check_scheduled_campaigns",
        "schedule": 60.0,
    },
    "run-due-campaigns-every-30s": {
        "task": "campaigns.run_due_campaigns",
        "schedule": 30.0,  # a cada 30s
    },    
}



