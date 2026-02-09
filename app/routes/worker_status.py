# app/routes/worker_status.py
from fastapi import APIRouter
from app.workers.celery_app import celery_app

router = APIRouter(prefix="/worker", tags=["Worker"])

@router.get("/status")
def worker_status():
    insp = celery_app.control.inspect(timeout=1.0)

    try:
        ping = insp.ping()          # workers respondem aqui
        active = insp.active()      # tarefas em execução
        reserved = insp.reserved()  # tarefas reservadas
        scheduled = insp.scheduled()# tarefas agendadas (countdown/eta)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    return {
        "ok": True,
        "ping": ping,
        "active": active,
        "reserved": reserved,
        "scheduled": scheduled,
    }
