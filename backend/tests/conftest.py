import asyncio
import os
import selectors
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Coroutine, Dict, Generator, Optional, TypeVar

import httpx
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Provide safe deterministic test defaults BEFORE importing app modules
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("JWT_SIGNING_KEY", "test-only-signing-key-with-at-least-32-characters")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_placeholder.db")
os.environ.setdefault("DB_ECHO", "false")
os.environ.setdefault("MOUNT_LEGACY_ROUTERS", "false")

from app.core.config import get_settings
from app.core.database import UnitOfWork, reset_engine_cache
from app.domain.enums import (
    AssignmentStatus,
    AssignmentType,
    MembershipStatus,
    MemberType,
    OrganizationStatus,
    OrganizationType,
    RoleAssignmentScopeType,
    RoleAssignmentStatus,
    SiteStatus,
    SiteType,
    UserAccountStatus,
)
from app.domain.rbac_catalog import seed_rbac_catalog
from app.main import create_app
from app.models.domain import (
    Base,
    Notification,
    Organization,
    OrganizationAddress,
    OrganizationMembership,
    Role,
    Site,
    SiteAssignment,
    MembershipRoleAssignment,
    User,
)
from app.core.security import get_password_hash


def _sqlite_delivery_seq_listener(mapper, connection, target) -> None:
    if connection.dialect.name != "postgresql" and getattr(target, "delivery_seq", None) is None:
        max_seq = connection.execute(text("SELECT COALESCE(MAX(delivery_seq), 0) FROM notifications")).scalar()
        target.delivery_seq = int(max_seq or 0) + 1


event.listen(Notification, "before_insert", _sqlite_delivery_seq_listener)


@pytest.fixture()
def sqlite_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Dict[str, object], None, None]:
    """
    Provides an isolated SQLite database (with PRAGMA foreign_keys=ON) wired into
    Settings and app.core.database for sandboxed unit/API/security verification.
    """
    db_file = tmp_path / "phase0_test.sqlite3"
    db_url = f"sqlite:///{db_file.as_posix()}"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("JWT_SIGNING_KEY", "test-only-signing-key-with-at-least-32-characters")
    monkeypatch.setenv("DB_ECHO", "false")

    get_settings.cache_clear()
    reset_engine_cache()

    engine = create_engine(db_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=engine)

    import app.core.database as core_db

    core_db._engine = engine
    core_db._session_factory = session_factory

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        seed_rbac_catalog(uow.session)

    yield {"engine": engine, "session_factory": session_factory, "db_url": db_url}

    reset_engine_cache()
    get_settings.cache_clear()


_T = TypeVar("_T")


class _InMemorySelector(selectors._BaseSelectorImpl):
    """Minimal socket-free selector so ASGI & asyncio tests run cleanly without OS socketpair."""

    def __init__(self, wake_event: threading.Event) -> None:
        super().__init__()
        self._wake = wake_event

    def select(self, timeout: Optional[float] = None):
        if timeout != 0:
            self._wake.wait(timeout=0.05 if timeout is None else max(0.0, float(timeout)))
        self._wake.clear()
        return []

    def close(self) -> None:
        super().close()


class _InMemoryEventLoop(asyncio.BaseEventLoop):
    """Socket-free asyncio event loop for in-memory ASGI and SSE testing."""

    def __init__(self) -> None:
        self._wake = threading.Event()
        super().__init__()
        self._selector = _InMemorySelector(self._wake)

    def _write_to_self(self) -> None:
        self._wake.set()

    def _process_events(self, events) -> None:
        pass

    def _make_socket_transport(self, *args, **kwargs):
        raise NotImplementedError("Socket transports are not used in InMemoryEventLoop")

    def _make_datagram_transport(self, *args, **kwargs):
        raise NotImplementedError("Datagram transports are not used in InMemoryEventLoop")

    def _make_read_pipe_transport(self, *args, **kwargs):
        raise NotImplementedError("Pipe transports are not used in InMemoryEventLoop")

    def _make_write_pipe_transport(self, *args, **kwargs):
        raise NotImplementedError("Pipe transports are not used in InMemoryEventLoop")

    def _make_subprocess_transport(self, *args, **kwargs):
        raise NotImplementedError("Subprocess transports are not used in InMemoryEventLoop")

    def close(self) -> None:
        super().close()
        self._selector.close()


