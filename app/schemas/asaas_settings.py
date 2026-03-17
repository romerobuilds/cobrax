from __future__ import annotations

from pydantic import BaseModel


class AsaasSettingsOut(BaseModel):
    company_id: str
    company_name: str
    asaas_base_url: str | None = None
    api_key_configured: bool = False
    asaas_configured: bool = False
    dashboard_url: str = "https://www.asaas.com/"
    sandbox_url: str = "https://sandbox.asaas.com/"

    class Config:
        from_attributes = True


class AsaasSettingsUpdate(BaseModel):
    asaas_api_key: str | None = None
    asaas_base_url: str | None = "https://api.asaas.com/v3"