from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import Date, cast, case, func
from sqlalchemy.orm import Session

from app.core.jwt import verificar_token
from app.database_.database import get_db
from app.models.billing_charge import BillingCharge
from app.models.campaign import Campaign
from app.models.client import Client
from app.models.company import Company
from app.models.company_user import CompanyUser
from app.models.email_log import EmailLog
from app.models.email_template import EmailTemplate
from app.models.user import User

router = APIRouter(prefix="/empresas", tags=["Dashboard"])


def get_current_user(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
) -> User:
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

    try:
        user_uuid = UUID(str(user_id))
    except Exception:
        raise HTTPException(status_code=401, detail="Token 'sub' inválido (UUID esperado)")

    user = db.query(User).filter(User.id == user_uuid).first()
    if not user:
        raise HTTPException(status_code=401, detail="Usuário não encontrado")

    return user


def _get_company_or_403(db: Session, company_id: str, user: User) -> Company:
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    if user.is_master:
        if str(company.owner_id) != str(user.id):
            raise HTTPException(status_code=403, detail="Sem acesso a essa empresa")
        return company

    membership = (
        db.query(CompanyUser)
        .filter(
            CompanyUser.company_id == company.id,
            CompanyUser.user_id == user.id,
            CompanyUser.is_active.is_(True),
        )
        .first()
    )
    if not membership:
        raise HTTPException(status_code=403, detail="Sem acesso a essa empresa")

    return company


@router.get("/{company_id}/dashboard/metrics")
def dashboard_metrics(
    company_id: str,
    days: int = 30,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_403(db, company_id, user)

    days = max(1, min(int(days or 30), 365))
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

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

    series: List[Dict[str, Any]] = []
    for i in range(days - 1, -1, -1):
        d = (now - timedelta(days=i)).date().isoformat()
        v = series_map.get(d, {"sent": 0, "failed": 0, "total": 0})
        pending = max(0, int(v["total"]) - int(v["sent"]) - int(v["failed"]))
        series.append({"date": d, **v, "pending": pending})

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
            "template_id": str(c.template_id) if getattr(c, "template_id", None) else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "next_run_at": c.next_run_at.isoformat() if getattr(c, "next_run_at", None) else None,
        }
        for c in recent_campaigns
    ]

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


@router.get("/{company_id}/dashboard/finance")
def dashboard_finance(
    company_id: str,
    limit: int = 10,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_company_or_403(db, company_id, user)

    limit = max(1, min(int(limit or 10), 50))
    now = datetime.now(timezone.utc).date()

    total_emitido = (
        db.query(func.coalesce(func.sum(BillingCharge.value), 0))
        .filter(BillingCharge.company_id == company_id)
        .scalar()
        or 0
    )

    total_pago = (
        db.query(func.coalesce(func.sum(BillingCharge.value), 0))
        .filter(
            BillingCharge.company_id == company_id,
            func.upper(BillingCharge.status) == "PAID",
        )
        .scalar()
        or 0
    )

    total_pendente = (
        db.query(func.coalesce(func.sum(BillingCharge.value), 0))
        .filter(
            BillingCharge.company_id == company_id,
            func.upper(BillingCharge.status) == "PENDING",
        )
        .scalar()
        or 0
    )

    total_vencido = (
        db.query(func.coalesce(func.sum(BillingCharge.value), 0))
        .filter(
            BillingCharge.company_id == company_id,
            func.upper(BillingCharge.status) == "OVERDUE",
        )
        .scalar()
        or 0
    )

    total_cancelado = (
        db.query(func.coalesce(func.sum(BillingCharge.value), 0))
        .filter(
            BillingCharge.company_id == company_id,
            func.upper(BillingCharge.status) == "CANCELLED",
        )
        .scalar()
        or 0
    )

    status_rows = (
        db.query(
            func.upper(BillingCharge.status).label("status"),
            func.count(BillingCharge.id).label("count"),
            func.coalesce(func.sum(BillingCharge.value), 0).label("amount"),
        )
        .filter(BillingCharge.company_id == company_id)
        .group_by(func.upper(BillingCharge.status))
        .order_by(func.upper(BillingCharge.status))
        .all()
    )

    by_status = [
        {
            "status": str(r.status or ""),
            "count": int(r.count or 0),
            "amount": float(r.amount or 0),
        }
        for r in status_rows
    ]

    recent = (
        db.query(BillingCharge, Client)
        .outerjoin(Client, Client.id == BillingCharge.client_id)
        .filter(BillingCharge.company_id == company_id)
        .order_by(BillingCharge.created_at.desc())
        .limit(limit)
        .all()
    )

    recent_charges = []
    for charge, client in recent:
        due_date = charge.due_date.isoformat() if charge.due_date else None
        overdue = bool(
            charge.due_date
            and charge.due_date < now
            and str(charge.status).upper() not in ("PAID", "CANCELLED")
        )

        recent_charges.append(
            {
                "id": str(charge.id),
                "campaign_id": str(charge.campaign_id) if charge.campaign_id else None,
                "client_id": str(charge.client_id) if charge.client_id else None,
                "client_nome": getattr(client, "nome", None) if client else None,
                "client_email": getattr(client, "email", None) if client else None,
                "asaas_payment_id": charge.asaas_payment_id,
                "value": float(charge.value or 0),
                "status": charge.status,
                "due_date": due_date,
                "invoice_url": charge.invoice_url,
                "bank_slip_url": charge.bank_slip_url,
                "created_at": charge.created_at.isoformat() if charge.created_at else None,
                "paid_at": charge.paid_at.isoformat() if charge.paid_at else None,
                "overdue": overdue,
            }
        )

    return {
        "summary": {
            "emitido": float(total_emitido),
            "pago": float(total_pago),
            "pendente": float(total_pendente),
            "vencido": float(total_vencido),
            "cancelado": float(total_cancelado),
        },
        "by_status": by_status,
        "recent_charges": recent_charges,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }