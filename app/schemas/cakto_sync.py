from __future__ import annotations

from pydantic import BaseModel


class CaktoOverviewOut(BaseModel):
    ok: bool = True
    products_count: int = 0
    orders_count: int = 0
    last_sync_at: str | None = None


class CaktoSyncResultOut(BaseModel):
    ok: bool = True
    synced: int = 0
    created: int = 0
    updated: int = 0
    pages: int = 0
    message: str = ""