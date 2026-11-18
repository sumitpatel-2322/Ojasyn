import uuid
from typing import List, Optional, Tuple

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.models.domain.manufacturing_execution import (
    BatchExecutionRecord,
    BatchExecutionStep,
    DeterministicEvidenceCheck,
    MasterBatchRecord,
    ProcessParameterObservation,
)
from app.repositories.base import apply_tenant_filter, paginate


class ManufacturingExecutionRepository:
    """Data access for MasterBatchRecords, BatchExecutionRecords, Steps, Observations & DeterministicEvidenceChecks."""

    # --- Master Batch Records ---

    def get_mbr_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        mbr_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> Optional[MasterBatchRecord]:
        stmt = select(MasterBatchRecord).where(
            MasterBatchRecord.organization_id == organization_id,
            MasterBatchRecord.id == mbr_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return db.scalar(stmt)

    def get_mbr_by_composite_key(
        self,
        db: Session,
        organization_id: uuid.UUID,
        site_id: uuid.UUID,
        product_id: uuid.UUID,
        mbr_code: str,
        version_number: str,
    ) -> Optional[MasterBatchRecord]:
        return db.scalar(
            select(MasterBatchRecord).where(
                MasterBatchRecord.organization_id == organization_id,
                MasterBatchRecord.site_id == site_id,
                MasterBatchRecord.product_id == product_id,
                func.lower(MasterBatchRecord.mbr_code) == mbr_code.strip().lower(),
                func.lower(MasterBatchRecord.version_number) == version_number.strip().lower(),
            )
        )

    def list_mbrs_for_tenant(
        self,
        db: Session,
        tenant_ctx: object,
        *,
        site_id: Optional[uuid.UUID] = None,
        product_id: Optional[uuid.UUID] = None,
        approval_status: Optional[str] = None,
        mbr_code: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[MasterBatchRecord], int]:
        stmt = select(MasterBatchRecord)
        stmt = apply_tenant_filter(stmt, MasterBatchRecord, tenant_ctx, allow_org_wide_site_null=False)
        if site_id is not None:
            stmt = stmt.where(MasterBatchRecord.site_id == site_id)
        if product_id is not None:
            stmt = stmt.where(MasterBatchRecord.product_id == product_id)
        if approval_status:
            stmt = stmt.where(MasterBatchRecord.approval_status == approval_status)
        if mbr_code:
            stmt = stmt.where(func.lower(MasterBatchRecord.mbr_code) == mbr_code.strip().lower())
        stmt = stmt.order_by(MasterBatchRecord.created_at.desc())
        return paginate(db, stmt, page=page, page_size=page_size)

    # --- Batch Execution Records ---

    def get_execution_record_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        execution_record_id: uuid.UUID,
        *,
        with_children: bool = False,
        for_update: bool = False,
    ) -> Optional[BatchExecutionRecord]:
        stmt = select(BatchExecutionRecord).where(
            BatchExecutionRecord.organization_id == organization_id,
            BatchExecutionRecord.id == execution_record_id,
        )
        if with_children:
            stmt = stmt.options(
                selectinload(BatchExecutionRecord.steps).selectinload(BatchExecutionStep.observations),
                selectinload(BatchExecutionRecord.observations),
            )
        if for_update:
            stmt = stmt.with_for_update()
        return db.scalar(stmt)

    def get_execution_record_by_code(
        self,
        db: Session,
        organization_id: uuid.UUID,
        batch_id: uuid.UUID,
        execution_code: str,
    ) -> Optional[BatchExecutionRecord]:
        return db.scalar(
            select(BatchExecutionRecord).where(
                BatchExecutionRecord.organization_id == organization_id,
                BatchExecutionRecord.batch_id == batch_id,
                func.lower(BatchExecutionRecord.execution_code) == execution_code.strip().lower(),
            )
        )

    def get_execution_record_by_sequence(
        self,
        db: Session,
        organization_id: uuid.UUID,
        batch_id: uuid.UUID,
        execution_sequence: int,
    ) -> Optional[BatchExecutionRecord]:
        return db.scalar(
            select(BatchExecutionRecord).where(
                BatchExecutionRecord.organization_id == organization_id,
                BatchExecutionRecord.batch_id == batch_id,
                BatchExecutionRecord.execution_sequence == execution_sequence,
            )
        )

    def list_execution_records_for_batch(
        self,
        db: Session,
        organization_id: uuid.UUID,
        batch_id: uuid.UUID,
    ) -> List[BatchExecutionRecord]:
        return list(
            db.scalars(
                select(BatchExecutionRecord)
                .where(
                    BatchExecutionRecord.organization_id == organization_id,
                    BatchExecutionRecord.batch_id == batch_id,
                )
                .options(
                    selectinload(BatchExecutionRecord.steps).selectinload(BatchExecutionStep.observations),
                    selectinload(BatchExecutionRecord.observations),
                )
                .order_by(BatchExecutionRecord.execution_sequence.asc())
            ).all()
        )

    # --- Batch Execution Steps ---

    def get_step_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        step_id: uuid.UUID,
        *,
        execution_record_id: Optional[uuid.UUID] = None,
        for_update: bool = False,
    ) -> Optional[BatchExecutionStep]:
        stmt = (
            select(BatchExecutionStep)
            .where(
                BatchExecutionStep.organization_id == organization_id,
                BatchExecutionStep.id == step_id,
            )
            .options(selectinload(BatchExecutionStep.observations))
        )
        if execution_record_id is not None:
            stmt = stmt.where(BatchExecutionStep.batch_execution_record_id == execution_record_id)
        if for_update:
            stmt = stmt.with_for_update()
        return db.scalar(stmt)

    def get_step_by_code_or_seq(
        self,
        db: Session,
        organization_id: uuid.UUID,
        execution_record_id: uuid.UUID,
        *,
        step_code: Optional[str] = None,
        step_sequence: Optional[int] = None,
    ) -> Optional[BatchExecutionStep]:
        stmt = select(BatchExecutionStep).where(
            BatchExecutionStep.organization_id == organization_id,
            BatchExecutionStep.batch_execution_record_id == execution_record_id,
        )
        if step_code is not None:
            stmt = stmt.where(func.lower(BatchExecutionStep.step_code) == step_code.strip().lower())
        if step_sequence is not None:
            stmt = stmt.where(BatchExecutionStep.step_sequence == step_sequence)
        return db.scalar(stmt)

    def list_steps_for_execution_record(
        self,
        db: Session,
        organization_id: uuid.UUID,
        execution_record_id: uuid.UUID,
    ) -> List[BatchExecutionStep]:
        return list(
            db.scalars(
                select(BatchExecutionStep)
                .where(
                    BatchExecutionStep.organization_id == organization_id,
                    BatchExecutionStep.batch_execution_record_id == execution_record_id,
                )
                .options(selectinload(BatchExecutionStep.observations))
                .order_by(BatchExecutionStep.step_sequence.asc())
            ).all()
        )

    # --- Process Parameter Observations ---

    def get_observation_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        observation_id: uuid.UUID,
        *,
        execution_record_id: Optional[uuid.UUID] = None,
        for_update: bool = False,
    ) -> Optional[ProcessParameterObservation]:
        stmt = select(ProcessParameterObservation).where(
            ProcessParameterObservation.organization_id == organization_id,
            ProcessParameterObservation.id == observation_id,
        )
        if execution_record_id is not None:
            stmt = stmt.where(
                ProcessParameterObservation.batch_execution_record_id == execution_record_id
            )
        if for_update:
            stmt = stmt.with_for_update()
        return db.scalar(stmt)

    def list_observations_for_step(
        self,
        db: Session,
        organization_id: uuid.UUID,
        step_id: uuid.UUID,
    ) -> List[ProcessParameterObservation]:
        return list(
            db.scalars(
                select(ProcessParameterObservation)
                .where(
                    ProcessParameterObservation.organization_id == organization_id,
                    ProcessParameterObservation.batch_execution_step_id == step_id,
                )
                .order_by(ProcessParameterObservation.observed_at.asc())
            ).all()
        )

    def list_observations_for_execution_record(
        self,
        db: Session,
        organization_id: uuid.UUID,
        execution_record_id: uuid.UUID,
    ) -> List[ProcessParameterObservation]:
        return list(
            db.scalars(
                select(ProcessParameterObservation)
                .where(
                    ProcessParameterObservation.organization_id == organization_id,
                    ProcessParameterObservation.batch_execution_record_id == execution_record_id,
                )
                .order_by(ProcessParameterObservation.observed_at.asc())
            ).all()
        )

    # --- Deterministic Evidence Checks ---

    def replace_deterministic_checks_for_batch(
        self,
        db: Session,
        organization_id: uuid.UUID,
        batch_id: uuid.UUID,
        checks: List[DeterministicEvidenceCheck],
    ) -> List[DeterministicEvidenceCheck]:
        db.execute(
            delete(DeterministicEvidenceCheck).where(
                DeterministicEvidenceCheck.organization_id == organization_id,
                DeterministicEvidenceCheck.batch_id == batch_id,
            )
        )
        for chk in checks:
            db.add(chk)
        db.flush()
        return checks

    def list_deterministic_checks_for_batch(
        self,
        db: Session,
        organization_id: uuid.UUID,
        batch_id: uuid.UUID,
    ) -> List[DeterministicEvidenceCheck]:
        return list(
            db.scalars(
                select(DeterministicEvidenceCheck)
                .where(
                    DeterministicEvidenceCheck.organization_id == organization_id,
                    DeterministicEvidenceCheck.batch_id == batch_id,
                )
                .order_by(DeterministicEvidenceCheck.created_at.asc())
            ).all()
        )
