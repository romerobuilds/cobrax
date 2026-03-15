# app/routes/email_template.py
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_company_for_current_user
from app.database_.database import get_db
from app.models.company import Company
from app.models.email_template import EmailTemplate
from app.models.user import User
from app.models.client import Client

from app.schemas.email_template import (
    EmailTemplateCreate,
    EmailTemplatePublic,
    EmailTemplateUpdate,
)
from app.schemas.template_preview import TemplatePreviewRequest, TemplatePreviewResponse
from app.core.template_vars import build_default_context
from app.services.template_renderer import render_email_template


router = APIRouter(
    prefix="/empresas/{company_id}/templates",
    tags=["Templates"],
    dependencies=[Depends(get_company_for_current_user)],
)


@router.post(
    "/",
    response_model=EmailTemplatePublic,
    status_code=status.HTTP_201_CREATED,
)
def criar_template(
    company_id: UUID,
    payload: EmailTemplateCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    existe = (
        db.query(EmailTemplate)
        .filter(EmailTemplate.company_id == company_id, EmailTemplate.nome == payload.nome)
        .first()
    )
    if existe:
        raise HTTPException(status_code=400, detail="Já existe um template com esse nome")

    template = EmailTemplate(
        company_id=company_id,
        nome=payload.nome,
        assunto=payload.assunto,
        corpo_html=payload.corpo_html,
        ativo=payload.ativo,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


@router.get("/", response_model=List[EmailTemplatePublic])
def listar_templates(
    company_id: UUID,
    db: Session = Depends(get_db),
):
    return (
        db.query(EmailTemplate)
        .filter(EmailTemplate.company_id == company_id)
        .order_by(EmailTemplate.created_at.desc())
        .all()
    )


@router.get("/{template_id}", response_model=EmailTemplatePublic)
def obter_template(
    company_id: UUID,
    template_id: UUID,
    db: Session = Depends(get_db),
):
    template = (
        db.query(EmailTemplate)
        .filter(EmailTemplate.id == template_id, EmailTemplate.company_id == company_id)
        .first()
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template não encontrado")
    return template


@router.put("/{template_id}", response_model=EmailTemplatePublic)
def atualizar_template(
    company_id: UUID,
    template_id: UUID,
    payload: EmailTemplateUpdate,
    db: Session = Depends(get_db),
):
    template = (
        db.query(EmailTemplate)
        .filter(EmailTemplate.id == template_id, EmailTemplate.company_id == company_id)
        .first()
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template não encontrado")

    if payload.nome is not None:
        template.nome = payload.nome
    if payload.assunto is not None:
        template.assunto = payload.assunto
    if payload.corpo_html is not None:
        template.corpo_html = payload.corpo_html
    if payload.ativo is not None:
        template.ativo = payload.ativo

    db.commit()
    db.refresh(template)
    return template


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def deletar_template(
    company_id: UUID,
    template_id: UUID,
    db: Session = Depends(get_db),
):
    template = (
        db.query(EmailTemplate)
        .filter(EmailTemplate.id == template_id, EmailTemplate.company_id == company_id)
        .first()
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template não encontrado")

    db.delete(template)
    db.commit()
    return None


@router.post(
    "/{template_id}/preview",
    response_model=TemplatePreviewResponse,
)
def preview_template(
    company_id: UUID,
    template_id: UUID,
    payload: TemplatePreviewRequest,
    db: Session = Depends(get_db),
    company: Company = Depends(get_company_for_current_user),
):
    template = (
        db.query(EmailTemplate)
        .filter(EmailTemplate.id == template_id, EmailTemplate.company_id == company_id)
        .first()
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template não encontrado")

    client = (
        db.query(Client)
        .filter(Client.company_id == company_id)
        .order_by(Client.created_at.desc())
        .first()
    )
    if not client:
        raise HTTPException(
            status_code=400,
            detail="Crie pelo menos 1 cliente para testar o preview",
        )

    context = build_default_context(company=company, client=client, extra=payload.context)

    try:
        rendered = render_email_template(
            subject_tpl=template.assunto,
            body_tpl=template.corpo_html,
            context=context,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Erro ao renderizar template: {e}")

    return TemplatePreviewResponse(
        subject=rendered.subject,
        body=rendered.body,
        used_vars=sorted(list(rendered.used_vars)),
    )