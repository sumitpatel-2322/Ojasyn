import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import bcrypt
from jose import JWTError, jwt
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.database import get_session_factory
from app.core.exceptions import AuthenticationError, ValidationError
from app.domain.enums import CredentialType, SecurityEventOutcome, SecurityEventSeverity
from app.models.domain.identity import UserCredential

BCRYPT_MAX_BYTES = 72


def hash_token(raw_token: str) -> str:
    """Returns SHA-256 hex digest of an opaque token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plaintext password against a stored bcrypt hash without silent truncation."""
    if not plain_password or not hashed_password:
        return False
    password_bytes = plain_password.encode("utf-8")
    if len(password_bytes) > BCRYPT_MAX_BYTES:
        return False
    return bcrypt.checkpw(password_bytes, hashed_password.encode("utf-8"))


def get_password_hash(password: str) -> str:
    """Hashes a plaintext password with bcrypt, enforcing the 72-byte UTF-8 limit."""
    pwd_bytes = password.encode("utf-8")
    if len(pwd_bytes) > BCRYPT_MAX_BYTES:
        raise ValidationError(
            "Password must not exceed 72 UTF-8 bytes.",
            error_code="PASSWORD_TOO_LONG",
        )
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def create_access_token(
    subject: Optional[str] = None,
    *,
    data: Optional[Dict[str, Any]] = None,
    expires_delta: Optional[timedelta] = None,
    token_type: str = "access",
    amr: Optional[List[str]] = None,
    settings: Optional[Settings] = None,
) -> str:
    """
    Creates a signed JWT access token with strict claims (sub, jti, iss, aud, iat, nbf, exp, typ, amr).
    Never falls back to a hardcoded signing key.
    """
    cfg = settings or get_settings()
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=cfg.ACCESS_TOKEN_EXPIRE_MINUTES))

    payload: Dict[str, Any] = dict(data) if data else {}
    if subject is not None:
        payload["sub"] = subject
    if "sub" not in payload or not payload["sub"]:
        raise ValueError("JWT subject ('sub') is required.")

    payload.update(
        {
            "jti": str(uuid.uuid4()),
            "iss": cfg.JWT_ISSUER,
            "aud": cfg.JWT_AUDIENCE,
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int(expire.timestamp()),
            "typ": token_type,
            "amr": amr or ["pwd"],
        }
    )
    return jwt.encode(payload, cfg.jwt_signing_key, algorithm=cfg.JWT_ALGORITHM)