def run_in_memory_async(coro: Coroutine[Any, Any, _T]) -> _T:
    """Execute an async coroutine on a socket-free in-memory event loop."""
    loop = _InMemoryEventLoop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


class InMemoryASGIClient:
    """Synchronous HTTP client wrapper backed by httpx.ASGITransport and InMemoryEventLoop."""

    def __init__(self, app: Any) -> None:
        self.app = app

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        async def _send() -> httpx.Response:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url="http://testserver",
            ) as client:
                return await client.request(method, url, **kwargs)

        return run_in_memory_async(_send())

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", url, **kwargs)


@pytest.fixture()
def api_client(sqlite_db: Dict[str, object]) -> Generator[InMemoryASGIClient, None, None]:
    app = create_app(include_legacy_routers=False, start_sse_listener=False)
    yield InMemoryASGIClient(app)


def create_seeded_tenant_actor(
    db: Session,
    *,
    org_name: str,
    site_code: str,
    email: str,
    role_code: str,
    scope_type: str = RoleAssignmentScopeType.SITE.value,
    org_status: str = OrganizationStatus.ACTIVE.value,
    membership_status: str = MembershipStatus.ACTIVE.value,
    user_status: str = UserAccountStatus.ACTIVE.value,
    assign_site: bool = True,
) -> Dict[str, object]:
    """Helper creating an Organization, Site, User, Membership, SiteAssignment, and RoleAssignment."""
    org = Organization(
        legal_name=org_name,
        organization_type=OrganizationType.FDF_MANUFACTURER.value,
        country_of_registration="IN",
        business_email=f"contact@{site_code.lower()}.example.com",
        status=org_status,
    )
    db.add(org)
    db.flush()

    addr = OrganizationAddress(
        organization_id=org.id,
        line_1="Plot 10, Pharma Park",
        city="Pune",
        state_region="Maharashtra",
        postal_code="411001",
        country_code="IN",
        is_primary=True,
    )
    db.add(addr)
    db.flush()
    org.registered_address_id = addr.id

    site = Site(
        organization_id=org.id,
        site_code=site_code,
        site_name=f"{org_name} Site {site_code}",
        site_type=SiteType.FDF.value,
        address_id=addr.id,
        status=SiteStatus.ACTIVE.value,
        is_primary_site=True,
    )
    db.add(site)
    db.flush()

    user = User(
        email=email,
        full_name=f"User {email}",
        password_hash=get_password_hash("StrongPass!123"),
        account_status=user_status,
    )
    db.add(user)
    db.flush()

    membership = OrganizationMembership(
        user_id=user.id,
        organization_id=org.id,
        member_type=MemberType.EMPLOYEE.value,
        membership_status=membership_status,
        employee_code=f"EMP-{site_code}-{uuid.uuid4().hex[:4]}",
    )
    db.add(membership)
    db.flush()

    if assign_site:
        db.add(
            SiteAssignment(
                organization_id=org.id,
                membership_id=membership.id,
                site_id=site.id,
                assignment_type=AssignmentType.PRIMARY.value,
                status=AssignmentStatus.ACTIVE.value,
                assigned_by_user_id=user.id,
            )
        )

    role = db.query(Role).filter(Role.role_code == role_code).one()
    db.add(
        MembershipRoleAssignment(
            organization_id=org.id,
            membership_id=membership.id,
            role_id=role.id,
            scope_type=scope_type,
            site_id=site.id if scope_type == RoleAssignmentScopeType.SITE.value else None,
            granted_by_user_id=user.id,
            approved_by_user_id=user.id,
            status=RoleAssignmentStatus.ACTIVE.value,
        )
    )
    db.flush()

    return {
        "organization": org,
        "site": site,
        "user": user,
        "membership": membership,
    }


def get_live_postgres_url() -> Optional[str]:
    """Returns a PostgreSQL URL if TEST_DATABASE_URL (or PG env) is set and reachable."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        return None
    try:
        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return url
    except Exception:
        return None
