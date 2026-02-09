from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EmailLogPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    client_id: Optional[UUID] = None
    template_id: Optional[UUID] = None

    status: str

    subject_rendered: Optional[str] = None
    body_rendered: Optional[str] = None
    error_message: Optional[str] = None

    created_at: datetime
