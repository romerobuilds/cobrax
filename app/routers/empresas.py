from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.database_.database import get_db
from app.models.company import Company
from app.models.user import User
from app.schemas.company_smtp_settings import (
    CompanySmtpSettingsOut,
    CompanySmtpSettingsUpdate,
    CompanySmtpTestIn,
)
from app.services.mailer import send_smtp_email

router = APIRouter(prefix="/empresas", tags=["Empresas"])


class EmpresaCreate(BaseModel):
    nome: str
    cnpj: str
    email: EmailStr


def _get_company_or_404(db: Session, company_id: UUID, user_id: UUID) -> Company:
    company = (
        db.query(Company)
        .filter(Company.id == company_id, Company.owner_id == user_id)
        .first()
    )
    if not company:
        raise HTTPException(
            status_code=404,
            detail="Empresa não encontrada ou não pertence a você",
        )
    return company


def _smtp_out(company: Company) -> CompanySmtpSettingsOut:
    password_configured = bool((company.smtp_password or "").strip())
    smtp_configured = bool(
        (company.smtp_host or "").strip()
        and company.smtp_port
        and (company.smtp_user or "").strip()
        and password_configured
        and (company.from_email or company.smtp_user)
    )

    return CompanySmtpSettingsOut(
        company_id=str(company.id),
        company_name=company.nome,
        smtp_host=company.smtp_host,
        smtp_port=company.smtp_port,
        smtp_user=company.smtp_user,
        smtp_use_tls=bool(company.smtp_use_tls),
        from_email=company.from_email,
        from_name=company.from_name,
        password_configured=password_configured,
        smtp_configured=smtp_configured,
    )


@router.get("/me")
def listar_minhas_empresas(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    companies = (
        db.query(Company)
        .filter(Company.owner_id == current_user.id)
        .order_by(Company.nome.asc())
        .all()
    )

    return [
        {
            "id": str(c.id),
            "nome": c.nome,
            "cnpj": c.cnpj,
            "email": c.email,
            "owner_id": str(c.owner_id),
        }
        for c in companies
    ]


@router.post("/")
def criar_empresa(
    empresa: EmpresaCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cnpj_exists = db.query(Company).filter(Company.cnpj == empresa.cnpj).first()
    if cnpj_exists:
        raise HTTPException(status_code=400, detail="Já existe uma empresa com este CNPJ")

    email_exists = db.query(Company).filter(Company.email == empresa.email).first()
    if email_exists:
        raise HTTPException(status_code=400, detail="Já existe uma empresa com este e-mail")

    nova_empresa = Company(
        nome=empresa.nome.strip(),
        cnpj=empresa.cnpj.strip(),
        email=empresa.email.strip().lower(),
        owner_id=current_user.id,
    )

    db.add(nova_empresa)
    db.commit()
    db.refresh(nova_empresa)

    return {
        "message": "Empresa criada com sucesso",
        "empresa": {
            "id": str(nova_empresa.id),
            "nome": nova_empresa.nome,
            "cnpj": nova_empresa.cnpj,
            "email": nova_empresa.email,
            "owner_id": str(nova_empresa.owner_id),
        },
    }


@router.get("/{company_id}/smtp-settings", response_model=CompanySmtpSettingsOut)
def obter_smtp_empresa(
    company_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, current_user.id)
    return _smtp_out(company)


@router.put("/{company_id}/smtp-settings", response_model=CompanySmtpSettingsOut)
def atualizar_smtp_empresa(
    company_id: UUID,
    payload: CompanySmtpSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, current_user.id)

    def clean_str(v):
        if v is None:
            return None
        s = str(v).strip()
        return s if s else ""

    if payload.smtp_host is not None:
        company.smtp_host = clean_str(payload.smtp_host) or None

    if payload.smtp_port is not None:
        company.smtp_port = int(payload.smtp_port)

    if payload.smtp_user is not None:
        company.smtp_user = clean_str(payload.smtp_user) or None

    if payload.smtp_password is not None:
        pwd = clean_str(payload.smtp_password)
        if pwd:
            company.smtp_password = pwd
        # se vier vazio, mantém a senha atual

    if payload.smtp_use_tls is not None:
        company.smtp_use_tls = bool(payload.smtp_use_tls)

    if payload.from_email is not None:
        company.from_email = str(payload.from_email).strip().lower()

    if payload.from_name is not None:
        company.from_name = clean_str(payload.from_name) or None

    db.add(company)
    db.commit()
    db.refresh(company)

    return _smtp_out(company)


@router.post("/{company_id}/smtp-settings/test")
def testar_smtp_empresa(
    company_id: UUID,
    payload: CompanySmtpTestIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, current_user.id)

    if not company.smtp_host:
        raise HTTPException(status_code=400, detail="Configure o servidor de e-mail antes de testar")

    if not company.smtp_port:
        raise HTTPException(status_code=400, detail="Configure a porta de envio antes de testar")

    if not company.smtp_user:
        raise HTTPException(status_code=400, detail="Informe o usuário de autenticação antes de testar")

    if not company.smtp_password:
        raise HTTPException(status_code=400, detail="Informe a senha do e-mail antes de testar")

    from_email = (company.from_email or company.smtp_user or "").strip()
    from_name = (company.from_name or company.nome or "Cobrax").strip()

    if not from_email:
        raise HTTPException(status_code=400, detail="Configure o e-mail remetente antes de testar")

    try:
        send_smtp_email(
            smtp_host=company.smtp_host,
            smtp_port=int(company.smtp_port),
            smtp_user=company.smtp_user,
            smtp_password=company.smtp_password,
            use_tls=bool(company.smtp_use_tls),
            from_email=from_email,
            from_name=from_name,
            to_email=str(payload.to_email),
            subject=f"Teste de envio • {company.nome}",
            body_text=(
                f"Olá!\n\n"
                f"Este é um teste de envio configurado na empresa {company.nome}.\n\n"
                f"Se você recebeu esta mensagem, a integração SMTP está funcionando."
            ),
            body_html=f"""
                <div style="font-family:Arial,sans-serif;background:#f8fafc;padding:24px;color:#0f172a;">
                  <div style="max-width:640px;margin:0 auto;background:#ffffff;border:1px solid #e2e8f0;border-radius:16px;overflow:hidden;">
                    <div style="padding:22px 24px;background:linear-gradient(135deg,#052e16,#0f172a);color:#ffffff;">
                      <div style="font-size:12px;font-weight:700;opacity:.85;letter-spacing:.08em;">COBRAX</div>
                      <h1 style="margin:8px 0 0 0;font-size:24px;">Teste de envio realizado com sucesso</h1>
                    </div>
                    <div style="padding:24px;">
                      <p style="margin:0 0 12px 0;font-size:16px;">
                        Este é um e-mail de teste da empresa <b>{company.nome}</b>.
                      </p>
                      <p style="margin:0;color:#475569;">
                        Se esta mensagem chegou corretamente, sua configuração SMTP está pronta para uso.
                      </p>
                    </div>
                  </div>
                </div>
            """,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Não foi possível concluir o teste de envio: {e}")

    return {
        "ok": True,
        "message": "Teste de envio realizado com sucesso",
    }