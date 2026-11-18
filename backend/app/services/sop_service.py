import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import UnitOfWork, verify_and_increment_version
from app.core.deps import TenantContext
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.domain.enums import (
    ActorType,
    ApprovalDecision,
    SecurityEventOutcome,
    SecurityEventSeverity,
)
from app.domain.state_machines import assert_transition
from app.models.domain.documents import Document, DocumentVersion
from app.models.domain.governance import ApprovalRecord
from app.models.domain.identity import OrganizationMembership
from app.models.domain.sops import SOP, SOPRevision, SOPStep
from app.models.domain.tenancy import Department, Site
from app.models.schemas.sops import (
    SOPCreateRequest,
    SOPResolutionResponse,
    SOPRevisionActivateRequest,
    SOPRevisionApproveRequest,
    SOPRevisionCreateRequest,
    SOPRevisionResponse,
    SOPRevisionSubmitReviewRequest,
    SOPStepCreateRequest,
    SOPStepResponse,
    SOPStepUpdateRequest,
    SOPUpdateRequest,
)
from app.repositories.governance_repo import OutboxRepository
from app.repositories.sop_repo import SOPRepository
from app.services.audit_service import AuditService


SOP_STATUS_TRANSITIONS: Dict[str, Set[str]] = {
    "DRAFT": {"ACTIVE", "OBSOLETE", "ARCHIVED"},
    "ACTIVE": {"SUPERSEDED", "OBSOLETE", "ARCHIVED"},
    "SUPERSEDED": {"OBSOLETE", "ARCHIVED"},
    "OBSOLETE": {"ARCHIVED"},
    "ARCHIVED": set(),
}

SOP_REVISION_TRANSITIONS: Dict[str, Set[str]] = {
    "DRAFT": {"REVIEW"},
    "REVIEW": {"APPROVED", "REJECTED"},
    "APPROVED": {"EFFECTIVE", "OBSOLETE"},
    "EFFECTIVE": {"SUPERSEDED", "OBSOLETE"},
    "SUPERSEDED": {"OBSOLETE"},
    "OBSOLETE": set(),
    "REJECTED": set(),
}

VALID_SOP_PARAMETER_DATA_TYPES: Set[str] = {
    "NUMERIC",
    "TEXT",
    "BOOLEAN",
    "ENUM",
    "PASS_FAIL",
    "NONE",
}


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class SOPResolutionResult:
    """Structured result of historical SOP revision resolution at an execution timestamp (`C5`)."""

    outcome: str
    sop_code: str
    evaluated_at: datetime
    sop: Optional[SOP] = None
    sop_id: Optional[uuid.UUID] = None
    site_id: Optional[uuid.UUID] = None
    revision: Optional[SOPRevision] = None
    sop_revision_id: Optional[uuid.UUID] = None
    revision_number: Optional[str] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    steps: List[SOPStep] = field(default_factory=list)
    candidate_revision_ids: List[uuid.UUID] = field(default_factory=list)
    explanation: str = ""

    @property
    def is_resolved(self) -> bool:
        return self.outcome == "RESOLVED" and self.revision is not None

    def get_step(
        self,
        *,
        step_id: Optional[uuid.UUID] = None,
        step_code: Optional[str] = None,
        parameter_code: Optional[str] = None,
    ) -> Optional[SOPStep]:
        for step in self.steps:
            if step_id is not None and step.id == step_id:
                return step
            if (
                step_code is not None
                and step.step_code.strip().lower() == step_code.strip().lower()
            ):
                return step
            if (
                parameter_code is not None
                and step.parameter_code is not None
                and step.parameter_code.strip().lower() == parameter_code.strip().lower()
            ):
                return step
        return None

    def to_response(self) -> SOPResolutionResponse:
        rev_resp: Optional[SOPRevisionResponse] = None
        step_resps = [SOPStepResponse.model_validate(s) for s in self.steps]
        if self.revision is not None:
            rev_resp = SOPRevisionResponse.model_validate(self.revision)
            rev_resp.steps = step_resps
        return SOPResolutionResponse(
            outcome=self.outcome,
            sop_id=self.sop_id,
            sop_code=self.sop_code,
            site_id=self.site_id,
            evaluated_at=self.evaluated_at,
            revision=rev_resp,
            sop_revision_id=self.sop_revision_id,
            revision_number=self.revision_number,
            effective_from=self.effective_from,
            effective_to=self.effective_to,
            steps=step_resps,
            candidate_revision_ids=list(self.candidate_revision_ids),
            explanation=self.explanation,
        )


class _ResolveDescriptor:
    """Allows `SOPResolver.resolve(...)` to be called either on the class or on an `SOPResolver(db)` instance."""

    def __init__(self, func: Callable[..., SOPResolutionResult]) -> None:
        self._func = func

    def __get__(
        self, instance: Optional["SOPResolver"], owner: Any = None
    ) -> Callable[..., SOPResolutionResult]:
        if instance is None:
            return lambda *args, **kwargs: self._func(None, *args, **kwargs)
        return lambda *args, **kwargs: self._func(instance, *args, **kwargs)


