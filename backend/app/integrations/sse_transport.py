import asyncio
import json
import logging
import threading
import uuid
from typing import Any, AsyncGenerator, Dict, Optional, Set

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.repositories.governance_repo import NotificationRepository

logger = logging.getLogger(__name__)

PG_NOTIFY_CHANNEL = "ojasyn_notifications"


def _to_psycopg_conninfo(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return "postgresql://" + database_url[len("postgresql+psycopg://") :]
    return database_url


class SSETransport:
    """
    Cross-process SSE notification fanout backed by PostgreSQL LISTEN/NOTIFY
    plus delivery_seq keyset cursor replay for zero-daemon local/prod parity.
    """

    def __init__(self) -> None:
        self._subscribers: Dict[uuid.UUID, Set[asyncio.Queue[Dict[str, Any]]]] = {}
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event = threading.Event()
        self._listening_ready = threading.Event()
        self._listener_thread: Optional[threading.Thread] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, recipient_membership_id: uuid.UUID) -> asyncio.Queue[Dict[str, Any]]:
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        with self._lock:
            self._subscribers.setdefault(recipient_membership_id, set()).add(queue)
        return queue

    def unsubscribe(self, recipient_membership_id: uuid.UUID, queue: asyncio.Queue[Dict[str, Any]]) -> None:
        with self._lock:
            queues = self._subscribers.get(recipient_membership_id)
            if not queues:
                return
            queues.discard(queue)
            if not queues:
                self._subscribers.pop(recipient_membership_id, None)

    def dispatch_payload(self, payload: Dict[str, Any]) -> None:
        raw_recipient = payload.get("recipient_membership_id")
        if not raw_recipient:
            return
        try:
            recipient_id = uuid.UUID(str(raw_recipient))
        except ValueError:
            return

        with self._lock:
            queues = list(self._subscribers.get(recipient_id, ()))
        for q in queues:
            if self._loop is not None and self._loop.is_running():
                self._loop.call_soon_threadsafe(q.put_nowait, payload)
            else:
                q.put_nowait(payload)

    def start_listener(self, database_url: Optional[str] = None) -> None:
        if self._listener_thread is not None and self._listener_thread.is_alive():
            return
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        db_url = database_url or get_settings().DATABASE_URL
        if not db_url.startswith("postgresql"):
            return

        self._stop_event.clear()
        self._listening_ready.clear()
        conninfo = _to_psycopg_conninfo(db_url)
        self._listener_thread = threading.Thread(
            target=self._run_pg_listener,
            args=(conninfo,),
            name="ojasyn-pg-listen",
            daemon=True,
        )
        self._listener_thread.start()

    def wait_until_listening(self, timeout: float = 5.0) -> bool:
        """Wait until PostgreSQL LISTEN subscription is active or timeout expires."""
        return self._listening_ready.wait(timeout=timeout)

    def stop_listener(self) -> None:
        self._stop_event.set()
        self._listening_ready.clear()
        if self._listener_thread is not None and self._listener_thread.is_alive():
            self._listener_thread.join(timeout=3.0)
        self._listener_thread = None

    def _run_pg_listener(self, conninfo: str) -> None:
        try:
            import psycopg
        except ImportError:
            logger.warning("psycopg is not installed; PostgreSQL LISTEN listener disabled.")
            return

        while not self._stop_event.is_set():
            try:
                with psycopg.connect(conninfo, autocommit=True) as conn:
                    conn.execute(f"LISTEN {PG_NOTIFY_CHANNEL};")
                    self._listening_ready.set()
                    logger.info("Listening on PostgreSQL channel %s", PG_NOTIFY_CHANNEL)
                    while not self._stop_event.is_set():
                        for notify in conn.notifies(timeout=1.0):
                            try:
                                payload = json.loads(notify.payload)
                                self.dispatch_payload(payload)
                            except Exception as exc:
                                logger.warning("Invalid pg_notify payload: %s", exc)
            except Exception as exc:
                if not self._stop_event.is_set():
                    logger.warning("PostgreSQL LISTEN connection error: %s", exc)
                    self._stop_event.wait(2.0)

    @staticmethod
    def format_sse_event(payload: Dict[str, Any]) -> str:
        seq = payload.get("delivery_seq", "")
        event_type = payload.get("event_type", "notification")
        return f"id: {seq}\nevent: {event_type}\ndata: {json.dumps(payload, default=str)}\n\n"

    async def stream_events(
        self,
        *,
        organization_id: uuid.UUID,
        recipient_membership_id: uuid.UUID,
        last_event_id: int = 0,
        session_factory: Optional[sessionmaker[Session]] = None,
        keepalive_seconds: float = 15.0,
        max_events: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Replays missed notifications (delivery_seq > last_event_id) from DB,
        then streams live PostgreSQL NOTIFY wakeups via asyncio.Queue.
        """
        factory = session_factory or get_session_factory()
        repo = NotificationRepository()
        cursor = max(0, int(last_event_id))
        emitted = 0

        self.bind_loop(asyncio.get_running_loop())
        queue = self.subscribe(recipient_membership_id)
        try:
            # 1. Initial replay of missed durable notifications
            db = factory()
            try:
                missed = repo.list_since_seq(
                    db,
                    organization_id,
                    recipient_membership_id,
                    after_seq=cursor,
                )
            finally:
                db.close()

            for notif in missed:
                payload = {
                    "notification_id": str(notif.id),
                    "organization_id": str(notif.organization_id),
                    "recipient_membership_id": str(notif.recipient_membership_id),
                    "delivery_seq": notif.delivery_seq,
                    "event_type": notif.event_type,
                    "aggregate_type": notif.aggregate_type,
                    "aggregate_id": str(notif.aggregate_id),
                    "title": notif.title,
                    "body": notif.body,
                    "created_at": notif.created_at.isoformat(),
                    "read_at": notif.read_at.isoformat() if notif.read_at else None,
                }
                cursor = max(cursor, int(notif.delivery_seq))
                yield self.format_sse_event(payload)
                emitted += 1
                if max_events is not None and emitted >= max_events:
                    return

            # 2. Live wakeup loop
            while True:
                try:
                    incoming = await asyncio.wait_for(queue.get(), timeout=keepalive_seconds)
                    seq = int(incoming.get("delivery_seq") or 0)
                    if seq and seq <= cursor:
                        continue
                    # Reconcile from DB to guarantee full authoritative row & ordering
                    db = factory()
                    try:
                        rows = repo.list_since_seq(
                            db,
                            organization_id,
                            recipient_membership_id,
                            after_seq=cursor,
                        )
                    finally:
                        db.close()

                    if rows:
                        for notif in rows:
                            payload = {
                                "notification_id": str(notif.id),
                                "organization_id": str(notif.organization_id),
                                "recipient_membership_id": str(notif.recipient_membership_id),
                                "delivery_seq": notif.delivery_seq,
                                "event_type": notif.event_type,
                                "aggregate_type": notif.aggregate_type,
                                "aggregate_id": str(notif.aggregate_id),
                                "title": notif.title,
                                "body": notif.body,
                                "created_at": notif.created_at.isoformat(),
                                "read_at": notif.read_at.isoformat() if notif.read_at else None,
                            }
                            cursor = max(cursor, int(notif.delivery_seq))
                            yield self.format_sse_event(payload)
                            emitted += 1
                            if max_events is not None and emitted >= max_events:
                                return
                    else:
                        if seq:
                            cursor = max(cursor, seq)
                        yield self.format_sse_event(incoming)
                        emitted += 1
                        if max_events is not None and emitted >= max_events:
                            return
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            self.unsubscribe(recipient_membership_id, queue)


sse_transport = SSETransport()
