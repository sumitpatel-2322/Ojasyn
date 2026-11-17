import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session, sessionmaker

from app.core.database import get_session_factory
from app.core.logging import get_correlation_id
from app.domain.enums import ActorType, SecurityEventOutcome, SecurityEventSeverity
from app.models.domain.governance import AuditEvent, SecurityEvent
from app.repositories.governance_repo import AuditRepository, SecurityEventRepository


def _canonical_json_default(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)


def compute_record_hash(state: Optional[Dict[str, Any]]) -> Optional[str]:
    """Computes deterministic SHA-256 over canonical JSON representation of a record state."""
    if state is None:
        return None
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":"), default=_canonical_json_default)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AuditService:
    """
    Append-only business audit & out-of-band security telemetry service.
    - `record()` shares the caller's UnitOfWork (rolls back if business command rolls back).
    - `record_security_event()` commits in an isolated short-lived transaction so failed
      auth/RBAC/tenant probes survive request rollbacks.
    """

    def __init__(
        self,
        audit_repo: Optional[AuditRepository] = None,
        security_repo: Optional[SecurityEventRepository] = None,
    ) -> None:
        self._audit_repo = audit_repo or AuditRepository()
        self._security_repo = security_repo or SecurityEventRepository()

    def record(
        self,
        db: Session,
        *,
        organization_id: Optional[uuid.UUID],
        site_id: Optional[uuid.UUID] = None,
        actor_type: str = ActorType.USER.value,
        actor_id: Optional[uuid.UUID] = None,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID,
        reason: Optional[str] = None,
        before_state: Optional[Dict[str, Any]] = None,
        after_state: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
        source_ip: Optional[str] = None,
        metadata_json: Optional[Dict[str, Any]] = None,
    ) -> AuditEvent:
        event = AuditEvent(
            organization_id=organization_id,
            site_id=site_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            occurred_at=datetime.now(timezone.utc),
            reason=reason,
            before_hash=compute_record_hash(before_state),
            after_hash=compute_record_hash(after_state),
            correlation_id=correlation_id or get_correlation_id(),
            source_ip=source_ip,
            metadata_json=metadata_json,
        )
        return self._audit_repo.create(db, event)

    def record_security_event(
        self,
        *,
        event_type: str,
        outcome: str = SecurityEventOutcome.DENIED.value,
        severity: str = SecurityEventSeverity.WARNING.value,
        organization_id: Optional[uuid.UUID] = None,
        user_id: Optional[uuid.UUID] = None,
        source_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        correlation_id: Optional[str] = None,
        target_resource: Optional[str] = None,
        details_json: Optional[Dict[str, Any]] = None,
        session_factory: Optional[sessionmaker[Session]] = None,
    ) -> SecurityEvent:
        factory = session_factory or get_session_factory()
        session = factory()
        try:
            event = SecurityEvent(
                organization_id=organization_id,
                user_id=user_id,
                event_type=event_type,
                outcome=outcome,
                severity=severity,
                source_ip=source_ip,
                user_agent=user_agent[:255] if user_agent else None,
                correlation_id=correlation_id or get_correlation_id(),
                target_resource=target_resource[:255] if target_resource else None,
                details_json=details_json,
                occurred_at=datetime.now(timezone.utc),
            )
            self._security_repo.create(session, event)
            session.commit()
            return event
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
