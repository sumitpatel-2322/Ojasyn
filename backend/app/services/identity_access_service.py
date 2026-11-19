import base64
import hashlib
import hmac
import secrets
import struct
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.database import UnitOfWork, get_session_factory
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.core.security import (
    create_access_token,
    get_password_hash,
    hash_token,
    issue_refresh_token,
    rotate_refresh_token,
    verify_password,
)
from app.domain.enums import (
    ActorType,
    CredentialType,
    MFAState,
    MembershipStatus,
    SecurityEventOutcome,
    SecurityEventSeverity,
    UserAccountStatus,
)
from app.models.domain.actors import BusinessPartner, ChemistProfile, CustomerProfile
from app.models.domain.identity import Role, User, UserCredential
from app.models.domain.tenancy import Organization
from app.models.schemas.auth_onboarding import (
    LoginRequest,
    MFAChallengeResponse,
    MembershipBrief,
    PartnerMembershipBrief,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UpdateUserProfileRequest,
    UserProfileResponse,
    UserSummaryResponse,
    VerifyEmailResponse,
)
from app.repositories.governance_repo import OutboxRepository
from app.repositories.identity_repo import IdentityRepository
from app.repositories.partner_repo import PartnerRepository
from app.services.audit_service import AuditService

MAX_FAILED_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _get_mfa_fernet() -> Fernet:
    signing_key = get_settings().jwt_signing_key
    derived = hashlib.sha256(f"ojasyn:mfa-totp:v1:{signing_key}".encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def _encrypt_mfa_secret(secret_b32: str) -> str:
    token = _get_mfa_fernet().encrypt(secret_b32.encode("utf-8")).decode("ascii")
    return f"enc:v1:{token}"


def _decrypt_mfa_secret(stored: str) -> str:
    if not stored.startswith("enc:v1:"):
        raise AuthenticationError("MFA secret storage format is invalid.")
    ciphertext = stored[len("enc:v1:") :].encode("ascii")
    try:
        return _get_mfa_fernet().decrypt(ciphertext).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise AuthenticationError("Unable to decrypt stored MFA secret.") from exc


def _generate_totp(secret_b32: str, counter: int) -> str:
    padded = secret_b32.upper() + "=" * ((8 - len(secret_b32) % 8) % 8)
    key = base64.b32decode(padded, casefold=True)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{code_int % 1_000_000:06d}"


def _verify_totp(secret_b32: str, code: str, *, window: int = 1) -> bool:
    cleaned = code.strip().replace(" ", "")
    if not cleaned.isdigit() or len(cleaned) != 6:
        return False
    current_counter = int(time.time() // 30)
    for offset in range(-window, window + 1):
        if hmac.compare_digest(_generate_totp(secret_b32, current_counter + offset), cleaned):
            return True
    return False


class IdentityAccessService:
    """Authentication, registration, MFA, refresh rotation, and user profile service."""

    def __init__(
        self,
        identity_repo: Optional[IdentityRepository] = None,
        partner_repo: Optional[PartnerRepository] = None,
        audit_service: Optional[AuditService] = None,
        outbox_repo: Optional[OutboxRepository] = None,
    ) -> None:
        self._identity_repo = identity_repo or IdentityRepository()
        self._partner_repo = partner_repo or PartnerRepository()
        self._audit = audit_service or AuditService()
        self._outbox = outbox_repo or OutboxRepository()

    def register(
        self,
        db: Session,
        payload: RegisterRequest,
        *,
        source_ip: Optional[str] = None,
    ) -> RegisterResponse:
        existing = self._identity_repo.get_user_by_email(db, payload.email)
        if existing is not None:
            raise ConflictError("An account with this email already exists.", error_code="EMAIL_EXISTS")

        pwd_hash = get_password_hash(payload.password)
        raw_verify_token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)

        with UnitOfWork(db):
            user = User(
                email=payload.email,
                mobile_e164=payload.mobile_e164,
                password_hash=pwd_hash,
                full_name=payload.full_name.strip(),
                account_status=UserAccountStatus.PENDING_VERIFICATION.value,
            )
            self._identity_repo.create_user(db, user)

            self._identity_repo.create_credential(
                db,
                UserCredential(
                    user_id=user.id,
                    credential_type=CredentialType.PASSWORD.value,
                    credential_hash=pwd_hash,
                    issued_at=now,
                ),
            )
            self._identity_repo.create_credential(
                db,
                UserCredential(
                    user_id=user.id,
                    credential_type="EMAIL_VERIFY",
                    credential_hash=hash_token(raw_verify_token),
                    issued_at=now,
                    expires_at=now + timedelta(hours=24),
                ),
            )

            if payload.profile_type == "CUSTOMER" or payload.customer_profile is not None:
                cp = payload.customer_profile
                db.add(
                    CustomerProfile(
                        user_id=user.id,
                        full_name=(cp.full_name if cp and cp.full_name else user.full_name),
                        phone_e164=(cp.phone_e164 if cp else user.mobile_e164),
                        address_line=cp.address_line if cp else None,
                        city=cp.city if cp else None,
                        state=cp.state if cp else None,
                        pincode=cp.pincode if cp else None,
                        country_code=cp.country_code if cp else "IN",
                        preferred_language=cp.preferred_language if cp else "en",
                        created_by_user_id=user.id,
                        updated_by_user_id=user.id,
                    )
                )

            if payload.profile_type == "CHEMIST" or payload.chemist_profile is not None:
                if payload.chemist_profile is None:
                    raise ValidationError("chemist_profile details are required for CHEMIST registration.")
                ch = payload.chemist_profile
                db.add(
                    ChemistProfile(
                        user_id=user.id,
                        pharmacy_name=ch.pharmacy_name,
                        drug_license_number=ch.drug_license_number,
                        gst_number=ch.gst_number,
                        pharmacy_address=ch.pharmacy_address,
                        city=ch.city,
                        state=ch.state,
                        pincode=ch.pincode,
                        pharmacist_registration_number=ch.pharmacist_registration_number,
                        verification_status="PENDING",
                        created_by_user_id=user.id,
                        updated_by_user_id=user.id,
                    )
                )

            self._audit.record(
                db,
                organization_id=None,
                actor_type=ActorType.USER.value,
                actor_id=user.id,
                action="USER_REGISTERED",
                entity_type="users",
                entity_id=user.id,
                source_ip=source_ip,
                after_state={"email": user.email, "account_status": user.account_status},
            )
            # Never store raw verification tokens in outbox_events.payload_json
            self._outbox.enqueue(
                db,
                organization_id=None,
                aggregate_type="user",
                aggregate_id=user.id,
                event_type="EmailVerificationRequested",
                payload_json={"user_id": str(user.id), "email": user.email},
            )

        cfg = get_settings()
        exposed_token = raw_verify_token if cfg.APP_ENV in {"development", "test"} else None
        return RegisterResponse(
            user_id=user.id,
            email=user.email,
            account_status=user.account_status,
            verification_required=True,
            verification_token=exposed_token,
        )

    def verify_email(self, db: Session, raw_token: str, *, source_ip: Optional[str] = None) -> VerifyEmailResponse:
        digest = hash_token(raw_token)
        cred = self._identity_repo.get_active_credential_by_hash(db, "EMAIL_VERIFY", digest)
        if cred is None:
            raise AuthenticationError("Invalid or already used email verification token.")
        now = datetime.now(timezone.utc)
        if cred.expires_at is not None and _as_utc(cred.expires_at) <= now:
            raise AuthenticationError("Email verification token has expired.")

        user = self._identity_repo.get_user_by_id(db, cred.user_id)
        if user is None:
            raise NotFoundError("User account not found.")

        with UnitOfWork(db):
            cred.revoked_at = now
            cred.last_used_at = now
            user.email_verified_at = now
            if user.account_status == UserAccountStatus.PENDING_VERIFICATION.value:
                user.account_status = UserAccountStatus.ACTIVE.value
            self._audit.record(
                db,
                organization_id=None,
                actor_type=ActorType.USER.value,
                actor_id=user.id,
                action="EMAIL_VERIFIED",
                entity_type="users",
                entity_id=user.id,
                source_ip=source_ip,
            )

        return VerifyEmailResponse(
            user_id=user.id,
            account_status=user.account_status,
            email_verified_at=user.email_verified_at,  # type: ignore[arg-type]
        )

    def _record_failed_attempt_isolated(
        self,
        *,
        user_id: uuid.UUID,
        session_factory: sessionmaker[Session],
        source_ip: Optional[str],
        user_agent: Optional[str],
    ) -> None:
        now = datetime.now(timezone.utc)
        sec_session = session_factory()
        locked_now = False
        attempts = 0
        try:
            u = sec_session.get(User, user_id)
            if u is not None:
                u.failed_login_attempts = int(u.failed_login_attempts or 0) + 1
                attempts = u.failed_login_attempts
                if attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
                    u.account_status = UserAccountStatus.LOCKED.value
                    u.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
                    locked_now = True
                sec_session.commit()
        except Exception:
            sec_session.rollback()
            raise
        finally:
            sec_session.close()

        self._audit.record_security_event(
            event_type="ACCOUNT_LOCKED" if locked_now else "LOGIN_FAILED_BAD_PASSWORD",
            outcome=SecurityEventOutcome.BLOCKED.value if locked_now else SecurityEventOutcome.DENIED.value,
            severity=SecurityEventSeverity.HIGH.value if locked_now else SecurityEventSeverity.WARNING.value,
            user_id=user_id,
            source_ip=source_ip,
            user_agent=user_agent,
            target_resource="/api/v1/auth/login",
            details_json={"failed_login_attempts": attempts, "locked": locked_now},
            session_factory=session_factory,
        )

    def _unlock_expired_lockout_isolated(
        self,
        *,
        user_id: uuid.UUID,
        session_factory: sessionmaker[Session],
    ) -> None:
        with UnitOfWork(session_factory) as uow:
            assert uow.session is not None
            u = uow.session.get(User, user_id)
            if (
                u is not None
                and u.account_status == UserAccountStatus.LOCKED.value
                and u.email_verified_at is not None
            ):
                u.account_status = UserAccountStatus.ACTIVE.value
                u.failed_login_attempts = 0
                u.locked_until = None

    def _build_token_response(
        self,
        db: Session,
        *,
        user: User,
        access_token: str,
        refresh_token: str,
    ) -> TokenResponse:
        cfg = get_settings()
        active_memberships: List[MembershipBrief] = []
        for m in self._identity_repo.list_user_memberships(db, user.id, active_only=True):
            org = db.get(Organization, m.organization_id)
            if org is None:
                continue
            roles = self._identity_repo.list_membership_role_codes(db, org.id, m.id, active_only=True)
            active_memberships.append(
                MembershipBrief(
                    membership_id=m.id,
                    organization_id=org.id,
                    organization_name=org.legal_name,
                    organization_status=org.status,
                    membership_status=m.membership_status,
                    member_type=m.member_type,
                    role_codes=roles,
                )
            )

        active_partner_memberships: List[PartnerMembershipBrief] = []
        for pm in self._partner_repo.list_user_partner_memberships(db, user.id, active_only=True):
            partner = db.get(BusinessPartner, pm.partner_id)
            role = db.get(Role, pm.role_id)
            active_partner_memberships.append(
                PartnerMembershipBrief(
                    partner_membership_id=pm.id,
                    organization_id=pm.organization_id,
                    partner_id=pm.partner_id,
                    partner_name=partner.legal_name if partner else "",
                    membership_status=pm.membership_status,
                    role_code=role.role_code if role else "",
                )
            )

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=cfg.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user=UserSummaryResponse.model_validate(user),
            memberships=active_memberships,
            partner_memberships=active_partner_memberships,
        )

    def login(
        self,
        db: Session,
        payload: LoginRequest,
        *,
        session_factory: Optional[sessionmaker[Session]] = None,
        source_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> TokenResponse:
        factory = session_factory or get_session_factory()
        user = self._identity_repo.get_user_by_email(db, payload.email)
        if user is None:
            self._audit.record_security_event(
                event_type="LOGIN_FAILED_UNKNOWN_USER",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.WARNING.value,
                source_ip=source_ip,
                user_agent=user_agent,
                target_resource="/api/v1/auth/login",
                session_factory=factory,
            )
            raise AuthenticationError("Invalid email or password.")

        now = datetime.now(timezone.utc)
        if user.account_status == UserAccountStatus.LOCKED.value:
            if user.locked_until is not None and _as_utc(user.locked_until) > now:
                self._audit.record_security_event(
                    event_type="LOGIN_BLOCKED_LOCKED",
                    outcome=SecurityEventOutcome.BLOCKED.value,
                    severity=SecurityEventSeverity.HIGH.value,
                    user_id=user.id,
                    source_ip=source_ip,
                    user_agent=user_agent,
                    target_resource="/api/v1/auth/login",
                    session_factory=factory,
                )
                raise AuthenticationError("Account is temporarily locked due to failed login attempts.")
            # Lockout expired: restore ACTIVE prior to password check if verified
            if user.email_verified_at is not None:
                user_id = user.id
                db.rollback()
                self._unlock_expired_lockout_isolated(
                    user_id=user_id,
                    session_factory=factory,
                )
                user = self._identity_repo.get_user_by_id(db, user_id)
                if user is None:
                    raise AuthenticationError("Invalid email or password.")

        if not user.password_hash or not verify_password(payload.password, user.password_hash):
            user_id = user.id
            db.rollback()
            self._record_failed_attempt_isolated(
                user_id=user_id,
                session_factory=factory,
                source_ip=source_ip,
                user_agent=user_agent,
            )
            raise AuthenticationError("Invalid email or password.")

        if user.account_status != UserAccountStatus.ACTIVE.value:
            self._audit.record_security_event(
                event_type="LOGIN_DENIED_INACTIVE_ACCOUNT",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.WARNING.value,
                user_id=user.id,
                source_ip=source_ip,
                user_agent=user_agent,
                target_resource="/api/v1/auth/login",
                details_json={"account_status": user.account_status},
                session_factory=factory,
            )
            raise AuthenticationError("User account is not active.")

        amr = ["pwd"]
        if user.mfa_state in {MFAState.ENROLLED.value, MFAState.REQUIRED.value}:
            mfa_cred = self._identity_repo.get_latest_mfa_credential(db, user.id)
            if mfa_cred is None or mfa_cred.provider_subject != "active" or not mfa_cred.credential_hash:
                self._audit.record_security_event(
                    event_type="LOGIN_MFA_FAILED",
                    outcome=SecurityEventOutcome.DENIED.value,
                    severity=SecurityEventSeverity.HIGH.value,
                    user_id=user.id,
                    source_ip=source_ip,
                    user_agent=user_agent,
                    target_resource="/api/v1/auth/login",
                    session_factory=factory,
                )
                raise AuthenticationError("Valid MFA code is required.")

            totp_secret = _decrypt_mfa_secret(mfa_cred.credential_hash)
            if not payload.totp_code or not _verify_totp(totp_secret, payload.totp_code):
                self._audit.record_security_event(
                    event_type="LOGIN_MFA_FAILED",
                    outcome=SecurityEventOutcome.DENIED.value,
                    severity=SecurityEventSeverity.HIGH.value,
                    user_id=user.id,
                    source_ip=source_ip,
                    user_agent=user_agent,
                    target_resource="/api/v1/auth/login",
                    session_factory=factory,
                )
                raise AuthenticationError("Valid MFA code is required.")
            amr.append("mfa")

        with UnitOfWork(db):
            user.failed_login_attempts = 0
            user.locked_until = None
            user.last_login_at = now
            raw_refresh, _ = issue_refresh_token(
                db,
                user_id=user.id,
                session_metadata={"ip": source_ip, "user_agent": user_agent, "amr": amr},
            )
            access_token = create_access_token(subject=str(user.id), amr=amr)

        return self._build_token_response(
            db,
            user=user,
            access_token=access_token,
            refresh_token=raw_refresh,
        )

    def refresh(
        self,
        db: Session,
        raw_refresh_token: str,
        *,
        session_factory: Optional[sessionmaker[Session]] = None,
        source_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> TokenResponse:
        existing_cred = db.scalar(
            select(UserCredential).where(
                UserCredential.credential_hash == hash_token(raw_refresh_token),
                UserCredential.credential_type == CredentialType.REFRESH_TOKEN.value,
            )
        )
        preserved_amr = ["pwd"]
        if existing_cred and isinstance(existing_cred.session_metadata, dict):
            raw_amr = existing_cred.session_metadata.get("amr")
            if isinstance(raw_amr, list) and all(isinstance(item, str) for item in raw_amr) and raw_amr:
                preserved_amr = list(raw_amr)

        with UnitOfWork(db):
            new_refresh, new_cred = rotate_refresh_token(
                db,
                raw_refresh_token,
                session_factory=session_factory,
                source_ip=source_ip,
                user_agent=user_agent,
                session_metadata={"ip": source_ip, "user_agent": user_agent, "amr": preserved_amr},
            )
            user = self._identity_repo.get_user_by_id(db, new_cred.user_id)
            if user is None or user.account_status != UserAccountStatus.ACTIVE.value:
                raise AuthenticationError("User account is not active.")
            access_token = create_access_token(subject=str(user.id), amr=preserved_amr)

        return self._build_token_response(
            db,
            user=user,
            access_token=access_token,
            refresh_token=new_refresh,
        )

    def logout(
        self,
        db: Session,
        *,
        user_id: uuid.UUID,
        raw_refresh_token: Optional[str] = None,
        revoke_all: bool = False,
        source_ip: Optional[str] = None,
    ) -> None:
        user = self._identity_repo.get_user_by_id(db, user_id)
        if user is None:
            raise NotFoundError("User not found.")

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            if revoke_all:
                self._identity_repo.revoke_user_refresh_tokens(db, user.id)
                user.token_valid_after = now
            elif raw_refresh_token:
                cred = self._identity_repo.get_active_credential_by_hash(
                    db, CredentialType.REFRESH_TOKEN.value, hash_token(raw_refresh_token)
                )
                if cred is not None and cred.user_id == user.id:
                    cred.revoked_at = now
            self._audit.record(
                db,
                organization_id=None,
                actor_type=ActorType.USER.value,
                actor_id=user.id,
                action="USER_LOGOUT",
                entity_type="users",
                entity_id=user.id,
                source_ip=source_ip,
                metadata_json={"revoke_all": revoke_all},
            )

    def mfa_challenge(
        self,
        db: Session,
        *,
        user_id: uuid.UUID,
        action: str,
        code: Optional[str] = None,
        source_ip: Optional[str] = None,
    ) -> MFAChallengeResponse:
        user = self._identity_repo.get_user_by_id(db, user_id)
        if user is None:
            raise NotFoundError("User not found.")

        now = datetime.now(timezone.utc)
        if action == "enroll":
            secret_b32 = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
            encrypted_secret = _encrypt_mfa_secret(secret_b32)
            with UnitOfWork(db):
                existing = self._identity_repo.get_latest_mfa_credential(db, user.id)
                if existing is not None and existing.provider_subject == "pending":
                    existing.revoked_at = now
                self._identity_repo.create_credential(
                    db,
                    UserCredential(
                        user_id=user.id,
                        credential_type=CredentialType.MFA.value,
                        provider_subject="pending",
                        credential_hash=encrypted_secret,
                        issued_at=now,
                    ),
                )
            uri = f"otpauth://totp/Ojasyn:{user.email}?secret={secret_b32}&issuer=Ojasyn"
            return MFAChallengeResponse(
                mfa_state=user.mfa_state,
                verified=False,
                provisioning_uri=uri,
                secret=secret_b32,
            )

        mfa_cred = self._identity_repo.get_latest_mfa_credential(db, user.id)
        if mfa_cred is None or not mfa_cred.credential_hash or not code:
            raise AuthenticationError("MFA secret or verification code missing.")

        totp_secret = _decrypt_mfa_secret(mfa_cred.credential_hash)
        if not _verify_totp(totp_secret, code):
            self._audit.record_security_event(
                event_type="MFA_CHALLENGE_FAILED",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.WARNING.value,
                user_id=user.id,
                source_ip=source_ip,
                target_resource="/api/v1/auth/mfa/challenge",
            )
            raise AuthenticationError("Invalid MFA verification code.")

        with UnitOfWork(db):
            if action == "enroll_confirm":
                mfa_cred.provider_subject = "active"
                mfa_cred.last_used_at = now
                user.mfa_state = MFAState.ENROLLED.value
                self._audit.record(
                    db,
                    organization_id=None,
                    actor_type=ActorType.USER.value,
                    actor_id=user.id,
                    action="MFA_ENROLLED",
                    entity_type="users",
                    entity_id=user.id,
                )
            else:
                if mfa_cred.provider_subject != "active":
                    raise AuthenticationError("MFA credential is not active.")
                mfa_cred.last_used_at = now

        return MFAChallengeResponse(mfa_state=user.mfa_state, verified=True)

    def get_my_profile(self, db: Session, user_id: uuid.UUID) -> UserProfileResponse:
        user = self._identity_repo.get_user_by_id(db, user_id)
        if user is None:
            raise NotFoundError("User not found.")
        dummy = self._build_token_response(db, user=user, access_token="", refresh_token="")
        cust = self._identity_repo.get_customer_profile(db, user_id)
        chem = self._identity_repo.get_chemist_profile(db, user_id)
        cust_dict = (
            {
                "id": str(cust.id),
                "full_name": cust.full_name,
                "phone_e164": cust.phone_e164,
                "address_line": cust.address_line,
                "city": cust.city,
                "state": cust.state,
                "pincode": cust.pincode,
                "country_code": cust.country_code,
            }
            if cust
            else None
        )
        chem_dict = (
            {
                "id": str(chem.id),
                "pharmacy_name": chem.pharmacy_name,
                "drug_license_number": chem.drug_license_number,
                "gst_number": chem.gst_number,
                "pharmacy_address": chem.pharmacy_address,
                "city": chem.city,
                "state": chem.state,
                "pincode": chem.pincode,
                "verification_status": chem.verification_status,
            }
            if chem
            else None
        )
        return UserProfileResponse(
            user=dummy.user,
            memberships=dummy.memberships,
            partner_memberships=dummy.partner_memberships,
            customer_profile=cust_dict,
            chemist_profile=chem_dict,
        )

    def update_my_profile(
        self, db: Session, user_id: uuid.UUID, payload: UpdateUserProfileRequest
    ) -> UserProfileResponse:
        user = self._identity_repo.get_user_by_id(db, user_id)
        if user is None:
            raise NotFoundError("User not found.")

        with UnitOfWork(db):
            if payload.full_name is not None:
                user.full_name = payload.full_name.strip()
            if payload.mobile_e164 is not None:
                user.mobile_e164 = payload.mobile_e164
            if payload.customer_profile is not None:
                cust = self._identity_repo.get_customer_profile(db, user_id)
                if cust is None:
                    cust = CustomerProfile(
                        user_id=user.id,
                        full_name=payload.customer_profile.full_name or user.full_name,
                        created_by_user_id=user.id,
                    )
                    db.add(cust)
                for field in ("phone_e164", "address_line", "city", "state", "pincode", "country_code", "preferred_language"):
                    val = getattr(payload.customer_profile, field)
                    if val is not None:
                        setattr(cust, field, val)
                if payload.customer_profile.full_name:
                    cust.full_name = payload.customer_profile.full_name
                cust.updated_by_user_id = user.id
            if payload.chemist_profile is not None:
                chem = self._identity_repo.get_chemist_profile(db, user_id)
                if chem is None:
                    raise NotFoundError("Chemist profile does not exist for this user.")
                for field, value in payload.chemist_profile.model_dump(exclude_unset=True).items():
                    setattr(chem, field, value)
                chem.updated_by_user_id = user.id

            self._audit.record(
                db,
                organization_id=None,
                actor_type=ActorType.USER.value,
                actor_id=user.id,
                action="USER_PROFILE_UPDATED",
                entity_type="users",
                entity_id=user.id,
            )

        return self.get_my_profile(db, user_id)
