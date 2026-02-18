# app/schemas/campaign.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CampaignCreate(BaseModel):
    name: str
    template_id: str
    mode: str = Field(default="selected")  # selected | all | upload
    context: Dict[str, Any] = Field(default_factory=dict)
    rate_per_min: int = 15
    scheduled_at: Optional[datetime] = None


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    template_id: Optional[str] = None
    status: Optional[str] = None  # draft | ready | running | done | ...
    mode: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
    rate_per_min: Optional[int] = None
    scheduled_at: Optional[datetime] = None


class CampaignOut(BaseModel):
    id: str
    company_id: str
    name: str
    template_id: str
    status: str
    mode: str
    context: Dict[str, Any]
    rate_per_min: int
    scheduled_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class CampaignTargetAddSelected(BaseModel):
    client_ids: List[str] = Field(default_factory=list)
    payload: Dict[str, Any] = Field(default_factory=dict)


class CampaignTargetAddEmails(BaseModel):
    emails: List[str] = Field(default_factory=list)
    payload: Dict[str, Any] = Field(default_factory=dict)


class CampaignRunOut(BaseModel):
    id: str
    campaign_id: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    totals: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        from_attributes = True
