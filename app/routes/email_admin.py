# app/routes/email_admin.py
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.deps import get_current_user
from app.database_.database import get_db
from app.models.company import Company
from app.models.email_log import EmailLog
from app.models.user import User
from app.workers.tasks import send_email_job
from app.schemas.email_admin import CompanyEmailSettingsUpdate


router = APIRouter(prefix="/empresas/{company_id}/email-logs", tags=["Email Admin"])

ALLOWED_RATES = {5, 10, 15, 20, 25, 30}
CANCELABLE_STATUSES = ["PENDING", "SENDING", "RETRYING", "DEFERRED"]  # ✅ inclui DEFERRED


def _get_company_or_404(db: Session, company_id: UUID, user: User) -> Company:
    company = (
        db.query(Company)
        .filter(Company.id == company_id, Company.owner_id == user.id)
        .first()
    )
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return company


@router.get("/summary", status_code=status.HTTP_200_OK)
def summary(
    company_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    company = _get_company_or_404(db, company_id, user)

    totals = (
        db.query(EmailLog.status, func.count(EmailLog.id).label("count"))
        .filter(EmailLog.company_id == company.id)
        .group_by(EmailLog.status)
        .all()
    )
    summary_map = {row.status: row.count for row in totals}

    recent = (
        db.query(EmailLog.id, EmailLog.status, EmailLog.error_message, EmailLog.created_at)
        .filter(EmailLog.company_id == company.id)
        .order_by(EmailLog.created_at.desc())
        .limit(10)
        .all()
    )
    recent_list = [
        {
            "id": str(r.id),
            "status": r.status,
            "error_message": r.error_message,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in recent
    ]

    return {"summary": summary_map, "recent": recent_list}


# =========================
# SMTP PAUSE / RESUME
# =========================

@router.post("/pause-smtp", status_code=status.HTTP_200_OK)
def pause_smtp(
    company_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, user)
    company.smtp_paused = True
    db.commit()
    return {"smtp_paused": True}


@router.post("/resume-smtp", status_code=status.HTTP_200_OK)
def resume_smtp(
    company_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, user)
    company.smtp_paused = False
    db.commit()
    return {"smtp_paused": False}


# =========================
# CONFIG: RATE / DAILY LIMIT
# =========================

@router.post("/set-rate", status_code=status.HTTP_200_OK)
def set_rate(
    company_id: UUID,
    rate_per_min: int = Query(..., description="Use 5,10,15,20,25 ou 30"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, user)

    if rate_per_min not in ALLOWED_RATES:
        raise HTTPException(
            status_code=422,
            detail="rate_per_min inválido. Use 5,10,15,20,25 ou 30.",
        )

    company.rate_per_min = int(rate_per_min)
    db.commit()
    return {"rate_per_min": company.rate_per_min}


@router.post("/set-daily-limit", status_code=status.HTTP_200_OK)
def set_daily_limit(
    company_id: UUID,
    daily_email_limit: Optional[int] = Query(default=None, description="Ex: 500. Use null para ilimitado."),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, user)

    if daily_email_limit is not None and daily_email_limit <= 0:
        raise HTTPException(status_code=422, detail="daily_email_limit deve ser > 0 ou null.")

    company.daily_email_limit = daily_email_limit
    db.commit()
    return {"daily_email_limit": company.daily_email_limit}


# =========================
# CANCELAMENTO
# =========================

@router.post("/cancel-pending", status_code=status.HTTP_200_OK)
def cancel_pending(
    company_id: UUID,
    template_id: Optional[UUID] = Query(default=None),
    reason: str = Query(default="Cancelado pelo admin"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Cancela logs em PENDING/SENDING/RETRYING/DEFERRED (opcionalmente filtrando por template_id).
    """
    company = _get_company_or_404(db, company_id, user)

    q = db.query(EmailLog).filter(
        EmailLog.company_id == company.id,
        EmailLog.status.in_(CANCELABLE_STATUSES),
        EmailLog.cancelled_at.is_(None),
    )

    if template_id:
        q = q.filter(EmailLog.template_id == template_id)

    logs = q.all()
    if not logs:
        return {"cancelled": 0, "template_id": str(template_id) if template_id else None}

    now = datetime.now(timezone.utc)
    for log in logs:
        log.status = "CANCELLED"
        log.cancelled_at = now
        log.cancelled_reason = reason
        log.error_message = (log.error_message or "") + " | CANCELLED"

    db.commit()
    return {"cancelled": len(logs), "template_id": str(template_id) if template_id else None}


@router.post("/{log_id}/cancel", status_code=status.HTTP_200_OK)
def cancel_one(
    company_id: UUID,
    log_id: UUID,
    reason: str = Query(default="Cancelado pelo admin"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, user)

    log = (
        db.query(EmailLog)
        .filter(EmailLog.id == log_id, EmailLog.company_id == company.id)
        .first()
    )
    if not log:
        raise HTTPException(status_code=404, detail="Log não encontrado")

    if log.cancelled_at is not None or log.status == "CANCELLED":
        return {"cancelled": str(log.id), "already_cancelled": True}

    log.status = "CANCELLED"
    log.cancelled_at = datetime.now(timezone.utc)
    log.cancelled_reason = reason
    log.error_message = (log.error_message or "") + " | CANCELLED"
    db.commit()

    return {"cancelled": str(log.id)}


# =========================
# RETRY / REQUEUE
# =========================

@router.post("/{log_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_log(
    company_id: UUID,
    log_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, user)

    log = (
        db.query(EmailLog)
        .filter(EmailLog.id == log_id, EmailLog.company_id == company.id)
        .first()
    )
    if not log:
        raise HTTPException(status_code=404, detail="Log não encontrado")

    if log.cancelled_at is not None or log.status == "CANCELLED":
        raise HTTPException(status_code=422, detail="Log cancelado não pode ser reenfileirado")

    if not log.to_email:
        raise HTTPException(status_code=422, detail="Log sem to_email não pode ser reenfileirado")

    log.status = "PENDING"
    log.error_message = None
    db.commit()

    try:
        send_email_job.delay(str(log.id))
    except Exception as e:
        log.status = "FAILED"
        log.error_message = f"Falha ao enfileirar: {e}"
        db.commit()
        raise HTTPException(status_code=500, detail="Falha ao enfileirar job")

    return {"queued": str(log.id)}


@router.post("/requeue-failed", status_code=status.HTTP_202_ACCEPTED)
def requeue_failed(
    company_id: UUID,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, user)

    failed_logs = (
        db.query(EmailLog)
        .filter(
            EmailLog.company_id == company.id,
            EmailLog.status == "FAILED",
            EmailLog.cancelled_at.is_(None),
        )
        .order_by(EmailLog.created_at.asc())
        .limit(limit)
        .all()
    )

    if not failed_logs:
        return {"queued": 0, "queued_ids": []}

    queued_ids = []
    for log in failed_logs:
        if not log.to_email:
            log.error_message = (log.error_message or "") + " | Skip: sem to_email"
            db.commit()
            continue

        log.status = "PENDING"
        log.error_message = None
        db.commit()

        try:
            send_email_job.delay(str(log.id))
            queued_ids.append(str(log.id))
        except Exception as e:
            log.status = "FAILED"
            log.error_message = f"Falha ao enfileirar: {e}"
            db.commit()

    return {"queued": len(queued_ids), "queued_ids": queued_ids}


@router.get("/settings", status_code=status.HTTP_200_OK)
def get_settings(
    company_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, user)
    return {
        "smtp_paused": bool(getattr(company, "smtp_paused", False)),
        "rate_per_min": int(getattr(company, "rate_per_min", 20)),
        "daily_email_limit": getattr(company, "daily_email_limit", None),
        "emails_sent_today": int(getattr(company, "emails_sent_today", 0) or 0),
        "emails_sent_today_at": company.emails_sent_today_at.isoformat()
        if getattr(company, "emails_sent_today_at", None)
        else None,
    }


@router.patch("/settings", status_code=status.HTTP_200_OK)
def update_settings(
    company_id: UUID,
    payload: CompanyEmailSettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, user)

    if payload.rate_per_min is not None and payload.rate_per_min not in ALLOWED_RATES:
        raise HTTPException(status_code=422, detail="rate_per_min inválido. Use 5,10,15,20,25 ou 30.")

    if payload.smtp_paused is not None:
        company.smtp_paused = payload.smtp_paused

    if payload.rate_per_min is not None:
        company.rate_per_min = payload.rate_per_min

    if payload.clear_daily_limit:
        company.daily_email_limit = None
    elif payload.daily_email_limit is not None:
        company.daily_email_limit = payload.daily_email_limit

    db.commit()
    db.refresh(company)

    return {
        "smtp_paused": company.smtp_paused,
        "rate_per_min": company.rate_per_min,
        "daily_email_limit": company.daily_email_limit,
    }

# =========================
# REQUEUE PENDING / RETRYING (retomar fila)
# =========================

@router.post("/requeue-pending", status_code=status.HTTP_202_ACCEPTED)
def requeue_pending(
    company_id: UUID,
    template_id: Optional[UUID] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    include_sending: bool = Query(default=False, description="Se true, inclui status SENDING também"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Reenfileira logs que ficaram travados em PENDING/RETRYING (opcionalmente SENDING).
    Útil após dar resume-smtp ou após restart do worker.
    """
    company = _get_company_or_404(db, company_id, user)

    statuses = ["PENDING", "RETRYING"]
    if include_sending:
        statuses.append("SENDING")

    q = db.query(EmailLog).filter(
        EmailLog.company_id == company.id,
        EmailLog.status.in_(statuses),
        EmailLog.cancelled_at.is_(None),
    )

    if template_id:
        q = q.filter(EmailLog.template_id == template_id)

    logs = (
        q.order_by(EmailLog.created_at.asc())
        .limit(limit)
        .all()
    )

    if not logs:
        return {"queued": 0, "queued_ids": [], "template_id": str(template_id) if template_id else None}

    queued_ids = []
    skipped = 0

    for log in logs:
        if not log.to_email:
            skipped += 1
            log.error_message = (log.error_message or "") + " | Skip requeue: sem to_email"
            db.commit()
            continue

        # mantém status como está (PENDING/RETRYING/SENDING), só reenfileira o job
        try:
            send_email_job.delay(str(log.id))
            queued_ids.append(str(log.id))
        except Exception as e:
            # não muda status aqui, só registra erro
            log.error_message = (log.error_message or "") + f" | Falha ao reenfileirar: {e}"
            db.commit()

    return {
        "queued": len(queued_ids),
        "skipped": skipped,
        "queued_ids": queued_ids,
        "template_id": str(template_id) if template_id else None,
        "include_sending": include_sending,
    }
