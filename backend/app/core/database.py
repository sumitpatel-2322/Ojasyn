import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Union, overload

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from app.core.config import get_settings
from app.core.exceptions import ConflictError

_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker[Session]] = None


def get_engine() -> Engine:
    """Lazily initializes and caches the SQLAlchemy 2.0 engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.DATABASE_URL,
            echo=settings.DB_ECHO,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Lazily initializes and caches the SessionLocal factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
            bind=get_engine(),
        )
    return _session_factory


def reset_engine_cache() -> None:
    """Disposes cached engine/sessionmaker (used in tests when switching settings)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


class UnitOfWork:
    """
    Single-transaction Unit of Work wrapping a SQLAlchemy Session.
    Supports nested calls safely via session.info['_uow_depth']:
    - Inner `with UnitOfWork(db):` blocks flush only and never commit prematurely.
    - Only the outermost `UnitOfWork` commits on success or rolls back on any exception.
    """

    def __init__(self, session_or_factory: Optional[Union[Session, sessionmaker[Session]]] = None) -> None:
        self._target = session_or_factory
        self.session: Optional[Session] = None
        self._owns_session: bool = False
        self._is_outermost: bool = False

    def __enter__(self) -> "UnitOfWork":
        if isinstance(self._target, Session):
            self.session = self._target
            self._owns_session = False
        else:
            factory = self._target or get_session_factory()
            self.session = factory()
            self._owns_session = True

        depth = int(self.session.info.get("_uow_depth", 0))
        if depth == 0:
            self.session.info["_uow_aborted"] = False
        self.session.info["_uow_depth"] = depth + 1
        self._is_outermost = depth == 0
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        assert self.session is not None
        depth = max(0, int(self.session.info.get("_uow_depth", 1)) - 1)
        self.session.info["_uow_depth"] = depth

        try:
            if exc_type is not None:
                self.session.info["_uow_aborted"] = True
                if self._is_outermost:
                    self.session.rollback()
                return False

            if self.session.info.get("_uow_aborted"):
                if self._is_outermost:
                    self.session.rollback()
                return False

            if self._is_outermost:
                try:
                    self.session.commit()
                except StaleDataError as exc:
                    self.session.rollback()
                    raise ConflictError(
                        "Concurrent modification detected; record version mismatch.",
                        error_code="OPTIMISTIC_LOCK_CONFLICT",
                    ) from exc
                except Exception:
                    self.session.rollback()
                    raise
            else:
                self.session.flush()
            return False
        finally:
            if self._owns_session and self._is_outermost:
                self.session.close()


def verify_and_increment_version(entity: Any, expected_version: int) -> int:
    """
    Verifies optimistic concurrency `row_version` before mutating an entity,
    raising ConflictError (409) on stale version and incrementing `row_version`.
    """
    current = getattr(entity, "row_version", None)
    if current != expected_version:
        raise ConflictError(
            "Concurrent modification detected; record version mismatch.",
            error_code="OPTIMISTIC_LOCK_CONFLICT",
            details={"expected_version": expected_version, "actual_version": current},
        )
    entity.row_version = current + 1
    return entity.row_version


@overload
def next_tenant_sequence(
    db: Session,
    organization_id: Optional[uuid.UUID],
    entity_type: str,
    *,
    period_year: Optional[int] = None,
    prefix: None = None,
) -> int: ...


@overload
def next_tenant_sequence(
    db: Session,
    organization_id: Optional[uuid.UUID],
    entity_type: str,
    *,
    period_year: Optional[int] = None,
    prefix: str,
) -> str: ...


def next_tenant_sequence(
    db: Session,
    organization_id: Optional[uuid.UUID],
    entity_type: str,
    *,
    period_year: Optional[int] = None,
    prefix: Optional[str] = None,
) -> Union[int, str]:
    """
    Atomically increments and returns a tenant-scoped sequential counter inside the active transaction.
    Uses PostgreSQL ON CONFLICT DO UPDATE RETURNING (concurrency-safe under parallel transactions).
    """
    year = period_year or datetime.now(timezone.utc).year
    dialect_name = db.get_bind().dialect.name

    if dialect_name == "postgresql":
        stmt = text(
            """
            INSERT INTO organization_sequences (id, organization_id, entity_type, period_year, last_value)
            VALUES (:seq_id, :org_id, :entity_type, :period_year, 1)
            ON CONFLICT ON CONSTRAINT uq_org_sequences_scope_type_year
            DO UPDATE SET last_value = organization_sequences.last_value + 1
            RETURNING last_value
            """
        )
        next_val = int(
            db.execute(
                stmt,
                {
                    "seq_id": uuid.uuid4(),
                    "org_id": organization_id,
                    "entity_type": entity_type,
                    "period_year": year,
                },
            ).scalar_one()
        )
    else:
        from sqlalchemy import select
        from app.models.domain.tenancy import OrganizationSequence

        row = db.scalar(
            select(OrganizationSequence).where(
                OrganizationSequence.organization_id == organization_id,
                OrganizationSequence.entity_type == entity_type,
                OrganizationSequence.period_year == year,
            )
        )
        if row is None:
            row = OrganizationSequence(
                organization_id=organization_id,
                entity_type=entity_type,
                period_year=year,
                last_value=1,
            )
            db.add(row)
            db.flush()
            next_val = 1
        else:
            row.last_value += 1
            db.flush()
            next_val = row.last_value

    if prefix:
        return f"{prefix}-{year}-{next_val:06d}"
    return next_val
