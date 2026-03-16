from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database_.database import get_db
from app.core.deps import get_current_user
from app.core.security import hash_senha
from app.models.company import Company
from app.models.company_user import CompanyUser
from app.models.user import User
from app.models.client import Client
from app.models.billing_charge import BillingCharge
from app.schemas.company import CompanyCreate, CompanyPublic
from app.schemas.company_smtp_settings import (
    CompanySmtpSettingsOut,
    CompanySmtpSettingsUpdate,
    CompanySmtpTestIn,
    CompanyAsaasSettingsOut,
    CompanyAsaasSettingsUpdate,
)
from app.schemas.email_admin import CompanyEmailSettingsUpdate
from app.services.mailer import send_smtp_email
from app.services.asaas_client import ping_asaas

router = APIRouter(prefix="/empresas", tags=["Empresas"])

MASTER_DELETE_KEY = "An@ly2904"


def _master_accessible_company_ids(db: Session, user: User) -> set[str]:
    owned_ids = {
        str(row[0])
        for row in db.query(Company.id).filter(Company.owner_id == user.id).all()
    }

    membership_ids = {
        str(row[0])
        for row in db.query(CompanyUser.company_id)
        .filter(
            CompanyUser.user_id == user.id,
            CompanyUser.is_active.is_(True),
        )
        .all()
    }

    return owned_ids | membership_ids


def _get_company_or_404(db: Session, company_id: UUID, user: User) -> Company:
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    if user.is_master:
        allowed_ids = _master_accessible_company_ids(db, user)
        if str(company.id) not in allowed_ids:
            raise HTTPException(status_code=403, detail="Sem acesso a esta empresa")
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
    if not membership:
        raise HTTPException(status_code=403, detail="Sem acesso a esta empresa")

    return company


