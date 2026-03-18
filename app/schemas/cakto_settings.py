from __future__ import annotations

from pydantic import BaseModel


class CaktoSettingsOut(BaseModel):
    company_id: str
    company_name: str
    cakto_enabled: bool = False
    client_id_configured: bool = False
    client_secret_configured: bool = False
    cakto_configured: bool = False
    cakto_connected_at: str | None = None
    cakto_last_sync_at: str | None = None
    api_base_url: str = "https://api.cakto.com.br"
    token_url: str = "https://api.cakto.com.br/public_api/token/"

    class Config:
        from_attributes = True


class CaktoSettingsUpdate(BaseModel):
    cakto_client_id: str | None = None
    cakto_client_secret: str | None = None
    cakto_enabled: bool | None = None