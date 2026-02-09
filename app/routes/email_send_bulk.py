# app/routes/email_send_bulk.py

from uuid import UUID
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database_.database import get_db

from app.models.company import Company
from app.models.client import Client
from app.models.email_template import EmailTemplate
from app.models.email_log import EmailLog
from app.models.user import User

from app.schemas.email_send_bulk import EmailSendBulkRequest

from app.core.template_vars import build_default_context
from app.services.template_renderer import render_email_template
from app.workers.tasks import send_email_job


router = APIRouter(
    prefix="/empresas/{company_id}/templates/{template_id}",
    tags=["Envio de Email"],
)

ALLOWED_RATES = {5, 10, 15, 20, 25, 30}
DEFAULT_RATE = 15


def _get_company_or_404(db: Session, company_id: UUID, user: User) -> Company:
    company = (
        db.query(Company)
        .filter(Company.id == company_id, Company.owner_id == user.id)
        .first()
    )
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return company


def _get_template_or_404(db: Session, template_id: UUID, company_id: UUID) -> EmailTemplate:
    template = (
        db.query(EmailTemplate)
        .filter(EmailTemplate.id == template_id, EmailTemplate.company_id == company_id)
        .first()
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template não encontrado")
    return template


def _normalize_rate_per_min(payload: EmailSendBulkRequest) -> int:
    # aceita rate_per_min opcional (se não vier, usa DEFAULT_RATE)
    rate = getattr(payload, "rate_per_min", None) or DEFAULT_RATE

    if rate not in ALLOWED_RATES:
        raise HTTPException(
            status_code=422,
            detail="rate_per_min inválido. Use 5,10,15,20,25 ou 30.",
        )
    return rate


def _queue_for_clients(
    db: Session,
    company: Company,
    template: EmailTemplate,
    clients: List[Client],
    extra_context: dict,
    rate_per_min: int,
):
    interval = 60 / rate_per_min  # ex: 20/min = 3s
    queued_ids: List[str] = []
    failed_ids: List[str] = []

    for i, client in enumerate(clients):
        if not client.email:
            failed_ids.append(str(client.id))
            continue

        try:
            context = build_default_context(
                company=company,
                client=client,
                extra=extra_context,
            )

            rendered = render_email_template(
                subject_tpl=template.assunto,
                body_tpl=template.corpo_html,
                context=context,
            )

            log = EmailLog(
                company_id=company.id,
                client_id=client.id,
                template_id=template.id,
                status="PENDING",
                subject_rendered=rendered.subject,
                body_rendered=rendered.body,
                to_email=client.email,
                to_name=getattr(client, "nome", None),
            )

            db.add(log)
            db.commit()
            db.refresh(log)

            # ⏱️ rate-limit REAL via countdown
            send_email_job.apply_async(
                args=[str(log.id)],
                countdown=int(round(i * interval)),
            )

            queued_ids.append(str(log.id))

        except Exception:
            db.rollback()
            failed_ids.append(str(client.id))

    return {
        "queued": len(queued_ids),
        "rate_per_min": rate_per_min,
        "estimated_minutes": round((len(queued_ids) / rate_per_min), 2) if queued_ids else 0,
        "queued_ids": queued_ids,
        "failed_ids": failed_ids,
    }


@router.post("/send-bulk", status_code=status.HTTP_200_OK)
def enviar_template_em_lote(
    company_id: UUID,
    template_id: UUID,
    payload: EmailSendBulkRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, user)
    template = _get_template_or_404(db, template_id, company_id)

    rate_per_min = _normalize_rate_per_min(payload)

    # aqui depende do teu schema: payload.client_ids OU payload.client_idS
    client_ids = getattr(payload, "client_ids", None) or getattr(payload, "client_idS", None)

    if not client_ids:
        raise HTTPException(status_code=422, detail="client_ids é obrigatório para /send-bulk")

    clients = (
        db.query(Client)
        .filter(Client.company_id == company_id, Client.id.in_(client_ids))
        .all()
    )
    if not clients:
        raise HTTPException(status_code=404, detail="Nenhum cliente encontrado com esses IDs")

    return _queue_for_clients(db, company, template, clients, payload.context or {}, rate_per_min)


@router.post("/send-bulk/all", status_code=status.HTTP_200_OK)
def enviar_template_para_todos(
    company_id: UUID,
    template_id: UUID,
    payload: EmailSendBulkRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, user)
    template = _get_template_or_404(db, template_id, company_id)

    rate_per_min = _normalize_rate_per_min(payload)

    clients = db.query(Client).filter(Client.company_id == company_id).all()
    if not clients:
        raise HTTPException(status_code=400, detail="Nenhum cliente cadastrado")

    return _queue_for_clients(db, company, template, clients, payload.context or {}, rate_per_min)
