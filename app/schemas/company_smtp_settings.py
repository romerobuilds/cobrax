from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class CompanySmtpSettingsUpdate(BaseModel):
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = Field(default=None, ge=1, le=65535)
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: Optional[bool] = True
    from_email: Optional[EmailStr] = None
    from_name: Optional[str] = Field(default=None, max_length=120)


class CompanySmtpSettingsOut(BaseModel):
    company_id: str
    company_name: str

    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_use_tls: bool = True

    from_email: Optional[EmailStr] = None
    from_name: Optional[str] = None

    password_configured: bool = False
    smtp_configured: bool = False

    class Config:
        from_attributes = True


class CompanySmtpTestIn(BaseModel):
    to_email: EmailStr


# =========================
# ASAAS
# =========================
class CompanyAsaasSettingsUpdate(BaseModel):
    asaas_api_key: Optional[str] = None
    asaas_base_url: Optional[str] = Field(default=None, max_length=255)


class CompanyAsaasSettingsOut(BaseModel):
    company_id: str
    company_name: str

    asaas_base_url: Optional[str] = None

    api_key_configured: bool = False
    asaas_configured: bool = False

    dashboard_url: str = "https://www.asaas.com/"
    sandbox_url: str = "https://sandbox.asaas.com/"

    class Config:
        from_attributes = True