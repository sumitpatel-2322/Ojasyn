import uuid
from typing import List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.domain.qc_equipment import (
    CalibrationRecord,
    Equipment,
    EquipmentUsage,
    QCSample,
    QCTestDefinition,
    QCTestResult,
)
from app.repositories.base import apply_tenant_filter, paginate


class QCEquipmentRepository:
    """Data access layer for QC Test Definitions, QC Samples, QC Results, Equipment, Calibrations & Equipment Usages."""

    # --- QC Test Definitions (Module C6) ---

    def get_test_definition_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        test_definition_id: uuid.UUID,
    ) -> Optional[QCTestDefinition]:
        return db.scalar(
            select(QCTestDefinition).where(
                QCTestDefinition.organization_id == organization_id,
                QCTestDefinition.id == test_definition_id,
            )
        )

    def get_test_definition_by_code_version(
        self,
        db: Session,
        organization_id: uuid.UUID,
        test_code: str,
        version_number: str,
    ) -> Optional[QCTestDefinition]:
        return db.scalar(
            select(QCTestDefinition).where(
                QCTestDefinition.organization_id == organization_id,
                func.lower(QCTestDefinition.test_code) == test_code.strip().lower(),
                func.lower(QCTestDefinition.version_number) == version_number.strip().lower(),
            )
        )

    def list_test_definitions_for_tenant(
        self,
        db: Session,
        tenant_ctx: object,
        *,
        site_id: Optional[uuid.UUID] = None,
        product_id: Optional[uuid.UUID] = None,
        product_category: Optional[str] = None,
        test_code: Optional[str] = None,
        status: Optional[str] = None,
        q: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[QCTestDefinition], int]:
        stmt = select(QCTestDefinition)
        stmt = apply_tenant_filter(
            stmt, QCTestDefinition, tenant_ctx, allow_org_wide_site_null=True
        )
        if site_id is not None:
            stmt = stmt.where(
                or_(QCTestDefinition.site_id == site_id, QCTestDefinition.site_id.is_(None))
            )
        if product_id is not None:
            stmt = stmt.where(QCTestDefinition.product_id == product_id)
        if product_category:
            stmt = stmt.where(
                func.lower(QCTestDefinition.product_category)
                == product_category.strip().lower()
            )
        if test_code:
            stmt = stmt.where(
                func.lower(QCTestDefinition.test_code) == test_code.strip().lower()
            )
        if status:
            stmt = stmt.where(QCTestDefinition.status == status.strip().upper())
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(
                    QCTestDefinition.test_code.ilike(like),
                    QCTestDefinition.test_name.ilike(like),
                    QCTestDefinition.test_method_reference.ilike(like),
                )
            )
        stmt = stmt.order_by(QCTestDefinition.created_at.desc())
        return paginate(db, stmt, page=page, page_size=page_size)

    # --- QC Samples (Module C6) ---

    def get_sample_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        sample_id: uuid.UUID,
        *,
        with_results: bool = True,
    ) -> Optional[QCSample]:
        stmt = select(QCSample).where(
            QCSample.organization_id == organization_id,
            QCSample.id == sample_id,
        )
        if with_results:
            stmt = stmt.options(selectinload(QCSample.results))
        return db.scalar(stmt)

    def get_sample_by_identifier(
        self,
        db: Session,
        organization_id: uuid.UUID,
        sample_identifier: str,
    ) -> Optional[QCSample]:
        return db.scalar(
            select(QCSample).where(
                QCSample.organization_id == organization_id,
                func.lower(QCSample.sample_identifier) == sample_identifier.strip().lower(),
            )
        )

    def list_samples_for_batch(
        self,
        db: Session,
        organization_id: uuid.UUID,
        batch_id: uuid.UUID,
        *,
        with_results: bool = True,
    ) -> List[QCSample]:
        stmt = (
            select(QCSample)
            .where(
                QCSample.organization_id == organization_id,
                QCSample.batch_id == batch_id,
            )
            .order_by(QCSample.sampled_at.asc(), QCSample.created_at.asc())
        )
        if with_results:
            stmt = stmt.options(selectinload(QCSample.results))
        return list(db.scalars(stmt).all())

    # --- QC Test Results (Module C6) ---

    def get_result_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        result_id: uuid.UUID,
    ) -> Optional[QCTestResult]:
        return db.scalar(
            select(QCTestResult).where(
                QCTestResult.organization_id == organization_id,
                QCTestResult.id == result_id,
            )
        )

    def list_results_for_sample(
        self,
        db: Session,
        organization_id: uuid.UUID,
        sample_id: uuid.UUID,
    ) -> List[QCTestResult]:
        return list(
            db.scalars(
                select(QCTestResult)
                .where(
                    QCTestResult.organization_id == organization_id,
                    QCTestResult.qc_sample_id == sample_id,
                )
                .order_by(QCTestResult.tested_at.asc(), QCTestResult.created_at.asc())
            ).all()
        )

    def list_results_for_batch(
        self,
        db: Session,
        organization_id: uuid.UUID,
        batch_id: uuid.UUID,
    ) -> List[QCTestResult]:
        return list(
            db.scalars(
                select(QCTestResult)
                .where(
                    QCTestResult.organization_id == organization_id,
                    QCTestResult.batch_id == batch_id,
                )
                .order_by(QCTestResult.tested_at.asc(), QCTestResult.created_at.asc())
            ).all()
        )

    # --- Equipment (Module C8) ---

    def get_equipment_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        equipment_id: uuid.UUID,
        *,
        with_calibrations: bool = False,
    ) -> Optional[Equipment]:
        stmt = select(Equipment).where(
            Equipment.organization_id == organization_id,
            Equipment.id == equipment_id,
        )
        if with_calibrations:
            stmt = stmt.options(selectinload(Equipment.calibrations))
        return db.scalar(stmt)

    def get_equipment_by_site_code(
        self,
        db: Session,
        organization_id: uuid.UUID,
        site_id: uuid.UUID,
        equipment_code: str,
    ) -> Optional[Equipment]:
        return db.scalar(
            select(Equipment).where(
                Equipment.organization_id == organization_id,
                Equipment.site_id == site_id,
                func.lower(Equipment.equipment_code) == equipment_code.strip().lower(),
            )
        )

    def list_equipment_for_tenant(
        self,
        db: Session,
        tenant_ctx: object,
        *,
        site_id: Optional[uuid.UUID] = None,
        department_id: Optional[uuid.UUID] = None,
        equipment_category: Optional[str] = None,
        operational_status: Optional[str] = None,
        qualification_status: Optional[str] = None,
        q: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Equipment], int]:
        stmt = select(Equipment)
        stmt = apply_tenant_filter(
            stmt, Equipment, tenant_ctx, allow_org_wide_site_null=False
        )
        if site_id is not None:
            stmt = stmt.where(Equipment.site_id == site_id)
        if department_id is not None:
            stmt = stmt.where(Equipment.department_id == department_id)
        if equipment_category:
            stmt = stmt.where(
                func.lower(Equipment.equipment_category)
                == equipment_category.strip().lower()
            )
        if operational_status:
            stmt = stmt.where(
                Equipment.operational_status == operational_status.strip().upper()
            )
        if qualification_status:
            stmt = stmt.where(
                Equipment.qualification_status == qualification_status.strip().upper()
            )
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(
                    Equipment.equipment_code.ilike(like),
                    Equipment.equipment_name.ilike(like),
                    Equipment.serial_number.ilike(like),
                )
            )
        stmt = stmt.order_by(Equipment.created_at.desc())
        return paginate(db, stmt, page=page, page_size=page_size)

    # --- Calibration Records (Module C8) ---

    def get_calibration_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        equipment_id: uuid.UUID,
        calibration_id: uuid.UUID,
    ) -> Optional[CalibrationRecord]:
        return db.scalar(
            select(CalibrationRecord).where(
                CalibrationRecord.organization_id == organization_id,
                CalibrationRecord.equipment_id == equipment_id,
                CalibrationRecord.id == calibration_id,
            )
        )

    def get_calibration_by_id_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        calibration_id: uuid.UUID,
    ) -> Optional[CalibrationRecord]:
        return db.scalar(
            select(CalibrationRecord).where(
                CalibrationRecord.organization_id == organization_id,
                CalibrationRecord.id == calibration_id,
            )
        )

    def get_calibration_by_code(
        self,
        db: Session,
        organization_id: uuid.UUID,
        equipment_id: uuid.UUID,
        calibration_code: str,
    ) -> Optional[CalibrationRecord]:
        return db.scalar(
            select(CalibrationRecord).where(
                CalibrationRecord.organization_id == organization_id,
                CalibrationRecord.equipment_id == equipment_id,
                func.lower(CalibrationRecord.calibration_code)
                == calibration_code.strip().lower(),
            )
        )

    def list_calibrations_for_equipment(
        self,
        db: Session,
        organization_id: uuid.UUID,
        equipment_id: uuid.UUID,
    ) -> List[CalibrationRecord]:
        return list(
            db.scalars(
                select(CalibrationRecord)
                .where(
                    CalibrationRecord.organization_id == organization_id,
                    CalibrationRecord.equipment_id == equipment_id,
                )
                .order_by(
                    CalibrationRecord.calibrated_at.desc(),
                    CalibrationRecord.created_at.desc(),
                )
            ).all()
        )

    # --- Equipment Usages (Module C8) ---

    def get_usage_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        usage_id: uuid.UUID,
    ) -> Optional[EquipmentUsage]:
        return db.scalar(
            select(EquipmentUsage).where(
                EquipmentUsage.organization_id == organization_id,
                EquipmentUsage.id == usage_id,
            )
        )

    def list_usages_for_execution_record(
        self,
        db: Session,
        organization_id: uuid.UUID,
        execution_record_id: uuid.UUID,
    ) -> List[EquipmentUsage]:
        return list(
            db.scalars(
                select(EquipmentUsage)
                .where(
                    EquipmentUsage.organization_id == organization_id,
                    EquipmentUsage.batch_execution_record_id == execution_record_id,
                )
                .order_by(
                    EquipmentUsage.usage_start_at.asc(),
                    EquipmentUsage.created_at.asc(),
                )
            ).all()
        )

    def list_usages_for_batch(
        self,
        db: Session,
        organization_id: uuid.UUID,
        batch_id: uuid.UUID,
    ) -> List[EquipmentUsage]:
        return list(
            db.scalars(
                select(EquipmentUsage)
                .where(
                    EquipmentUsage.organization_id == organization_id,
                    EquipmentUsage.batch_id == batch_id,
                )
                .order_by(
                    EquipmentUsage.usage_start_at.asc(),
                    EquipmentUsage.created_at.asc(),
                )
            ).all()
        )
