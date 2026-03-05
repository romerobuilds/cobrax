# app/routes/email_send.py
from __future__ import annotations

from uuid import UUID
from typing import Any, Dict

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


def _template_uses_link_pagamento(template: EmailTemplate) -> bool:
    """
    Regra: se o template usa {{link_pagamento}}, NÃO precisa anexar PDF.
    Fazemos a checagem no template bruto (antes de renderizar).
    """
    needle = "{{link_pagamento}}"
    subj = (getattr(template, "assunto", None) or "")
    body = (getattr(template, "corpo_html", None) or "")
    return (needle in subj) or (needle in body)


def _is_billing_context(extra_ctx: Dict[str, Any] | None) -> bool:
    """
    Heurística simples para "parece cobrança".
    Como envio manual não tem campaign_id/tipo, usamos as chaves do context.
    """
    if not extra_ctx:
        return False

    keys = {str(k).strip().lower() for k in extra_ctx.keys()}

    billing_keys = {
        "valor",
        "vencimento",
        "numero_fatura",
        "fatura",
        "descricao",
        "observacao",
        "link_pagamento",
        "link_boleto",
        "boleto_url",
        "boleto_pdf_url",
        "bankslipurl",
        "bankslip_url",
        "bank_slip_url",
        "invoiceurl",
        "invoice_url",
        "payment_url",
    }
    return any(k in keys for k in billing_keys)


def _pick_payment_url(extra_ctx: Dict[str, Any] | None) -> str | None:
    if not extra_ctx:
        return None
    for k in ["link_pagamento", "invoiceUrl", "invoice_url", "payment_url", "link_boleto"]:
        if k in extra_ctx and extra_ctx.get(k):
            return str(extra_ctx.get(k))
    return None


def _pick_boleto_pdf_url(extra_ctx: Dict[str, Any] | None) -> str | None:
    """
    Atenção: no Asaas, o campo que costuma vir é bankSlipUrl (geralmente PDF/URL do boleto).
    """
    if not extra_ctx:
        return None
    for k in [
        "boleto_pdf_url",
        "bankSlipUrl",
        "bank_slip_url",
        "bankSlipURL",
        "bankslipUrl",
        "bankslip_url",
        "boleto_url",
        "pdf_url",
    ]:
        if k in extra_ctx and extra_ctx.get(k):
            return str(extra_ctx.get(k))
    return None


@router.post(
    "/send",
    response_model=EmailSendResponse,
    status_code=status.HTTP_202_ACCEPTED,
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

    extra_ctx: Dict[str, Any] = payload.context or {}

    # Renderiza (variáveis padrão + extras do payload)
    context = build_default_context(company=company, client=client, extra=extra_ctx)

    try:
        rendered = render_email_template(
            subject_tpl=template.assunto,
            body_tpl=template.corpo_html,
            context=context,
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Erro ao renderizar template: {e}")

    uses_link = _template_uses_link_pagamento(template)
    is_billing = _is_billing_context(extra_ctx)

    # ✅ Regra:
    # - se usa {{link_pagamento}} -> não anexa
    # - se NÃO usa e é cobrança -> tenta anexar
    should_attach_pdf = bool(is_billing and (not uses_link))

    payment_url = _pick_payment_url(extra_ctx)
    boleto_pdf_url = _pick_boleto_pdf_url(extra_ctx)

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

    # ✅ grava flags/urls se existirem no model
    # (isso evita quebrar caso seu model ainda esteja diferente)
    if hasattr(log, "should_attach_pdf"):
        setattr(log, "should_attach_pdf", should_attach_pdf)

    # Aqui guardamos a URL do boleto (PDF/URL do Asaas) para o worker anexar.
    # Preferimos a do boleto; se não tiver, guarda payment_url como fallback.
    if hasattr(log, "asaas_bank_slip_url"):
        setattr(log, "asaas_bank_slip_url", boleto_pdf_url or None)

    if hasattr(log, "payment_url"):
        setattr(log, "payment_url", payment_url or None)

    db.add(log)
    db.commit()
    db.refresh(log)

    try:
        send_email_job.delay(str(log.id))
    except Exception as e:
        log.status = "FAILED"
        log.error_message = f"Falha ao enfileirar job: {e}"
        db.commit()
        raise HTTPException(
            status_code=500,
            detail="Fila/worker não disponível. Verifique Redis/Celery.",
        )

    return EmailSendResponse(
        log_id=log.id,
        status=log.status,
        subject=rendered.subject,
    )