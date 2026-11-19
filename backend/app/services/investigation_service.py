import difflib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.database import (
    UnitOfWork,
    get_session_factory,
    next_tenant_sequence,
    verify_and_increment_version,
)
from app.core.deps import TenantContext
from app.core.exceptions import (
    ConflictError,
    ExternalProviderError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.domain.enums import (
    ActorType,
    BatchMatchStatus,
    BatchResolutionOutcome,
    ComplaintStatus,
    EvidenceSourceType,
    HypothesisStatus,
    InvestigationRunStatus,
    InvestigationStatus,
    InvestigationStepStatus,
    InvestigationTriggerType,
    MembershipStatus,
    SecurityEventOutcome,
    SecurityEventSeverity,
)
from app.domain.rules_engine import RULE_ENGINE_VERSION
from app.domain.state_machines import assert_transition
from app.integrations.llm_provider import LLMProvider
from app.models.domain.actors import Complainant
from app.models.domain.complaints import (
    Complaint,
    ComplaintAttachment,
    ComplaintBatchLink,
    ComplaintEvent,
)
from app.models.domain.documents import Document, DocumentVersion, ExtractedContent
from app.models.domain.governance import ApprovalRecord, OutboxEvent
from app.models.domain.identity import OrganizationMembership
from app.models.domain.investigations import (
    EvidenceReference,
    Investigation,
    InvestigationRun,
    InvestigationStep,
    RootCauseHypothesis,
    SOPComplianceResult,
)
from app.models.domain.manufacturing_execution import (
    BatchExecutionRecord,
    BatchExecutionStep,
    ProcessParameterObservation,
)
from app.models.domain.products_materials import Product
from app.models.domain.qc_equipment import (
    CalibrationRecord,
    Equipment,
    QCSample,
    QCTestResult,
)
from app.models.domain.qms_loop import Deviation
from app.models.domain.tenancy import Organization, Site
from app.models.domain.traceability import (
    Batch,
    BatchDisposition,
    BatchMaterialConsumption,
    BatchMovementEvent,
    ShipmentLine,
)
from app.models.schemas.investigations import (
    HypothesisDecisionRequest,
    InvestigationCreateRequest,
    InvestigationReviewRequest,
    InvestigationRunTriggerRequest,
    InvestigationRunTriggerResponse,
    RCASynthesisFindingItem,
    RCASynthesisHypothesisItem,
    RCASynthesisLLMDispatchOutput,
    RCASynthesisLLMOutput,
)
from app.repositories.governance_repo import OutboxRepository
from app.repositories.investigation_repo import InvestigationRepository
from app.services.audit_service import AuditService
from app.services.rca_analysis_service import RCAAnalysisService
from app.services.sop_compliance_service import SOPComplianceService
from app.workflows.investigation_graph import InvestigationGraphState, build_investigation_graph
from app.workflows.prompts import INVESTIGATION_PROMPT_VERSION, RCA_SYNTHESIS_SYSTEM_PROMPT

INVESTIGATION_TRANSITIONS: Dict[str, Set[str]] = {
    InvestigationStatus.QUEUED.value: {
        InvestigationStatus.PROCESSING.value,
        InvestigationStatus.WAITING_FOR_INFORMATION.value,
        InvestigationStatus.FAILED.value,
        InvestigationStatus.UNDER_QA_REVIEW.value,
    },
    InvestigationStatus.PROCESSING.value: {
        InvestigationStatus.WAITING_FOR_INFORMATION.value,
        InvestigationStatus.COMPLETED.value,
        InvestigationStatus.FAILED.value,
    },
    InvestigationStatus.WAITING_FOR_INFORMATION.value: {
        InvestigationStatus.QUEUED.value,
        InvestigationStatus.PROCESSING.value,
        InvestigationStatus.COMPLETED.value,
        InvestigationStatus.UNDER_QA_REVIEW.value,
        InvestigationStatus.FAILED.value,
    },
    InvestigationStatus.FAILED.value: {
        InvestigationStatus.QUEUED.value,
        InvestigationStatus.PROCESSING.value,
    },
    InvestigationStatus.COMPLETED.value: {
        InvestigationStatus.QUEUED.value,
        InvestigationStatus.UNDER_QA_REVIEW.value,
        InvestigationStatus.APPROVED.value,
        InvestigationStatus.WAITING_FOR_INFORMATION.value,
    },
    InvestigationStatus.UNDER_QA_REVIEW.value: {
        InvestigationStatus.APPROVED.value,
        InvestigationStatus.WAITING_FOR_INFORMATION.value,
        InvestigationStatus.QUEUED.value,
    },
    InvestigationStatus.APPROVED.value: {
        InvestigationStatus.CLOSED.value,
        InvestigationStatus.UNDER_QA_REVIEW.value,
    },
    InvestigationStatus.CLOSED.value: set(),
}

HYPOTHESIS_TRANSITIONS: Dict[str, Set[str]] = {
    HypothesisStatus.PROPOSED.value: {
        HypothesisStatus.UNDER_REVIEW.value,
        HypothesisStatus.NEEDS_MORE_EVIDENCE.value,
        HypothesisStatus.ACCEPTED.value,
        HypothesisStatus.REJECTED.value,
        HypothesisStatus.INCONCLUSIVE.value,
    },
    HypothesisStatus.UNDER_REVIEW.value: {
        HypothesisStatus.NEEDS_MORE_EVIDENCE.value,
        HypothesisStatus.ACCEPTED.value,
        HypothesisStatus.REJECTED.value,
        HypothesisStatus.INCONCLUSIVE.value,
    },
    HypothesisStatus.NEEDS_MORE_EVIDENCE.value: {
        HypothesisStatus.UNDER_REVIEW.value,
        HypothesisStatus.ACCEPTED.value,
        HypothesisStatus.REJECTED.value,
        HypothesisStatus.INCONCLUSIVE.value,
    },
    HypothesisStatus.ACCEPTED.value: set(),
    HypothesisStatus.REJECTED.value: set(),
    HypothesisStatus.INCONCLUSIVE.value: {HypothesisStatus.UNDER_REVIEW.value},
}


def _org_inv_prefix(org: Optional[Organization]) -> str:
    if org is None:
        return "ORG-INV"
    slug = re.sub(r"[^A-Z0-9]", "", (org.trade_name or org.legal_name or "ORG").upper())[:5] or "ORG"
    return f"{slug}-INV"


def _normalize_batch_code(raw: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (raw or "").upper())


def _build_worker_ctx(organization_id: uuid.UUID) -> TenantContext:
    from app.domain.rbac_catalog import WORKER_PERMISSIONS

    return TenantContext(
        user_id=None,
        organization_id=organization_id,
        membership_id=None,
        allowed_site_ids=frozenset(),
        is_org_wide=True,
        permission_codes=WORKER_PERMISSIONS,
        role_codes=frozenset(),
        actor_type=ActorType.SYSTEM_WORKER.value,
    )


class BatchResolver:
    """
    Deterministic Complaint Batch Resolver (`D2`).
    Matches `reported_batch_number` within organization/product scope in priority order:
    1. Exact match -> single match auto-links cleanly (`MATCHED`)
    2. Normalized match (ignoring whitespace/punctuation) -> single match auto-links cleanly (`MATCHED`)
    3. Fuzzy match (`SequenceMatcher >= 0.72`) -> single or multiple ambiguous candidates create
       `ComplaintBatchLink(PROPOSED)` rows and pause the InvestigationRun (`WAITING_FOR_HUMAN`).
    """

    FUZZY_THRESHOLD = 0.72

    def resolve_for_complaint_in_tx(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        complaint: Complaint,
        user_id: Optional[uuid.UUID] = None,
    ) -> Tuple[bool, Optional[uuid.UUID], str, Optional[str], List[uuid.UUID]]:
        """
        Returns `(resolved_without_pause, batch_id, outcome, waiting_reason, link_ids)`.
        """
        existing_links = list(
            db.scalars(
                select(ComplaintBatchLink)
                .where(
                    ComplaintBatchLink.organization_id == organization_id,
                    ComplaintBatchLink.complaint_id == complaint.id,
                )
                .order_by(ComplaintBatchLink.created_at.desc())
            ).all()
        )
        for lnk in existing_links:
            if lnk.match_status in {BatchMatchStatus.VERIFIED.value, BatchMatchStatus.MANUAL_LINK.value}:
                return True, lnk.batch_id, lnk.resolution_outcome, None, [lnk.id]

        reported = (complaint.reported_batch_number or "").strip()
        if not reported:
            lnk = ComplaintBatchLink(
                organization_id=organization_id,
                complaint_id=complaint.id,
                batch_id=None,
                reported_batch_number=None,
                resolution_outcome=BatchResolutionOutcome.INSUFFICIENT_INFO.value,
                match_method="NONE",
                match_status=BatchMatchStatus.PROPOSED.value,
                match_evidence_json={"reason": "No reported_batch_number on complaint."},
                created_by_user_id=user_id,
            )
            db.add(lnk)
            db.flush()
            return True, None, BatchResolutionOutcome.INSUFFICIENT_INFO.value, None, [lnk.id]

        batch_stmt = select(Batch).where(Batch.organization_id == organization_id)
        if complaint.product_id is not None:
            batch_stmt = batch_stmt.where(Batch.product_id == complaint.product_id)
        candidates = list(db.scalars(batch_stmt.order_by(Batch.created_at.asc())).all())

        # Optional product-name narrowing if product_id is not set but reported_product_name matches uniquely
        if complaint.product_id is None and complaint.reported_product_name and candidates:
            needle = complaint.reported_product_name.strip().lower()
            matching_prod_ids = set(
                db.scalars(
                    select(Product.id).where(
                        Product.organization_id == organization_id,
                        (Product.generic_name.ilike(f"%{needle}%"))
                        | (Product.brand_name.ilike(f"%{needle}%"))
                        | (Product.product_code.ilike(f"%{needle}%")),
                    )
                ).all()
            )
            if matching_prod_ids:
                narrowed = [b for b in candidates if b.product_id in matching_prod_ids]
                if narrowed:
                    candidates = narrowed

        exact_matches = [
            b for b in candidates if b.batch_number.strip().lower() == reported.lower()
        ]
        if len(exact_matches) == 1:
            chosen = exact_matches[0]
            lnk = ComplaintBatchLink(
                organization_id=organization_id,
                complaint_id=complaint.id,
                batch_id=chosen.id,
                reported_batch_number=reported,
                resolution_outcome=BatchResolutionOutcome.MATCHED.value,
                match_method="EXACT",
                match_score=1.0,
                match_status=BatchMatchStatus.VERIFIED.value,
                verified_at=datetime.now(timezone.utc),
                match_evidence_json={"matched_batch_number": chosen.batch_number},
                created_by_user_id=user_id,
            )
            db.add(lnk)
            db.flush()
            return True, chosen.id, BatchResolutionOutcome.MATCHED.value, None, [lnk.id]

        norm_reported = _normalize_batch_code(reported)
        norm_matches = [
            b for b in candidates if _normalize_batch_code(b.batch_number) == norm_reported
        ]
        if len(exact_matches) == 0 and len(norm_matches) == 1:
            chosen = norm_matches[0]
            lnk = ComplaintBatchLink(
                organization_id=organization_id,
                complaint_id=complaint.id,
                batch_id=chosen.id,
                reported_batch_number=reported,
                resolution_outcome=BatchResolutionOutcome.MATCHED.value,
                match_method="NORMALIZED",
                match_score=0.95,
                match_status=BatchMatchStatus.VERIFIED.value,
                verified_at=datetime.now(timezone.utc),
                match_evidence_json={"matched_batch_number": chosen.batch_number, "normalized": norm_reported},
                created_by_user_id=user_id,
            )
            db.add(lnk)
            db.flush()
            return True, chosen.id, BatchResolutionOutcome.MATCHED.value, None, [lnk.id]

        # Multiple exact/normalized OR fuzzy candidates -> require human verification & pause
        ambiguous_pool: List[Tuple[Batch, str, float]] = []
        if len(exact_matches) > 1:
            ambiguous_pool = [(b, "EXACT", 1.0) for b in exact_matches]
        elif len(norm_matches) > 1:
            ambiguous_pool = [(b, "NORMALIZED", 0.95) for b in norm_matches]
        else:
            for b in candidates:
                ratio = difflib.SequenceMatcher(
                    None, norm_reported, _normalize_batch_code(b.batch_number)
                ).ratio()
                if ratio >= self.FUZZY_THRESHOLD:
                    ambiguous_pool.append((b, "FUZZY", round(ratio, 4)))
            ambiguous_pool.sort(key=lambda t: t[2], reverse=True)

        if ambiguous_pool:
            outcome = (
                BatchResolutionOutcome.MULTIPLE_CANDIDATES.value
                if len(ambiguous_pool) > 1
                else BatchResolutionOutcome.HUMAN_VERIFICATION_REQUIRED.value
            )
            wait_reason = (
                "AMBIGUOUS_BATCH_CANDIDATES"
                if len(ambiguous_pool) > 1
                else "BATCH_HUMAN_VERIFICATION_REQUIRED"
            )
            created_ids: List[uuid.UUID] = []
            for b, method, score in ambiguous_pool[:10]:
                lnk = ComplaintBatchLink(
                    organization_id=organization_id,
                    complaint_id=complaint.id,
                    batch_id=b.id,
                    reported_batch_number=reported,
                    resolution_outcome=outcome,
                    match_method=method,
                    match_score=score,
                    match_status=BatchMatchStatus.PROPOSED.value,
                    match_evidence_json={
                        "candidate_batch_number": b.batch_number,
                        "reported_batch_number": reported,
                        "method": method,
                        "score": score,
                    },
                    created_by_user_id=user_id,
                )
                db.add(lnk)
                db.flush()
                created_ids.append(lnk.id)
            return False, None, outcome, wait_reason, created_ids

        # Not found
        lnk = ComplaintBatchLink(
            organization_id=organization_id,
            complaint_id=complaint.id,
            batch_id=None,
            reported_batch_number=reported,
            resolution_outcome=BatchResolutionOutcome.NOT_FOUND.value,
            match_method="NONE",
            match_status=BatchMatchStatus.PROPOSED.value,
            match_evidence_json={"reported_batch_number": reported},
            created_by_user_id=user_id,
        )
        db.add(lnk)
        db.flush()
        return True, None, BatchResolutionOutcome.NOT_FOUND.value, None, [lnk.id]


class InvestigationService:
    """
    Durable AI Investigation Orchestration, Evidence Collection, Pause/Resume, and QA Review (`D1`-`D5`, `D7`).
    """

    def __init__(
        self,
        repo: Optional[InvestigationRepository] = None,
        compliance_service: Optional[SOPComplianceService] = None,
        rca_service: Optional[RCAAnalysisService] = None,
        batch_resolver: Optional[BatchResolver] = None,
        audit_service: Optional[AuditService] = None,
        outbox_repo: Optional[OutboxRepository] = None,
    ) -> None:
        self._repo = repo or InvestigationRepository()
        self._compliance = compliance_service or SOPComplianceService(repo=self._repo)
        self._rca = rca_service or RCAAnalysisService(repo=self._repo)
        self._batch_resolver = batch_resolver or BatchResolver()
        self._audit = audit_service or AuditService()
        self._outbox = outbox_repo or OutboxRepository()

    def _assert_site_access(
        self,
        tenant_ctx: TenantContext,
        site_id: Optional[uuid.UUID],
        *,
        source_ip: Optional[str] = None,
        target_resource: str = "/api/v1/investigations",
    ) -> None:
        if site_id is not None and not tenant_ctx.is_org_wide and site_id not in tenant_ctx.allowed_site_ids:
            self._audit.record_security_event(
                event_type="SITE_SCOPE_VIOLATION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.WARNING.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                source_ip=source_ip,
                target_resource=target_resource,
                details_json={"site_id": str(site_id)},
            )
            raise PermissionDeniedError(
                "Not authorized to access investigations for this site.",
                error_code="SITE_SCOPE_DENIED",
            )

    def create_investigation(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        payload: InvestigationCreateRequest,
        source_ip: Optional[str] = None,
    ) -> Investigation:
        org_id = tenant_ctx.organization_id
        resolved_site_id = payload.site_id
        resolved_product_id = payload.product_id
        resolved_batch_id = payload.batch_id

        complaint: Optional[Complaint] = None
        if payload.complaint_id is not None:
            complaint = db.scalar(
                select(Complaint).where(
                    Complaint.organization_id == org_id,
                    Complaint.id == payload.complaint_id,
                )
            )
            if complaint is None:
                raise NotFoundError("Complaint not found in this organization.")
            if resolved_site_id is None:
                resolved_site_id = complaint.site_id
            if resolved_product_id is None:
                resolved_product_id = complaint.product_id

        deviation: Optional[Deviation] = None
        if payload.deviation_id is not None:
            deviation = db.scalar(
                select(Deviation).where(
                    Deviation.organization_id == org_id,
                    Deviation.id == payload.deviation_id,
                )
            )
            if deviation is None:
                raise NotFoundError("Deviation not found in this organization.")
            if resolved_site_id is None:
                resolved_site_id = deviation.site_id
            if resolved_batch_id is None:
                resolved_batch_id = deviation.batch_id

        if resolved_batch_id is not None:
            batch = db.scalar(
                select(Batch).where(Batch.organization_id == org_id, Batch.id == resolved_batch_id)
            )
            if batch is None:
                raise NotFoundError("Batch not found in this organization.")
            if resolved_site_id is None:
                resolved_site_id = batch.site_id
            if resolved_product_id is None:
                resolved_product_id = batch.product_id

        if resolved_product_id is not None:
            prod = db.scalar(
                select(Product).where(Product.organization_id == org_id, Product.id == resolved_product_id)
            )
            if prod is None:
                raise NotFoundError("Product not found in this organization.")

        if resolved_site_id is not None:
            site = db.scalar(
                select(Site).where(Site.organization_id == org_id, Site.id == resolved_site_id)
            )
            if site is None:
                raise NotFoundError("Site not found in this organization.")
        self._assert_site_access(
            tenant_ctx,
            resolved_site_id,
            source_ip=source_ip,
            target_resource="/api/v1/investigations",
        )

        if payload.owner_membership_id is not None:
            owner = db.scalar(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == org_id,
                    OrganizationMembership.id == payload.owner_membership_id,
                    OrganizationMembership.membership_status == MembershipStatus.ACTIVE.value,
                )
            )
            if owner is None:
                raise NotFoundError("Owner membership not found or not ACTIVE.")

        if payload.trigger_type is not None:
            trigger = payload.trigger_type.value
        elif payload.complaint_id is not None:
            trigger = InvestigationTriggerType.COMPLAINT.value
        elif payload.deviation_id is not None:
            trigger = InvestigationTriggerType.DEVIATION.value
        else:
            trigger = InvestigationTriggerType.MANUAL.value

        title = (
            payload.title.strip()
            if payload.title
            else (
                f"Investigation for {complaint.complaint_number}"
                if complaint
                else (f"Investigation for {deviation.deviation_number}" if deviation else "Quality Investigation")
            )
        )

        org = db.get(Organization, org_id)
        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            inv_num = next_tenant_sequence(
                db, org_id, "INVESTIGATION", period_year=now.year, prefix=_org_inv_prefix(org)
            )
            inv = Investigation(
                organization_id=org_id,
                complaint_id=payload.complaint_id,
                deviation_id=payload.deviation_id,
                batch_id=resolved_batch_id,
                product_id=resolved_product_id,
                site_id=resolved_site_id,
                investigation_number=inv_num,
                trigger_type=trigger,
                title=title,
                scope_summary=payload.scope_summary,
                status=InvestigationStatus.QUEUED.value,
                owner_membership_id=payload.owner_membership_id or tenant_ctx.membership_id,
                due_at=payload.due_at,
                opened_at=now,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(inv)
            db.flush()

            if deviation is not None and deviation.investigation_id is None:
                deviation.investigation_id = inv.id

            if complaint is not None and complaint.status in {
                ComplaintStatus.RECEIVED.value,
                ComplaintStatus.TRIAGED.value,
                ComplaintStatus.WAITING_INFO.value,
            }:
                prev_c_status = complaint.status
                complaint.status = ComplaintStatus.INVESTIGATION_OPEN.value
                complaint.row_version += 1
                db.add(
                    ComplaintEvent(
                        organization_id=org_id,
                        complaint_id=complaint.id,
                        event_type="INVESTIGATION_OPENED",
                        from_status=prev_c_status,
                        to_status=complaint.status,
                        actor_user_id=tenant_ctx.user_id,
                        actor_membership_id=tenant_ctx.membership_id,
                        summary=f"Opened investigation {inv.investigation_number}.",
                    )
                )

            self._audit.record(
                db,
                organization_id=org_id,
                site_id=inv.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="INVESTIGATION_OPENED",
                entity_type="investigations",
                entity_id=inv.id,
                source_ip=source_ip,
                after_state={
                    "investigation_number": inv.investigation_number,
                    "trigger_type": inv.trigger_type,
                    "complaint_id": str(inv.complaint_id) if inv.complaint_id else None,
                    "deviation_id": str(inv.deviation_id) if inv.deviation_id else None,
                },
            )
            self._outbox.enqueue(
                db,
                organization_id=org_id,
                aggregate_type="investigation",
                aggregate_id=inv.id,
                event_type="InvestigationOpened",
                payload_json={
                    "investigation_id": str(inv.id),
                    "organization_id": str(org_id),
                    "investigation_number": inv.investigation_number,
                },
            )
        db.refresh(inv)
        return inv

    def list_investigations(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        complaint_id: Optional[uuid.UUID] = None,
        deviation_id: Optional[uuid.UUID] = None,
        batch_id: Optional[uuid.UUID] = None,
        status: Optional[str] = None,
        site_id: Optional[uuid.UUID] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Investigation], int]:
        return self._repo.list_investigations_for_tenant(
            db,
            tenant_ctx,
            complaint_id=complaint_id,
            deviation_id=deviation_id,
            batch_id=batch_id,
            status=status,
            site_id=site_id,
            page=page,
            page_size=page_size,
        )

    def get_investigation(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        investigation_id: uuid.UUID,
        with_children: bool = True,
        source_ip: Optional[str] = None,
    ) -> Investigation:
        inv = self._repo.get_investigation_for_org(
            db,
            tenant_ctx.organization_id,
            investigation_id,
            with_children=with_children,
        )
        if inv is None:
            raise NotFoundError("Investigation not found.")
        self._assert_site_access(
            tenant_ctx,
            inv.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/investigations/{investigation_id}",
        )
        return inv

    def trigger_run(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        investigation_id: uuid.UUID,
        payload: InvestigationRunTriggerRequest,
        idempotency_key: Optional[str] = None,
        source_ip: Optional[str] = None,
    ) -> InvestigationRunTriggerResponse:
        inv = self.get_investigation(
            db,
            tenant_ctx=tenant_ctx,
            investigation_id=investigation_id,
            with_children=False,
            source_ip=source_ip,
        )
        if inv.status in {InvestigationStatus.CLOSED.value, InvestigationStatus.APPROVED.value}:
            raise ConflictError(
                f"Cannot trigger new run on investigation in '{inv.status}' status.",
                error_code="INVESTIGATION_IMMUTABLE_STATUS",
            )

        effective_key = (idempotency_key or payload.idempotency_key or "").strip() or None
        if effective_key is not None:
            existing_run = self._repo.get_run_by_idempotency(
                db, tenant_ctx.organization_id, effective_key
            )
            if existing_run is not None:
                if existing_run.investigation_id != inv.id:
                    raise ConflictError(
                        "Idempotency-Key already used for another investigation run.",
                        error_code="IDEMPOTENCY_CONFLICT",
                    )
                return InvestigationRunTriggerResponse(
                    run_id=existing_run.id,
                    investigation_id=existing_run.investigation_id,
                    run_number=existing_run.run_number,
                    status=existing_run.status,
                    idempotent_replay=True,
                )

        settings = get_settings()
        with UnitOfWork(db):
            locked_inv = self._repo.get_investigation_for_org(
                db, tenant_ctx.organization_id, inv.id, for_update=True
            )
            if locked_inv is None:
                raise NotFoundError("Investigation not found.")
            run_num = self._repo.get_next_run_number(db, tenant_ctx.organization_id, locked_inv.id)
            run = InvestigationRun(
                organization_id=tenant_ctx.organization_id,
                investigation_id=locked_inv.id,
                run_number=run_num,
                workflow_version=payload.workflow_version,
                model_name=settings.LLM_MODEL_NAME,
                prompt_version=INVESTIGATION_PROMPT_VERSION,
                rule_engine_version=RULE_ENGINE_VERSION,
                status=InvestigationRunStatus.QUEUED.value,
                resume_from_step_seq=1,
                attempt_count=0,
                max_attempts=payload.max_attempts,
                idempotency_key=effective_key,
                triggered_by_user_id=tenant_ctx.user_id,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(run)
            db.flush()

            locked_inv.status = InvestigationStatus.QUEUED.value
            locked_inv.row_version += 1
            locked_inv.updated_by_user_id = tenant_ctx.user_id

            ev = self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="investigation_run",
                aggregate_id=run.id,
                event_type="InvestigationRunRequested",
                payload_json={
                    "investigation_id": str(locked_inv.id),
                    "run_id": str(run.id),
                    "organization_id": str(tenant_ctx.organization_id),
                    "run_number": run.run_number,
                },
            )
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=locked_inv.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="INVESTIGATION_RUN_QUEUED",
                entity_type="investigation_runs",
                entity_id=run.id,
                source_ip=source_ip,
                after_state={"run_number": run.run_number, "status": run.status},
            )
        return InvestigationRunTriggerResponse(
            run_id=run.id,
            investigation_id=inv.id,
            run_number=run.run_number,
            status=run.status,
            idempotent_replay=False,
            outbox_event_id=ev.id,
        )

    def get_run(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        run_id: uuid.UUID,
        investigation_id: Optional[uuid.UUID] = None,
        source_ip: Optional[str] = None,
    ) -> InvestigationRun:
        run = self._repo.get_run_for_org(
            db,
            tenant_ctx.organization_id,
            run_id,
            investigation_id=investigation_id,
            with_steps=True,
        )
        if run is None:
            raise NotFoundError("Investigation run not found.")
        inv = self._repo.get_investigation_for_org(
            db, tenant_ctx.organization_id, run.investigation_id
        )
        if inv is None:
            raise NotFoundError("Investigation not found.")
        self._assert_site_access(
            tenant_ctx,
            inv.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/investigations/runs/{run_id}",
        )
        return run

    def retry_run(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        investigation_id: uuid.UUID,
        run_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> InvestigationRunTriggerResponse:
        inv = self.get_investigation(
            db, tenant_ctx=tenant_ctx, investigation_id=investigation_id, with_children=False
        )
        run = self._repo.get_run_for_org(
            db, tenant_ctx.organization_id, run_id, investigation_id=inv.id
        )
        if run is None:
            raise NotFoundError("Investigation run not found.")
        if run.status not in {
            InvestigationRunStatus.FAILED.value,
            InvestigationRunStatus.RETRYABLE.value,
            InvestigationRunStatus.DEAD_LETTER.value,
            InvestigationRunStatus.WAITING_FOR_HUMAN.value,
        }:
            raise ConflictError(
                f"Cannot retry investigation run in '{run.status}' status.",
                error_code="INVALID_RUN_RETRY_STATUS",
            )

        with UnitOfWork(db):
            locked_run = self._repo.get_run_for_org(
                db, tenant_ctx.organization_id, run.id, for_update=True
            )
            assert locked_run is not None
            locked_inv = self._repo.get_investigation_for_org(
                db, tenant_ctx.organization_id, inv.id, for_update=True
            )
            assert locked_inv is not None

            locked_run.status = InvestigationRunStatus.QUEUED.value
            locked_run.last_error = None
            locked_run.waiting_reason = None
            locked_run.finished_at = None
            locked_run.row_version += 1
            if locked_run.attempt_count >= locked_run.max_attempts:
                locked_run.max_attempts = locked_run.attempt_count + 1

            locked_inv.status = InvestigationStatus.QUEUED.value
            locked_inv.row_version += 1

            ev = self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="investigation_run",
                aggregate_id=locked_run.id,
                event_type="InvestigationRunRequested",
                payload_json={
                    "investigation_id": str(locked_inv.id),
                    "run_id": str(locked_run.id),
                    "organization_id": str(tenant_ctx.organization_id),
                    "retry": True,
                },
            )
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=locked_inv.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="INVESTIGATION_RUN_RETRIED",
                entity_type="investigation_runs",
                entity_id=locked_run.id,
                source_ip=source_ip,
            )
        return InvestigationRunTriggerResponse(
            run_id=locked_run.id,
            investigation_id=inv.id,
            run_number=locked_run.run_number,
            status=locked_run.status,
            idempotent_replay=False,
            outbox_event_id=ev.id,
        )

    def resume_waiting_runs_for_complaint_in_tx(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        complaint_id: uuid.UUID,
        resolved_batch_id: Optional[uuid.UUID],
        resolution_outcome: str,
        actor_user_id: Optional[uuid.UUID],
    ) -> List[uuid.UUID]:
        """
        Called inside `ComplaintService.resolve_batch_candidates` UoW when a human resolves
        ambiguous batch candidates (`D2`). Transitions waiting InvestigationRuns to `QUEUED`
        with `resume_from_step_seq=2` and enqueues `InvestigationRunResumeRequested`.
        """
        waiting_runs = self._repo.list_waiting_runs_for_complaint(db, organization_id, complaint_id)
        resumed_ids: List[uuid.UUID] = []
        now = datetime.now(timezone.utc)

        for run in waiting_runs:
            inv = self._repo.get_investigation_for_org(db, organization_id, run.investigation_id)
            if inv is None:
                continue
            if resolved_batch_id is not None and inv.batch_id is None:
                inv.batch_id = resolved_batch_id
                batch = db.scalar(
                    select(Batch).where(Batch.organization_id == organization_id, Batch.id == resolved_batch_id)
                )
                if batch is not None:
                    if inv.product_id is None:
                        inv.product_id = batch.product_id
                    if inv.site_id is None:
                        inv.site_id = batch.site_id
            inv.status = InvestigationStatus.QUEUED.value
            inv.row_version += 1

            step1 = self._repo.get_step_by_run_seq(db, organization_id, run.id, 1)
            if step1 is not None and step1.status == InvestigationStepStatus.WAITING.value:
                step1.status = InvestigationStepStatus.COMPLETED.value
                step1.finished_at = now
                step1.output_refs_json = {
                    **(step1.output_refs_json or {}),
                    "human_resolved": True,
                    "resolved_batch_id": str(resolved_batch_id) if resolved_batch_id else None,
                    "resolution_outcome": resolution_outcome,
                }

            run.status = InvestigationRunStatus.QUEUED.value
            run.waiting_reason = None
            run.resume_from_step_seq = 2
            run.checkpoint_state_json = {
                **(run.checkpoint_state_json or {}),
                "batch_id": str(resolved_batch_id) if resolved_batch_id else None,
                "resolution_outcome": resolution_outcome,
                "resumed_by_user_id": str(actor_user_id) if actor_user_id else None,
            }
            run.row_version += 1

            self._outbox.enqueue(
                db,
                organization_id=organization_id,
                aggregate_type="investigation_run",
                aggregate_id=run.id,
                event_type="InvestigationRunResumeRequested",
                payload_json={
                    "investigation_id": str(inv.id),
                    "run_id": str(run.id),
                    "organization_id": str(organization_id),
                    "resolved_batch_id": str(resolved_batch_id) if resolved_batch_id else None,
                },
            )
            resumed_ids.append(run.id)

        db.flush()
        return resumed_ids

    def _upsert_step_in_tx(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        investigation_id: uuid.UUID,
        run_id: uuid.UUID,
        step_seq: int,
        node_name: str,
        status: str,
        input_refs: Optional[Dict[str, Any]] = None,
        output_refs: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> InvestigationStep:
        now = datetime.now(timezone.utc)
        existing = self._repo.get_step_by_run_seq(db, organization_id, run_id, step_seq)
        if existing is None:
            step = InvestigationStep(
                organization_id=organization_id,
                investigation_id=investigation_id,
                investigation_run_id=run_id,
                step_seq=step_seq,
                node_name=node_name,
                status=status,
                input_refs_json=input_refs,
                output_refs_json=output_refs,
                error_message=error_message,
                started_at=now,
                finished_at=now if status in {"COMPLETED", "FAILED", "SKIPPED"} else None,
            )
            db.add(step)
            db.flush()
            return step
        existing.node_name = node_name
        existing.status = status
        if input_refs is not None:
            existing.input_refs_json = input_refs
        if output_refs is not None:
            existing.output_refs_json = output_refs
        existing.error_message = error_message
        if status in {"COMPLETED", "FAILED", "SKIPPED"}:
            existing.finished_at = now
        existing.row_version += 1
        db.flush()
        return existing

    def _collect_evidence_in_tx(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        investigation: Investigation,
        run_id: uuid.UUID,
    ) -> List[EvidenceReference]:
        existing = self._repo.list_evidence_for_investigation(db, organization_id, investigation.id)
        by_source: Dict[Tuple[str, uuid.UUID, str], EvidenceReference] = {
            (e.source_table, e.source_record_id, e.field_path or ""): e for e in existing
        }
        collected: List[EvidenceReference] = list(existing)

        def _add_ev(
            *,
            source_type: str,
            source_table: str,
            source_record_id: uuid.UUID,
            source_row_version: Optional[int] = None,
            document_id: Optional[uuid.UUID] = None,
            document_version_id: Optional[uuid.UUID] = None,
            page_number: Optional[int] = None,
            section_ref: Optional[str] = None,
            field_path: Optional[str] = None,
            quoted_value: Optional[str] = None,
            meta: Optional[Dict[str, Any]] = None,
        ) -> EvidenceReference:
            key = (source_table, source_record_id, field_path or "")
            if key in by_source:
                return by_source[key]
            checksum: Optional[str] = None
            resolved_doc_id = document_id
            if document_version_id is not None:
                ver = db.get(DocumentVersion, document_version_id)
                if ver is not None:
                    checksum = ver.checksum_sha256
                    if resolved_doc_id is None:
                        parent = db.scalar(
                            select(Document).where(
                                Document.organization_id == organization_id,
                                Document.id == ver.document_id,
                            )
                        )
                        if parent is not None:
                            resolved_doc_id = parent.id
            elif resolved_doc_id is not None:
                latest_ver = db.scalar(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == resolved_doc_id)
                    .order_by(DocumentVersion.version_number.desc())
                )
                if latest_ver is not None:
                    document_version_id = latest_ver.id
                    checksum = latest_ver.checksum_sha256

            ev = EvidenceReference(
                organization_id=organization_id,
                investigation_id=investigation.id,
                investigation_run_id=run_id,
                source_type=source_type,
                source_table=source_table,
                source_record_id=source_record_id,
                source_row_version=source_row_version,
                document_id=resolved_doc_id,
                document_version_id=document_version_id,
                document_checksum_sha256=checksum,
                page_number=page_number,
                section_ref=section_ref,
                field_path=field_path,
                quoted_value=quoted_value,
                provenance_metadata_json=meta,
                collected_by=ActorType.SYSTEM_WORKER.value,
            )
            db.add(ev)
            db.flush()
            by_source[key] = ev
            collected.append(ev)
            return ev

        if investigation.complaint_id is not None:
            comp = db.scalar(
                select(Complaint).where(
                    Complaint.organization_id == organization_id,
                    Complaint.id == investigation.complaint_id,
                )
            )
            if comp is not None:
                _add_ev(
                    source_type=EvidenceSourceType.COMPLAINT.value,
                    source_table="complaints",
                    source_record_id=comp.id,
                    source_row_version=comp.row_version,
                    field_path="complaint.description",
                    quoted_value=comp.description[:1000],
                    meta={"complaint_number": comp.complaint_number, "reported_batch": comp.reported_batch_number},
                )
                for att in db.scalars(
                    select(ComplaintAttachment).where(
                        ComplaintAttachment.organization_id == organization_id,
                        ComplaintAttachment.complaint_id == comp.id,
                    )
                ).all():
                    ev_att = _add_ev(
                        source_type=EvidenceSourceType.COMPLAINT_ATTACHMENT.value,
                        source_table="complaint_attachments",
                        source_record_id=att.id,
                        source_row_version=att.row_version,
                        document_id=att.document_id,
                        field_path="attachment.document",
                        quoted_value=att.caption or att.attachment_type,
                    )
                    if ev_att.document_version_id is not None:
                        for ec in db.scalars(
                            select(ExtractedContent)
                            .where(ExtractedContent.document_version_id == ev_att.document_version_id)
                            .order_by(ExtractedContent.page_number.asc())
                        ).all():
                            _add_ev(
                                source_type=EvidenceSourceType.EXTRACTED_CONTENT.value,
                                source_table="extracted_contents",
                                source_record_id=ec.id,
                                document_id=ev_att.document_id,
                                document_version_id=ec.document_version_id,
                                page_number=ec.page_number,
                                section_ref=ec.section_ref,
                                field_path=f"extracted_contents.page_{ec.page_number}",
                                quoted_value=(ec.text or "")[:1000],
                            )

        if investigation.deviation_id is not None:
            dev = db.scalar(
                select(Deviation).where(
                    Deviation.organization_id == organization_id,
                    Deviation.id == investigation.deviation_id,
                )
            )
            if dev is not None:
                _add_ev(
                    source_type=EvidenceSourceType.DEVIATION.value,
                    source_table="deviations",
                    source_record_id=dev.id,
                    source_row_version=dev.row_version,
                    field_path="deviation.description",
                    quoted_value=dev.description[:1000],
                    meta={"deviation_number": dev.deviation_number, "classification": dev.classification},
                )

        if investigation.batch_id is not None:
            batch = db.scalar(
                select(Batch).where(Batch.organization_id == organization_id, Batch.id == investigation.batch_id)
            )
            if batch is not None:
                _add_ev(
                    source_type=EvidenceSourceType.BATCH.value,
                    source_table="batches",
                    source_record_id=batch.id,
                    source_row_version=batch.row_version,
                    document_id=batch.primary_document_id,
                    field_path="batch.batch_status",
                    quoted_value=f"{batch.batch_number} ({batch.batch_status})",
                )
                for disp in db.scalars(
                    select(BatchDisposition).where(
                        BatchDisposition.organization_id == organization_id,
                        BatchDisposition.batch_id == batch.id,
                    )
                ).all():
                    _add_ev(
                        source_type=EvidenceSourceType.BATCH_DISPOSITION.value,
                        source_table="batch_dispositions",
                        source_record_id=disp.id,
                        document_id=disp.certificate_document_id,
                        field_path="batch_disposition.decision",
                        quoted_value=disp.disposition_decision,
                    )
                for ship_line in db.scalars(
                    select(ShipmentLine).where(
                        ShipmentLine.organization_id == organization_id,
                        ShipmentLine.batch_id == batch.id,
                    )
                ).all():
                    _add_ev(
                        source_type=EvidenceSourceType.SHIPMENT.value,
                        source_table="shipment_lines",
                        source_record_id=ship_line.id,
                        source_row_version=ship_line.row_version,
                        field_path="shipment_line.quantity",
                        quoted_value=f"{ship_line.quantity} {ship_line.unit_code}",
                    )
                for rec in db.scalars(
                    select(BatchExecutionRecord).where(
                        BatchExecutionRecord.organization_id == organization_id,
                        BatchExecutionRecord.batch_id == batch.id,
                    )
                ).all():
                    _add_ev(
                        source_type=EvidenceSourceType.BATCH_EXECUTION_RECORD.value,
                        source_table="batch_execution_records",
                        source_record_id=rec.id,
                        source_row_version=rec.row_version,
                        document_id=rec.primary_document_id,
                        document_version_id=rec.primary_document_version_id,
                        field_path="execution_record.status",
                        quoted_value=f"{rec.execution_code} ({rec.execution_status})",
                    )
                for obs in db.scalars(
                    select(ProcessParameterObservation)
                    .join(
                        BatchExecutionRecord,
                        BatchExecutionRecord.id == ProcessParameterObservation.batch_execution_record_id,
                    )
                    .where(
                        ProcessParameterObservation.organization_id == organization_id,
                        BatchExecutionRecord.batch_id == batch.id,
                    )
                ).all():
                    val_txt = (
                        f"{obs.numeric_value} {obs.unit_code or ''}".strip()
                        if obs.numeric_value is not None
                        else (obs.text_value or str(obs.boolean_value))
                    )
                    _add_ev(
                        source_type=EvidenceSourceType.PROCESS_PARAMETER_OBSERVATION.value,
                        source_table="process_parameter_observations",
                        source_record_id=obs.id,
                        source_row_version=obs.row_version,
                        document_id=obs.source_document_id,
                        document_version_id=obs.source_document_version_id,
                        page_number=obs.source_page_number,
                        section_ref=obs.source_section_ref,
                        field_path=f"observation.{obs.parameter_code}",
                        quoted_value=val_txt,
                    )
                for qcr in db.scalars(
                    select(QCTestResult).where(
                        QCTestResult.organization_id == organization_id,
                        QCTestResult.batch_id == batch.id,
                    )
                ).all():
                    q_txt = (
                        f"{qcr.numeric_result} {qcr.unit_code or ''}".strip()
                        if qcr.numeric_result is not None
                        else (qcr.text_result or str(qcr.boolean_result))
                    )
                    _add_ev(
                        source_type=EvidenceSourceType.QC_TEST_RESULT.value,
                        source_table="qc_test_results",
                        source_record_id=qcr.id,
                        source_row_version=qcr.row_version,
                        document_id=qcr.supporting_document_id,
                        document_version_id=qcr.supporting_document_version_id,
                        field_path="qc_test_result.outcome",
                        quoted_value=f"{q_txt} ({qcr.result_outcome})",
                    )
                    if qcr.equipment_id is not None:
                        for cal in db.scalars(
                            select(CalibrationRecord).where(
                                CalibrationRecord.organization_id == organization_id,
                                CalibrationRecord.equipment_id == qcr.equipment_id,
                            )
                        ).all():
                            _add_ev(
                                source_type=EvidenceSourceType.CALIBRATION_RECORD.value,
                                source_table="calibration_records",
                                source_record_id=cal.id,
                                source_row_version=cal.row_version,
                                document_id=cal.certificate_document_id,
                                document_version_id=cal.certificate_document_version_id,
                                field_path="calibration.result",
                                quoted_value=f"{cal.calibration_code} ({cal.calibration_result})",
                            )

        db.flush()
        return collected

    def execute_run(
        self,
        run_id: uuid.UUID,
        *,
        session_factory: Optional[sessionmaker[Session]] = None,
        llm_provider: Optional[LLMProvider] = None,
    ) -> InvestigationRun:
        """
        Executes a durable `InvestigationRun` using LangGraph with per-node DB checkpoints.
        Strictly guarantees that external LLM inference runs OUTSIDE any open DB transaction.
        """
        factory = session_factory or get_session_factory()
        settings = get_settings()
        provider = llm_provider or LLMProvider()

        # Step 0: Short transaction to claim run -> RUNNING (idempotent under outbox redelivery)
        already_handled = False
        with UnitOfWork(factory) as uow:
            assert uow.session is not None
            db = uow.session
            run = db.get(InvestigationRun, run_id)
            if run is None:
                raise NotFoundError(f"InvestigationRun {run_id} not found.")
            inv = db.get(Investigation, run.investigation_id)
            if inv is None:
                raise NotFoundError(f"Investigation {run.investigation_id} not found.")

            org_id = run.organization_id
            inv_id = inv.id
            if run.status not in {
                InvestigationRunStatus.QUEUED.value,
                InvestigationRunStatus.RETRYABLE.value,
            }:
                already_handled = True
            else:
                resume_seq = run.resume_from_step_seq or 1
                run.status = InvestigationRunStatus.RUNNING.value
                run.attempt_count += 1
                run.started_at = run.started_at or datetime.now(timezone.utc)
                run.last_error = None
                run.row_version += 1

                inv.status = InvestigationStatus.PROCESSING.value
                inv.row_version += 1

        if already_handled:
            read_db = factory()
            try:
                existing_run = self._repo.get_run_for_org(
                    read_db, org_id, run_id, with_steps=True
                )
                assert existing_run is not None
                return existing_run
            finally:
                read_db.close()

        current_node = {"seq": resume_seq, "name": "batch_resolution"}

        def _node_batch_resolution(state: InvestigationGraphState) -> Dict[str, Any]:
            current_node["seq"] = 1
            current_node["name"] = "batch_resolution"
            with UnitOfWork(factory) as uow:
                assert uow.session is not None
                db = uow.session
                inv = db.get(Investigation, inv_id)
                run = db.get(InvestigationRun, run_id)
                assert inv is not None and run is not None

                if inv.batch_id is not None:
                    self._upsert_step_in_tx(
                        db,
                        organization_id=org_id,
                        investigation_id=inv.id,
                        run_id=run.id,
                        step_seq=1,
                        node_name="batch_resolution",
                        status=InvestigationStepStatus.COMPLETED.value,
                        output_refs={"batch_id": str(inv.batch_id), "outcome": "ALREADY_LINKED"},
                    )
                    run.resume_from_step_seq = 2
                    run.checkpoint_state_json = {**(run.checkpoint_state_json or {}), "batch_id": str(inv.batch_id)}
                    return {"paused_for_human": False, "batch_id": str(inv.batch_id)}

                if inv.complaint_id is None:
                    self._upsert_step_in_tx(
                        db,
                        organization_id=org_id,
                        investigation_id=inv.id,
                        run_id=run.id,
                        step_seq=1,
                        node_name="batch_resolution",
                        status=InvestigationStepStatus.SKIPPED.value,
                        output_refs={"reason": "No complaint linked (deviation/manual case)."},
                    )
                    run.resume_from_step_seq = 2
                    return {"paused_for_human": False, "batch_id": None}

                comp = db.get(Complaint, inv.complaint_id)
                assert comp is not None
                ok, resolved_bid, outcome, wait_reason, link_ids = self._batch_resolver.resolve_for_complaint_in_tx(
                    db,
                    organization_id=org_id,
                    complaint=comp,
                )
                if not ok:
                    self._upsert_step_in_tx(
                        db,
                        organization_id=org_id,
                        investigation_id=inv.id,
                        run_id=run.id,
                        step_seq=1,
                        node_name="batch_resolution",
                        status=InvestigationStepStatus.WAITING.value,
                        output_refs={
                            "outcome": outcome,
                            "candidate_link_ids": [str(lid) for lid in link_ids],
                        },
                    )
                    run.status = InvestigationRunStatus.WAITING_FOR_HUMAN.value
                    run.waiting_reason = wait_reason
                    run.resume_from_step_seq = 1
                    run.checkpoint_state_json = {
                        **(run.checkpoint_state_json or {}),
                        "waiting_outcome": outcome,
                        "candidate_link_ids": [str(lid) for lid in link_ids],
                    }
                    run.row_version += 1
                    inv.status = InvestigationStatus.WAITING_FOR_INFORMATION.value
                    inv.row_version += 1
                    self._outbox.enqueue(
                        db,
                        organization_id=org_id,
                        aggregate_type="investigation_run",
                        aggregate_id=run.id,
                        event_type="InvestigationRunWaitingHuman",
                        payload_json={
                            "investigation_id": str(inv.id),
                            "run_id": str(run.id),
                            "organization_id": str(org_id),
                            "waiting_reason": wait_reason,
                        },
                    )
                    return {"paused_for_human": True, "waiting_reason": wait_reason, "batch_id": None}

                if resolved_bid is not None:
                    inv.batch_id = resolved_bid
                    batch = db.get(Batch, resolved_bid)
                    if batch is not None:
                        if inv.product_id is None:
                            inv.product_id = batch.product_id
                        if inv.site_id is None:
                            inv.site_id = batch.site_id
                self._upsert_step_in_tx(
                    db,
                    organization_id=org_id,
                    investigation_id=inv.id,
                    run_id=run.id,
                    step_seq=1,
                    node_name="batch_resolution",
                    status=InvestigationStepStatus.COMPLETED.value,
                    output_refs={
                        "batch_id": str(resolved_bid) if resolved_bid else None,
                        "outcome": outcome,
                        "link_ids": [str(lid) for lid in link_ids],
                    },
                )
                run.resume_from_step_seq = 2
                run.checkpoint_state_json = {
                    **(run.checkpoint_state_json or {}),
                    "batch_id": str(resolved_bid) if resolved_bid else None,
                    "batch_outcome": outcome,
                }
                return {"paused_for_human": False, "batch_id": str(resolved_bid) if resolved_bid else None}

        def _node_evidence_collection(state: InvestigationGraphState) -> Dict[str, Any]:
            current_node["seq"] = 2
            current_node["name"] = "evidence_collection"
            with UnitOfWork(factory) as uow:
                assert uow.session is not None
                db = uow.session
                inv = db.get(Investigation, inv_id)
                run = db.get(InvestigationRun, run_id)
                assert inv is not None and run is not None
                ev_rows = self._collect_evidence_in_tx(
                    db, organization_id=org_id, investigation=inv, run_id=run.id
                )
                ev_ids = [str(e.id) for e in ev_rows]
                self._upsert_step_in_tx(
                    db,
                    organization_id=org_id,
                    investigation_id=inv.id,
                    run_id=run.id,
                    step_seq=2,
                    node_name="evidence_collection",
                    status=InvestigationStepStatus.COMPLETED.value,
                    output_refs={"evidence_ref_ids": ev_ids, "count": len(ev_ids)},
                )
                run.resume_from_step_seq = 3
                run.checkpoint_state_json = {**(run.checkpoint_state_json or {}), "evidence_count": len(ev_ids)}
                return {"evidence_ref_ids": ev_ids}

        def _node_sop_compliance(state: InvestigationGraphState) -> Dict[str, Any]:
            current_node["seq"] = 3
            current_node["name"] = "deterministic_sop_compliance"
            with UnitOfWork(factory) as uow:
                assert uow.session is not None
                db = uow.session
                inv = db.get(Investigation, inv_id)
                run = db.get(InvestigationRun, run_id)
                assert inv is not None and run is not None
                worker_ctx = _build_worker_ctx(org_id)
                comp_rows = self._compliance.evaluate_in_tx(
                    db,
                    tenant_ctx=worker_ctx,
                    investigation=inv,
                    batch_id=inv.batch_id,
                    run_id=run.id,
                )
                det_findings = self._rca.persist_deterministic_findings_in_tx(
                    db,
                    investigation=inv,
                    run=run,
                    compliance_results=comp_rows,
                    created_by_user_id=None,
                )
                comp_ids = [str(c.id) for c in comp_rows]
                self._upsert_step_in_tx(
                    db,
                    organization_id=org_id,
                    investigation_id=inv.id,
                    run_id=run.id,
                    step_seq=3,
                    node_name="deterministic_sop_compliance",
                    status=InvestigationStepStatus.COMPLETED.value,
                    output_refs={
                        "compliance_result_ids": comp_ids,
                        "fail_count": sum(1 for c in comp_rows if c.status == "FAIL"),
                        "deterministic_finding_ids": [str(f.id) for f in det_findings],
                    },
                )
                run.resume_from_step_seq = 4
                return {
                    "compliance_result_ids": comp_ids,
                    "finding_ids": [str(f.id) for f in det_findings],
                }

        def _node_rca_synthesis(state: InvestigationGraphState) -> Dict[str, Any]:
            current_node["seq"] = 4
            current_node["name"] = "rca_synthesis"

            # 1. Read snapshot & close session strictly BEFORE calling external LLM
            read_db = factory()
            try:
                inv = read_db.get(Investigation, inv_id)
                assert inv is not None
                known_pii: Dict[str, List[str]] = {"person": [], "email": [], "phone": []}
                complaint_summary: Dict[str, Any] = {}
                if inv.complaint_id is not None:
                    comp = read_db.get(Complaint, inv.complaint_id)
                    if comp is not None:
                        complaint_summary = {
                            "complaint_number": comp.complaint_number,
                            "reported_product_name": comp.reported_product_name,
                            "reported_batch_number": comp.reported_batch_number,
                            "defect_category": comp.defect_category,
                            "description": comp.description,
                        }
                        if comp.complainant_id is not None:
                            c_person = read_db.get(Complainant, comp.complainant_id)
                            if c_person is not None:
                                if c_person.full_name:
                                    known_pii["person"].append(c_person.full_name)
                                if c_person.email:
                                    known_pii["email"].append(c_person.email)
                                if c_person.phone_e164:
                                    known_pii["phone"].append(c_person.phone_e164)

                ev_rows = self._repo.list_evidence_for_investigation(read_db, org_id, inv.id)
                comp_rows = self._repo.list_compliance_results_for_investigation(read_db, org_id, inv.id)

                # Use opaque aliases (EV-001, EV-002, ...) so PIIRedactor regexes
                # never mangle hex/digit segments inside raw UUIDs.
                alias_to_uuid: Dict[str, uuid.UUID] = {}
                uuid_to_alias: Dict[uuid.UUID, str] = {}
                for idx, ev_row in enumerate(ev_rows, start=1):
                    alias = f"EV-{idx:03d}"
                    alias_to_uuid[alias] = ev_row.id
                    uuid_to_alias[ev_row.id] = alias

                payload_for_llm = {
                    "investigation_number": inv.investigation_number,
                    "title": inv.title,
                    "trigger_type": inv.trigger_type,
                    "complaint": complaint_summary or None,
                    "available_evidence_ids": [uuid_to_alias[e.id] for e in ev_rows],
                    "evidence_items": [
                        {
                            "evidence_id": uuid_to_alias[e.id],
                            "source_type": e.source_type,
                            "source_table": e.source_table,
                            "field_path": e.field_path,
                            "quoted_value": e.quoted_value,
                            "document_checksum_sha256": e.document_checksum_sha256,
                        }
                        for e in ev_rows
                    ],
                    "deterministic_compliance_results": [
                        {
                            "rule_identifier": c.rule_identifier,
                            "status": c.status,
                            "explanation": c.explanation,
                            "evidence_ref_id": (
                                uuid_to_alias.get(c.evidence_ref_id, str(c.evidence_ref_id))
                                if c.evidence_ref_id
                                else None
                            ),
                        }
                        for c in comp_rows
                    ],
                }
                untrusted_text = json.dumps(payload_for_llm, default=str)
            finally:
                read_db.close()

            # 2. External LLM inference strictly OUTSIDE any open DB transaction
            wire_out = provider.generate_structured(
                system_prompt=RCA_SYNTHESIS_SYSTEM_PROMPT,
                untrusted_text=untrusted_text,
                response_model=RCASynthesisLLMDispatchOutput,
                known_pii=known_pii,
            )

            def _resolve_ref(ref_str: str) -> uuid.UUID:
                if ref_str in alias_to_uuid:
                    return alias_to_uuid[ref_str]
                try:
                    return uuid.UUID(str(ref_str))
                except (ValueError, TypeError) as exc:
                    raise ValidationError(
                        message=f"Evidence citation '{ref_str}' is not a valid EvidenceReference ID.",
                        error_code="INVALID_EVIDENCE_CITATION",
                    ) from exc

            ai_out = RCASynthesisLLMOutput(
                executive_summary=wire_out.executive_summary,
                findings=[
                    RCASynthesisFindingItem(
                        category=f.category,
                        title=f.title,
                        statement=f.statement,
                        severity=f.severity,
                        evidence_ref_ids=[_resolve_ref(r) for r in f.evidence_ref_ids],
                    )
                    for f in wire_out.findings
                ],
                hypotheses=[
                    RCASynthesisHypothesisItem(
                        category=h.category,
                        title=h.title,
                        description=h.description,
                        supporting_evidence_ids=[_resolve_ref(r) for r in h.supporting_evidence_ids],
                        contradicting_evidence_ids=[
                            _resolve_ref(r) for r in h.contradicting_evidence_ids
                        ],
                        uncertainty_level=h.uncertainty_level,
                        confidence_score=h.confidence_score,
                        recommended_verification=h.recommended_verification,
                        limitations=h.limitations,
                    )
                    for h in wire_out.hypotheses
                ],
                limitations=list(wire_out.limitations),
            )

            # 3. Short write transaction enforcing evidence-citation gate
            with UnitOfWork(factory) as uow:
                assert uow.session is not None
                db = uow.session
                inv = db.get(Investigation, inv_id)
                run = db.get(InvestigationRun, run_id)
                assert inv is not None and run is not None
                ai_findings, ai_hyps = self._rca.persist_ai_synthesis_in_tx(
                    db,
                    investigation=inv,
                    run=run,
                    synthesis=ai_out,
                    model_name=run.model_name or settings.LLM_MODEL_NAME,
                    prompt_version=run.prompt_version or INVESTIGATION_PROMPT_VERSION,
                    created_by_user_id=None,
                )
                self._upsert_step_in_tx(
                    db,
                    organization_id=org_id,
                    investigation_id=inv.id,
                    run_id=run.id,
                    step_seq=4,
                    node_name="rca_synthesis",
                    status=InvestigationStepStatus.COMPLETED.value,
                    output_refs={
                        "ai_finding_ids": [str(f.id) for f in ai_findings],
                        "hypothesis_ids": [str(h.id) for h in ai_hyps],
                    },
                )
                run.resume_from_step_seq = 5
                run.summary_json = {
                    **(run.summary_json or {}),
                    "executive_summary": ai_out.executive_summary,
                    "limitations": list(ai_out.limitations),
                }
                return {"hypothesis_ids": [str(h.id) for h in ai_hyps]}

        def _node_finalize_run(state: InvestigationGraphState) -> Dict[str, Any]:
            current_node["seq"] = 5
            current_node["name"] = "finalize_run"
            now = datetime.now(timezone.utc)
            with UnitOfWork(factory) as uow:
                assert uow.session is not None
                db = uow.session
                inv = db.get(Investigation, inv_id)
                run = db.get(InvestigationRun, run_id)
                assert inv is not None and run is not None
                self._upsert_step_in_tx(
                    db,
                    organization_id=org_id,
                    investigation_id=inv.id,
                    run_id=run.id,
                    step_seq=5,
                    node_name="finalize_run",
                    status=InvestigationStepStatus.COMPLETED.value,
                    output_refs={"finalized_at": now.isoformat()},
                )
                run.status = InvestigationRunStatus.COMPLETED.value
                run.finished_at = now
                run.row_version += 1

                inv.status = InvestigationStatus.COMPLETED.value
                inv.completed_at = now
                inv.row_version += 1

                self._audit.record(
                    db,
                    organization_id=org_id,
                    site_id=inv.site_id,
                    actor_type=ActorType.SYSTEM_WORKER.value,
                    actor_id=None,
                    action="INVESTIGATION_RUN_COMPLETED",
                    entity_type="investigation_runs",
                    entity_id=run.id,
                    after_state={"investigation_id": str(inv.id), "status": run.status},
                )
                self._outbox.enqueue(
                    db,
                    organization_id=org_id,
                    aggregate_type="investigation_run",
                    aggregate_id=run.id,
                    event_type="InvestigationRunCompleted",
                    payload_json={
                        "investigation_id": str(inv.id),
                        "run_id": str(run.id),
                        "organization_id": str(org_id),
                    },
                )
            return {}

        graph = build_investigation_graph(
            batch_resolution_fn=_node_batch_resolution,
            evidence_collection_fn=_node_evidence_collection,
            sop_compliance_fn=_node_sop_compliance,
            rca_synthesis_fn=_node_rca_synthesis,
            finalize_run_fn=_node_finalize_run,
        )

        try:
            graph.invoke(
                {
                    "organization_id": str(org_id),
                    "investigation_id": str(inv_id),
                    "run_id": str(run_id),
                    "resume_from_step_seq": resume_seq,
                }
            )
        except Exception as exc:
            now = datetime.now(timezone.utc)
            with UnitOfWork(factory) as uow:
                assert uow.session is not None
                db = uow.session
                run = db.get(InvestigationRun, run_id)
                inv = db.get(Investigation, inv_id)
                if run is not None:
                    self._upsert_step_in_tx(
                        db,
                        organization_id=org_id,
                        investigation_id=inv_id,
                        run_id=run.id,
                        step_seq=current_node["seq"],
                        node_name=current_node["name"],
                        status=InvestigationStepStatus.FAILED.value,
                        error_message=str(exc)[:2000],
                    )
                    is_validation = isinstance(exc, ValidationError)
                    if not is_validation and run.attempt_count < run.max_attempts:
                        run.status = InvestigationRunStatus.RETRYABLE.value
                    elif not is_validation and run.attempt_count >= run.max_attempts:
                        run.status = InvestigationRunStatus.DEAD_LETTER.value
                    else:
                        run.status = InvestigationRunStatus.FAILED.value
                    run.last_error = str(exc)[:2000]
                    run.finished_at = now
                    run.row_version += 1
                if inv is not None:
                    if run is not None and run.status in {
                        InvestigationRunStatus.FAILED.value,
                        InvestigationRunStatus.DEAD_LETTER.value,
                    }:
                        inv.status = InvestigationStatus.FAILED.value
                        inv.row_version += 1
                    self._outbox.enqueue(
                        db,
                        organization_id=org_id,
                        aggregate_type="investigation_run",
                        aggregate_id=run_id,
                        event_type="InvestigationRunFailed",
                        payload_json={
                            "investigation_id": str(inv_id),
                            "run_id": str(run_id),
                            "organization_id": str(org_id),
                            "error": str(exc)[:500],
                        },
                    )
            raise

        read_db = factory()
        try:
            final_run = self._repo.get_run_for_org(read_db, org_id, run_id, with_steps=True)
            assert final_run is not None
            return final_run
        finally:
            read_db.close()

    def handle_outbox_investigation_run(
        self,
        session_factory: sessionmaker[Session],
        event: OutboxEvent,
        _tenant_ctx: Optional[TenantContext] = None,
    ) -> None:
        """ExternalOutboxHandler entrypoint executed strictly outside open DB transactions."""
        payload = event.payload_json or {}
        run_id = uuid.UUID(str(payload.get("run_id") or event.aggregate_id))
        self.execute_run(run_id, session_factory=session_factory)

    # --- D7 QA Review Endpoints ---

    def decide_hypothesis(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        hypothesis_id: uuid.UUID,
        payload: HypothesisDecisionRequest,
        source_ip: Optional[str] = None,
    ) -> RootCauseHypothesis:
        hyp = self._repo.get_hypothesis_for_org(db, tenant_ctx.organization_id, hypothesis_id)
        if hyp is None:
            raise NotFoundError("Root cause hypothesis not found.")
        inv = self._repo.get_investigation_for_org(
            db, tenant_ctx.organization_id, hyp.investigation_id
        )
        if inv is None:
            raise NotFoundError("Parent investigation not found.")
        self._assert_site_access(
            tenant_ctx,
            inv.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/hypotheses/{hypothesis_id}/decision",
        )
        assert_transition(
            HYPOTHESIS_TRANSITIONS, hyp.status, payload.decision, entity_name="RootCauseHypothesis"
        )
        if tenant_ctx.membership_id is None:
            raise PermissionDeniedError("Active organization membership required.")

        if hyp.created_by_user_id is not None and hyp.created_by_user_id == tenant_ctx.user_id:
            self._audit.record_security_event(
                event_type="MAKER_CHECKER_VIOLATION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                source_ip=source_ip,
                target_resource=f"/api/v1/hypotheses/{hypothesis_id}/decision",
            )
            raise PermissionDeniedError(
                "Maker-checker violation: hypothesis reviewer must differ from creator.",
                error_code="MAKER_CHECKER_VIOLATION",
            )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            locked = self._repo.get_hypothesis_for_org(
                db, tenant_ctx.organization_id, hyp.id, for_update=True
            )
            assert locked is not None
            verify_and_increment_version(locked, payload.row_version)
            prev_status = locked.status

            approval_dec = (
                "APPROVED"
                if payload.decision == HypothesisStatus.ACCEPTED.value
                else (
                    "REJECTED"
                    if payload.decision == HypothesisStatus.REJECTED.value
                    else "REQUEST_MORE_WORK"
                )
            )
            approval = ApprovalRecord(
                organization_id=tenant_ctx.organization_id,
                entity_type="root_cause_hypothesis",
                entity_id=locked.id,
                approval_stage="QA_HYPOTHESIS_DECISION",
                decision=approval_dec,
                approver_membership_id=tenant_ctx.membership_id,
                decided_at=now,
                comments=payload.comments.strip(),
            )
            db.add(approval)
            db.flush()

            locked.status = payload.decision
            locked.reviewed_by_user_id = tenant_ctx.user_id
            locked.reviewed_by_membership_id = tenant_ctx.membership_id
            locked.reviewed_at = now
            locked.review_comments = payload.comments.strip()
            locked.approval_record_id = approval.id
            locked.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=inv.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="HYPOTHESIS_DECIDED",
                entity_type="root_cause_hypotheses",
                entity_id=locked.id,
                reason=payload.comments.strip(),
                source_ip=source_ip,
                before_state={"status": prev_status},
                after_state={"status": locked.status, "approval_record_id": str(approval.id)},
            )
        db.refresh(locked)
        return locked

    def review_investigation(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        investigation_id: uuid.UUID,
        payload: InvestigationReviewRequest,
        source_ip: Optional[str] = None,
    ) -> Investigation:
        inv = self.get_investigation(
            db,
            tenant_ctx=tenant_ctx,
            investigation_id=investigation_id,
            with_children=False,
            source_ip=source_ip,
        )
        if tenant_ctx.membership_id is None:
            raise PermissionDeniedError("Active organization membership required.")

        if inv.created_by_user_id is not None and inv.created_by_user_id == tenant_ctx.user_id:
            self._audit.record_security_event(
                event_type="MAKER_CHECKER_VIOLATION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                source_ip=source_ip,
                target_resource=f"/api/v1/investigations/{investigation_id}/review",
            )
            raise PermissionDeniedError(
                "Maker-checker violation: investigation QA reviewer must be distinct from the creator.",
                error_code="MAKER_CHECKER_VIOLATION",
            )

        target_status = (
            InvestigationStatus.APPROVED.value
            if payload.decision == "APPROVED"
            else InvestigationStatus.WAITING_FOR_INFORMATION.value
        )
        assert_transition(
            INVESTIGATION_TRANSITIONS, inv.status, target_status, entity_name="Investigation"
        )

        now = datetime.now(timezone.utc)
        with UnitOfWork(db):
            locked = self._repo.get_investigation_for_org(
                db, tenant_ctx.organization_id, inv.id, for_update=True
            )
            assert locked is not None
            verify_and_increment_version(locked, payload.row_version)
            prev_status = locked.status

            approval = ApprovalRecord(
                organization_id=tenant_ctx.organization_id,
                entity_type="investigation",
                entity_id=locked.id,
                approval_stage="QA_INVESTIGATION_REVIEW",
                decision=payload.decision,
                approver_membership_id=tenant_ctx.membership_id,
                decided_at=now,
                comments=payload.comments.strip(),
            )
            db.add(approval)
            db.flush()

            locked.status = target_status
            locked.qa_reviewed_by_user_id = tenant_ctx.user_id
            locked.qa_reviewed_by_membership_id = tenant_ctx.membership_id
            locked.qa_reviewed_at = now
            locked.qa_decision = payload.decision
            locked.qa_decision_comments = payload.comments.strip()
            locked.approval_record_id = approval.id
            if payload.closure_reason:
                locked.closure_reason = payload.closure_reason.strip()
            locked.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=locked.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="INVESTIGATION_QA_REVIEWED",
                entity_type="investigations",
                entity_id=locked.id,
                reason=payload.comments.strip(),
                source_ip=source_ip,
                before_state={"status": prev_status},
                after_state={"status": locked.status, "qa_decision": locked.qa_decision},
            )
            self._outbox.enqueue(
                db,
                organization_id=tenant_ctx.organization_id,
                aggregate_type="investigation",
                aggregate_id=locked.id,
                event_type="InvestigationReviewed",
                payload_json={
                    "investigation_id": str(locked.id),
                    "organization_id": str(tenant_ctx.organization_id),
                    "decision": payload.decision,
                    "status": locked.status,
                },
            )
        db.refresh(locked)
        return locked
