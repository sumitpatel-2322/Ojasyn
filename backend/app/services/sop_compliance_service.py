import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import UnitOfWork
from app.core.deps import TenantContext
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.domain.enums import (
    ActorType,
    DeviationClassification,
    DeviationSourceType,
    EvidenceSourceType,
    SOPComplianceResultStatus,
    SecurityEventOutcome,
    SecurityEventSeverity,
)
from app.domain.rules_engine import RULE_ENGINE_VERSION
from app.models.domain.documents import Document, DocumentVersion
from app.models.domain.investigations import (
    EvidenceReference,
    Investigation,
    SOPComplianceResult,
)
from app.models.domain.manufacturing_execution import (
    BatchExecutionRecord,
    BatchExecutionStep,
    DeterministicEvidenceCheck,
    ProcessParameterObservation,
)
from app.models.domain.qc_equipment import CalibrationRecord, QCTestResult
from app.models.domain.sops import SOPRevision, SOPStep
from app.models.schemas.manufacturing_execution import DeterministicEvidenceCheckResponse
from app.repositories.investigation_repo import InvestigationRepository
from app.services.audit_service import AuditService
from app.services.manufacturing_execution_service import ManufacturingExecutionService
from app.services.qc_equipment_service import QCEquipmentService


def _build_suggested_deviation_payload(
    *,
    investigation: Investigation,
    batch_id: Optional[uuid.UUID],
    check: Union[DeterministicEvidenceCheck, DeterministicEvidenceCheckResponse],
) -> Dict[str, Any]:
    classification = DeviationClassification.MAJOR.value
    if check.check_type in {"EQUIPMENT_CALIBRATION_VALIDITY", "QC_RESULT_VS_SPECIFICATION"}:
        classification = DeviationClassification.CRITICAL.value
    return {
        "site_id": str(investigation.site_id) if investigation.site_id else None,
        "source_type": DeviationSourceType.SOP_COMPLIANCE_RESULT.value,
        "classification": classification,
        "title": f"SOP Compliance Failure: {check.rule_identifier}",
        "description": (
            f"Deterministic rule '{check.rule_identifier}' (v{check.rule_version}) returned FAIL "
            f"during investigation {investigation.investigation_number}: {check.explanation}"
        ),
        "complaint_id": str(investigation.complaint_id) if investigation.complaint_id else None,
        "investigation_id": str(investigation.id),
        "batch_id": str(batch_id) if batch_id else None,
        "batch_execution_step_id": (
            str(check.batch_execution_step_id) if check.batch_execution_step_id else None
        ),
        "qc_test_result_id": str(check.qc_test_result_id) if check.qc_test_result_id else None,
        "immediate_containment": (
            "Review batch disposition and quarantine affected material/equipment pending QA evaluation."
        ),
        "impact_assessment": check.explanation,
        "auto_created": False,
    }


