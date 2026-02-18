from typing import Any, Dict, List, Optional, Literal
from datetime import datetime
from pydantic import BaseModel, Field, EmailStr
from uuid import UUID


CampaignStatus = Literal["draft", "scheduled", "running", "done", "cancelled"]
CampaignMode = Literal["selected", "all", "upload"]

RunStatus = Literal["running", "finished", "failed", "cancelled"]


class CampaignCreate(BaseModel):
    name: str = Field(..., min_length=1)
    template_id: UUID
    mode: CampaignMode = "selected"
    context: Dict[str, Any] = Field(default_factory=dict)
    rate_per_min: int = 15
    scheduled_at: Optional[datetime] = None


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    template_id: Optional[UUID] = None
    status: Optional[CampaignStatus] = None
    mode: Optional[CampaignMode] = None
    context: Optional[Dict[str, Any]] = None
    rate_per_min: Optional[int] = None
    scheduled_at: Optional[datetime] = None


class CampaignOut(BaseModel):
    id: UUID
    company_id: UUID
    name: str
    template_id: UUID
    status: str
    mode: str
    context: Dict[str, Any]
    rate_per_min: int
    scheduled_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class CampaignTargetIn(BaseModel):
    client_id: Optional[UUID] = None
    email: Optional[EmailStr] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class CampaignTargetsAdd(BaseModel):
    # pode mandar qualquer combinação abaixo
    client_ids: Optional[List[UUID]] = None
    emails: Optional[List[EmailStr]] = None
    targets: Optional[List[CampaignTargetIn]] = None


class CampaignTargetsAddResult(BaseModel):
    added: int
    skipped: int
    total_now: int


class CampaignRunOut(BaseModel):
    id: UUID
    campaign_id: UUID
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    totals: Dict[str, Any]

    class Config:
        from_attributes = True


class CampaignDetailOut(BaseModel):
    campaign: CampaignOut
    targets_count: int
    last_run: Optional[CampaignRunOut] = None
