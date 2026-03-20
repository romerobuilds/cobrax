from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


CaktoEventType = Literal["order_paid"]
CaktoActionType = Literal[
    "sync_customer",
    "send_email",
    "sync_customer_and_send_email",
]


class CaktoAutomationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    is_active: bool = True
    event_type: CaktoEventType = "order_paid"
    action_type: CaktoActionType = "sync_customer"
    cakto_product_id: Optional[str] = None
    run_on_status_paid: bool = True
    template_id: Optional[UUID] = None


class CaktoAutomationUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    is_active: Optional[bool] = None
    event_type: Optional[CaktoEventType] = None
    action_type: Optional[CaktoActionType] = None
    cakto_product_id: Optional[str] = None
    run_on_status_paid: Optional[bool] = None
    template_id: Optional[UUID] = None


class CaktoAutomationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    name: str
    is_active: bool
    event_type: CaktoEventType
    action_type: CaktoActionType
    cakto_product_id: Optional[str] = None
    run_on_status_paid: bool
    template_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    last_run_at: Optional[datetime] = None


class CaktoAutomationRunResultOut(BaseModel):
    ok: bool = True
    automation_id: str
    matched_orders: int = 0
    created: int = 0
    updated: int = 0
    skipped_no_email: int = 0
    skipped_unchanged: int = 0
    emails_queued: int = 0
    message: str = ""