class SOPComplianceService:
    """
    Deterministic SOP, Manufacturing Execution, QC & Calibration Compliance Engine (`D3`).
    Consumes Phase 4 `rules_engine.py`, `SOPResolver`, `ManufacturingExecutionService`, and `QCEquipmentService`
    with ZERO LLM involvement. FAIL results suggest (never auto-create) Deviation drafts.
    """

    def __init__(
        self,
        repo: Optional[InvestigationRepository] = None,
        mfg_service: Optional[ManufacturingExecutionService] = None,
        qc_service: Optional[QCEquipmentService] = None,
        audit_service: Optional[AuditService] = None,
    ) -> None:
        self._repo = repo or InvestigationRepository()
        self._mfg = mfg_service or ManufacturingExecutionService()
        self._qc = qc_service or QCEquipmentService()
        self._audit = audit_service or AuditService()

    def _assert_site_access(
        self,
        tenant_ctx: TenantContext,
        site_id: Optional[uuid.UUID],
        *,
        target_resource: str,
    ) -> None:
        if site_id is not None and not tenant_ctx.is_org_wide and site_id not in tenant_ctx.allowed_site_ids:
            self._audit.record_security_event(
                event_type="SITE_SCOPE_VIOLATION",
                outcome=SecurityEventOutcome.DENIED.value,
                severity=SecurityEventSeverity.WARNING.value,
                organization_id=tenant_ctx.organization_id,
                user_id=tenant_ctx.user_id,
                target_resource=target_resource,
                details_json={"site_id": str(site_id)},
            )
            raise PermissionDeniedError(
                "Not authorized to access records for this site.",
                error_code="SITE_SCOPE_DENIED",
            )

    def _ensure_evidence_ref_for_check(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        investigation_id: uuid.UUID,
        run_id: Optional[uuid.UUID],
        check: Union[DeterministicEvidenceCheck, DeterministicEvidenceCheckResponse],
        existing_by_source: Dict[Tuple[str, uuid.UUID], EvidenceReference],
        user_id: Optional[uuid.UUID],
    ) -> EvidenceReference:
        key = (check.source_entity_type, check.source_entity_id)
        if key in existing_by_source:
            return existing_by_source[key]

        source_type = EvidenceSourceType.DETERMINISTIC_EVIDENCE_CHECK.value
        row_version: Optional[int] = None
        doc_id: Optional[uuid.UUID] = None
        doc_ver_id: Optional[uuid.UUID] = None
        checksum: Optional[str] = None
        page_num: Optional[int] = None
        section_ref: Optional[str] = None
        field_path: Optional[str] = None
        quoted_val: Optional[str] = check.actual_value_text

        if check.source_entity_type == "process_parameter_observations":
            source_type = EvidenceSourceType.PROCESS_PARAMETER_OBSERVATION.value
            obs = db.scalar(
                select(ProcessParameterObservation).where(
                    ProcessParameterObservation.organization_id == organization_id,
                    ProcessParameterObservation.id == check.source_entity_id,
                )
            )
            if obs is not None:
                row_version = obs.row_version
                doc_id = obs.source_document_id
                doc_ver_id = obs.source_document_version_id
                page_num = obs.source_page_number
                section_ref = obs.source_section_ref
                field_path = f"observation.{obs.parameter_code}"
                if quoted_val is None:
                    quoted_val = (
                        f"{obs.numeric_value} {obs.unit_code or ''}".strip()
                        if obs.numeric_value is not None
                        else (obs.text_value or str(obs.boolean_value))
                    )
        elif check.source_entity_type == "batch_execution_steps":
            source_type = EvidenceSourceType.BATCH_EXECUTION_STEP.value
            step = db.scalar(
                select(BatchExecutionStep).where(
                    BatchExecutionStep.organization_id == organization_id,
                    BatchExecutionStep.id == check.source_entity_id,
                )
            )
            if step is not None:
                row_version = step.row_version
                doc_ver_id = step.supporting_document_version_id
                field_path = f"execution_step.{step.step_code}.status"
                quoted_val = quoted_val or step.step_status
        elif check.source_entity_type == "batch_execution_records":
            source_type = EvidenceSourceType.BATCH_EXECUTION_RECORD.value
            rec = db.scalar(
                select(BatchExecutionRecord).where(
                    BatchExecutionRecord.organization_id == organization_id,
                    BatchExecutionRecord.id == check.source_entity_id,
                )
            )
            if rec is not None:
                row_version = rec.row_version
                doc_id = rec.primary_document_id
                doc_ver_id = rec.primary_document_version_id
                field_path = f"execution_record.{rec.execution_code}"
                quoted_val = quoted_val or rec.execution_status
        elif check.source_entity_type == "qc_test_results":
            source_type = EvidenceSourceType.QC_TEST_RESULT.value
            qcr = db.scalar(
                select(QCTestResult).where(
                    QCTestResult.organization_id == organization_id,
                    QCTestResult.id == check.source_entity_id,
                )
            )
            if qcr is not None:
                row_version = qcr.row_version
                doc_id = qcr.supporting_document_id
                doc_ver_id = qcr.supporting_document_version_id
                field_path = f"qc_test_result.{qcr.id}"
        elif check.source_entity_type == "equipment_usages":
            source_type = EvidenceSourceType.EQUIPMENT_USAGE.value
            field_path = "equipment_usage.calibration_validity"

        if doc_ver_id is not None:
            ver = db.get(DocumentVersion, doc_ver_id)
            if ver is not None:
                checksum = ver.checksum_sha256
                if doc_id is None:
                    parent_doc = db.scalar(
                        select(Document).where(
                            Document.organization_id == organization_id,
                            Document.id == ver.document_id,
                        )
                    )
                    if parent_doc is not None:
                        doc_id = parent_doc.id

        ev = EvidenceReference(
            organization_id=organization_id,
            investigation_id=investigation_id,
            investigation_run_id=run_id,
            source_type=source_type,
            source_table=check.source_entity_type,
            source_record_id=check.source_entity_id,
            source_row_version=row_version,
            document_id=doc_id,
            document_version_id=doc_ver_id,
            document_checksum_sha256=checksum,
            page_number=page_num,
            section_ref=section_ref,
            field_path=field_path,
            quoted_value=quoted_val,
            provenance_metadata_json={
                "rule_identifier": check.rule_identifier,
                "rule_version": check.rule_version,
                "check_type": check.check_type,
            },
            collected_by=ActorType.USER.value if user_id else ActorType.SYSTEM_WORKER.value,
            created_by_user_id=user_id,
        )
        db.add(ev)
        db.flush()
        existing_by_source[key] = ev
        return ev

    def evaluate_in_tx(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        investigation: Investigation,
        batch_id: Optional[uuid.UUID] = None,
        run_id: Optional[uuid.UUID] = None,
    ) -> List[SOPComplianceResult]:
        """
        Runs deterministic SOP, execution, QC, and calibration checks inside the caller's active transaction.
        Never calls any LLM. Appends immutable `SOPComplianceResult` rows linked to persisted `EvidenceReference` IDs.
        """
        org_id = tenant_ctx.organization_id
        if run_id is not None:
            run_obj = self._repo.get_run_for_org(db, org_id, run_id)
            if run_obj is None or run_obj.investigation_id != investigation.id:
                raise NotFoundError("InvestigationRun not found for this investigation.")
        target_batch_id = batch_id or investigation.batch_id

        now = datetime.now(timezone.utc)
        existing_ev = self._repo.list_evidence_for_investigation(db, org_id, investigation.id)
        existing_by_source: Dict[Tuple[str, uuid.UUID], EvidenceReference] = {
            (ev.source_table, ev.source_record_id): ev for ev in existing_ev
        }

        if target_batch_id is None:
            # Persist explicit MISSING compliance result when no batch is resolved yet
            inv_ev = existing_by_source.get(("investigations", investigation.id))
            if inv_ev is None:
                inv_ev = EvidenceReference(
                    organization_id=org_id,
                    investigation_id=investigation.id,
                    investigation_run_id=run_id,
                    source_type=EvidenceSourceType.OTHER.value,
                    source_table="investigations",
                    source_record_id=investigation.id,
                    source_row_version=investigation.row_version,
                    field_path="investigation.batch_id",
                    quoted_value="NONE",
                    collected_by=ActorType.USER.value if tenant_ctx.user_id else ActorType.SYSTEM_WORKER.value,
                    created_by_user_id=tenant_ctx.user_id,
                )
                db.add(inv_ev)
                db.flush()

            missing_row = SOPComplianceResult(
                organization_id=org_id,
                investigation_id=investigation.id,
                investigation_run_id=run_id,
                batch_id=None,
                evidence_ref_id=inv_ev.id,
                check_type="BATCH_LINKAGE_PREREQUISITE",
                rule_identifier="RULE_BATCH_REQUIRED_FOR_SOP_CHECK",
                rule_version=RULE_ENGINE_VERSION,
                status=SOPComplianceResultStatus.MISSING.value,
                actual_value_text="NO_LINKED_BATCH",
                expected_requirement_text="Resolved manufacturing batch linked to investigation",
                explanation="No batch is linked to this investigation; SOP/BMR/QC/calibration compliance cannot be evaluated.",
                deviation_suggested=False,
                evaluated_at=now,
                created_by_user_id=tenant_ctx.user_id,
            )
            db.add(missing_row)
            db.flush()
            return [missing_row]

        # Execute Phase 4 deterministic evidence & rules engine evaluation.
        # `SOPComplianceResult` stores `deterministic_check_id=None` so future calls to
        # `get_batch_manufacturing_evidence(..., persist_checks=True)` can refresh
        # `deterministic_evidence_checks` without hitting `ON DELETE RESTRICT`.
        self._mfg.get_batch_manufacturing_evidence(
            db,
            tenant_ctx=tenant_ctx,
            batch_id=target_batch_id,
            persist_checks=True,
        )
        checks = self._mfg.list_deterministic_checks_for_batch(
            db,
            tenant_ctx=tenant_ctx,
            batch_id=target_batch_id,
        )

        # Also evaluate any calibration records referenced directly by QC results without execution records
        # (already covered by get_batch_manufacturing_evidence; map sop_id for each sop_revision_id)
        sop_id_by_rev: Dict[uuid.UUID, uuid.UUID] = {}
        rev_ids = {c.sop_revision_id for c in checks if c.sop_revision_id is not None}
        if rev_ids:
            for rev in db.scalars(
                select(SOPRevision).where(
                    SOPRevision.organization_id == org_id,
                    SOPRevision.id.in_(rev_ids),
                )
            ).all():
                sop_id_by_rev[rev.id] = rev.sop_id

        persisted_results: List[SOPComplianceResult] = []
        if not checks:
            # Batch exists but has no execution records, observations, or QC results yet
            batch_ev = existing_by_source.get(("batches", target_batch_id))
            if batch_ev is None:
                batch_ev = EvidenceReference(
                    organization_id=org_id,
                    investigation_id=investigation.id,
                    investigation_run_id=run_id,
                    source_type=EvidenceSourceType.BATCH.value,
                    source_table="batches",
                    source_record_id=target_batch_id,
                    field_path="batch.execution_evidence",
                    quoted_value="NO_EXECUTION_OR_QC_RECORDS",
                    collected_by=ActorType.USER.value if tenant_ctx.user_id else ActorType.SYSTEM_WORKER.value,
                    created_by_user_id=tenant_ctx.user_id,
                )
                db.add(batch_ev)
                db.flush()
            empty_row = SOPComplianceResult(
                organization_id=org_id,
                investigation_id=investigation.id,
                investigation_run_id=run_id,
                batch_id=target_batch_id,
                evidence_ref_id=batch_ev.id,
                check_type="BATCH_EVIDENCE_COMPLETENESS",
                rule_identifier="RULE_BATCH_EVIDENCE_PRESENT",
                rule_version=RULE_ENGINE_VERSION,
                status=SOPComplianceResultStatus.MISSING.value,
                actual_value_text="NO_RECORDS",
                expected_requirement_text="Executed BMR/BPR and/or QC records for batch",
                explanation="Linked batch has no recorded execution records, parameter observations, or QC test results.",
                deviation_suggested=False,
                evaluated_at=now,
                created_by_user_id=tenant_ctx.user_id,
            )
            db.add(empty_row)
            db.flush()
            return [empty_row]

        for chk in checks:
            ev_ref = self._ensure_evidence_ref_for_check(
                db,
                organization_id=org_id,
                investigation_id=investigation.id,
                run_id=run_id,
                check=chk,
                existing_by_source=existing_by_source,
                user_id=tenant_ctx.user_id,
            )
            is_fail = chk.outcome == SOPComplianceResultStatus.FAIL.value
            suggested_payload = (
                _build_suggested_deviation_payload(
                    investigation=investigation,
                    batch_id=target_batch_id,
                    check=chk,
                )
                if is_fail
                else None
            )
            row = SOPComplianceResult(
                organization_id=org_id,
                investigation_id=investigation.id,
                investigation_run_id=run_id,
                batch_id=target_batch_id,
                sop_id=sop_id_by_rev.get(chk.sop_revision_id) if chk.sop_revision_id else None,
                sop_revision_id=chk.sop_revision_id,
                sop_step_id=chk.sop_step_id,
                deterministic_check_id=None,
                batch_execution_record_id=chk.batch_execution_record_id,
                batch_execution_step_id=chk.batch_execution_step_id,
                observation_id=chk.observation_id,
                equipment_id=chk.equipment_id,
                calibration_record_id=chk.calibration_record_id,
                qc_test_result_id=chk.qc_test_result_id,
                evidence_ref_id=ev_ref.id,
                check_type=chk.check_type,
                rule_identifier=chk.rule_identifier,
                rule_version=chk.rule_version,
                status=chk.outcome,
                actual_value_text=chk.actual_value_text,
                actual_numeric_value=chk.actual_numeric_value,
                expected_requirement_text=chk.expected_requirement_text,
                expected_min_value=chk.expected_min_value,
                expected_max_value=chk.expected_max_value,
                unit_code=chk.unit_code,
                explanation=chk.explanation,
                deviation_suggested=is_fail,
                suggested_deviation_payload_json=suggested_payload,
                evaluated_at=chk.evaluated_at or now,
                created_by_user_id=tenant_ctx.user_id,
            )
            db.add(row)
            persisted_results.append(row)

        db.flush()
        return persisted_results

    def run_compliance_check(
        self,
        db: Session,
        *,
        tenant_ctx: TenantContext,
        investigation_id: uuid.UUID,
        batch_id: Optional[uuid.UUID] = None,
        run_id: Optional[uuid.UUID] = None,
    ) -> List[SOPComplianceResult]:
        """Public command endpoint handler for deterministic SOP compliance evaluation."""
        investigation = self._repo.get_investigation_for_org(
            db, tenant_ctx.organization_id, investigation_id
        )
        if investigation is None:
            raise NotFoundError("Investigation not found.")
        self._assert_site_access(
            tenant_ctx,
            investigation.site_id,
            target_resource=f"/api/v1/investigations/{investigation_id}/compliance-checks",
        )
        with UnitOfWork(db):
            results = self.evaluate_in_tx(
                db,
                tenant_ctx=tenant_ctx,
                investigation=investigation,
                batch_id=batch_id,
                run_id=run_id,
            )
            self._audit.record(
                db,
                organization_id=tenant_ctx.organization_id,
                site_id=investigation.site_id,
                actor_type=ActorType.USER.value if tenant_ctx.user_id else ActorType.SYSTEM_WORKER.value,
                actor_id=tenant_ctx.user_id,
                action="SOP_COMPLIANCE_EVALUATED",
                entity_type="investigations",
                entity_id=investigation.id,
                after_state={
                    "total_results": len(results),
                    "fail_count": sum(1 for r in results if r.status == "FAIL"),
                    "missing_count": sum(1 for r in results if r.status == "MISSING"),
                },
            )
        return results
