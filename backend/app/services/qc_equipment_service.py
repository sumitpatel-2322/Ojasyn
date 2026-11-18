import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

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
    MembershipStatus,
    SecurityEventOutcome,
    SecurityEventSeverity,
)
from app.domain.rules_engine import evaluate_qc_result_vs_specification
from app.models.domain.governance import ApprovalRecord
from app.models.domain.identity import OrganizationMembership
from app.models.domain.products_materials import Product
from app.models.domain.qc_equipment import (
    CalibrationRecord,
    Equipment,
    EquipmentUsage,
    QCSample,
    QCTestDefinition,
    QCTestResult,
)
from app.models.domain.tenancy import Department
from app.models.domain.traceability import Batch
from app.models.schemas.qc_equipment import (
    BatchQCHistoryResponse,
    CalibrationRecordCreateRequest,
    CalibrationRecordReviewRequest,
    CalibrationValidityResponse,
    EquipmentCreateRequest,
    EquipmentUpdateRequest,
    EquipmentUsageCreateRequest,
    QCSampleCreateRequest,
    QCSampleResponse,
    QCTestDefinitionCreateRequest,
    QCTestResultCreateRequest,
    QCTestResultResponse,
    QCTestResultReviewRequest,
)
from app.repositories.document_sop_repo import DocumentSOPRepository
from app.repositories.organization_repo import OrganizationRepository
from app.repositories.product_batch_repo import ProductBatchRepository
from app.repositories.product_material_repo import ProductMaterialRepository
from app.repositories.qc_equipment_repo import QCEquipmentRepository
from app.services.audit_service import AuditService


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def evaluate_calibration_validity(
    db: Session,
    *,
    organization_id: uuid.UUID,
    equipment_id: uuid.UUID,
    at: datetime,
    repo: Optional[QCEquipmentRepository] = None,
) -> CalibrationValidityResponse:
    """
    Deterministically evaluates whether an equipment asset had a valid, approved calibration
    covering the historical timestamp `at`.
    Never infers historical validity from `equipment.operational_status`.
    """
    qc_repo = repo or QCEquipmentRepository()
    equipment = qc_repo.get_equipment_for_org(db, organization_id, equipment_id)
    if equipment is None:
        raise NotFoundError("Equipment not found in this organization.")

    at_utc = _as_utc(at)

    if not equipment.calibration_required:
        return CalibrationValidityResponse(
            equipment_id=equipment.id,
            equipment_code=equipment.equipment_code,
            equipment_name=equipment.equipment_name,
            site_id=equipment.site_id,
            calibration_required=False,
            evaluated_at_timestamp=at_utc,
            validity_status="VALID",
            explanation=(
                f"Equipment '{equipment.equipment_code}' does not require calibration."
            ),
        )

    all_records = qc_repo.list_calibrations_for_equipment(
        db, organization_id, equipment.id
    )
    if not all_records:
        return CalibrationValidityResponse(
            equipment_id=equipment.id,
            equipment_code=equipment.equipment_code,
            equipment_name=equipment.equipment_name,
            site_id=equipment.site_id,
            calibration_required=True,
            evaluated_at_timestamp=at_utc,
            validity_status="MISSING_RECORD",
            explanation=(
                f"Equipment '{equipment.equipment_code}' has 0 calibration records on file."
            ),
        )

    prior_or_equal = [
        rec for rec in all_records if _as_utc(rec.calibrated_at) <= at_utc
    ]
    prior_or_equal.sort(key=lambda r: _as_utc(r.calibrated_at), reverse=True)

    if not prior_or_equal:
        return CalibrationValidityResponse(
            equipment_id=equipment.id,
            equipment_code=equipment.equipment_code,
            equipment_name=equipment.equipment_name,
            site_id=equipment.site_id,
            calibration_required=True,
            evaluated_at_timestamp=at_utc,
            validity_status="NOT_CALIBRATED",
            explanation=(
                f"Equipment '{equipment.equipment_code}' had no calibration record on or before "
                f"{at_utc.isoformat()}."
            ),
        )

    latest = prior_or_equal[0]
    cal_at_utc = _as_utc(latest.calibrated_at)
    due_at_utc = _as_utc(latest.due_at)

    if (
        latest.calibration_result in ("FAIL", "OUT_OF_TOLERANCE")
        or latest.record_status == "REJECTED"
    ):
        return CalibrationValidityResponse(
            equipment_id=equipment.id,
            equipment_code=equipment.equipment_code,
            equipment_name=equipment.equipment_name,
            site_id=equipment.site_id,
            calibration_required=True,
            evaluated_at_timestamp=at_utc,
            validity_status="NOT_CALIBRATED",
            applicable_calibration_record_id=latest.id,
            applicable_calibration_code=latest.calibration_code,
            calibrated_at=cal_at_utc,
            due_at=due_at_utc,
            calibration_result=latest.calibration_result,
            record_status=latest.record_status,
            certificate_document_version_id=latest.certificate_document_version_id,
            explanation=(
                f"Calibration record '{latest.calibration_code}' failed or was rejected "
                f"(result='{latest.calibration_result}', status='{latest.record_status}')."
            ),
        )

    if at_utc > due_at_utc:
        return CalibrationValidityResponse(
            equipment_id=equipment.id,
            equipment_code=equipment.equipment_code,
            equipment_name=equipment.equipment_name,
            site_id=equipment.site_id,
            calibration_required=True,
            evaluated_at_timestamp=at_utc,
            validity_status="EXPIRED",
            applicable_calibration_record_id=latest.id,
            applicable_calibration_code=latest.calibration_code,
            calibrated_at=cal_at_utc,
            due_at=due_at_utc,
            calibration_result=latest.calibration_result,
            record_status=latest.record_status,
            certificate_document_version_id=latest.certificate_document_version_id,
            explanation=(
                f"Calibration record '{latest.calibration_code}' expired on "
                f"{due_at_utc.isoformat()} prior to {at_utc.isoformat()}."
            ),
        )

    if latest.record_status != "APPROVED":
        return CalibrationValidityResponse(
            equipment_id=equipment.id,
            equipment_code=equipment.equipment_code,
            equipment_name=equipment.equipment_name,
            site_id=equipment.site_id,
            calibration_required=True,
            evaluated_at_timestamp=at_utc,
            validity_status="UNVERIFIED",
            applicable_calibration_record_id=latest.id,
            applicable_calibration_code=latest.calibration_code,
            calibrated_at=cal_at_utc,
            due_at=due_at_utc,
            calibration_result=latest.calibration_result,
            record_status=latest.record_status,
            certificate_document_version_id=latest.certificate_document_version_id,
            explanation=(
                f"Calibration record '{latest.calibration_code}' covers {at_utc.isoformat()} "
                f"but is in '{latest.record_status}' status (not yet APPROVED)."
            ),
        )

    return CalibrationValidityResponse(
        equipment_id=equipment.id,
        equipment_code=equipment.equipment_code,
        equipment_name=equipment.equipment_name,
        site_id=equipment.site_id,
        calibration_required=True,
        evaluated_at_timestamp=at_utc,
        validity_status="VALID",
        applicable_calibration_record_id=latest.id,
        applicable_calibration_code=latest.calibration_code,
        calibrated_at=cal_at_utc,
        due_at=due_at_utc,
        calibration_result=latest.calibration_result,
        record_status=latest.record_status,
        certificate_document_version_id=latest.certificate_document_version_id,
        explanation=(
            f"Approved calibration record '{latest.calibration_code}' was valid from "
            f"{cal_at_utc.isoformat()} through {due_at_utc.isoformat()}."
        ),
    )


