# app/schemas/campaign.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator
from typing_extensions import Literal

RepeatType = Literal["none", "minutes", "hours", "days", "weeks"]


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_weekdays(v: Any) -> Optional[List[int]]:
    if v is None:
        return None

    items: List[int] = []
    if isinstance(v, str):
        raw = [x.strip() for x in v.split(",") if x.strip() != ""]
        for x in raw:
            try:
                items.append(int(x))
            except Exception:
                continue
    elif isinstance(v, (list, tuple)):
        for x in v:
            try:
                items.append(int(x))
            except Exception:
                continue
    else:
        return None

    items = sorted(set([d for d in items if 0 <= d <= 6]))
    return items or None


# =============================
# CREATE
# =============================
class CampaignCreate(BaseModel):
    name: str
    template_id: UUID
    mode: str = Field(default="selected")  # selected | all | upload
    context: Dict[str, Any] = Field(default_factory=dict)
    rate_per_min: int = 15

    # LEGACY: one-shot scheduling
    scheduled_at: Optional[datetime] = None

    # NOVO: cobrança / boletos
    is_cobranca: bool = False
    emitir_boletos: bool = False
    anexar_pdf: bool = False
    stop_on_paid: bool = True
    boleto_due_days: int = Field(default=3, ge=0, le=365)

    @field_validator("scheduled_at", mode="before")
    @classmethod
    def scheduled_at_before(cls, v):
        return v

    @field_validator("scheduled_at", mode="after")
    @classmethod
    def scheduled_at_after(cls, v: Optional[datetime]):
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

    # LEGACY
    scheduled_at: Optional[datetime] = None

    # NOVO: cobrança / boletos
    is_cobranca: Optional[bool] = None
    emitir_boletos: Optional[bool] = None
    anexar_pdf: Optional[bool] = None
    stop_on_paid: Optional[bool] = None
    boleto_due_days: Optional[int] = Field(default=None, ge=0, le=365)

    @field_validator("scheduled_at", mode="before")
    @classmethod
    def scheduled_at_before(cls, v):
        return v

    @field_validator("scheduled_at", mode="after")
    @classmethod
    def scheduled_at_after(cls, v: Optional[datetime]):
        return _as_utc(v)


# =============================
# OUTPUT (Campaign)
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

    # LEGACY
    scheduled_at: Optional[datetime] = None

    created_at: datetime

    # NOVO: schedule/recorrência (Fase C+)
    is_schedule_enabled: bool = False
    start_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    max_occurrences: Optional[int] = None
    occurrences: int = 0
    repeat_type: RepeatType = "none"
    repeat_every: int = 0
    repeat_weekdays: Optional[List[int]] = None
    timezone: str = "America/Sao_Paulo"

    # NOVO: cobrança / boletos
    is_cobranca: bool = False
    emitir_boletos: bool = False
    anexar_pdf: bool = False
    stop_on_paid: bool = True
    boleto_due_days: int = 3

    @field_validator("scheduled_at", "start_at", "next_run_at", "end_at", mode="after")
    @classmethod
    def dt_to_utc(cls, v: Optional[datetime]):
        return _as_utc(v)

    @field_validator("repeat_weekdays", mode="before")
    @classmethod
    def repeat_weekdays_before(cls, v):
        return _parse_weekdays(v)

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

    @field_validator("started_at", "finished_at", mode="after")
    @classmethod
    def dt_to_utc(cls, v: Optional[datetime]):
        return _as_utc(v)

    class Config:
        from_attributes = True


# =============================
# SCHEDULE (Advanced)
# =============================
class CampaignScheduleIn(BaseModel):
    is_enabled: bool = True
    start_at: Optional[datetime] = None
    timezone: str = "America/Sao_Paulo"
    repeat_type: RepeatType = "none"
    repeat_every: int = Field(default=0, ge=0)
    repeat_weekdays: Optional[List[int]] = None
    end_at: Optional[datetime] = None
    max_occurrences: Optional[int] = Field(default=None, ge=1)

    @field_validator("start_at", "end_at", mode="after")
    @classmethod
    def dt_to_utc(cls, v: Optional[datetime]):
        return _as_utc(v)

    @field_validator("repeat_weekdays", mode="before")
    @classmethod
    def weekdays_before(cls, v):
        return _parse_weekdays(v)

    @model_validator(mode="after")
    def validate_rules(self):
        if self.is_enabled:
            if self.start_at is None:
                raise ValueError("start_at é obrigatório quando is_enabled=true")

            if self.repeat_type == "none":
                self.repeat_every = 0
                self.repeat_weekdays = None
            else:
                if self.repeat_every <= 0:
                    raise ValueError("repeat_every precisa ser > 0 quando repetir")

                if self.repeat_type == "weeks":
                    if not self.repeat_weekdays:
                        raise ValueError("repeat_weekdays é obrigatório quando repeat_type='weeks'")
        else:
            self.start_at = None
            self.end_at = None
            self.max_occurrences = None
            self.repeat_type = "none"
            self.repeat_every = 0
            self.repeat_weekdays = None

        return self


class CampaignScheduleOut(BaseModel):
    is_schedule_enabled: bool

    start_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    end_at: Optional[datetime] = None

    max_occurrences: Optional[int] = None
    occurrences: int

    repeat_type: RepeatType
    repeat_every: int
    repeat_weekdays: Optional[List[int]] = None

    timezone: str

    @field_validator("start_at", "next_run_at", "end_at", mode="after")
    @classmethod
    def dt_to_utc(cls, v: Optional[datetime]):
        return _as_utc(v)

    @field_validator("repeat_weekdays", mode="before")
    @classmethod
    def weekdays_before(cls, v):
        return _parse_weekdays(v)

    class Config:
        from_attributes = True