def decode_access_token(
    token: str,
    *,
    expected_type: str = "access",
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    """Validates JWT signature, issuer, audience, expiry, nbf, and token purpose."""
    cfg = settings or get_settings()
    try:
        payload = jwt.decode(
            token,
            cfg.jwt_signing_key,
            algorithms=[cfg.JWT_ALGORITHM],
            audience=cfg.JWT_AUDIENCE,
            issuer=cfg.JWT_ISSUER,
            options={"require_sub": True, "require_exp": True, "require_iat": True, "require_nbf": True},
        )
    except JWTError as exc:
        raise AuthenticationError("Invalid or expired authentication token.") from exc

    if payload.get("typ") != expected_type:
        raise AuthenticationError("Invalid token type.")
    if not payload.get("jti"):
        raise AuthenticationError("Malformed authentication token.")
    return payload


def issue_refresh_token(
    db: Session,
    *,
    user_id: uuid.UUID,
    token_family_id: Optional[uuid.UUID] = None,
    session_metadata: Optional[Dict[str, Any]] = None,
    expires_delta: Optional[timedelta] = None,
    settings: Optional[Settings] = None,
) -> Tuple[str, UserCredential]:
    """Issues a high-entropy refresh token, storing only its SHA-256 hash and family ID."""
    cfg = settings or get_settings()
    now = datetime.now(timezone.utc)
    ttl = expires_delta or timedelta(days=cfg.REFRESH_TOKEN_EXPIRE_DAYS)
    raw_token = secrets.token_urlsafe(48)
    cred = UserCredential(
        user_id=user_id,
        credential_type=CredentialType.REFRESH_TOKEN.value,
        credential_hash=hash_token(raw_token),
        token_family_id=token_family_id or uuid.uuid4(),
        issued_at=now,
        expires_at=now + ttl,
        session_metadata=session_metadata,
    )
    db.add(cred)
    db.flush()
    return raw_token, cred


def _revoke_token_family_out_of_band(
    *,
    token_family_id: uuid.UUID,
    user_id: uuid.UUID,
    session_factory: sessionmaker[Session],
    source_ip: Optional[str],
    user_agent: Optional[str],
    details_json: Dict[str, Any],
) -> None:
    """
    Revokes an entire refresh-token family and records a CRITICAL SecurityEvent in an
    isolated transaction so rollback of the failing HTTP request never reverts the revocation.
    """
    from app.services.audit_service import AuditService

    now = datetime.now(timezone.utc)
    sec_session = session_factory()
    try:
        sec_session.execute(
            update(UserCredential)
            .where(
                UserCredential.token_family_id == token_family_id,
                UserCredential.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        sec_session.commit()
    except Exception:
        sec_session.rollback()
        raise
    finally:
        sec_session.close()

    AuditService().record_security_event(
        event_type="REFRESH_TOKEN_REUSE_DETECTED",
        outcome=SecurityEventOutcome.BLOCKED.value,
        severity=SecurityEventSeverity.CRITICAL.value,
        user_id=user_id,
        source_ip=source_ip,
        user_agent=user_agent,
        target_resource="/api/v1/auth/refresh",
        details_json=details_json,
        session_factory=session_factory,
    )


def rotate_refresh_token(
    db: Session,
    raw_refresh_token: str,
    *,
    session_factory: Optional[sessionmaker[Session]] = None,
    source_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    session_metadata: Optional[Dict[str, Any]] = None,
    settings: Optional[Settings] = None,
) -> Tuple[str, UserCredential]:
    """
    Atomically rotates a refresh token.
    If an already-revoked or already-replaced token is presented (reuse attack),
    revokes the entire token family out-of-band, logs a CRITICAL SecurityEvent, and raises 401.
    """
    factory = session_factory or get_session_factory()
    token_digest = hash_token(raw_refresh_token)
    cred = db.scalar(
        select(UserCredential).where(
            UserCredential.credential_hash == token_digest,
            UserCredential.credential_type == CredentialType.REFRESH_TOKEN.value,
        )
    )
    if cred is None:
        raise AuthenticationError("Invalid refresh token.")

    now = datetime.now(timezone.utc)

    if cred.revoked_at is not None or cred.replaced_by_id is not None:
        family_id = cred.token_family_id
        cred_id = cred.id
        cred_user_id = cred.user_id
        db.rollback()
        if family_id is not None:
            _revoke_token_family_out_of_band(
                token_family_id=family_id,
                user_id=cred_user_id,
                session_factory=factory,
                source_ip=source_ip,
                user_agent=user_agent,
                details_json={
                    "credential_id": str(cred_id),
                    "token_family_id": str(family_id),
                },
            )
        raise AuthenticationError("Refresh token reuse detected; session family revoked.")

    expires_at = cred.expires_at
    if expires_at is not None:
        exp_utc = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        if exp_utc <= now:
            cred.revoked_at = now
            db.flush()
            raise AuthenticationError("Refresh token expired.")

    new_raw, new_cred = issue_refresh_token(
        db,
        user_id=cred.user_id,
        token_family_id=cred.token_family_id,
        session_metadata=session_metadata,
        settings=settings,
    )

    result = db.execute(
        update(UserCredential)
        .where(
            UserCredential.id == cred.id,
            UserCredential.revoked_at.is_(None),
            UserCredential.replaced_by_id.is_(None),
        )
        .values(
            revoked_at=now,
            last_used_at=now,
            replaced_by_id=new_cred.id,
        )
    )
    if result.rowcount != 1:
        if cred.token_family_id is not None:
            _revoke_token_family_out_of_band(
                token_family_id=cred.token_family_id,
                user_id=cred.user_id,
                session_factory=factory,
                source_ip=source_ip,
                user_agent=user_agent,
                details_json={
                    "credential_id": str(cred.id),
                    "token_family_id": str(cred.token_family_id),
                    "race_condition": True,
                },
            )
        raise AuthenticationError("Refresh token reuse detected; session family revoked.")

    db.flush()
    return new_raw, new_cred