class QCEquipmentService:
    """Tenant-scoped service for QC Laboratory (Module C6) and Equipment & Calibration (Module C8)."""

    def __init__(
        self,
        repo: Optional[QCEquipmentRepository] = None,
        batch_repo: Optional[ProductBatchRepository] = None,
        pm_repo: Optional[ProductMaterialRepository] = None,
        org_repo: Optional[OrganizationRepository] = None,
        doc_repo: Optional[DocumentSOPRepository] = None,
        audit_service: Optional[AuditService] = None,
    ) -> None:
        self._repo = repo or QCEquipmentRepository()
        self._batch_repo = batch_repo or ProductBatchRepository()
        self._pm_repo = pm_repo or ProductMaterialRepository()
        self._org_repo = org_repo or OrganizationRepository()
        self._doc_repo = doc_repo or DocumentSOPRepository()
        self._audit = audit_service or AuditService()

    def _assert_site_access(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        site_id: uuid.UUID,
        *,
        source_ip: Optional[str] = None,
        target_resource: str = "/api/v1/qc",
    ) -> None:
        site = self._org_repo.get_site_for_org(db, tenant_ctx.organization_id, site_id)
        if site is None:
            raise NotFoundError("Site not found in this organization.")
        if not tenant_ctx.is_org_wide and site.id not in tenant_ctx.allowed_site_ids:
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
                "Not authorized to access or manage records for this site.",
                error_code="SITE_SCOPE_DENIED",
            )

    def _get_active_membership(
        self,
        db: Session,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
        label: str,
    ) -> OrganizationMembership:
        member = db.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.id == membership_id,
            )
        )
        if member is None:
            raise NotFoundError(f"{label} membership not found in this organization.")
        if member.membership_status != MembershipStatus.ACTIVE.value:
            raise ValidationError(f"{label} membership must be ACTIVE.")
        return member

    def _assert_document_and_version_in_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        document_id: Optional[uuid.UUID],
        document_version_id: Optional[uuid.UUID],
    ) -> Optional[uuid.UUID]:
        resolved_doc_id = document_id
        if document_id is not None:
            doc = self._doc_repo.get_document_for_org(db, organization_id, document_id)
            if doc is None:
                raise NotFoundError("Referenced document not found in this organization.")
        if document_version_id is not None:
            ver_doc = self._doc_repo.get_version_with_document(db, document_version_id)
            if ver_doc is None or ver_doc[1].organization_id != organization_id:
                raise NotFoundError(
                    "Referenced document version not found in this organization."
                )
            if document_id is not None and ver_doc[0].document_id != document_id:
                raise ValidationError(
                    "Referenced document_version_id does not belong to document_id."
                )
            resolved_doc_id = ver_doc[1].id
        return resolved_doc_id

    # --- QC Test Definitions (Module C6) ---

    def create_test_definition(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        payload: QCTestDefinitionCreateRequest,
        source_ip: Optional[str] = None,
    ) -> QCTestDefinition:
        if payload.site_id is not None:
            self._assert_site_access(
                db,
                tenant_ctx,
                payload.site_id,
                source_ip=source_ip,
                target_resource="/api/v1/qc/test-definitions",
            )
        elif not tenant_ctx.is_org_wide:
            raise PermissionDeniedError(
                "Site-scoped actors must specify an authorized site_id for QC test definitions.",
                error_code="SITE_SCOPE_DENIED",
            )

        if payload.product_id is not None:
            product: Optional[Product] = self._pm_repo.get_product_for_org(
                db, tenant_ctx.organization_id, payload.product_id
            )
            if product is None:
                raise NotFoundError("Product not found in this organization.")
            if (
                payload.site_id is not None
                and product.site_id is not None
                and product.site_id != payload.site_id
            ):
                raise ValidationError(
                    "Product site_id does not match QC test definition site_id."
                )

        if payload.sop_revision_id is not None:
            from app.models.domain.sops import SOPRevision

            sop_rev = db.scalar(
                select(SOPRevision).where(
                    SOPRevision.organization_id == tenant_ctx.organization_id,
                    SOPRevision.id == payload.sop_revision_id,
                )
            )
            if sop_rev is None:
                raise NotFoundError("Referenced SOP revision not found in this organization.")

        resolved_spec_doc_id = self._assert_document_and_version_in_org(
            db,
            tenant_ctx.organization_id,
            payload.specification_document_id,
            payload.specification_document_version_id,
        )

        existing = self._repo.get_test_definition_by_code_version(
            db,
            tenant_ctx.organization_id,
            payload.test_code,
            payload.version_number,
        )
        if existing is not None:
            raise ConflictError(
                f"QC test definition '{payload.test_code}' version '{payload.version_number}' already exists.",
                error_code="DUPLICATE_QC_TEST_DEFINITION",
            )

        with UnitOfWork(db):
            test_def = QCTestDefinition(
                organization_id=tenant_ctx.organization_id,
                site_id=payload.site_id,
                product_id=payload.product_id,
                product_category=(
                    payload.product_category.strip() if payload.product_category else None
                ),
                test_code=payload.test_code.strip().upper(),
                version_number=payload.version_number.strip(),
                test_name=payload.test_name.strip(),
                description=payload.description,
                test_method_reference=payload.test_method_reference.strip(),
                sop_revision_id=payload.sop_revision_id,
                specification_document_id=resolved_spec_doc_id,
                specification_document_version_id=payload.specification_document_version_id,
                test_type=payload.test_type,
                expected_unit_code=(
                    payload.expected_unit_code.strip()
                    if payload.expected_unit_code
                    else None
                ),
                min_limit=payload.min_limit,
                max_limit=payload.max_limit,
                target_value=payload.target_value,
                expected_text_specification=(
                    payload.expected_text_specification.strip()
                    if payload.expected_text_specification
                    else None
                ),
                acceptance_criteria=payload.acceptance_criteria.strip(),
                effective_from=payload.effective_from,
                effective_to=payload.effective_to,
                status=payload.status,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(test_def)
            db.flush()

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=test_def.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="QC_TEST_DEFINITION_CREATED",
                entity_type="qc_test_definitions",
                entity_id=test_def.id,
                source_ip=source_ip,
                after_state={
                    "test_code": test_def.test_code,
                    "version_number": test_def.version_number,
                    "test_type": test_def.test_type,
                    "status": test_def.status,
                },
            )
        db.refresh(test_def)
        return test_def

    def list_test_definitions(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        site_id: Optional[uuid.UUID] = None,
        product_id: Optional[uuid.UUID] = None,
        product_category: Optional[str] = None,
        test_code: Optional[str] = None,
        status: Optional[str] = None,
        q: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[QCTestDefinition], int]:
        return self._repo.list_test_definitions_for_tenant(
            db,
            tenant_ctx,
            site_id=site_id,
            product_id=product_id,
            product_category=product_category,
            test_code=test_code,
            status=status,
            q=q,
            page=page,
            page_size=page_size,
        )

    def get_test_definition(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        test_definition_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> QCTestDefinition:
        test_def = self._repo.get_test_definition_for_org(
            db, tenant_ctx.organization_id, test_definition_id
        )
        if test_def is None:
            raise NotFoundError("QC test definition not found.")
        if test_def.site_id is not None:
            self._assert_site_access(
                db,
                tenant_ctx,
                test_def.site_id,
                source_ip=source_ip,
                target_resource=f"/api/v1/qc/test-definitions/{test_definition_id}",
            )
        return test_def

    # --- QC Samples (Module C6) ---

    def _get_batch_or_404_with_site_check(
        self,
        db: Session,
        tenant_ctx: TenantContext,
        batch_id: uuid.UUID,
        *,
        source_ip: Optional[str] = None,
        target_resource: Optional[str] = None,
    ) -> Batch:
        batch = self._batch_repo.get_batch_for_org(
            db, tenant_ctx.organization_id, batch_id
        )
        if batch is None:
            raise NotFoundError("Batch not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            batch.site_id,
            source_ip=source_ip,
            target_resource=target_resource or f"/api/v1/batches/{batch_id}/qc-samples",
        )
        return batch

    def create_qc_sample(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        batch_id: uuid.UUID,
        payload: QCSampleCreateRequest,
        source_ip: Optional[str] = None,
    ) -> QCSample:
        batch = self._get_batch_or_404_with_site_check(
            db,
            tenant_ctx,
            batch_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/batches/{batch_id}/qc-samples",
        )

        existing = self._repo.get_sample_by_identifier(
            db, tenant_ctx.organization_id, payload.sample_identifier
        )
        if existing is not None:
            raise ConflictError(
                f"QC sample '{payload.sample_identifier}' already exists in this organization.",
                error_code="DUPLICATE_QC_SAMPLE_IDENTIFIER",
            )

        sampler_mid = payload.sampled_by_membership_id or tenant_ctx.membership_id
        sampler = self._get_active_membership(
            db, tenant_ctx.organization_id, sampler_mid, "Sampler"
        )

        resolved_doc_id = self._assert_document_and_version_in_org(
            db,
            tenant_ctx.organization_id,
            payload.supporting_document_id,
            payload.supporting_document_version_id,
        )

        sampled_at = payload.sampled_at or datetime.now(timezone.utc)

        with UnitOfWork(db):
            sample = QCSample(
                organization_id=tenant_ctx.organization_id,
                site_id=batch.site_id,
                batch_id=batch.id,
                product_id=batch.product_id,
                sample_identifier=payload.sample_identifier.strip(),
                sample_type=payload.sample_type,
                sampled_at=sampled_at,
                sampled_by_user_id=sampler.user_id,
                sampled_by_membership_id=sampler.id,
                sample_quantity=payload.sample_quantity,
                sample_unit_code=payload.sample_unit_code.strip(),
                storage_location=(
                    payload.storage_location.strip() if payload.storage_location else None
                ),
                testing_status="REGISTERED",
                supporting_document_id=resolved_doc_id,
                supporting_document_version_id=payload.supporting_document_version_id,
                notes=payload.notes,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(sample)
            db.flush()

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=sample.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="QC_SAMPLE_REGISTERED",
                entity_type="qc_samples",
                entity_id=sample.id,
                source_ip=source_ip,
                after_state={
                    "sample_identifier": sample.sample_identifier,
                    "batch_id": str(sample.batch_id),
                    "sample_type": sample.sample_type,
                    "testing_status": sample.testing_status,
                },
            )
        return self._repo.get_sample_for_org(
            db, tenant_ctx.organization_id, sample.id, with_results=True
        )  # type: ignore[return-value]

    def list_batch_qc_samples(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        batch_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> List[QCSample]:
        batch = self._get_batch_or_404_with_site_check(
            db,
            tenant_ctx,
            batch_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/batches/{batch_id}/qc-samples",
        )
        return self._repo.list_samples_for_batch(
            db, tenant_ctx.organization_id, batch.id, with_results=True
        )

    def get_qc_sample(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        sample_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> QCSample:
        sample = self._repo.get_sample_for_org(
            db, tenant_ctx.organization_id, sample_id, with_results=True
        )
        if sample is None:
            raise NotFoundError("QC sample not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            sample.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/qc/samples/{sample_id}",
        )
        return sample

    # --- QC Test Results & Review (Module C6) ---

    def record_qc_result(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        sample_id: uuid.UUID,
        payload: QCTestResultCreateRequest,
        source_ip: Optional[str] = None,
    ) -> QCTestResult:
        sample = self._repo.get_sample_for_org(
            db, tenant_ctx.organization_id, sample_id, with_results=False
        )
        if sample is None:
            raise NotFoundError("QC sample not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            sample.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/qc/samples/{sample_id}/results",
        )

        test_def = self._repo.get_test_definition_for_org(
            db, tenant_ctx.organization_id, payload.test_definition_id
        )
        if test_def is None:
            raise NotFoundError("QC test definition not found in this organization.")
        if test_def.site_id is not None and test_def.site_id != sample.site_id:
            raise ValidationError(
                "QC test definition site_id does not match QC sample site_id."
            )
        if test_def.status in {"OBSOLETE", "DRAFT"}:
            raise ConflictError(
                f"Cannot record QC result against test definition in '{test_def.status}' status.",
                error_code="INACTIVE_QC_TEST_DEFINITION",
            )

        if payload.equipment_id is not None:
            equipment = self._repo.get_equipment_for_org(
                db, tenant_ctx.organization_id, payload.equipment_id
            )
            if equipment is None:
                raise NotFoundError("Referenced equipment not found in this organization.")
            if equipment.site_id != sample.site_id:
                self._assert_site_access(
                    db,
                    tenant_ctx,
                    equipment.site_id,
                    source_ip=source_ip,
                    target_resource=f"/api/v1/qc/samples/{sample_id}/results",
                )
                raise ValidationError(
                    "Referenced equipment site_id does not match QC sample site_id.",
                    error_code="CROSS_SITE_EQUIPMENT",
                )
            if equipment.operational_status == "DECOMMISSIONED":
                raise ConflictError(
                    "Decommissioned equipment cannot be used for QC testing.",
                    error_code="EQUIPMENT_DECOMMISSIONED",
                )

        analyst_mid = payload.analyst_membership_id or tenant_ctx.membership_id
        analyst = self._get_active_membership(
            db, tenant_ctx.organization_id, analyst_mid, "Analyst"
        )

        resolved_doc_id = self._assert_document_and_version_in_org(
            db,
            tenant_ctx.organization_id,
            payload.supporting_document_id,
            payload.supporting_document_version_id,
        )

        eval_check = evaluate_qc_result_vs_specification(
            test_code=test_def.test_code,
            test_type=test_def.test_type,
            result_type=payload.result_type,
            numeric_result=payload.numeric_result,
            text_result=payload.text_result,
            boolean_result=payload.boolean_result,
            actual_unit_code=payload.unit_code,
            expected_unit_code=test_def.expected_unit_code,
            min_limit=test_def.min_limit,
            max_limit=test_def.max_limit,
            expected_text_specification=test_def.expected_text_specification,
            acceptance_criteria=test_def.acceptance_criteria,
            review_status="PENDING_REVIEW",
            require_approved_review=False,
        )

        if eval_check.outcome == "FAIL":
            result_outcome = "FAIL"
            is_oos_flagged = True
        elif eval_check.outcome == "PASS":
            result_outcome = "PASS"
            is_oos_flagged = False
        else:
            result_outcome = "UNVERIFIED"
            is_oos_flagged = False

        tested_at = payload.tested_at or datetime.now(timezone.utc)
        spec_ver = (
            payload.specification_version_applied.strip()
            if payload.specification_version_applied
            else test_def.version_number
        )

        with UnitOfWork(db):
            result = QCTestResult(
                organization_id=tenant_ctx.organization_id,
                site_id=sample.site_id,
                batch_id=sample.batch_id,
                qc_sample_id=sample.id,
                test_definition_id=test_def.id,
                specification_version_applied=spec_ver,
                result_type=payload.result_type,
                numeric_result=payload.numeric_result,
                text_result=(
                    payload.text_result.strip() if payload.text_result else None
                ),
                boolean_result=payload.boolean_result,
                unit_code=payload.unit_code.strip() if payload.unit_code else None,
                equipment_id=payload.equipment_id,
                tested_at=tested_at,
                analyst_user_id=analyst.user_id,
                analyst_membership_id=analyst.id,
                result_outcome=result_outcome,
                review_status="PENDING_REVIEW",
                is_out_of_specification_flagged=is_oos_flagged,
                supporting_document_id=resolved_doc_id,
                supporting_document_version_id=payload.supporting_document_version_id,
                comments=payload.comments,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(result)

            if sample.testing_status == "REGISTERED":
                sample.testing_status = "TESTED"
                sample.updated_by_user_id = tenant_ctx.user_id
                sample.row_version += 1

            db.flush()

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=result.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="QC_TEST_RESULT_RECORDED",
                entity_type="qc_test_results",
                entity_id=result.id,
                source_ip=source_ip,
                after_state={
                    "qc_sample_id": str(sample.id),
                    "test_definition_id": str(test_def.id),
                    "result_outcome": result.result_outcome,
                    "review_status": result.review_status,
                    "is_out_of_specification_flagged": result.is_out_of_specification_flagged,
                },
            )
        db.refresh(result)
        return result

    def review_qc_result(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        result_id: uuid.UUID,
        payload: QCTestResultReviewRequest,
        source_ip: Optional[str] = None,
    ) -> QCTestResult:
        result = self._repo.get_result_for_org(
            db, tenant_ctx.organization_id, result_id
        )
        if result is None:
            raise NotFoundError("QC test result not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            result.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/qc/results/{result_id}/review",
        )

        if result.review_status in {"APPROVED", "REJECTED"}:
            raise ConflictError(
                f"QC test result in '{result.review_status}' status is immutable and cannot be modified.",
                error_code="QC_RESULT_IMMUTABLE",
            )

        reviewer = self._get_active_membership(
            db, tenant_ctx.organization_id, tenant_ctx.membership_id, "Reviewer"
        )
        if reviewer.id == result.analyst_membership_id or (
            result.analyst_user_id is not None
            and reviewer.user_id == result.analyst_user_id
        ):
            self._audit.record_security_event(
                event_type="MAKER_CHECKER_VIOLATION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                source_ip=source_ip,
                target_resource=f"/api/v1/qc/results/{result_id}/review",
                details_json={
                    "result_id": str(result.id),
                    "analyst_membership_id": str(result.analyst_membership_id),
                    "reviewer_membership_id": str(reviewer.id),
                },
            )
            raise PermissionDeniedError(
                "Maker-checker violation: QC test result reviewer and analyst must be distinct users.",
                error_code="MAKER_CHECKER_VIOLATION",
            )

        decision = payload.resolved_decision()
        now = datetime.now(timezone.utc)

        with UnitOfWork(db):
            if payload.row_version is not None:
                verify_and_increment_version(result, payload.row_version)
            else:
                result.row_version += 1

            approval = ApprovalRecord(
                organization_id=tenant_ctx.organization_id,
                entity_type="qc_test_result",
                entity_id=result.id,
                approval_stage="QC_RESULT_REVIEW",
                decision=decision,
                approver_membership_id=reviewer.id,
                decided_at=now,
                comments=payload.comments,
            )
            db.add(approval)
            db.flush()

            before_review = result.review_status
            result.review_status = decision
            result.reviewer_user_id = reviewer.user_id
            result.reviewer_membership_id = reviewer.id
            result.reviewed_at = now
            result.approval_record_id = approval.id
            if payload.result_outcome_override is not None:
                result.result_outcome = payload.result_outcome_override
                if payload.result_outcome_override == "FAIL":
                    result.is_out_of_specification_flagged = True
            if payload.comments:
                result.comments = payload.comments
            result.updated_by_user_id = tenant_ctx.user_id

            sample = self._repo.get_sample_for_org(
                db, tenant_ctx.organization_id, result.qc_sample_id, with_results=True
            )
            if sample is not None:
                all_results = sample.results
                if all(r.review_status in {"APPROVED", "REJECTED"} for r in all_results):
                    sample.testing_status = (
                        "APPROVED"
                        if all(r.review_status == "APPROVED" for r in all_results)
                        else "REJECTED"
                    )
                    sample.updated_by_user_id = tenant_ctx.user_id
                    sample.row_version += 1

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=result.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="QC_TEST_RESULT_REVIEWED",
                entity_type="qc_test_results",
                entity_id=result.id,
                reason=payload.comments,
                source_ip=source_ip,
                before_state={"review_status": before_review},
                after_state={
                    "review_status": result.review_status,
                    "result_outcome": result.result_outcome,
                    "is_out_of_specification_flagged": result.is_out_of_specification_flagged,
                    "approval_record_id": str(approval.id),
                },
            )
        db.refresh(result)
        return result

    def get_batch_qc_history(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        batch_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> BatchQCHistoryResponse:
        batch = self._get_batch_or_404_with_site_check(
            db,
            tenant_ctx,
            batch_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/batches/{batch_id}/qc-history",
        )
        samples = self._repo.list_samples_for_batch(
            db, tenant_ctx.organization_id, batch.id, with_results=True
        )
        results = self._repo.list_results_for_batch(
            db, tenant_ctx.organization_id, batch.id
        )

        passed_count = sum(1 for r in results if r.result_outcome == "PASS")
        failed_count = sum(1 for r in results if r.result_outcome == "FAIL")
        unverified_count = sum(
            1
            for r in results
            if r.result_outcome not in {"PASS", "FAIL"}
            or r.review_status != "APPROVED"
        )
        pending_review_count = sum(
            1 for r in results if r.review_status == "PENDING_REVIEW"
        )
        approved_count = sum(1 for r in results if r.review_status == "APPROVED")
        rejected_count = sum(1 for r in results if r.review_status == "REJECTED")
        oos_count = sum(1 for r in results if r.is_out_of_specification_flagged)

        return BatchQCHistoryResponse(
            batch_id=batch.id,
            site_id=batch.site_id,
            product_id=batch.product_id,
            total_samples=len(samples),
            total_results=len(results),
            passed_count=passed_count,
            failed_count=failed_count,
            unverified_count=unverified_count,
            pending_review_count=pending_review_count,
            approved_count=approved_count,
            rejected_count=rejected_count,
            oos_flagged_count=oos_count,
            has_out_of_specification=oos_count > 0,
            samples=[QCSampleResponse.model_validate(s) for s in samples],
            results=[QCTestResultResponse.model_validate(r) for r in results],
        )

    # --- Equipment & Calibration (Module C8) ---

    def create_equipment(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        payload: EquipmentCreateRequest,
        source_ip: Optional[str] = None,
    ) -> Equipment:
        self._assert_site_access(
            db,
            tenant_ctx,
            payload.site_id,
            source_ip=source_ip,
            target_resource="/api/v1/equipment",
        )

        if payload.department_id is not None:
            dept = db.scalar(
                select(Department).where(
                    Department.organization_id == tenant_ctx.organization_id,
                    Department.id == payload.department_id,
                )
            )
            if dept is None:
                raise NotFoundError("Department not found in this organization.")
            if dept.site_id is not None and dept.site_id != payload.site_id:
                raise ValidationError("Department site_id does not match equipment site_id.")

        existing = self._repo.get_equipment_by_site_code(
            db, tenant_ctx.organization_id, payload.site_id, payload.equipment_code
        )
        if existing is not None:
            raise ConflictError(
                f"Equipment '{payload.equipment_code}' already exists for this site.",
                error_code="DUPLICATE_EQUIPMENT_CODE",
            )

        with UnitOfWork(db):
            equipment = Equipment(
                organization_id=tenant_ctx.organization_id,
                site_id=payload.site_id,
                department_id=payload.department_id,
                equipment_code=payload.equipment_code.strip().upper(),
                equipment_name=payload.equipment_name.strip(),
                description=payload.description,
                equipment_category=payload.equipment_category.strip().upper(),
                manufacturer=(
                    payload.manufacturer.strip() if payload.manufacturer else None
                ),
                model_number=(
                    payload.model_number.strip() if payload.model_number else None
                ),
                serial_number=(
                    payload.serial_number.strip() if payload.serial_number else None
                ),
                commissioned_on=payload.commissioned_on,
                operational_status=payload.operational_status,
                qualification_status=payload.qualification_status,
                calibration_required=payload.calibration_required,
                calibration_frequency_days=payload.calibration_frequency_days,
                calibration_specification_json=payload.calibration_specification_json,
                maintenance_metadata_json=payload.maintenance_metadata_json,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(equipment)
            db.flush()

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=equipment.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="EQUIPMENT_CREATED",
                entity_type="equipment",
                entity_id=equipment.id,
                source_ip=source_ip,
                after_state={
                    "equipment_code": equipment.equipment_code,
                    "equipment_category": equipment.equipment_category,
                    "operational_status": equipment.operational_status,
                    "calibration_required": equipment.calibration_required,
                },
            )
        db.refresh(equipment)
        return equipment

    def list_equipment(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        site_id: Optional[uuid.UUID] = None,
        department_id: Optional[uuid.UUID] = None,
        equipment_category: Optional[str] = None,
        operational_status: Optional[str] = None,
        qualification_status: Optional[str] = None,
        q: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Equipment], int]:
        return self._repo.list_equipment_for_tenant(
            db,
            tenant_ctx,
            site_id=site_id,
            department_id=department_id,
            equipment_category=equipment_category,
            operational_status=operational_status,
            qualification_status=qualification_status,
            q=q,
            page=page,
            page_size=page_size,
        )

    def get_equipment(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        equipment_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> Equipment:
        equipment = self._repo.get_equipment_for_org(
            db, tenant_ctx.organization_id, equipment_id
        )
        if equipment is None:
            raise NotFoundError("Equipment not found.")
        self._assert_site_access(
            db,
            tenant_ctx,
            equipment.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/equipment/{equipment_id}",
        )
        return equipment

    def update_equipment(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        equipment_id: uuid.UUID,
        payload: EquipmentUpdateRequest,
        source_ip: Optional[str] = None,
    ) -> Equipment:
        equipment = self.get_equipment(
            db,
            tenant_ctx=tenant_ctx,
            equipment_id=equipment_id,
            source_ip=source_ip,
        )

        updates = payload.model_dump(exclude_unset=True, exclude={"row_version", "reason"})
        if "department_id" in updates and updates["department_id"] is not None:
            dept = db.scalar(
                select(Department).where(
                    Department.organization_id == tenant_ctx.organization_id,
                    Department.id == updates["department_id"],
                )
            )
            if dept is None:
                raise NotFoundError("Department not found in this organization.")
            if dept.site_id is not None and dept.site_id != equipment.site_id:
                raise ValidationError("Department site_id does not match equipment site_id.")

        with UnitOfWork(db):
            if payload.row_version is not None:
                verify_and_increment_version(equipment, payload.row_version)
            else:
                equipment.row_version += 1

            before_status = equipment.operational_status
            for field, val in updates.items():
                if field == "equipment_category" and isinstance(val, str):
                    val = val.strip().upper()
                elif isinstance(val, str):
                    val = val.strip()
                setattr(equipment, field, val)

            equipment.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=equipment.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="EQUIPMENT_UPDATED",
                entity_type="equipment",
                entity_id=equipment.id,
                reason=payload.reason,
                source_ip=source_ip,
                before_state={"operational_status": before_status},
                after_state={
                    "operational_status": equipment.operational_status,
                    "qualification_status": equipment.qualification_status,
                    "row_version": equipment.row_version,
                },
            )
        db.refresh(equipment)
        return equipment

    def create_calibration_record(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        equipment_id: uuid.UUID,
        payload: CalibrationRecordCreateRequest,
        source_ip: Optional[str] = None,
    ) -> CalibrationRecord:
        equipment = self.get_equipment(
            db,
            tenant_ctx=tenant_ctx,
            equipment_id=equipment_id,
            source_ip=source_ip,
        )

        existing = self._repo.get_calibration_by_code(
            db, tenant_ctx.organization_id, equipment.id, payload.calibration_code
        )
        if existing is not None:
            raise ConflictError(
                f"Calibration record '{payload.calibration_code}' already exists for this equipment.",
                error_code="DUPLICATE_CALIBRATION_CODE",
            )

        recorder_mid = payload.recorded_by_membership_id or tenant_ctx.membership_id
        recorder = self._get_active_membership(
            db, tenant_ctx.organization_id, recorder_mid, "Recorder"
        )

        resolved_doc_id = self._assert_document_and_version_in_org(
            db,
            tenant_ctx.organization_id,
            payload.certificate_document_id,
            payload.certificate_document_version_id,
        )

        with UnitOfWork(db):
            cal = CalibrationRecord(
                organization_id=tenant_ctx.organization_id,
                site_id=equipment.site_id,
                equipment_id=equipment.id,
                calibration_code=payload.calibration_code.strip().upper(),
                calibrated_at=payload.calibrated_at,
                due_at=payload.due_at,
                calibration_provider=payload.calibration_provider.strip(),
                certificate_number=(
                    payload.certificate_number.strip()
                    if payload.certificate_number
                    else None
                ),
                certificate_document_id=resolved_doc_id,
                certificate_document_version_id=payload.certificate_document_version_id,
                calibration_result=payload.calibration_result,
                record_status=payload.record_status,
                recorded_by_user_id=recorder.user_id,
                recorded_by_membership_id=recorder.id,
                deviation_reference=(
                    payload.deviation_reference.strip()
                    if payload.deviation_reference
                    else None
                ),
                notes=payload.notes,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(cal)
            db.flush()

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=cal.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="CALIBRATION_RECORD_CREATED",
                entity_type="calibration_records",
                entity_id=cal.id,
                source_ip=source_ip,
                after_state={
                    "equipment_id": str(equipment.id),
                    "calibration_code": cal.calibration_code,
                    "calibration_result": cal.calibration_result,
                    "record_status": cal.record_status,
                },
            )
        db.refresh(cal)
        return cal

    def list_calibration_records(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        equipment_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> List[CalibrationRecord]:
        equipment = self.get_equipment(
            db,
            tenant_ctx=tenant_ctx,
            equipment_id=equipment_id,
            source_ip=source_ip,
        )
        return self._repo.list_calibrations_for_equipment(
            db, tenant_ctx.organization_id, equipment.id
        )

    def review_calibration_record(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        equipment_id: uuid.UUID,
        calibration_id: uuid.UUID,
        payload: CalibrationRecordReviewRequest,
        source_ip: Optional[str] = None,
    ) -> CalibrationRecord:
        equipment = self.get_equipment(
            db,
            tenant_ctx=tenant_ctx,
            equipment_id=equipment_id,
            source_ip=source_ip,
        )
        cal = self._repo.get_calibration_for_org(
            db, tenant_ctx.organization_id, equipment.id, calibration_id
        )
        if cal is None:
            raise NotFoundError("Calibration record not found for this equipment.")

        if cal.record_status in {"APPROVED", "REJECTED", "SUPERSEDED"}:
            raise ConflictError(
                f"Calibration record in '{cal.record_status}' status is immutable and cannot be modified.",
                error_code="CALIBRATION_RECORD_IMMUTABLE",
            )

        reviewer = self._get_active_membership(
            db, tenant_ctx.organization_id, tenant_ctx.membership_id, "Reviewer"
        )
        if reviewer.id == cal.recorded_by_membership_id or (
            cal.recorded_by_user_id is not None
            and reviewer.user_id == cal.recorded_by_user_id
        ):
            self._audit.record_security_event(
                event_type="MAKER_CHECKER_VIOLATION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.HIGH.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                source_ip=source_ip,
                target_resource=f"/api/v1/equipment/{equipment_id}/calibrations/{calibration_id}/review",
                details_json={
                    "equipment_id": str(equipment.id),
                    "calibration_id": str(cal.id),
                    "recorded_by_membership_id": str(cal.recorded_by_membership_id),
                    "reviewed_by_membership_id": str(reviewer.id),
                },
            )
            raise PermissionDeniedError(
                "Maker-checker violation: calibration record reviewer and recorder must be distinct users.",
                error_code="MAKER_CHECKER_VIOLATION",
            )

        decision = payload.resolved_decision()
        now = datetime.now(timezone.utc)
        comment_text = payload.comments or payload.notes

        with UnitOfWork(db):
            if payload.row_version is not None:
                verify_and_increment_version(cal, payload.row_version)
            else:
                cal.row_version += 1

            approval = ApprovalRecord(
                organization_id=tenant_ctx.organization_id,
                entity_type="calibration_record",
                entity_id=cal.id,
                approval_stage="CALIBRATION_REVIEW",
                decision=decision,
                approver_membership_id=reviewer.id,
                decided_at=now,
                comments=comment_text,
            )
            db.add(approval)
            db.flush()

            before_status = cal.record_status
            cal.record_status = decision
            cal.reviewed_by_user_id = reviewer.user_id
            cal.reviewed_by_membership_id = reviewer.id
            cal.approved_at = now if decision == "APPROVED" else None
            cal.approval_record_id = approval.id
            if payload.deviation_reference is not None:
                cal.deviation_reference = payload.deviation_reference.strip()
            if comment_text:
                cal.notes = comment_text
            cal.updated_by_user_id = tenant_ctx.user_id

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=cal.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="CALIBRATION_RECORD_REVIEWED",
                entity_type="calibration_records",
                entity_id=cal.id,
                reason=comment_text,
                source_ip=source_ip,
                before_state={"record_status": before_status},
                after_state={
                    "record_status": cal.record_status,
                    "approval_record_id": str(approval.id),
                },
            )
        db.refresh(cal)
        return cal

    def evaluate_calibration_validity(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        equipment_id: uuid.UUID,
        at: datetime,
    ) -> CalibrationValidityResponse:
        return evaluate_calibration_validity(
            db,
            organization_id=organization_id,
            equipment_id=equipment_id,
            at=at,
            repo=self._repo,
        )

    def get_equipment_calibration_validity(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        equipment_id: uuid.UUID,
        at: Optional[datetime] = None,
        source_ip: Optional[str] = None,
    ) -> CalibrationValidityResponse:
        equipment = self.get_equipment(
            db,
            tenant_ctx=tenant_ctx,
            equipment_id=equipment_id,
            source_ip=source_ip,
        )
        eval_at = at or datetime.now(timezone.utc)
        return self.evaluate_calibration_validity(
            db,
            organization_id=tenant_ctx.organization_id,
            equipment_id=equipment.id,
            at=eval_at,
        )

    # --- Equipment Usages (Module C8) ---

    def record_equipment_usage(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        execution_record_id: uuid.UUID,
        payload: EquipmentUsageCreateRequest,
        source_ip: Optional[str] = None,
    ) -> EquipmentUsage:
        from app.models.domain.manufacturing_execution import (
            BatchExecutionRecord,
            BatchExecutionStep,
        )

        exec_record = db.scalar(
            select(BatchExecutionRecord).where(
                BatchExecutionRecord.organization_id == tenant_ctx.organization_id,
                BatchExecutionRecord.id == execution_record_id,
            )
        )
        if exec_record is None:
            raise NotFoundError("Batch execution record not found.")

        self._assert_site_access(
            db,
            tenant_ctx,
            exec_record.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/execution-records/{execution_record_id}/equipment-usage",
        )

        if exec_record.execution_status in {"VERIFIED", "APPROVED", "VOIDED"}:
            raise ConflictError(
                f"Batch execution record in '{exec_record.execution_status}' status is immutable.",
                error_code="EXECUTION_RECORD_IMMUTABLE",
            )

        equipment = self._repo.get_equipment_for_org(
            db, tenant_ctx.organization_id, payload.equipment_id
        )
        if equipment is None:
            raise NotFoundError("Equipment not found in this organization.")

        if equipment.site_id != exec_record.site_id:
            self._assert_site_access(
                db,
                tenant_ctx,
                equipment.site_id,
                source_ip=source_ip,
                target_resource=f"/api/v1/execution-records/{execution_record_id}/equipment-usage",
            )
            raise ValidationError(
                "Equipment site_id does not match batch execution record site_id.",
                error_code="CROSS_SITE_EQUIPMENT_USAGE",
            )

        if payload.batch_execution_step_id is not None:
            step = db.scalar(
                select(BatchExecutionStep).where(
                    BatchExecutionStep.organization_id == tenant_ctx.organization_id,
                    BatchExecutionStep.id == payload.batch_execution_step_id,
                )
            )
            if step is None or step.batch_execution_record_id != exec_record.id:
                raise NotFoundError(
                    "Referenced batch execution step not found on this execution record."
                )

        operator_mid = payload.operator_membership_id or tenant_ctx.membership_id
        operator = self._get_active_membership(
            db, tenant_ctx.organization_id, operator_mid, "Operator"
        )

        resolved_doc_id = self._assert_document_and_version_in_org(
            db,
            tenant_ctx.organization_id,
            payload.supporting_document_id,
            payload.supporting_document_version_id,
        )

        cal_validity = self.evaluate_calibration_validity(
            db,
            organization_id=tenant_ctx.organization_id,
            equipment_id=equipment.id,
            at=payload.usage_start_at,
        )

        with UnitOfWork(db):
            usage = EquipmentUsage(
                organization_id=tenant_ctx.organization_id,
                site_id=exec_record.site_id,
                equipment_id=equipment.id,
                batch_id=exec_record.batch_id,
                batch_execution_record_id=exec_record.id,
                batch_execution_step_id=payload.batch_execution_step_id,
                usage_start_at=payload.usage_start_at,
                usage_end_at=payload.usage_end_at,
                operator_user_id=operator.user_id,
                operator_membership_id=operator.id,
                usage_purpose=payload.usage_purpose.strip(),
                status_at_time_of_use=(
                    payload.status_at_time_of_use.strip()
                    if payload.status_at_time_of_use
                    else equipment.operational_status
                ),
                calibration_status_at_use=cal_validity.validity_status,
                applicable_calibration_record_id=cal_validity.applicable_calibration_record_id,
                supporting_document_id=resolved_doc_id,
                supporting_document_version_id=payload.supporting_document_version_id,
                notes=payload.notes,
                created_by_user_id=tenant_ctx.user_id,
                updated_by_user_id=tenant_ctx.user_id,
            )
            db.add(usage)
            db.flush()

            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=usage.site_id,
                actor_type=ActorType.USER.value,
                actor_id=tenant_ctx.user_id,
                action="EQUIPMENT_USAGE_RECORDED",
                entity_type="equipment_usages",
                entity_id=usage.id,
                source_ip=source_ip,
                after_state={
                    "equipment_id": str(equipment.id),
                    "batch_execution_record_id": str(exec_record.id),
                    "calibration_status_at_use": usage.calibration_status_at_use,
                    "applicable_calibration_record_id": (
                        str(usage.applicable_calibration_record_id)
                        if usage.applicable_calibration_record_id
                        else None
                    ),
                },
            )
        db.refresh(usage)
        return usage

    def list_equipment_usages_for_execution_record(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        execution_record_id: uuid.UUID,
        source_ip: Optional[str] = None,
    ) -> List[EquipmentUsage]:
        from app.models.domain.manufacturing_execution import BatchExecutionRecord

        exec_record = db.scalar(
            select(BatchExecutionRecord).where(
                BatchExecutionRecord.organization_id == tenant_ctx.organization_id,
                BatchExecutionRecord.id == execution_record_id,
            )
        )
        if exec_record is None:
            raise NotFoundError("Batch execution record not found.")

        self._assert_site_access(
            db,
            tenant_ctx,
            exec_record.site_id,
            source_ip=source_ip,
            target_resource=f"/api/v1/execution-records/{execution_record_id}/equipment-usage",
        )
        return self._repo.list_usages_for_execution_record(
            db, tenant_ctx.organization_id, exec_record.id
        )
