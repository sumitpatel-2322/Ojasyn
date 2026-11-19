import uuid
from typing import List, Optional, Set, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.domain.enums import InvestigationRunStatus
from app.models.domain.investigations import (
    EvidenceReference,
    GeneratedReport,
    Investigation,
    InvestigationFinding,
    InvestigationRun,
    InvestigationStep,
    RootCauseHypothesis,
    SOPComplianceResult,
)
from app.repositories.base import apply_tenant_filter, paginate


class InvestigationRepository:
    """Tenant-scoped data access for Investigations, Runs, Steps, Evidence, Compliance, Findings, Hypotheses & Reports."""

    def get_investigation_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        investigation_id: uuid.UUID,
        *,
        with_children: bool = False,
        for_update: bool = False,
    ) -> Optional[Investigation]:
        stmt = select(Investigation).where(
            Investigation.organization_id == organization_id,
            Investigation.id == investigation_id,
        )
        if with_children:
            stmt = stmt.options(
                selectinload(Investigation.runs).selectinload(InvestigationRun.steps),
                selectinload(Investigation.evidence_references),
                selectinload(Investigation.compliance_results),
                selectinload(Investigation.findings),
                selectinload(Investigation.hypotheses),
                selectinload(Investigation.reports),
            )
        if for_update:
            stmt = stmt.with_for_update()
        return db.scalar(stmt)

    def list_investigations_for_tenant(
        self,
        db: Session,
        tenant_ctx: object,
        *,
        complaint_id: Optional[uuid.UUID] = None,
        deviation_id: Optional[uuid.UUID] = None,
        batch_id: Optional[uuid.UUID] = None,
        status: Optional[str] = None,
        site_id: Optional[uuid.UUID] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Investigation], int]:
        stmt = select(Investigation)
        stmt = apply_tenant_filter(stmt, Investigation, tenant_ctx, allow_org_wide_site_null=True)
        if complaint_id is not None:
            stmt = stmt.where(Investigation.complaint_id == complaint_id)
        if deviation_id is not None:
            stmt = stmt.where(Investigation.deviation_id == deviation_id)
        if batch_id is not None:
            stmt = stmt.where(Investigation.batch_id == batch_id)
        if status:
            stmt = stmt.where(Investigation.status == status.strip().upper())
        if site_id is not None:
            stmt = stmt.where(Investigation.site_id == site_id)
        stmt = stmt.order_by(Investigation.created_at.desc())
        return paginate(db, stmt, page=page, page_size=page_size)

    def list_investigations_for_complaint(
        self,
        db: Session,
        organization_id: uuid.UUID,
        complaint_id: uuid.UUID,
    ) -> List[Investigation]:
        return list(
            db.scalars(
                select(Investigation)
                .where(
                    Investigation.organization_id == organization_id,
                    Investigation.complaint_id == complaint_id,
                )
                .order_by(Investigation.created_at.asc())
            ).all()
        )

    def get_run_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        *,
        investigation_id: Optional[uuid.UUID] = None,
        with_steps: bool = False,
        for_update: bool = False,
    ) -> Optional[InvestigationRun]:
        stmt = select(InvestigationRun).where(
            InvestigationRun.organization_id == organization_id,
            InvestigationRun.id == run_id,
        )
        if investigation_id is not None:
            stmt = stmt.where(InvestigationRun.investigation_id == investigation_id)
        if with_steps:
            stmt = stmt.options(selectinload(InvestigationRun.steps))
        if for_update:
            stmt = stmt.with_for_update()
        return db.scalar(stmt)

    def get_run_by_idempotency(
        self,
        db: Session,
        organization_id: uuid.UUID,
        idempotency_key: str,
    ) -> Optional[InvestigationRun]:
        return db.scalar(
            select(InvestigationRun).where(
                InvestigationRun.organization_id == organization_id,
                InvestigationRun.idempotency_key == idempotency_key,
            )
        )

    def get_next_run_number(
        self,
        db: Session,
        organization_id: uuid.UUID,
        investigation_id: uuid.UUID,
    ) -> int:
        max_num = db.scalar(
            select(func.max(InvestigationRun.run_number)).where(
                InvestigationRun.organization_id == organization_id,
                InvestigationRun.investigation_id == investigation_id,
            )
        )
        return int(max_num or 0) + 1

    def list_waiting_runs_for_complaint(
        self,
        db: Session,
        organization_id: uuid.UUID,
        complaint_id: uuid.UUID,
    ) -> List[InvestigationRun]:
        return list(
            db.scalars(
                select(InvestigationRun)
                .join(Investigation, Investigation.id == InvestigationRun.investigation_id)
                .where(
                    InvestigationRun.organization_id == organization_id,
                    Investigation.organization_id == organization_id,
                    Investigation.complaint_id == complaint_id,
                    InvestigationRun.status == InvestigationRunStatus.WAITING_FOR_HUMAN.value,
                )
                .order_by(InvestigationRun.created_at.asc())
            ).all()
        )

    def list_steps_for_run(
        self,
        db: Session,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> List[InvestigationStep]:
        return list(
            db.scalars(
                select(InvestigationStep)
                .where(
                    InvestigationStep.organization_id == organization_id,
                    InvestigationStep.investigation_run_id == run_id,
                )
                .order_by(InvestigationStep.step_seq.asc())
            ).all()
        )

    def get_step_by_run_seq(
        self,
        db: Session,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        step_seq: int,
    ) -> Optional[InvestigationStep]:
        return db.scalar(
            select(InvestigationStep).where(
                InvestigationStep.organization_id == organization_id,
                InvestigationStep.investigation_run_id == run_id,
                InvestigationStep.step_seq == step_seq,
            )
        )

    def list_evidence_for_investigation(
        self,
        db: Session,
        organization_id: uuid.UUID,
        investigation_id: uuid.UUID,
    ) -> List[EvidenceReference]:
        return list(
            db.scalars(
                select(EvidenceReference)
                .where(
                    EvidenceReference.organization_id == organization_id,
                    EvidenceReference.investigation_id == investigation_id,
                )
                .order_by(EvidenceReference.created_at.asc())
            ).all()
        )

    def get_evidence_ids_for_investigation(
        self,
        db: Session,
        organization_id: uuid.UUID,
        investigation_id: uuid.UUID,
    ) -> Set[uuid.UUID]:
        rows = db.scalars(
            select(EvidenceReference.id).where(
                EvidenceReference.organization_id == organization_id,
                EvidenceReference.investigation_id == investigation_id,
            )
        ).all()
        return set(rows)

    def list_compliance_results_for_investigation(
        self,
        db: Session,
        organization_id: uuid.UUID,
        investigation_id: uuid.UUID,
    ) -> List[SOPComplianceResult]:
        return list(
            db.scalars(
                select(SOPComplianceResult)
                .where(
                    SOPComplianceResult.organization_id == organization_id,
                    SOPComplianceResult.investigation_id == investigation_id,
                )
                .order_by(SOPComplianceResult.created_at.asc())
            ).all()
        )

    def get_compliance_result_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        compliance_result_id: uuid.UUID,
    ) -> Optional[SOPComplianceResult]:
        return db.scalar(
            select(SOPComplianceResult).where(
                SOPComplianceResult.organization_id == organization_id,
                SOPComplianceResult.id == compliance_result_id,
            )
        )

    def list_findings_for_investigation(
        self,
        db: Session,
        organization_id: uuid.UUID,
        investigation_id: uuid.UUID,
    ) -> List[InvestigationFinding]:
        return list(
            db.scalars(
                select(InvestigationFinding)
                .where(
                    InvestigationFinding.organization_id == organization_id,
                    InvestigationFinding.investigation_id == investigation_id,
                )
                .order_by(InvestigationFinding.created_at.asc())
            ).all()
        )

    def list_hypotheses_for_investigation(
        self,
        db: Session,
        organization_id: uuid.UUID,
        investigation_id: uuid.UUID,
    ) -> List[RootCauseHypothesis]:
        return list(
            db.scalars(
                select(RootCauseHypothesis)
                .where(
                    RootCauseHypothesis.organization_id == organization_id,
                    RootCauseHypothesis.investigation_id == investigation_id,
                )
                .order_by(RootCauseHypothesis.created_at.asc())
            ).all()
        )

    def get_hypothesis_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        hypothesis_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> Optional[RootCauseHypothesis]:
        stmt = select(RootCauseHypothesis).where(
            RootCauseHypothesis.organization_id == organization_id,
            RootCauseHypothesis.id == hypothesis_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return db.scalar(stmt)

    def list_reports_for_investigation(
        self,
        db: Session,
        organization_id: uuid.UUID,
        investigation_id: uuid.UUID,
    ) -> List[GeneratedReport]:
        return list(
            db.scalars(
                select(GeneratedReport)
                .where(
                    GeneratedReport.organization_id == organization_id,
                    GeneratedReport.investigation_id == investigation_id,
                )
                .order_by(GeneratedReport.version_number.asc())
            ).all()
        )

    def get_report_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        report_id: uuid.UUID,
        *,
        investigation_id: Optional[uuid.UUID] = None,
        for_update: bool = False,
    ) -> Optional[GeneratedReport]:
        stmt = select(GeneratedReport).where(
            GeneratedReport.organization_id == organization_id,
            GeneratedReport.id == report_id,
        )
        if investigation_id is not None:
            stmt = stmt.where(GeneratedReport.investigation_id == investigation_id)
        if for_update:
            stmt = stmt.with_for_update()
        return db.scalar(stmt)

    def get_next_report_version(
        self,
        db: Session,
        organization_id: uuid.UUID,
        investigation_id: uuid.UUID,
    ) -> int:
        max_ver = db.scalar(
            select(func.max(GeneratedReport.version_number)).where(
                GeneratedReport.organization_id == organization_id,
                GeneratedReport.investigation_id == investigation_id,
            )
        )
        return int(max_ver or 0) + 1
