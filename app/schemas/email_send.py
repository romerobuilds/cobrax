from typing import Any, Dict
from uuid import UUID

from pydantic import BaseModel, Field


class EmailSendRequest(BaseModel):
    client_id: UUID
    context: Dict[str, Any] = Field(default_factory=dict)


class EmailSendResponse(BaseModel):
    log_id: UUID
    status: str
    subject: str