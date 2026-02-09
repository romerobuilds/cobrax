# app/routes/email_send.py
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database_.database import get_db

from app.models.company import Company
from app.models.client import Client
from app.models.email_template import EmailTemplate
from app.models.email_log import EmailLog
from app.models.user import User

from app.schemas.email_send import EmailSendRequest, EmailSendResponse

from app.core.template_vars import build_default_context
from app.services.template_renderer import render_email_template

# ✅ fila/worker
from app.workers.tasks import send_email_job


router = APIRouter(
    prefix="/empresas/{company_id}/templates/{template_id}",
    tags=["Envio de Email"],
)


def _get_company_or_404(db: Session, company_id: UUID, user: User) -> Company:
    company = (
        db.query(Company)
        .filter(Company.id == company_id, Company.owner_id == user.id)
        .first()
    )
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return company


@router.post(
    "/send",
    response_model=EmailSendResponse,
    status_code=status.HTTP_202_ACCEPTED,  # ✅ agora é async via fila
)
def enviar_template(
    company_id: UUID,
    template_id: UUID,
    payload: EmailSendRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, user)

    template = (
        db.query(EmailTemplate)
        .filter(EmailTemplate.id == template_id, EmailTemplate.company_id == company_id)
        .first()
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template não encontrado")

    client = (
        db.query(Client)
        .filter(
            Client.id == payload.client_id,
            Client.company_id == company_id,
        )
        .first()
    )
    if not client:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")

    # Renderiza (com variáveis padrão + extras do payload)
    context = build_default_context(company=company, client=client, extra=payload.context)

    try:
        rendered = render_email_template(
            subject_tpl=template.assunto,
            body_tpl=template.corpo_html,
            context=context,
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Erro ao renderizar template: {e}")

    # ✅ Cria log PENDING ANTES de enviar (agora com to_email/to_name)
    log = EmailLog(
        company_id=company_id,
        client_id=client.id,
        template_id=template.id,
        status="PENDING",
        to_email=client.email,
        to_name=client.nome,
        subject_rendered=rendered.subject,
        body_rendered=rendered.body,
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    # ✅ Enfileira (não envia aqui)
    try:
        send_email_job.delay(str(log.id))
    except Exception as e:
        # Se a fila não estiver rodando, marca FAIL e devolve erro claro
        log.status = "FAILED"
        log.error_message = f"Falha ao enfileirar job: {e}"
        db.commit()
        raise HTTPException(status_code=500, detail="Fila/worker não disponível. Verifique Redis/Celery.")

    return EmailSendResponse(
        log_id=log.id,
        status=log.status,        # PENDING
        subject=rendered.subject,
    )
