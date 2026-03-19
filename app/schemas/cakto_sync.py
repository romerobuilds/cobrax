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

    automation_runs: int = 0
    automation_clients_created: int = 0
    automation_clients_updated: int = 0
    automation_emails_queued: int = 0

    message: str = ""


class CaktoCustomerSyncResultOut(BaseModel):
    ok: bool = True
    created: int = 0
    updated: int = 0
    skipped_no_email: int = 0
    skipped_unchanged: int = 0
    scanned_orders: int = 0
    message: str = ""