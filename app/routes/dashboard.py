from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import Date, cast, case, func
from sqlalchemy.orm import Session

from app.core.jwt import verificar_token
from app.database_.database import get_db
from app.models.campaign import Campaign
from app.models.client import Client
from app.models.company import Company
from app.models.email_log import EmailLog
from app.models.email_template import EmailTemplate
from app.models.user import User

router = APIRouter(prefix="/empresas", tags=["Dashboard"])


def get_current_user(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
) -> User:
    """
    Lê Authorization: Bearer <token>, decodifica e busca o usuário no banco.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header")

    token = parts[1].strip()
    payload = verificar_token(token)

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token sem 'sub'")

    # User.id é UUID; token vem string
    try:
        user_uuid = UUID(str(user_id))
    except Exception:
        raise HTTPException(
            status_code=401, detail="Token 'sub' inválido (UUID esperado)"
        )

    user = db.query(User).filter(User.id == user_uuid).first()
    if not user:
        raise HTTPException(status_code=401, detail="Usuário não encontrado")

    return user


@router.get("/{company_id}/dashboard/metrics")
def dashboard_metrics(
    company_id: str,
    days: int = 30,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    KPI + séries temporais + últimos eventos.
    """
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    if str(company.owner_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Sem acesso a essa empresa")

    days = max(1, min(int(days or 30), 365))
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    # ---------- KPIs básicos ----------
    total_clients = (
        db.query(func.count(Client.id))
        .filter(Client.company_id == company_id)
        .scalar()
        or 0
    )

    total_templates = (
        db.query(func.count(EmailTemplate.id))
        .filter(EmailTemplate.company_id == company_id)
        .scalar()
        or 0
    )

    total_campaigns = (
        db.query(func.count(Campaign.id))
        .filter(Campaign.company_id == company_id)
        .scalar()
        or 0
    )

    # ---------- Logs no período ----------
    q_logs_period = db.query(EmailLog).filter(
        EmailLog.company_id == company_id,
        EmailLog.created_at >= since,
    )

    sent_period = q_logs_period.filter(func.upper(EmailLog.status) == "SENT").count()
    failed_period = q_logs_period.filter(func.upper(EmailLog.status) == "FAILED").count()

    pending_period = q_logs_period.filter(
        func.upper(EmailLog.status).notin_(["SENT", "FAILED"])
    ).count()

    total_period = q_logs_period.count()

    success_rate = 0.0
    if (sent_period + failed_period) > 0:
        success_rate = round((sent_period / (sent_period + failed_period)) * 100.0, 2)

    # ---------- Série por dia (SENT/FAILED) ----------
    rows = (
        db.query(
            cast(func.date_trunc("day", EmailLog.created_at), Date).label("day"),
            func.sum(
                case((func.upper(EmailLog.status) == "SENT", 1), else_=0)
            ).label("sent"),
            func.sum(
                case((func.upper(EmailLog.status) == "FAILED", 1), else_=0)
            ).label("failed"),
            func.count(EmailLog.id).label("total"),
        )
        .filter(
            EmailLog.company_id == company_id,
            EmailLog.created_at >= since,
        )
        .group_by("day")
        .order_by("day")
        .all()
    )

    series_map: Dict[str, Dict[str, int]] = {}
    for r in rows:
        key = r.day.isoformat() if r.day else None
        if not key:
            continue
        series_map[key] = {
            "sent": int(r.sent or 0),
            "failed": int(r.failed or 0),
            "total": int(r.total or 0),
        }

    # exatamente "days" dias
    series: List[Dict[str, Any]] = []
    for i in range(days - 1, -1, -1):
        d = (now - timedelta(days=i)).date().isoformat()
        v = series_map.get(d, {"sent": 0, "failed": 0, "total": 0})
        series.append({"date": d, **v})

    # ---------- Últimas campanhas ----------
    recent_campaigns = (
        db.query(Campaign)
        .filter(Campaign.company_id == company_id)
        .order_by(Campaign.created_at.desc())
        .limit(10)
        .all()
    )

    campaigns_out = [
        {
            "id": str(c.id),
            "name": c.name,
            "status": c.status,
            "mode": getattr(c, "mode", None),
            "rate_per_min": getattr(c, "rate_per_min", None),
            "template_id": str(c.template_id),
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "next_run_at": c.next_run_at.isoformat()
            if getattr(c, "next_run_at", None)
            else None,
        }
        for c in recent_campaigns
    ]

    # ---------- Últimos envios ----------
    recent_logs = (
        db.query(EmailLog)
        .filter(EmailLog.company_id == company_id)
        .order_by(EmailLog.created_at.desc())
        .limit(20)
        .all()
    )

    logs_out = [
        {
            "id": str(l.id),
            "status": l.status,
            "to_email": l.to_email,
            "subject": l.subject_rendered,
            "error": l.error_message,
            "campaign_id": str(l.campaign_id) if l.campaign_id else None,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in recent_logs
    ]

    return {
        "range_days": days,
        "company": {"id": str(company.id), "nome": company.nome},
        "kpis": {
            "clients": int(total_clients),
            "templates": int(total_templates),
            "campaigns": int(total_campaigns),
            "sent": int(sent_period),
            "failed": int(failed_period),
            "pending": int(pending_period),
            "total": int(total_period),
            "success_rate": success_rate,
        },
        "series": series,
        "recent_campaigns": campaigns_out,
        "recent_sends": logs_out,
        "updated_at": now.isoformat(),
    }