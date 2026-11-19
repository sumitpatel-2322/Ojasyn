import base64
import email
import hashlib
import hmac
import imaplib
import re
import secrets
import smtplib
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Dict, List, Optional, Protocol, Sequence

from app.core.exceptions import ExternalProviderError, ValidationError
from app.domain.enums import EmailProviderType

_SCRIPT_OR_ACTIVE_TAG_RE = re.compile(
    r"<\s*(script|iframe|object|embed|style)\b[^>]*>.*?<\s*/\s*\1\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)
_SELF_CLOSING_ACTIVE_TAG_RE = re.compile(
    r"<\s*(script|iframe|object|embed)\b[^>]*/?\s*>",
    flags=re.IGNORECASE,
)
_INLINE_EVENT_HANDLER_RE = re.compile(
    r"""\s+on[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""",
    flags=re.IGNORECASE,
)
_JS_URI_RE = re.compile(r"javascript\s*:", flags=re.IGNORECASE)


def generate_webhook_secret() -> str:
    """Generates a cryptographically random 256-bit webhook secret."""
    return secrets.token_urlsafe(32)


def hash_webhook_secret(raw_secret: str) -> str:
    """Computes the canonical SHA-256 hex digest for storing webhook secrets at rest."""
    return hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()


def verify_webhook_secret(provided_secret: Optional[str], stored_hash: Optional[str]) -> bool:
    """Constant-time SHA-256 verification against stored `inbound_secret_hash`."""
    if not provided_secret or not stored_hash:
        return False
    candidate_hash = hash_webhook_secret(provided_secret.strip())
    return hmac.compare_digest(candidate_hash, stored_hash)


def sanitize_email_html(raw_html: Optional[str]) -> Optional[str]:
    """Strips dangerous active tags, inline event handlers, and javascript: URIs from HTML bodies."""
    if raw_html is None:
        return None
    cleaned = _SCRIPT_OR_ACTIVE_TAG_RE.sub("", raw_html)
    cleaned = _SELF_CLOSING_ACTIVE_TAG_RE.sub("", cleaned)
    cleaned = _INLINE_EVENT_HANDLER_RE.sub("", cleaned)
    cleaned = _JS_URI_RE.sub("blocked:", cleaned)
    return cleaned.strip()


@dataclass(frozen=True)
class OutboundEmailMessage:
    to_email: str
    subject: str
    body_text: str
    to_name: Optional[str] = None
    from_address: str = "qms-noreply@ojasyn.local"
    reply_to: Optional[str] = None
    in_reply_to_message_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EmailSendResult:
    provider_message_id: str
    sent_at: datetime
    raw_metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FetchedEmailAttachment:
    original_filename: str
    mime_type: Optional[str]
    content_bytes: bytes


@dataclass(frozen=True)
class FetchedInboundEmail:
    provider_message_id: str
    from_email: str
    from_name: Optional[str] = None
    to_addresses: List[str] = field(default_factory=list)
    cc_addresses: List[str] = field(default_factory=list)
    subject: Optional[str] = None
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    thread_id: Optional[str] = None
    in_reply_to_header: Optional[str] = None
    raw_headers: Dict[str, Any] = field(default_factory=dict)
    attachments: List[FetchedEmailAttachment] = field(default_factory=list)


@dataclass(frozen=True)
class EmailFetchBatch:
    messages: List[FetchedInboundEmail]
    next_cursor: Optional[str] = None


class EmailAdapter(Protocol):
    """Protocol for swappable outbound & inbound email provider adapters."""

    def send_email(
        self,
        message: OutboundEmailMessage,
        *,
        config: Optional[Dict[str, Any]] = None,
    ) -> EmailSendResult: ...

    def fetch_messages(
        self,
        *,
        cursor: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        limit: int = 50,
    ) -> EmailFetchBatch: ...


class MockEmailAdapter:
    """In-memory deterministic email adapter for local/test outbound sends and sync polling."""

    def __init__(
        self,
        *,
        inbound_queue: Optional[Sequence[FetchedInboundEmail]] = None,
        fail_send: bool = False,
    ) -> None:
        self._lock = threading.Lock()
        self.sent_messages: List[OutboundEmailMessage] = []
        self._inbound_queue: List[FetchedInboundEmail] = list(inbound_queue or [])
        self._fail_send = fail_send

    def enqueue_inbound(self, msg: FetchedInboundEmail) -> None:
        with self._lock:
            self._inbound_queue.append(msg)

    def send_email(
        self,
        message: OutboundEmailMessage,
        *,
        config: Optional[Dict[str, Any]] = None,
    ) -> EmailSendResult:
        cfg = config or {}
        if self._fail_send or cfg.get("simulate_send_failure"):
            raise ExternalProviderError("Simulated outbound email transport failure.")
        msg_id = f"<mock-{uuid.uuid4()}@ojasyn.local>"
        with self._lock:
            self.sent_messages.append(message)
        return EmailSendResult(
            provider_message_id=msg_id,
            sent_at=datetime.now(timezone.utc),
            raw_metadata={"adapter": "MOCK"},
        )

    def fetch_messages(
        self,
        *,
        cursor: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        limit: int = 50,
    ) -> EmailFetchBatch:
        cfg = config or {}
        if cfg.get("simulate_sync_failure"):
            raise ExternalProviderError("Simulated inbound email sync failure.")

        messages: List[FetchedInboundEmail] = []
        with self._lock:
            if self._inbound_queue:
                take = self._inbound_queue[:limit]
                self._inbound_queue = self._inbound_queue[limit:]
                messages.extend(take)

        # Also support deterministic config-seeded mock messages for HTTP sync integration tests
        cfg_items = cfg.get("mock_inbound_messages") or []
        if isinstance(cfg_items, list) and cfg_items:
            start_idx = int(cursor) if (cursor and str(cursor).isdigit()) else 0
            slice_items = cfg_items[start_idx : start_idx + limit]
            for item in slice_items:
                if isinstance(item, dict):
                    messages.append(parse_dict_inbound_message(item))
            next_cursor = str(start_idx + len(slice_items))
        else:
            next_cursor = cursor or f"mock-cursor-{int(datetime.now(timezone.utc).timestamp())}"

        return EmailFetchBatch(messages=messages, next_cursor=next_cursor)


class SMTPEmailAdapter:
    """Standard-library SMTP email adapter with TLS support; executes strictly outside DB transactions."""

    def send_email(
        self,
        message: OutboundEmailMessage,
        *,
        config: Optional[Dict[str, Any]] = None,
    ) -> EmailSendResult:
        cfg = config or {}
        host = str(cfg.get("smtp_host") or "").strip()
        if not host:
            raise ExternalProviderError("SMTP host is not configured for this email connection.")
        port = int(cfg.get("smtp_port") or 587)
        use_ssl = bool(cfg.get("smtp_use_ssl", False))
        use_tls = bool(cfg.get("smtp_use_tls", True))
        username = cfg.get("smtp_username")
        password = cfg.get("smtp_password")
        timeout = float(cfg.get("timeout_seconds") or 15.0)

        msg = EmailMessage()
        msg["From"] = message.from_address
        msg["To"] = f"{message.to_name} <{message.to_email}>" if message.to_name else message.to_email
        msg["Subject"] = message.subject
        msg_id = f"<{uuid.uuid4()}@ojasyn.local>"
        msg["Message-ID"] = msg_id
        if message.reply_to:
            msg["Reply-To"] = message.reply_to
        if message.in_reply_to_message_id:
            msg["In-Reply-To"] = message.in_reply_to_message_id
        msg.set_content(message.body_text)

        try:
            if use_ssl:
                with smtplib.SMTP_SSL(host, port, timeout=timeout) as client:
                    if username and password:
                        client.login(str(username), str(password))
                    client.send_message(msg)
            else:
                with smtplib.SMTP(host, port, timeout=timeout) as client:
                    if use_tls:
                        client.starttls()
                    if username and password:
                        client.login(str(username), str(password))
                    client.send_message(msg)
        except Exception as exc:
            raise ExternalProviderError(f"SMTP delivery failed: {exc}") from exc

        return EmailSendResult(
            provider_message_id=msg_id,
            sent_at=datetime.now(timezone.utc),
            raw_metadata={"adapter": "SMTP", "host": host, "port": port},
        )

    def fetch_messages(
        self,
        *,
        cursor: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        limit: int = 50,
    ) -> EmailFetchBatch:
        return EmailFetchBatch(messages=[], next_cursor=cursor)


class IMAPEmailAdapter:
    """Controlled IMAP polling adapter fetching messages above UID `cursor` outside DB transactions."""

    def send_email(
        self,
        message: OutboundEmailMessage,
        *,
        config: Optional[Dict[str, Any]] = None,
    ) -> EmailSendResult:
        cfg = config or {}
        if cfg.get("smtp_host"):
            return SMTPEmailAdapter().send_email(message, config=cfg)
        raise ExternalProviderError("IMAP adapter requires smtp_host configuration for outbound send.")

    def fetch_messages(
        self,
        *,
        cursor: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        limit: int = 50,
    ) -> EmailFetchBatch:
        cfg = config or {}
        host = str(cfg.get("imap_host") or "").strip()
        if not host:
            raise ExternalProviderError("IMAP host is not configured for this email connection.")
        port = int(cfg.get("imap_port") or 993)
        use_ssl = bool(cfg.get("imap_use_ssl", True))
        username = cfg.get("imap_username")
        password = cfg.get("imap_password")
        mailbox = str(cfg.get("imap_mailbox") or "INBOX")

        try:
            client = imaplib.IMAP4_SSL(host, port) if use_ssl else imaplib.IMAP4(host, port)
            try:
                if username and password:
                    client.login(str(username), str(password))
                client.select(mailbox, readonly=True)
                last_uid = int(cursor) if (cursor and str(cursor).isdigit()) else 0
                search_criteria = f"UID {last_uid + 1}:*" if last_uid > 0 else "ALL"
                status_code, data = client.uid("search", None, search_criteria)
                if status_code != "OK" or not data or not data[0]:
                    return EmailFetchBatch(messages=[], next_cursor=cursor)

                uids = [u.decode("utf-8") for u in data[0].split() if int(u) > last_uid][:limit]
                fetched: List[FetchedInboundEmail] = []
                highest_uid = last_uid
                for uid_str in uids:
                    f_status, f_data = client.uid("fetch", uid_str, "(RFC822)")
                    if f_status != "OK" or not f_data:
                        continue
                    raw_bytes = None
                    for part in f_data:
                        if isinstance(part, tuple) and len(part) >= 2:
                            raw_bytes = part[1]
                            break
                    if not raw_bytes:
                        continue
                    fetched.append(_parse_rfc822_bytes(raw_bytes, fallback_id=f"imap-uid-{uid_str}"))
                    highest_uid = max(highest_uid, int(uid_str))
                next_cur = str(highest_uid) if highest_uid > last_uid else cursor
                return EmailFetchBatch(messages=fetched, next_cursor=next_cur)
            finally:
                try:
                    client.logout()
                except Exception:
                    pass
        except ExternalProviderError:
            raise
        except Exception as exc:
            raise ExternalProviderError(f"IMAP sync failed: {exc}") from exc


class WebhookEmailAdapter:
    """Push-based webhook email adapter; outbound falls back to SMTP if configured or Mock."""

    def __init__(self, *, fallback_mock: Optional[MockEmailAdapter] = None) -> None:
        self._fallback_mock = fallback_mock or MockEmailAdapter()

    def send_email(
        self,
        message: OutboundEmailMessage,
        *,
        config: Optional[Dict[str, Any]] = None,
    ) -> EmailSendResult:
        cfg = config or {}
        if cfg.get("smtp_host"):
            return SMTPEmailAdapter().send_email(message, config=cfg)
        return self._fallback_mock.send_email(message, config=cfg)

    def fetch_messages(
        self,
        *,
        cursor: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        limit: int = 50,
    ) -> EmailFetchBatch:
        return EmailFetchBatch(messages=[], next_cursor=cursor)


def _decode_base64_attachment(item: Dict[str, Any], *, max_bytes: int) -> FetchedEmailAttachment:
    raw_b64 = str(item.get("content_base64") or "").strip()
    if not raw_b64:
        raise ValidationError("Attachment content_base64 must not be empty.")
    try:
        raw_bytes = base64.b64decode(raw_b64, validate=True)
    except Exception as exc:
        raise ValidationError("Invalid base64 encoding in email attachment.") from exc
    if len(raw_bytes) > max_bytes:
        raise ValidationError(
            f"Decoded email attachment exceeds maximum size of {max_bytes} bytes.",
            error_code="PAYLOAD_TOO_LARGE",
        )
    return FetchedEmailAttachment(
        original_filename=str(item.get("original_filename") or "attachment.bin"),
        mime_type=item.get("mime_type"),
        content_bytes=raw_bytes,
    )


def parse_dict_inbound_message(
    data: Dict[str, Any],
    *,
    max_attachment_bytes: int = 10 * 1024 * 1024,
) -> FetchedInboundEmail:
    """Normalizes a structured webhook/mock email dict into `FetchedInboundEmail`."""
    raw_recv = data.get("received_at")
    if isinstance(raw_recv, datetime):
        received_at = raw_recv if raw_recv.tzinfo else raw_recv.replace(tzinfo=timezone.utc)
    elif isinstance(raw_recv, str) and raw_recv.strip():
        try:
            dt = datetime.fromisoformat(raw_recv.replace("Z", "+00:00"))
            received_at = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            received_at = datetime.now(timezone.utc)
    else:
        received_at = datetime.now(timezone.utc)

    attachments: List[FetchedEmailAttachment] = []
    for att in data.get("attachments") or []:
        if isinstance(att, FetchedEmailAttachment):
            attachments.append(att)
        elif isinstance(att, dict):
            attachments.append(_decode_base64_attachment(att, max_bytes=max_attachment_bytes))

    return FetchedInboundEmail(
        provider_message_id=str(data["provider_message_id"]).strip(),
        from_email=str(data["from_email"]).strip().lower(),
        from_name=data.get("from_name"),
        to_addresses=list(data.get("to_addresses") or []),
        cc_addresses=list(data.get("cc_addresses") or []),
        subject=data.get("subject"),
        body_text=data.get("body_text"),
        body_html=data.get("body_html"),
        received_at=received_at,
        thread_id=data.get("thread_id"),
        in_reply_to_header=data.get("in_reply_to_header"),
        raw_headers=dict(data.get("raw_headers") or {}),
        attachments=attachments,
    )


def _parse_rfc822_bytes(raw_bytes: bytes, *, fallback_id: str) -> FetchedInboundEmail:
    msg = email.message_from_bytes(raw_bytes)
    msg_id = (msg.get("Message-ID") or fallback_id).strip()
    from_name, from_email = parseaddr(msg.get("From") or "")
    to_addrs = [parseaddr(addr)[1] for addr in msg.get_all("To", []) if parseaddr(addr)[1]]
    cc_addrs = [parseaddr(addr)[1] for addr in msg.get_all("Cc", []) if parseaddr(addr)[1]]
    subject = msg.get("Subject")
    in_reply_to = msg.get("In-Reply-To")
    thread_id = msg.get("Thread-Index") or msg.get("References") or in_reply_to

    received_at = datetime.now(timezone.utc)
    if msg.get("Date"):
        try:
            dt = parsedate_to_datetime(msg["Date"])
            received_at = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass

    body_text_parts: List[str] = []
    body_html_parts: List[str] = []
    attachments: List[FetchedEmailAttachment] = []

    if msg.is_multipart():
        for part in msg.walk():
            cdisp = str(part.get("Content-Disposition") or "").lower()
            ctype = (part.get_content_type() or "application/octet-stream").lower()
            filename = part.get_filename()
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            if filename or "attachment" in cdisp:
                attachments.append(
                    FetchedEmailAttachment(
                        original_filename=filename or "attachment.bin",
                        mime_type=ctype,
                        content_bytes=payload,
                    )
                )
            elif ctype == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                body_text_parts.append(payload.decode(charset, errors="replace"))
            elif ctype == "text/html":
                charset = part.get_content_charset() or "utf-8"
                body_html_parts.append(payload.decode(charset, errors="replace"))
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        if (msg.get_content_type() or "").lower() == "text/html":
            body_html_parts.append(payload.decode(charset, errors="replace"))
        else:
            body_text_parts.append(payload.decode(charset, errors="replace"))

    return FetchedInboundEmail(
        provider_message_id=msg_id,
        from_email=(from_email or "unknown@example.com").lower(),
        from_name=from_name or None,
        to_addresses=to_addrs,
        cc_addresses=cc_addrs,
        subject=subject,
        body_text="\n".join(body_text_parts).strip() or None,
        body_html="\n".join(body_html_parts).strip() or None,
        received_at=received_at,
        thread_id=thread_id,
        in_reply_to_header=in_reply_to,
        raw_headers={k: str(v) for k, v in msg.items()},
        attachments=attachments,
    )


class EmailProvider:
    """Unified email transport gateway dispatching across MOCK, SMTP, IMAP, and WEBHOOK adapters."""

    def __init__(
        self,
        *,
        adapters: Optional[Dict[str, EmailAdapter]] = None,
        default_mock: Optional[MockEmailAdapter] = None,
    ) -> None:
        self._mock = default_mock or MockEmailAdapter()
        self._adapters: Dict[str, EmailAdapter] = {
            EmailProviderType.MOCK.value: self._mock,
            EmailProviderType.SMTP.value: SMTPEmailAdapter(),
            EmailProviderType.IMAP.value: IMAPEmailAdapter(),
            EmailProviderType.WEBHOOK.value: WebhookEmailAdapter(fallback_mock=self._mock),
        }
        if adapters:
            self._adapters.update(adapters)

    def get_adapter(self, provider_type: str) -> EmailAdapter:
        key = (provider_type or EmailProviderType.MOCK.value).strip().upper()
        adapter = self._adapters.get(key)
        if adapter is None:
            raise ValidationError(f"Unsupported email provider_type '{provider_type}'.")
        return adapter

    def send_email(
        self,
        *,
        provider_type: str,
        message: OutboundEmailMessage,
        config: Optional[Dict[str, Any]] = None,
    ) -> EmailSendResult:
        return self.get_adapter(provider_type).send_email(message, config=config)

    def fetch_messages(
        self,
        *,
        provider_type: str,
        cursor: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        limit: int = 50,
    ) -> EmailFetchBatch:
        return self.get_adapter(provider_type).fetch_messages(
            cursor=cursor,
            config=config,
            limit=limit,
        )
