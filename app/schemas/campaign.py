# app/schemas/campaign.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# =============================
# CREATE
# =============================

class CampaignCreate(BaseModel):
    name: str
    template_id: UUID
    mode: str = Field(default="selected")  # selected | all | upload
    context: Dict[str, Any] = Field(default_factory=dict)
    rate_per_min: int = 15
    scheduled_at: Optional[datetime] = None

    @field_validator("scheduled_at", mode="before")
    @classmethod
    def scheduled_at_to_utc(cls, v):
        if v is None:
            return None
        # pydantic pode entregar datetime pronto ou string ISO
        if isinstance(v, str):
            # deixa o pydantic converter primeiro (mode="before" -> ainda string)
            return v
        if isinstance(v, datetime):
            return _as_utc(v)
        return v

    @field_validator("scheduled_at", mode="after")
    @classmethod
    def scheduled_at_to_utc_after(cls, v):
        return _as_utc(v)


# =============================
# UPDATE
# =============================

class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    template_id: Optional[UUID] = None
    status: Optional[str] = None  # draft | scheduled | ready | running | done | ...
    mode: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
    rate_per_min: Optional[int] = None
    scheduled_at: Optional[datetime] = None

    @field_validator("scheduled_at", mode="before")
    @classmethod
    def scheduled_at_to_utc(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            return v
        if isinstance(v, datetime):
            return _as_utc(v)
        return v

    @field_validator("scheduled_at", mode="after")
    @classmethod
    def scheduled_at_to_utc_after(cls, v):
        return _as_utc(v)


# =============================
# OUTPUT
# =============================

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
        from_attributes = True  # Pydantic v2


# =============================
# TARGETS
# =============================

class CampaignTargetAddSelected(BaseModel):
    client_ids: List[UUID] = Field(default_factory=list)
    payload: Dict[str, Any] = Field(default_factory=dict)


class CampaignTargetAddEmails(BaseModel):
    emails: List[str] = Field(default_factory=list)
    payload: Dict[str, Any] = Field(default_factory=dict)


# =============================
# RUN OUTPUT
# =============================

class CampaignRunOut(BaseModel):
    id: UUID
    campaign_id: UUID
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    totals: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        from_attributes = True