class SOPResolver:
    """
    Deterministic historical effective-date SOP revision resolver (`C5`).
    Never defaults to the latest revision; always resolves strictly against `[effective_from, effective_to)` at `at`.
    """

    def __init__(
        self,
        db: Optional[Session] = None,
        organization_id: Optional[uuid.UUID] = None,
    ) -> None:
        self._db = db
        self._organization_id = organization_id

    def _resolve_impl(
        self,
        *args: Any,
        sop_code: Optional[str] = None,
        site_id: Optional[uuid.UUID] = None,
        at: Optional[datetime] = None,
        db: Optional[Session] = None,
        organization_id: Optional[uuid.UUID] = None,
        sop_id: Optional[uuid.UUID] = None,
    ) -> SOPResolutionResult:
        # Support both `(sop_code, site_id, at, *, db=..., organization_id=...)`
        # and `(db, organization_id, sop_code, site_id, at)` calling styles.
        rem_args = list(args)
        if rem_args and isinstance(rem_args[0], Session):
            db = rem_args.pop(0)
        if rem_args and isinstance(rem_args[0], uuid.UUID) and organization_id is None and sop_code is not None:
            organization_id = rem_args.pop(0)
        if rem_args and sop_code is None and isinstance(rem_args[0], str):
            sop_code = rem_args.pop(0)
        if rem_args and site_id is None and (isinstance(rem_args[0], uuid.UUID) or rem_args[0] is None):
            site_id = rem_args.pop(0)
        if rem_args and at is None and isinstance(rem_args[0], datetime):
            at = rem_args.pop(0)

        session = db or (self._db if self is not None else None)
        org_id = organization_id or (self._organization_id if self is not None else None)
        if session is None or org_id is None:
            raise ValidationError(
                "SOPResolver.resolve requires both 'db' and 'organization_id'.",
                error_code="SOP_RESOLVER_CONTEXT_REQUIRED",
            )
        if at is None:
            raise ValidationError(
                "SOPResolver.resolve requires an explicit historical timestamp 'at'.",
                error_code="SOP_RESOLVER_TIMESTAMP_REQUIRED",
            )
        at_utc = _as_utc(at)
        assert at_utc is not None

        repo = SOPRepository()
        sop: Optional[SOP] = None
        if sop_id is not None:
            sop = repo.get_sop_for_org(session, org_id, sop_id)
        if sop is None and sop_code:
            sop = repo.get_sop_by_code(session, org_id, sop_code)

        resolved_code = sop.sop_code if sop is not None else (sop_code or "")
        if sop is None:
            return SOPResolutionResult(
                outcome="SOP_NOT_FOUND",
                sop_code=resolved_code,
                evaluated_at=at_utc,
                site_id=site_id,
                explanation=f"No SOP matching '{resolved_code or sop_id}' exists in this organization.",
            )

        # Verify SOP-level site applicability if the SOP is restricted to specific site(s)
        if site_id is not None:
            if sop.site_id is not None and sop.site_id != site_id:
                allowed_extra = {str(s) for s in (sop.applicable_site_ids or [])}
                if str(site_id) not in allowed_extra:
                    return SOPResolutionResult(
                        outcome="NO_APPLICABLE_REVISION",
                        sop=sop,
                        sop_id=sop.id,
                        sop_code=sop.sop_code,
                        site_id=site_id,
                        evaluated_at=at_utc,
                        explanation=(
                            f"SOP '{sop.sop_code}' is scoped to a different site and is not applicable to site {site_id}."
                        ),
                    )
            elif sop.site_id is None and sop.applicable_site_ids:
                allowed_extra = {str(s) for s in sop.applicable_site_ids}
                if str(site_id) not in allowed_extra:
                    return SOPResolutionResult(
                        outcome="NO_APPLICABLE_REVISION",
                        sop=sop,
                        sop_id=sop.id,
                        sop_code=sop.sop_code,
                        site_id=site_id,
                        evaluated_at=at_utc,
                        explanation=(
                            f"SOP '{sop.sop_code}' applicable_site_ids does not include site {site_id}."
                        ),
                    )

        all_revisions = repo.list_revisions_for_sop(session, org_id, sop.id)
        if not all_revisions:
            return SOPResolutionResult(
                outcome="NO_APPLICABLE_REVISION",
                sop=sop,
                sop_id=sop.id,
                sop_code=sop.sop_code,
                site_id=site_id,
                evaluated_at=at_utc,
                explanation=f"SOP '{sop.sop_code}' has no revisions.",
            )

        applicable_revisions: List[SOPRevision] = []
        for rev in all_revisions:
            if site_id is not None and rev.site_id is not None and rev.site_id != site_id:
                continue
            applicable_revisions.append(rev)

        if not applicable_revisions:
            return SOPResolutionResult(
                outcome="NO_APPLICABLE_REVISION",
                sop=sop,
                sop_id=sop.id,
                sop_code=sop.sop_code,
                site_id=site_id,
                evaluated_at=at_utc,
                explanation=f"SOP '{sop.sop_code}' has no revisions applicable to site {site_id}.",
            )

        matching_active: List[SOPRevision] = []
        expired_revs: List[SOPRevision] = []
        pending_revs: List[SOPRevision] = []

        for rev in applicable_revisions:
            eff_from = _as_utc(rev.effective_from)
            eff_to = _as_utc(rev.effective_to)
            if rev.revision_status in ("EFFECTIVE", "SUPERSEDED"):
                if eff_from is not None and eff_from <= at_utc and (eff_to is None or eff_to > at_utc):
                    matching_active.append(rev)
                elif eff_to is not None and eff_to <= at_utc:
                    expired_revs.append(rev)
            elif rev.revision_status == "OBSOLETE":
                if eff_to is not None and eff_to <= at_utc:
                    expired_revs.append(rev)
            elif rev.revision_status in ("DRAFT", "REVIEW", "APPROVED"):
                pending_revs.append(rev)

        if len(matching_active) == 1:
            chosen = matching_active[0]
            steps = repo.list_steps_for_revision(session, org_id, chosen.id)
            return SOPResolutionResult(
                outcome="RESOLVED",
                sop=sop,
                sop_id=sop.id,
                sop_code=sop.sop_code,
                site_id=site_id,
                evaluated_at=at_utc,
                revision=chosen,
                sop_revision_id=chosen.id,
                revision_number=chosen.revision_number,
                effective_from=chosen.effective_from,
                effective_to=chosen.effective_to,
                steps=steps,
                candidate_revision_ids=[chosen.id],
                explanation=(
                    f"Resolved SOP '{sop.sop_code}' revision '{chosen.revision_number}' "
                    f"effective at {at_utc.isoformat()}."
                ),
            )

        if len(matching_active) > 1:
            return SOPResolutionResult(
                outcome="AMBIGUOUS_MULTIPLE_REVISIONS",
                sop=sop,
                sop_id=sop.id,
                sop_code=sop.sop_code,
                site_id=site_id,
                evaluated_at=at_utc,
                candidate_revision_ids=[r.id for r in matching_active],
                explanation=(
                    f"Ambiguous SOP resolution: {len(matching_active)} revisions of '{sop.sop_code}' "
                    f"are simultaneously effective at {at_utc.isoformat()}."
                ),
            )

        if expired_revs:
            return SOPResolutionResult(
                outcome="EXPIRED_ONLY",
                sop=sop,
                sop_id=sop.id,
                sop_code=sop.sop_code,
                site_id=site_id,
                evaluated_at=at_utc,
                candidate_revision_ids=[r.id for r in expired_revs],
                explanation=(
                    f"No revision of SOP '{sop.sop_code}' was effective at {at_utc.isoformat()}; "
                    f"{len(expired_revs)} prior revision(s) expired at or before that timestamp."
                ),
            )

        if pending_revs:
            return SOPResolutionResult(
                outcome="PENDING_APPROVAL_ONLY",
                sop=sop,
                sop_id=sop.id,
                sop_code=sop.sop_code,
                site_id=site_id,
                evaluated_at=at_utc,
                candidate_revision_ids=[r.id for r in pending_revs],
                explanation=(
                    f"No revision of SOP '{sop.sop_code}' was activated for {at_utc.isoformat()}; "
                    f"{len(pending_revs)} revision(s) remain in DRAFT/REVIEW/APPROVED."
                ),
            )

        return SOPResolutionResult(
            outcome="NO_APPLICABLE_REVISION",
            sop=sop,
            sop_id=sop.id,
            sop_code=sop.sop_code,
            site_id=site_id,
            evaluated_at=at_utc,
            explanation=(
                f"No revision of SOP '{sop.sop_code}' covers timestamp {at_utc.isoformat()}."
            ),
        )

    resolve = _ResolveDescriptor(_resolve_impl)


