# app/routers/health.py
from fastapi import APIRouter, Depends
from sqlalchemy import text
import redis

from app.core.deps import get_current_user
from app.database_.database import get_db
from app.models.user import User
from sqlalchemy.orm import Session

router = APIRouter(tags=["Health"])

# =========================
# AUTH / ME
# =========================

@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {
        "id": str(user.id),
        "email": user.email,
        "nome": user.nome,
    }

# =========================
# HEALTHCHECKS
# =========================

@router.get("/health")
def health_check():
    """
    Liveness probe — só diz se a API está no ar.
    """
    return {
        "status": "ok",
        "service": "cobrax",
    }


@router.get("/health/deps")
def health_dependencies(db: Session = Depends(get_db)):
    """
    Readiness probe — testa dependências reais:
    - PostgreSQL
    - Redis
    """
    checks = {
        "database": "ok",
        "redis": "ok",
    }

    # 🔍 Testa Postgres
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        checks["database"] = f"error: {str(e)}"

    # 🔍 Testa Redis
    try:
        r = redis.Redis(host="localhost", port=6379, db=0, socket_connect_timeout=2)
        r.ping()
    except Exception as e:
        checks["redis"] = f"error: {str(e)}"

    overall_status = "ok" if all(v == "ok" for v in checks.values()) else "error"

    return {
        "status": overall_status,
        "service": "cobrax",
        "checks": checks,
    }
