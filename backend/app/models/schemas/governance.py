import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    delivery_seq: int
    organization_id: uuid.UUID
    recipient_membership_id: uuid.UUID
    event_type: str
    aggregate_type: str
    aggregate_id: uuid.UUID
    title: str
    body: str
    created_at: datetime
    read_at: Optional[datetime] = None
    delivery_status: str


class UnreadCountResponse(BaseModel):
    unread_count: int


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: Optional[uuid.UUID] = None
    site_id: Optional[uuid.UUID] = None
    actor_type: str
    actor_id: Optional[uuid.UUID] = None
    action: str
    entity_type: str
    entity_id: uuid.UUID
    occurred_at: datetime
    reason: Optional[str] = None
    before_hash: Optional[str] = None
    after_hash: Optional[str] = None
    correlation_id: str
    source_ip: Optional[str] = None
    metadata_json: Optional[Dict[str, Any]] = None


class SecurityEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: Optional[uuid.UUID] = None
    user_id: Optional[uuid.UUID] = None
    event_type: str
    outcome: str
    severity: str
    correlation_id: str
    target_resource: Optional[str] = None
    details_json: Optional[Dict[str, Any]] = None
    occurred_at: datetime


class HealthResponse(BaseModel):
    status: str
