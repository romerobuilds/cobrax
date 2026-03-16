# app/routes/email_log.py
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database_.database import get_db
from app.models.company import Company
from app.models.company_user import CompanyUser
from app.models.email_log import EmailLog
from app.models.user import User
from app.schemas.email_log import EmailLogPublic


router = APIRouter(
    prefix="/empresas/{company_id}/logs",
    tags=["Logs de Email"],
)


def _get_company_or_404(db: Session, company_id: UUID, user: User) -> Company:
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    if user.is_master:
        if str(company.owner_id) == str(user.id):
            return company

        membership = (
            db.query(CompanyUser)
            .filter(
                CompanyUser.company_id == company_id,
                CompanyUser.user_id == user.id,
                CompanyUser.is_active.is_(True),
            )
            .first()
        )
        if membership:
            return company

        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    membership = (
        db.query(CompanyUser)
        .filter(
            CompanyUser.company_id == company_id,
            CompanyUser.user_id == user.id,
            CompanyUser.is_active.is_(True),
        )
        .first()
    )
    if not membership:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    return company


@router.get("/", response_model=List[EmailLogPublic])
def listar_logs(
    company_id: UUID,
    status: Optional[str] = Query(
        default=None,
        description="Filtra por status (PENDING, SENDING, SENT, FAILED, CANCELLED)",
    ),
    template_id: Optional[UUID] = Query(default=None),
    client_id: Optional[UUID] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_company_or_404(db, company_id, user)

    q = db.query(EmailLog).filter(EmailLog.company_id == company_id)

    if status:
        q = q.filter(EmailLog.status == status)

    if template_id:
        q = q.filter(EmailLog.template_id == template_id)

    if client_id:
        q = q.filter(EmailLog.client_id == client_id)

    return q.order_by(EmailLog.created_at.desc()).limit(limit).all()


@router.get("/stats", status_code=200)
def logs_stats(
    company_id: UUID,
    hours: int = Query(default=24, ge=1, le=720, description="Janela em horas (padrão 24h, máx 30 dias)"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    _get_company_or_404(db, company_id, user)

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)

    totals = (
        db.query(EmailLog.status, func.count(EmailLog.id))
        .filter(EmailLog.company_id == company_id)
        .group_by(EmailLog.status)
        .all()
    )
    totals_map = {status: int(count) for status, count in totals}

    recent = (
        db.query(EmailLog.status, func.count(EmailLog.id))
        .filter(EmailLog.company_id == company_id, EmailLog.created_at >= since)
        .group_by(EmailLog.status)
        .all()
    )
    recent_map = {status: int(count) for status, count in recent}

    top_errors = (
        db.query(EmailLog.error_message, func.count(EmailLog.id).label("count"))
        .filter(
            EmailLog.company_id == company_id,
            EmailLog.created_at >= since,
            EmailLog.status == "FAILED",
            EmailLog.error_message.isnot(None),
        )
        .group_by(EmailLog.error_message)
        .order_by(func.count(EmailLog.id).desc())
        .limit(8)
        .all()
    )

    sent_recent = int(recent_map.get("SENT", 0))
    failed_recent = int(recent_map.get("FAILED", 0))
    total_recent = sum(int(v) for v in recent_map.values())
    failure_rate = round((failed_recent / total_recent) * 100, 2) if total_recent else 0.0

    return {
        "window": {
            "hours": hours,
            "since": since.isoformat(),
            "now": now.isoformat(),
        },
        "totals": totals_map,
        "recent": recent_map,
        "recent_total": total_recent,
        "failure_rate_percent": failure_rate,
        "top_errors": [
            {"error_message": (msg or ""), "count": int(count)}
            for msg, count in top_errors
        ],
        "quick": {
            "sent_recent": sent_recent,
            "failed_recent": failed_recent,
        },
    }


@router.get("/{log_id}", response_model=EmailLogPublic)
def obter_log(
    company_id: UUID,
    log_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_company_or_404(db, company_id, user)

    log = (
        db.query(EmailLog)
        .filter(EmailLog.company_id == company_id, EmailLog.id == log_id)
        .first()
    )
    if not log:
        raise HTTPException(status_code=404, detail="Log não encontrado")
    return log