class SOPService:
    """Domain service for SOPs, Effective-Dated SOP Revisions, SOP Steps, and Historical Resolution (`C5`)."""

    def __init__(
        self,
        repo: Optional[SOPRepository] = None,
        audit_service: Optional[AuditService] = None,
        outbox_repo: Optional[OutboxRepository] = None,
    ) -> None:
        self._repo = repo or SOPRepository()
        self._audit = audit_service or AuditService()
        self._outbox = outbox_repo or OutboxRepository()

    # --- Internal Helpers ---

    def _verify_site_belongs_to_org_and_scope(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        site_id: Optional[uuid.UUID],
    ) -> None:
        if site_id is None:
            return
        site = db.scalar(
            select(Site).where(
                Site.organization_id == tenant_ctx.organization_id,
                Site.id == site_id,
            )
        )
        if site is None:
            raise NotFoundError("Site not found in this organization.")
        if not tenant_ctx.is_org_wide and site_id not in tenant_ctx.allowed_site_ids:
            self._audit.record_security_event(
                event_type="SITE_SCOPE_VIOLATION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.WARNING.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                target_resource=f"sites/{site_id}",
                details_json={"site_id": str(site_id)},
            )
            raise PermissionDeniedError(
                "You do not have access to the specified site.",
                error_code="SITE_SCOPE_DENIED",
            )

    def _verify_applicable_sites(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        applicable_site_ids: Optional[List[uuid.UUID]],
    ) -> Optional[List[str]]:
        if applicable_site_ids is None:
            return None
        normalized: List[str] = []
        seen: Set[str] = set()
        for sid in applicable_site_ids:
            self._verify_site_belongs_to_org_and_scope(db, tenant_ctx, sid)
            s_str = str(sid)
            if s_str not in seen:
                seen.add(s_str)
                normalized.append(s_str)
        return normalized

    def _verify_department_belongs_to_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        department_id: Optional[uuid.UUID],
    ) -> None:
        if department_id is None:
            return
        dept = db.scalar(
            select(Department).where(
                Department.organization_id == organization_id,
                Department.id == department_id,
            )
        )
        if dept is None:
            raise NotFoundError("Department not found in this organization.")

    def _verify_membership_belongs_to_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        membership_id: Optional[uuid.UUID],
    ) -> None:
        if membership_id is None:
            return
        m = db.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.id == membership_id,
            )
        )
        if m is None:
            raise NotFoundError("Organization membership not found in this organization.")

    def _verify_document_and_version_belong_to_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        document_id: Optional[uuid.UUID],
        document_version_id: Optional[uuid.UUID] = None,
    ) -> Optional[uuid.UUID]:
        resolved_doc_id = document_id
        if document_id is not None:
            doc = db.scalar(
                select(Document).where(
                    Document.organization_id == organization_id,
                    Document.id == document_id,
                )
            )
            if doc is None:
                raise NotFoundError("Document not found in this organization.")
        if document_version_id is not None:
            ver = db.scalar(
                select(DocumentVersion)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    Document.organization_id == organization_id,
                    DocumentVersion.id == document_version_id,
                )
            )
            if ver is None:
                raise NotFoundError("Document version not found in this organization.")
            if resolved_doc_id is not None and ver.document_id != resolved_doc_id:
                raise ValidationError(
                    "document_version_id does not belong to the specified document_id.",
                    error_code="DOCUMENT_VERSION_MISMATCH",
                )
            resolved_doc_id = ver.document_id
        return resolved_doc_id

    def _assert_site_access(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        site_id: Optional[uuid.UUID],
        *,
        applicable_site_ids: Optional[List[str]] = None,
        resource: str = "sops",
    ) -> None:
        if tenant_ctx.is_org_wide:
            return
        if site_id is not None:
            if site_id not in tenant_ctx.allowed_site_ids:
                self._audit.record_security_event(
                    event_type="SITE_SCOPE_VIOLATION",
                    outcome=SecurityEventOutcome.DENIED.value,
                    severity=SecurityEventSeverity.WARNING.value,
                    organization_id=tenant_ctx.organization_id,
                    user_id=tenant_ctx.user_id,
                    target_resource=resource,
                    details_json={"site_id": str(site_id)},
                )
                raise PermissionDeniedError(
                    "Record belongs to a site outside your assigned scope.",
                    error_code="SITE_SCOPE_DENIED",
                )
            return
        if applicable_site_ids:
            allowed_strs = {str(s) for s in tenant_ctx.allowed_site_ids}
            if not any(s in allowed_strs for s in applicable_site_ids):
                self._audit.record_security_event(
                    event_type="SITE_SCOPE_VIOLATION",
                    outcome=SecurityEventOutcome.DENIED.value,
                    severity=SecurityEventSeverity.WARNING.value,
                    organization_id=tenant_ctx.organization_id,
                    user_id=tenant_ctx.user_id,
                    target_resource=resource,
                    details_json={"applicable_site_ids": applicable_site_ids},
                )
                raise PermissionDeniedError(
                    "Record belongs to sites outside your assigned scope.",
                    error_code="SITE_SCOPE_DENIED",
                )

    def _bump_version(self, db: Session, instance: Any, expected_version: Optional[int]) -> None:
        if expected_version is not None:
            verify_and_increment_version(instance, expected_version)
        else:
            instance.row_version = int(instance.row_version) + 1

    def _validate_step_data_type(self, expected_data_type: str) -> str:
        norm = expected_data_type.strip().upper()
        if norm not in VALID_SOP_PARAMETER_DATA_TYPES:
            raise ValidationError(
                f"Unsupported SOP step expected_data_type '{expected_data_type}'.",
                error_code="INVALID_SOP_STEP_DATA_TYPE",
                details={"allowed": sorted(VALID_SOP_PARAMETER_DATA_TYPES)},
            )
        return norm

    def _assert_revision_mutable(self, revision: SOPRevision) -> None:
        if revision.revision_status != "DRAFT":
            raise ConflictError(
                f"SOP revision '{revision.revision_number}' is in status '{revision.revision_status}' and is immutable.",
                error_code="SOP_REVISION_IMMUTABLE",
                details={
                    "revision_id": str(revision.id),
                    "revision_status": revision.revision_status,
                },
            )

    def _revisions_share_site_scope(
        self,
        rev_a_site_id: Optional[uuid.UUID],
        rev_b_site_id: Optional[uuid.UUID],
    ) -> bool:
        if rev_a_site_id is None or rev_b_site_id is None:
            return True
        return rev_a_site_id == rev_b_site_id

    # --- SOP Master Commands & Queries ---

    def create_sop(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        payload: SOPCreateRequest,
    ) -> SOP:
        code = payload.sop_code.strip().upper()
        if self._repo.get_sop_by_code(db, tenant_ctx.organization_id, code) is not None:
            raise ConflictError(
                f"SOP code '{code}' already exists in this organization.",
                error_code="DUPLICATE_SOP_CODE",
            )
        self._verify_site_belongs_to_org_and_scope(db, tenant_ctx, payload.site_id)
        applicable_sites = self._verify_applicable_sites(
            db, tenant_ctx, payload.applicable_site_ids
        )
        if not tenant_ctx.is_org_wide and payload.site_id is None and not applicable_sites:
            self._audit.record_security_event(
                event_type="SITE_SCOPE_VIOLATION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.WARNING.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                target_resource="sops",
                details_json={"reason": "site_scoped_actor_cannot_create_org_wide_sop"},
            )
            raise PermissionDeniedError(
                "Site-scoped users must specify a site_id or non-empty applicable_site_ids.",
                error_code="SITE_SCOPE_DENIED",
            )
        self._verify_department_belongs_to_org(
            db, tenant_ctx.organization_id, payload.department_id
        )
        self._verify_document_and_version_belong_to_org(
            db, tenant_ctx.organization_id, payload.document_id
        )
        owner_membership_id = payload.owner_membership_id or tenant_ctx.membership_id
        self._verify_membership_belongs_to_org(
            db, tenant_ctx.organization_id, owner_membership_id
        )

        with UnitOfWork(db) as uow:
            sop = SOP(
                organization_id=tenant_ctx.organization_id,
                site_id=payload.site_id,
                applicable_site_ids=applicable_sites,
                department_id=payload.department_id,
                sop_code=code,
                title=payload.title.strip(),
                description=payload.description,
                category=payload.category.strip() if payload.category else None,
                document_id=payload.document_id,
                owner_membership_id=owner_membership_id,
                sop_status="DRAFT",
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            self._repo.create_sop(uow.session, sop)
            self._audit.record(
                uow.session,
                organization_id=tenant_ctx.organization_id,
                site_id=sop.site_id,
                actor_type=ActorType.USER,
                actor_id=tenant_ctx.user_id,
                action="SOP_CREATED",
                entity_type="SOP",
                entity_id=sop.id,
                metadata_json={"sop_code": sop.sop_code, "sop_status": sop.sop_status},
            )
            self._outbox.enqueue(
                uow.session,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="SOP",
                aggregate_id=sop.id,
                event_type="SOPCreated",
                payload_json={
                    "sop_id": str(sop.id),
                    "sop_code": sop.sop_code,
                    "sop_status": sop.sop_status,
                },
            )
        return sop

    def list_sops(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        *,
        status: Optional[str] = None,
        site_id: Optional[uuid.UUID] = None,
        category: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[SOP], int]:
        if site_id is not None:
            self._assert_site_access(db, tenant_ctx, site_id)
        return self._repo.list_sops_for_tenant(
            db,
            tenant_ctx,
            status=status,
            site_id=site_id,
            category=category,
            search=search,
            page=page,
            page_size=page_size,
        )

    def get_sop(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        sop_id: uuid.UUID,
    ) -> SOP:
        sop = self._repo.get_sop_for_org(db, tenant_ctx.organization_id, sop_id)
        if sop is None:
            raise NotFoundError("SOP not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            sop.site_id,
            applicable_site_ids=sop.applicable_site_ids,
            resource=f"sops/{sop_id}",
        )
        return sop

    def update_sop(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        sop_id: uuid.UUID,
        payload: SOPUpdateRequest,
    ) -> SOP:
        sop = self.get_sop(db, tenant_ctx, sop_id)
        updates = payload.model_dump(exclude_unset=True, exclude={"row_version"})
        if "site_id" in updates:
            self._verify_site_belongs_to_org_and_scope(db, tenant_ctx, payload.site_id)
        if "applicable_site_ids" in updates:
            updates["applicable_site_ids"] = self._verify_applicable_sites(
                db, tenant_ctx, payload.applicable_site_ids
            )
        if "site_id" in updates or "applicable_site_ids" in updates:
            effective_site_id = updates.get("site_id", sop.site_id)
            effective_applicable = updates.get(
                "applicable_site_ids", sop.applicable_site_ids
            )
            if (
                not tenant_ctx.is_org_wide
                and effective_site_id is None
                and not effective_applicable
            ):
                self._audit.record_security_event(
                    event_type="SITE_SCOPE_VIOLATION",
                    outcome=SecurityEventOutcome.DENIED.value,
                    severity=SecurityEventSeverity.WARNING.value,
                    organization_id=tenant_ctx.organization_id,
                    user_id=tenant_ctx.user_id,
                    target_resource=f"sops/{sop_id}",
                    details_json={"reason": "site_scoped_actor_cannot_clear_site_scope"},
                )
                raise PermissionDeniedError(
                    "Site-scoped users cannot convert an SOP to org-wide.",
                    error_code="SITE_SCOPE_DENIED",
                )
        if "department_id" in updates:
            self._verify_department_belongs_to_org(
                db, tenant_ctx.organization_id, payload.department_id
            )
        if "document_id" in updates:
            self._verify_document_and_version_belong_to_org(
                db, tenant_ctx.organization_id, payload.document_id
            )
        if "owner_membership_id" in updates:
            self._verify_membership_belongs_to_org(
                db, tenant_ctx.organization_id, payload.owner_membership_id
            )
        if "sop_status" in updates and updates["sop_status"] is not None:
            target_status = updates["sop_status"].strip().upper()
            if target_status != sop.sop_status:
                assert_transition(
                    SOP_STATUS_TRANSITIONS,
                    sop.sop_status,
                    target_status,
                    entity_name="SOP",
                )
            updates["sop_status"] = target_status

        with UnitOfWork(db) as uow:
            self._bump_version(uow.session, sop, payload.row_version)
            for field_name, val in updates.items():
                setattr(sop, field_name, val)
            sop.updated_by_user_id = tenant_ctx.user_id
            uow.session.flush()
            self._audit.record(
                uow.session,
                organization_id=tenant_ctx.organization_id,
                site_id=sop.site_id,
                actor_type=ActorType.USER,
                actor_id=tenant_ctx.user_id,
                action="SOP_UPDATED",
                entity_type="SOP",
                entity_id=sop.id,
                metadata_json={"updated_fields": sorted(updates.keys())},
            )
        return sop

    # --- SOP Revisions & Lifecycle ---

    def create_revision(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        sop_id: uuid.UUID,
        payload: SOPRevisionCreateRequest,
    ) -> Tuple[SOPRevision, List[SOPStep]]:
        sop = self.get_sop(db, tenant_ctx, sop_id)
        rev_num = payload.revision_number.strip()
        if (
            self._repo.get_revision_by_number(
                db, tenant_ctx.organization_id, sop.id, rev_num
            )
            is not None
        ):
            raise ConflictError(
                f"Revision '{rev_num}' already exists for SOP '{sop.sop_code}'.",
                error_code="DUPLICATE_SOP_REVISION_NUMBER",
            )
        rev_site_id = payload.site_id if payload.site_id is not None else sop.site_id
        self._verify_site_belongs_to_org_and_scope(db, tenant_ctx, rev_site_id)
        resolved_doc_id = self._verify_document_and_version_belong_to_org(
            db,
            tenant_ctx.organization_id,
            payload.document_id or sop.document_id,
            payload.document_version_id,
        )
        if payload.supersedes_revision_id is not None:
            prior = self._repo.get_revision_for_org(
                db,
                tenant_ctx.organization_id,
                payload.supersedes_revision_id,
                sop_id=sop.id,
            )
            if prior is None:
                raise NotFoundError(
                    "Supersedes SOP revision not found on this SOP."
                )

        # Pre-validate inline steps if provided
        inline_steps = payload.steps or []
        seen_codes: Set[str] = set()
        seen_seqs: Set[int] = set()
        for st in inline_steps:
            self._validate_step_data_type(st.expected_data_type)
            c_lower = st.step_code.strip().lower()
            if c_lower in seen_codes:
                raise ConflictError(
                    f"Duplicate step_code '{st.step_code}' in revision request.",
                    error_code="DUPLICATE_SOP_STEP_CODE",
                )
            seen_codes.add(c_lower)
            if st.step_sequence in seen_seqs:
                raise ConflictError(
                    f"Duplicate step_sequence '{st.step_sequence}' in revision request.",
                    error_code="DUPLICATE_SOP_STEP_SEQUENCE",
                )
            seen_seqs.add(st.step_sequence)

        with UnitOfWork(db) as uow:
            seq = payload.revision_sequence or self._repo.get_next_revision_sequence(
                uow.session, tenant_ctx.organization_id, sop.id
            )
            revision = SOPRevision(
                organization_id=tenant_ctx.organization_id,
                sop_id=sop.id,
                site_id=rev_site_id,
                revision_number=rev_num,
                revision_sequence=seq,
                revision_description=payload.revision_description,
                change_reason=payload.change_reason,
                document_id=resolved_doc_id,
                document_version_id=payload.document_version_id,
                effective_from=payload.effective_from,
                effective_to=payload.effective_to,
                revision_status="DRAFT",
                approval_status="DRAFT",
                supersedes_revision_id=payload.supersedes_revision_id,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            self._repo.create_revision(uow.session, revision)

            created_steps: List[SOPStep] = []
            for st in inline_steps:
                step = SOPStep(
                    organization_id=tenant_ctx.organization_id,
                    sop_revision_id=revision.id,
                    step_code=st.step_code.strip().upper(),
                    step_sequence=st.step_sequence,
                    process_stage=st.process_stage,
                    title=st.title.strip(),
                    instruction=st.instruction,
                    is_mandatory=st.is_mandatory,
                    parameter_name=st.parameter_name,
                    parameter_code=st.parameter_code.strip().upper()
                    if st.parameter_code
                    else None,
                    expected_data_type=self._validate_step_data_type(
                        st.expected_data_type
                    ),
                    unit_code=st.unit_code.strip() if st.unit_code else None,
                    min_value=st.min_value,
                    max_value=st.max_value,
                    target_value=st.target_value,
                    expected_text_value=st.expected_text_value,
                    allowed_values_json=st.allowed_values_json,
                    acceptance_criteria=st.acceptance_criteria,
                    requires_operator_signoff=st.requires_operator_signoff,
                    requires_checker_signoff=st.requires_checker_signoff,
                    requires_qa_signoff=st.requires_qa_signoff,
                    required_equipment_category=st.required_equipment_category,
                    required_equipment_code=st.required_equipment_code,
                    created_by_user_id=tenant_ctx.user_id,
                    updated_by_user_id=tenant_ctx.user_id,
                )
                self._repo.create_step(uow.session, step)
                created_steps.append(step)

            self._audit.record(
                uow.session,
                organization_id=tenant_ctx.organization_id,
                site_id=revision.site_id,
                actor_type=ActorType.USER,
                actor_id=tenant_ctx.user_id,
                action="SOP_REVISION_CREATED",
                entity_type="SOPRevision",
                entity_id=revision.id,
                metadata_json={
                    "sop_id": str(sop.id),
                    "sop_code": sop.sop_code,
                    "revision_number": revision.revision_number,
                    "step_count": len(created_steps),
                },
            )
        return revision, created_steps

    def list_revisions(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        sop_id: uuid.UUID,
    ) -> List[Tuple[SOPRevision, List[SOPStep]]]:
        sop = self.get_sop(db, tenant_ctx, sop_id)
        revs = self._repo.list_revisions_for_sop(db, tenant_ctx.organization_id, sop.id)
        result: List[Tuple[SOPRevision, List[SOPStep]]] = []
        for rev in revs:
            steps = self._repo.list_steps_for_revision(
                db, tenant_ctx.organization_id, rev.id
            )
            result.append((rev, steps))
        return result

    def get_revision(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        sop_id: uuid.UUID,
        revision_id: uuid.UUID,
    ) -> Tuple[SOPRevision, List[SOPStep]]:
        sop = self.get_sop(db, tenant_ctx, sop_id)
        rev = self._repo.get_revision_for_org(
            db, tenant_ctx.organization_id, revision_id, sop_id=sop.id
        )
        if rev is None:
            raise NotFoundError("SOP revision not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            rev.site_id or sop.site_id,
            applicable_site_ids=sop.applicable_site_ids,
            resource=f"sops/{sop_id}/revisions/{revision_id}",
        )
        steps = self._repo.list_steps_for_revision(
            db, tenant_ctx.organization_id, rev.id
        )
        return rev, steps

    # --- SOP Steps ---

    def add_step(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        sop_id: uuid.UUID,
        revision_id: uuid.UUID,
        payload: SOPStepCreateRequest,
    ) -> SOPStep:
        sop = self.get_sop(db, tenant_ctx, sop_id)
        rev = self._repo.get_revision_for_org(
            db, tenant_ctx.organization_id, revision_id, sop_id=sop.id
        )
        if rev is None:
            raise NotFoundError("SOP revision not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            rev.site_id or sop.site_id,
            applicable_site_ids=sop.applicable_site_ids,
            resource=f"sops/{sop_id}/revisions/{revision_id}/steps",
        )
        self._assert_revision_mutable(rev)
        data_type = self._validate_step_data_type(payload.expected_data_type)
        step_code = payload.step_code.strip().upper()

        if (
            self._repo.get_step_by_code(
                db, tenant_ctx.organization_id, rev.id, step_code
            )
            is not None
        ):
            raise ConflictError(
                f"Step code '{step_code}' already exists on revision '{rev.revision_number}'.",
                error_code="DUPLICATE_SOP_STEP_CODE",
            )
        if (
            self._repo.get_step_by_sequence(
                db, tenant_ctx.organization_id, rev.id, payload.step_sequence
            )
            is not None
        ):
            raise ConflictError(
                f"Step sequence '{payload.step_sequence}' already exists on revision '{rev.revision_number}'.",
                error_code="DUPLICATE_SOP_STEP_SEQUENCE",
            )

        with UnitOfWork(db) as uow:
            step = SOPStep(
                organization_id=tenant_ctx.organization_id,
                sop_revision_id=rev.id,
                step_code=step_code,
                step_sequence=payload.step_sequence,
                process_stage=payload.process_stage,
                title=payload.title.strip(),
                instruction=payload.instruction,
                is_mandatory=payload.is_mandatory,
                parameter_name=payload.parameter_name,
                parameter_code=payload.parameter_code.strip().upper()
                if payload.parameter_code
                else None,
                expected_data_type=data_type,
                unit_code=payload.unit_code.strip() if payload.unit_code else None,
                min_value=payload.min_value,
                max_value=payload.max_value,
                target_value=payload.target_value,
                expected_text_value=payload.expected_text_value,
                allowed_values_json=payload.allowed_values_json,
                acceptance_criteria=payload.acceptance_criteria,
                requires_operator_signoff=payload.requires_operator_signoff,
                requires_checker_signoff=payload.requires_checker_signoff,
                requires_qa_signoff=payload.requires_qa_signoff,
                required_equipment_category=payload.required_equipment_category,
                required_equipment_code=payload.required_equipment_code,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            self._repo.create_step(uow.session, step)
            self._audit.record(
                uow.session,
                organization_id=tenant_ctx.organization_id,
                site_id=rev.site_id,
                actor_type=ActorType.USER,
                actor_id=tenant_ctx.user_id,
                action="SOP_STEP_ADDED",
                entity_type="SOPStep",
                entity_id=step.id,
                metadata_json={
                    "sop_revision_id": str(rev.id),
                    "step_code": step.step_code,
                    "step_sequence": step.step_sequence,
                },
            )
        return step

    def list_steps(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        sop_id: uuid.UUID,
        revision_id: uuid.UUID,
    ) -> List[SOPStep]:
        _, steps = self.get_revision(db, tenant_ctx, sop_id, revision_id)
        return steps

    def update_step(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        sop_id: uuid.UUID,
        revision_id: uuid.UUID,
        step_id: uuid.UUID,
        payload: SOPStepUpdateRequest,
    ) -> SOPStep:
        sop = self.get_sop(db, tenant_ctx, sop_id)
        rev = self._repo.get_revision_for_org(
            db, tenant_ctx.organization_id, revision_id, sop_id=sop.id
        )
        if rev is None:
            raise NotFoundError("SOP revision not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            rev.site_id or sop.site_id,
            applicable_site_ids=sop.applicable_site_ids,
            resource=f"sops/{sop_id}/revisions/{revision_id}/steps/{step_id}",
        )
        self._assert_revision_mutable(rev)

        step = self._repo.get_step_for_org(
            db,
            tenant_ctx.organization_id,
            step_id,
            sop_revision_id=rev.id,
        )
        if step is None:
            raise NotFoundError("SOP step not found on this revision.")

        updates = payload.model_dump(exclude_unset=True, exclude={"row_version"})
        if "expected_data_type" in updates and updates["expected_data_type"] is not None:
            updates["expected_data_type"] = self._validate_step_data_type(
                updates["expected_data_type"]
            )
        if "step_code" in updates and updates["step_code"] is not None:
            new_code = updates["step_code"].strip().upper()
            existing = self._repo.get_step_by_code(
                db, tenant_ctx.organization_id, rev.id, new_code
            )
            if existing is not None and existing.id != step.id:
                raise ConflictError(
                    f"Step code '{new_code}' already exists on revision '{rev.revision_number}'.",
                    error_code="DUPLICATE_SOP_STEP_CODE",
                )
            updates["step_code"] = new_code
        if "step_sequence" in updates and updates["step_sequence"] is not None:
            new_seq = int(updates["step_sequence"])
            existing_seq = self._repo.get_step_by_sequence(
                db, tenant_ctx.organization_id, rev.id, new_seq
            )
            if existing_seq is not None and existing_seq.id != step.id:
                raise ConflictError(
                    f"Step sequence '{new_seq}' already exists on revision '{rev.revision_number}'.",
                    error_code="DUPLICATE_SOP_STEP_SEQUENCE",
                )
        if "parameter_code" in updates and updates["parameter_code"] is not None:
            updates["parameter_code"] = updates["parameter_code"].strip().upper()

        new_min = updates.get("min_value", step.min_value)
        new_max = updates.get("max_value", step.max_value)
        if new_min is not None and new_max is not None and new_min > new_max:
            raise ValidationError(
                "min_value must be less than or equal to max_value.",
                error_code="INVALID_SOP_STEP_LIMITS",
            )

        with UnitOfWork(db) as uow:
            self._bump_version(uow.session, step, payload.row_version)
            for field_name, val in updates.items():
                setattr(step, field_name, val)
            step.updated_by_user_id = tenant_ctx.user_id
            uow.session.flush()
            self._audit.record(
                uow.session,
                organization_id=tenant_ctx.organization_id,
                site_id=rev.site_id,
                actor_type=ActorType.USER,
                actor_id=tenant_ctx.user_id,
                action="SOP_STEP_UPDATED",
                entity_type="SOPStep",
                entity_id=step.id,
                metadata_json={"updated_fields": sorted(updates.keys())},
            )
        return step

    # --- Revision Review, Maker-Checker Approval & Activation ---

    def submit_revision_for_review(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        sop_id: uuid.UUID,
        revision_id: uuid.UUID,
        payload: Optional[SOPRevisionSubmitReviewRequest] = None,
    ) -> Tuple[SOPRevision, List[SOPStep]]:
        sop = self.get_sop(db, tenant_ctx, sop_id)
        now = datetime.now(timezone.utc)
        req = payload or SOPRevisionSubmitReviewRequest()

        with UnitOfWork(db) as uow:
            rev = self._repo.get_revision_for_org(
                uow.session,
                tenant_ctx.organization_id,
                revision_id,
                sop_id=sop.id,
                for_update=True,
            )
            if rev is None:
                raise NotFoundError("SOP revision not found.")
            self._assert_site_access(
                uow.session,
                tenant_ctx,
                rev.site_id or sop.site_id,
                applicable_site_ids=sop.applicable_site_ids,
                resource=f"sops/{sop_id}/revisions/{revision_id}/submit-review",
            )
            assert_transition(
                SOP_REVISION_TRANSITIONS,
                rev.revision_status,
                "REVIEW",
                entity_name="SOPRevision",
            )
            self._bump_version(uow.session, rev, req.row_version)
            rev.revision_status = "REVIEW"
            rev.approval_status = "REVIEW"
            rev.submitted_by_membership_id = tenant_ctx.membership_id
            rev.submitted_at = now
            rev.updated_by_user_id = tenant_ctx.user_id
            uow.session.flush()

            self._audit.record(
                uow.session,
                organization_id=tenant_ctx.organization_id,
                site_id=rev.site_id,
                actor_type=ActorType.USER,
                actor_id=tenant_ctx.user_id,
                action="SOP_REVISION_SUBMITTED_FOR_REVIEW",
                entity_type="SOPRevision",
                entity_id=rev.id,
                reason=req.comments,
                metadata_json={
                    "sop_id": str(sop.id),
                    "revision_number": rev.revision_number,
                    "revision_status": rev.revision_status,
                },
            )
            steps = self._repo.list_steps_for_revision(
                uow.session, tenant_ctx.organization_id, rev.id
            )
        return rev, steps

    def approve_revision(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        sop_id: uuid.UUID,
        revision_id: uuid.UUID,
        payload: Optional[SOPRevisionApproveRequest] = None,
    ) -> Tuple[SOPRevision, List[SOPStep]]:
        sop = self.get_sop(db, tenant_ctx, sop_id)
        req = payload or SOPRevisionApproveRequest()
        raw_decision = (req.decision or "APPROVED").strip().upper()
        if raw_decision in ("APPROVE", "APPROVED"):
            target_status = "APPROVED"
            approval_decision = ApprovalDecision.APPROVED.value
        elif raw_decision in ("REJECT", "REJECTED"):
            target_status = "REJECTED"
            approval_decision = ApprovalDecision.REJECTED.value
        else:
            raise ValidationError(
                f"Invalid SOP revision approval decision '{req.decision}'.",
                error_code="INVALID_APPROVAL_DECISION",
            )

        rev_pre = self._repo.get_revision_for_org(
            db, tenant_ctx.organization_id, revision_id, sop_id=sop.id
        )
        if rev_pre is None:
            raise NotFoundError("SOP revision not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            rev_pre.site_id or sop.site_id,
            applicable_site_ids=sop.applicable_site_ids,
            resource=f"sops/{sop_id}/revisions/{revision_id}/approve",
        )
        assert_transition(
            SOP_REVISION_TRANSITIONS,
            rev_pre.revision_status,
            target_status,
            entity_name="SOPRevision",
        )

        # Dual user_id + membership_id maker-checker enforcement before write transaction
        is_creator = (
            rev_pre.created_by_user_id is not None
            and rev_pre.created_by_user_id == tenant_ctx.user_id
        )
        is_submitter = (
            rev_pre.submitted_by_membership_id is not None
            and rev_pre.submitted_by_membership_id == tenant_ctx.membership_id
        )
        if is_creator or is_submitter:
            self._audit.record_security_event(
                event_type="SELF_APPROVAL_BLOCKED",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.WARNING.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                target_resource=f"sop_revisions/{rev_pre.id}",
                details_json={
                    "sop_id": str(sop.id),
                    "sop_revision_id": str(rev_pre.id),
                    "created_by_user_id": str(rev_pre.created_by_user_id)
                    if rev_pre.created_by_user_id
                    else None,
                    "submitted_by_membership_id": str(rev_pre.submitted_by_membership_id)
                    if rev_pre.submitted_by_membership_id
                    else None,
                    "approver_user_id": str(tenant_ctx.user_id),
                    "approver_membership_id": str(tenant_ctx.membership_id),
                },
            )
            raise PermissionDeniedError(
                "Maker-checker violation: SOP revision creator or submitter cannot approve or reject their own revision.",
                error_code="SELF_APPROVAL_BLOCKED",
            )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db) as uow:
            rev = self._repo.get_revision_for_org(
                uow.session,
                tenant_ctx.organization_id,
                revision_id,
                sop_id=sop.id,
                for_update=True,
            )
            if rev is None:
                raise NotFoundError("SOP revision not found.")
            assert_transition(
                SOP_REVISION_TRANSITIONS,
                rev.revision_status,
                target_status,
                entity_name="SOPRevision",
            )
            self._bump_version(uow.session, rev, req.row_version)

            approval = ApprovalRecord(
                organization_id=tenant_ctx.organization_id,
                entity_type="SOPRevision",
                entity_id=rev.id,
                approval_stage="SOP_REVISION_APPROVAL",
                decision=approval_decision,
                approver_membership_id=tenant_ctx.membership_id,
                comments=req.comments,
                record_version=rev.row_version,
            )
            uow.session.add(approval)
            uow.session.flush()

            rev.revision_status = target_status
            rev.approval_status = target_status
            rev.approval_record_id = approval.id
            if target_status == "APPROVED":
                rev.approved_by_user_id = tenant_ctx.user_id
                rev.approved_by_membership_id = tenant_ctx.membership_id
                rev.approved_at = now
            rev.updated_by_user_id = tenant_ctx.user_id
            uow.session.flush()

            self._audit.record(
                uow.session,
                organization_id=tenant_ctx.organization_id,
                site_id=rev.site_id,
                actor_type=ActorType.USER,
                actor_id=tenant_ctx.user_id,
                action="SOP_REVISION_APPROVED"
                if target_status == "APPROVED"
                else "SOP_REVISION_REJECTED",
                entity_type="SOPRevision",
                entity_id=rev.id,
                reason=req.comments,
                metadata_json={
                    "sop_id": str(sop.id),
                    "revision_number": rev.revision_number,
                    "decision": target_status,
                    "approval_record_id": str(approval.id),
                },
            )
            self._outbox.enqueue(
                uow.session,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="SOPRevision",
                aggregate_id=rev.id,
                event_type="SOPRevisionApproved"
                if target_status == "APPROVED"
                else "SOPRevisionRejected",
                payload_json={
                    "sop_id": str(sop.id),
                    "sop_revision_id": str(rev.id),
                    "revision_number": rev.revision_number,
                    "revision_status": rev.revision_status,
                },
            )
            steps = self._repo.list_steps_for_revision(
                uow.session, tenant_ctx.organization_id, rev.id
            )
        return rev, steps

    def activate_revision(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        sop_id: uuid.UUID,
        revision_id: uuid.UUID,
        payload: Optional[SOPRevisionActivateRequest] = None,
    ) -> Tuple[SOPRevision, List[SOPStep]]:
        sop = self.get_sop(db, tenant_ctx, sop_id)
        req = payload or SOPRevisionActivateRequest()
        now = datetime.now(timezone.utc)

        with UnitOfWork(db) as uow:
            locked_sop = self._repo.get_sop_for_org(
                uow.session, tenant_ctx.organization_id, sop.id, for_update=True
            )
            assert locked_sop is not None
            rev = self._repo.get_revision_for_org(
                uow.session,
                tenant_ctx.organization_id,
                revision_id,
                sop_id=locked_sop.id,
                for_update=True,
            )
            if rev is None:
                raise NotFoundError("SOP revision not found.")
            self._assert_site_access(
                uow.session,
                tenant_ctx,
                rev.site_id or locked_sop.site_id,
                applicable_site_ids=locked_sop.applicable_site_ids,
                resource=f"sops/{sop_id}/revisions/{revision_id}/activate",
            )
            assert_transition(
                SOP_REVISION_TRANSITIONS,
                rev.revision_status,
                "EFFECTIVE",
                entity_name="SOPRevision",
            )

            eff_from = req.effective_from or rev.effective_from or now
            eff_to = (
                req.effective_to
                if req.effective_to is not None
                else rev.effective_to
            )
            eff_from_utc = _as_utc(eff_from)
            eff_to_utc = _as_utc(eff_to)
            assert eff_from_utc is not None
            if eff_to_utc is not None and eff_to_utc <= eff_from_utc:
                raise ValidationError(
                    "effective_to must be strictly after effective_from.",
                    error_code="INVALID_EFFECTIVE_WINDOW",
                )

            supersedes_id = req.supersedes_revision_id or rev.supersedes_revision_id
            existing_revs = self._repo.list_revisions_for_sop(
                uow.session, tenant_ctx.organization_id, locked_sop.id
            )
            rev_effective_site = rev.site_id or locked_sop.site_id

            revisions_to_supersede: List[SOPRevision] = []
            for other in existing_revs:
                if other.id == rev.id:
                    continue
                if other.revision_status not in ("EFFECTIVE", "SUPERSEDED"):
                    continue
                other_site = other.site_id or locked_sop.site_id
                if not self._revisions_share_site_scope(rev_effective_site, other_site):
                    continue
                old_from = _as_utc(other.effective_from)
                old_to = _as_utc(other.effective_to)
                if old_from is None:
                    continue

                if old_to is not None:
                    # Bounded window [old_from, old_to): any overlap [eff_from_utc, eff_to_utc) is rejected
                    overlaps_bounded = eff_from_utc < old_to and (
                        eff_to_utc is None or eff_to_utc > old_from
                    )
                    if overlaps_bounded:
                        raise ConflictError(
                            f"Effective window overlaps with revision '{other.revision_number}' "
                            f"[{old_from.isoformat()}, {old_to.isoformat()}).",
                            error_code="OVERLAPPING_SOP_REVISION_WINDOW",
                            details={
                                "conflicting_revision_id": str(other.id),
                                "conflicting_revision_number": other.revision_number,
                            },
                        )
                else:
                    # Open-ended window [old_from, None):
                    # Valid supersession requires eff_from_utc > old_from and other in EFFECTIVE state.
                    if eff_from_utc <= old_from:
                        if eff_to_utc is None or eff_to_utc > old_from:
                            raise ConflictError(
                                f"Effective start {eff_from_utc.isoformat()} overlaps with or precedes "
                                f"active revision '{other.revision_number}' ({old_from.isoformat()}).",
                                error_code="OVERLAPPING_SOP_REVISION_WINDOW",
                                details={
                                    "conflicting_revision_id": str(other.id),
                                    "conflicting_revision_number": other.revision_number,
                                },
                            )
                    else:
                        if other.revision_status == "EFFECTIVE":
                            revisions_to_supersede.append(other)

            if supersedes_id is not None:
                explicit_prior = self._repo.get_revision_for_org(
                    uow.session,
                    tenant_ctx.organization_id,
                    supersedes_id,
                    sop_id=locked_sop.id,
                    for_update=True,
                )
                if explicit_prior is None:
                    raise NotFoundError("Supersedes SOP revision not found on this SOP.")
                prior_from = _as_utc(explicit_prior.effective_from)
                prior_to = _as_utc(explicit_prior.effective_to)
                if prior_from is not None and eff_from_utc <= prior_from:
                    raise ConflictError(
                        "Cannot supersede a revision with an effective_from at or before the prior revision's effective_from.",
                        error_code="OVERLAPPING_SOP_REVISION_WINDOW",
                        details={"conflicting_revision_id": str(explicit_prior.id)},
                    )
                if (
                    prior_from is not None
                    and prior_to is not None
                    and eff_from_utc < prior_to
                    and (eff_to_utc is None or eff_to_utc > prior_from)
                ):
                    raise ConflictError(
                        "Effective window overlaps an already bounded prior revision window.",
                        error_code="OVERLAPPING_SOP_REVISION_WINDOW",
                        details={"conflicting_revision_id": str(explicit_prior.id)},
                    )
                if (
                    explicit_prior.revision_status == "EFFECTIVE"
                    and all(r.id != explicit_prior.id for r in revisions_to_supersede)
                ):
                    revisions_to_supersede.append(explicit_prior)

            for prior_rev in revisions_to_supersede:
                prior_rev.effective_to = eff_from
                prior_rev.revision_status = "SUPERSEDED"
                prior_rev.superseded_by_revision_id = rev.id
                prior_rev.row_version = int(prior_rev.row_version) + 1
                prior_rev.updated_by_user_id = tenant_ctx.user_id
                if supersedes_id is None:
                    supersedes_id = prior_rev.id

            self._bump_version(uow.session, rev, req.row_version)
            rev.effective_from = eff_from
            rev.effective_to = eff_to
            rev.revision_status = "EFFECTIVE"
            rev.activated_by_membership_id = tenant_ctx.membership_id
            rev.activated_at = now
            rev.supersedes_revision_id = supersedes_id
            rev.updated_by_user_id = tenant_ctx.user_id

            locked_sop.current_revision_id = rev.id
            if locked_sop.sop_status == "DRAFT":
                locked_sop.sop_status = "ACTIVE"
            locked_sop.row_version = int(locked_sop.row_version) + 1
            locked_sop.updated_by_user_id = tenant_ctx.user_id
            uow.session.flush()

            self._audit.record(
                uow.session,
                organization_id=tenant_ctx.organization_id,
                site_id=rev.site_id,
                actor_type=ActorType.USER,
                actor_id=tenant_ctx.user_id,
                action="SOP_REVISION_ACTIVATED",
                entity_type="SOPRevision",
                entity_id=rev.id,
                reason=req.comments,
                metadata_json={
                    "sop_id": str(locked_sop.id),
                    "sop_code": locked_sop.sop_code,
                    "revision_number": rev.revision_number,
                    "effective_from": eff_from_utc.isoformat(),
                    "effective_to": eff_to_utc.isoformat() if eff_to_utc else None,
                    "superseded_revision_ids": [str(r.id) for r in revisions_to_supersede],
                },
            )
            self._outbox.enqueue(
                uow.session,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="SOPRevision",
                aggregate_id=rev.id,
                event_type="SOPRevisionActivated",
                payload_json={
                    "sop_id": str(locked_sop.id),
                    "sop_code": locked_sop.sop_code,
                    "sop_revision_id": str(rev.id),
                    "revision_number": rev.revision_number,
                    "effective_from": eff_from_utc.isoformat(),
                    "effective_to": eff_to_utc.isoformat() if eff_to_utc else None,
                },
            )
            steps = self._repo.list_steps_for_revision(
                uow.session, tenant_ctx.organization_id, rev.id
            )
        return rev, steps

    def resolve_sop(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        *,
        sop_code: str,
        at: datetime,
        site_id: Optional[uuid.UUID] = None,
    ) -> SOPResolutionResult:
        if site_id is not None:
            self._verify_site_belongs_to_org_and_scope(db, tenant_ctx, site_id)
        result = SOPResolver.resolve(
            sop_code=sop_code,
            site_id=site_id,
            at=at,
            db=db,
            organization_id=tenant_ctx.organization_id,
        )
        if result.sop is not None:
            self._assert_site_access(
                db,
                tenant_ctx,
                result.sop.site_id,
                applicable_site_ids=result.sop.applicable_site_ids,
                resource=f"sops/{result.sop.id}/resolve",
            )
        return result