def _smtp_out(company: Company) -> CompanySmtpSettingsOut:
    password_configured = bool((company.smtp_password or "").strip())
    smtp_configured = bool(
        (company.smtp_host or "").strip()
        and company.smtp_port
        and (company.smtp_user or "").strip()
        and password_configured
        and ((company.from_email or "").strip() or (company.smtp_user or "").strip())
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


def _asaas_out(company: Company) -> CompanyAsaasSettingsOut:
    base_url = (company.asaas_base_url or "").strip() or "https://api.asaas.com/v3"
    api_key_configured = bool((company.asaas_api_key or "").strip())

    return CompanyAsaasSettingsOut(
        company_id=str(company.id),
        company_name=company.nome,
        asaas_base_url=base_url,
        api_key_configured=api_key_configured,
        asaas_configured=api_key_configured,
        dashboard_url="https://www.asaas.com/",
        sandbox_url="https://sandbox.asaas.com/",
    )


@router.post("/", response_model=CompanyPublic, status_code=status.HTTP_201_CREATED)
def criar_empresa(
    payload: CompanyCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not user.is_master:
        raise HTTPException(status_code=403, detail="Apenas master pode criar empresas")

    existe = (
        db.query(Company)
        .filter((Company.cnpj == payload.cnpj) | (Company.email == payload.email))
        .first()
    )
    if existe:
        raise HTTPException(status_code=400, detail="Empresa já existe (cnpj/email)")

    empresa = Company(
        nome=payload.nome,
        cnpj=payload.cnpj,
        email=payload.email,
        owner_id=user.id,
    )

    db.add(empresa)
    db.flush()

    initial_user_nome = getattr(payload, "initial_user_nome", None)
    initial_user_email = getattr(payload, "initial_user_email", None)
    initial_user_senha = getattr(payload, "initial_user_senha", None)

    if initial_user_nome and initial_user_email and initial_user_senha:
        existing_user = (
            db.query(User)
            .filter(User.email == str(initial_user_email).strip().lower())
            .first()
        )
        if existing_user:
            raise HTTPException(status_code=400, detail="Já existe usuário com este e-mail")

        new_user = User(
            nome=str(initial_user_nome).strip(),
            email=str(initial_user_email).strip().lower(),
            senha_hash=hash_senha(str(initial_user_senha)),
            is_master=False,
        )
        db.add(new_user)
        db.flush()

        db.add(
            CompanyUser(
                company_id=empresa.id,
                user_id=new_user.id,
                role="company_admin",
                is_active=True,
                is_primary=True,
            )
        )

    db.commit()
    db.refresh(empresa)
    return empresa


@router.get("/me", response_model=list[CompanyPublic])
def minhas_empresas(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.is_master:
        allowed_ids = _master_accessible_company_ids(db, user)
        if not allowed_ids:
            return []

        return (
            db.query(Company)
            .filter(Company.id.in_([UUID(x) for x in allowed_ids]))
            .order_by(Company.nome.asc())
            .all()
        )

    membership_ids = [
        row[0]
        for row in db.query(CompanyUser.company_id)
        .filter(
            CompanyUser.user_id == user.id,
            CompanyUser.is_active.is_(True),
        )
        .all()
    ]

    if not membership_ids:
        return []

    return (
        db.query(Company)
        .filter(Company.id.in_(membership_ids))
        .order_by(Company.nome.asc())
        .all()
    )


@router.get(
    "/{company_id}/smtp-settings",
    response_model=CompanySmtpSettingsOut,
    status_code=status.HTTP_200_OK,
)
def get_smtp_settings(
    company_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, user)
    return _smtp_out(company)


@router.put(
    "/{company_id}/smtp-settings",
    response_model=CompanySmtpSettingsOut,
    status_code=status.HTTP_200_OK,
)
def put_smtp_settings(
    company_id: UUID,
    payload: CompanySmtpSettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, user)

    if payload.smtp_host is not None:
        company.smtp_host = payload.smtp_host.strip() or None

    if payload.smtp_port is not None:
        company.smtp_port = int(payload.smtp_port)

    if payload.smtp_user is not None:
        company.smtp_user = payload.smtp_user.strip() or None

    if payload.smtp_password is not None:
        new_pwd = payload.smtp_password.strip()
        if new_pwd:
            company.smtp_password = new_pwd

    if payload.smtp_use_tls is not None:
        company.smtp_use_tls = bool(payload.smtp_use_tls)

    if payload.from_email is not None:
        company.from_email = str(payload.from_email).strip().lower() or None

    if payload.from_name is not None:
        company.from_name = payload.from_name.strip() or None

    db.add(company)
    db.commit()
    db.refresh(company)

    return _smtp_out(company)


@router.post(
    "/{company_id}/smtp-settings/test",
    status_code=status.HTTP_200_OK,
)
def test_smtp_settings(
    company_id: UUID,
    payload: CompanySmtpTestIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, user)

    if not company.smtp_host:
        raise HTTPException(status_code=400, detail="Configure o servidor de envio antes do teste")

    if not company.smtp_port:
        raise HTTPException(status_code=400, detail="Configure a porta antes do teste")

    if not company.smtp_user:
        raise HTTPException(status_code=400, detail="Configure o usuário de autenticação antes do teste")

    if not company.smtp_password:
        raise HTTPException(status_code=400, detail="Configure a senha da conta antes do teste")

    from_email = (company.from_email or company.smtp_user or "").strip()
    from_name = (company.from_name or company.nome or "Cobrax").strip()

    if not from_email:
        raise HTTPException(status_code=400, detail="Configure o e-mail remetente antes do teste")

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


# =========================
# ASAAS SETTINGS
# =========================
@router.get(
    "/{company_id}/asaas-settings",
    response_model=CompanyAsaasSettingsOut,
    status_code=status.HTTP_200_OK,
)
def get_asaas_settings(
    company_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, user)
    return _asaas_out(company)


@router.put(
    "/{company_id}/asaas-settings",
    response_model=CompanyAsaasSettingsOut,
    status_code=status.HTTP_200_OK,
)
def put_asaas_settings(
    company_id: UUID,
    payload: CompanyAsaasSettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, user)

    if payload.asaas_base_url is not None:
        company.asaas_base_url = payload.asaas_base_url.strip() or None

    if payload.asaas_api_key is not None:
        new_key = payload.asaas_api_key.strip()
        if new_key:
            company.asaas_api_key = new_key

    db.add(company)
    db.commit()
    db.refresh(company)

    return _asaas_out(company)


@router.post(
    "/{company_id}/asaas-settings/test",
    status_code=status.HTTP_200_OK,
)
def test_asaas_settings(
    company_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, user)

    if not (company.asaas_api_key or "").strip():
        raise HTTPException(status_code=400, detail="Configure a API Key do Asaas antes do teste")

    try:
        ping_asaas(
            api_key=company.asaas_api_key,
            base_url=company.asaas_base_url,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Não foi possível validar o Asaas: {e}")

    return {
        "ok": True,
        "message": "Integração Asaas validada com sucesso",
    }


@router.patch(
    "/{company_id}/email-settings",
    status_code=status.HTTP_200_OK,
)
def update_email_admin_settings(
    company_id: UUID,
    payload: CompanyEmailSettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    company = _get_company_or_404(db, company_id, user)

    if payload.rate_per_min is not None and payload.rate_per_min not in {5, 10, 15, 20, 25, 30}:
        raise HTTPException(
            status_code=422,
            detail="rate_per_min inválido. Use 5, 10, 15, 20, 25 ou 30.",
        )

    if payload.smtp_paused is not None:
        company.smtp_paused = payload.smtp_paused

    if payload.clear_daily_limit:
        company.daily_email_limit = None
    elif payload.daily_email_limit is not None:
        company.daily_email_limit = payload.daily_email_limit

    if payload.rate_per_min is not None:
        company.rate_per_min = payload.rate_per_min

    db.add(company)
    db.commit()
    db.refresh(company)

    return {
        "company_id": str(company.id),
        "smtp_paused": bool(company.smtp_paused),
        "daily_email_limit": company.daily_email_limit,
        "rate_per_min": int(company.rate_per_min),
    }


@router.delete(
    "/{company_id}",
    status_code=status.HTTP_200_OK,
)
def delete_company(
    company_id: UUID,
    master_key: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not user.is_master:
        raise HTTPException(status_code=403, detail="Apenas master pode excluir empresas")

    if master_key != MASTER_DELETE_KEY:
        raise HTTPException(status_code=403, detail="Chave master inválida")

    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    if str(company.nome or "").strip().lower() == "cobrax":
        raise HTTPException(status_code=400, detail="A empresa principal Cobrax não pode ser excluída")

    allowed_ids = _master_accessible_company_ids(db, user)
    if str(company.id) not in allowed_ids:
        raise HTTPException(status_code=403, detail="Sem acesso a esta empresa")

    client_ids = [
        row[0]
        for row in db.query(Client.id).filter(Client.company_id == company.id).all()
    ]

    if client_ids:
        db.query(BillingCharge).filter(BillingCharge.client_id.in_(client_ids)).delete(
            synchronize_session=False
        )

    db.query(BillingCharge).filter(BillingCharge.company_id == company.id).delete(
        synchronize_session=False
    )

    db.query(CompanyUser).filter(CompanyUser.company_id == company.id).delete(
        synchronize_session=False
    )

    db.query(Client).filter(Client.company_id == company.id).delete(
        synchronize_session=False
    )

    db.delete(company)
    db.commit()

    return {
        "ok": True,
        "message": "Empresa excluída com sucesso",
    }