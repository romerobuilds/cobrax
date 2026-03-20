from __future__ import annotations

from pydantic import BaseModel
from typing import Optional


class CaktoSettingsUpdate(BaseModel):
    cakto_client_id: Optional[str] = None
    cakto_client_secret: Optional[str] = None
    cakto_enabled: Optional[bool] = None


class CaktoSettingsOut(BaseModel):
    company_id: str
    company_name: str

    cakto_enabled: bool = False
    client_id_configured: bool = False
    client_secret_configured: bool = False
    cakto_configured: bool = False

    cakto_connected_at: str | None = None
    cakto_last_sync_at: str | None = None

    webhook_configured: bool = False
    webhook_url: str | None = None
    webhook_status: str | None = None
    webhook_registered_at: str | None = None
    webhook_last_event_at: str | None = None
    webhook_id: int | None = None

    api_base_url: str
    token_url: str