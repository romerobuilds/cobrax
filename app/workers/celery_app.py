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


