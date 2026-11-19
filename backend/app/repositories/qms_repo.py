import uuid
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domain.enums import CAPAStatus, DeviationStatus
from app.models.domain.investigations import Investigation
from app.models.domain.qms_loop import CAPA, CAPAAction, CAPAEffectivenessCheck, Deviation
from app.repositories.base import apply_tenant_filter, paginate


class QMSRepository:
    """Tenant-scoped data access for QMS Loop records (Deviations & CAPAs)."""

    def get_deviation_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        deviation_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> Optional[Deviation]:
        stmt = select(Deviation).where(
            Deviation.organization_id == organization_id,
            Deviation.id == deviation_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return db.scalar(stmt)

    def list_deviations_for_tenant(
        self,
        db: Session,
        tenant_ctx: object,
        *,
        status: Optional[str] = None,
        classification: Optional[str] = None,
        source_type: Optional[str] = None,
        complaint_id: Optional[uuid.UUID] = None,
        investigation_id: Optional[uuid.UUID] = None,
        batch_id: Optional[uuid.UUID] = None,
        site_id: Optional[uuid.UUID] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Deviation], int]:
        stmt = select(Deviation)
        stmt = apply_tenant_filter(stmt, Deviation, tenant_ctx, allow_org_wide_site_null=True)
        if status:
            stmt = stmt.where(Deviation.status == status.strip().upper())
        if classification:
            stmt = stmt.where(Deviation.classification == classification.strip().upper())
        if source_type:
            stmt = stmt.where(Deviation.source_type == source_type.strip().upper())
        if complaint_id is not None:
            stmt = stmt.where(Deviation.complaint_id == complaint_id)
        if investigation_id is not None:
            stmt = stmt.where(Deviation.investigation_id == investigation_id)
        if batch_id is not None:
            stmt = stmt.where(Deviation.batch_id == batch_id)
        if site_id is not None:
            stmt = stmt.where(Deviation.site_id == site_id)
        stmt = stmt.order_by(Deviation.created_at.desc())
        return paginate(db, stmt, page=page, page_size=page_size)

    def list_open_mandatory_deviations_for_complaint(
        self,
        db: Session,
        organization_id: uuid.UUID,
        complaint_id: uuid.UUID,
    ) -> List[Deviation]:
        inv_ids_stmt = select(Investigation.id).where(
            Investigation.organization_id == organization_id,
            Investigation.complaint_id == complaint_id,
        )
        return list(
            db.scalars(
                select(Deviation).where(
                    Deviation.organization_id == organization_id,
                    Deviation.status != DeviationStatus.CLOSED.value,
                    Deviation.is_mandatory_for_closure.is_(True),
                    or_(
                        Deviation.complaint_id == complaint_id,
                        Deviation.investigation_id.in_(inv_ids_stmt),
                    ),
                )
            ).all()
        )

    # ------------------------------------------------------------------
    # Phase 6 (E3): CAPA, CAPA Actions & Effectiveness Checks
    # ------------------------------------------------------------------

    def get_capa_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        capa_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> Optional[CAPA]:
        stmt = select(CAPA).where(
            CAPA.organization_id == organization_id,
            CAPA.id == capa_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return db.scalar(stmt)

    def list_capas_for_tenant(
        self,
        db: Session,
        tenant_ctx: object,
        *,
        status: Optional[str] = None,
        capa_type: Optional[str] = None,
        source_type: Optional[str] = None,
        severity: Optional[str] = None,
        complaint_id: Optional[uuid.UUID] = None,
        investigation_id: Optional[uuid.UUID] = None,
        deviation_id: Optional[uuid.UUID] = None,
        site_id: Optional[uuid.UUID] = None,
        owner_membership_id: Optional[uuid.UUID] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[CAPA], int]:
        stmt = select(CAPA)
        stmt = apply_tenant_filter(stmt, CAPA, tenant_ctx, allow_org_wide_site_null=True)
        if status:
            stmt = stmt.where(CAPA.status == status.strip().upper())
        if capa_type:
            stmt = stmt.where(CAPA.capa_type == capa_type.strip().upper())
        if source_type:
            stmt = stmt.where(CAPA.source_type == source_type.strip().upper())
        if severity:
            stmt = stmt.where(CAPA.severity == severity.strip().upper())
        if complaint_id is not None:
            stmt = stmt.where(CAPA.complaint_id == complaint_id)
        if investigation_id is not None:
            stmt = stmt.where(CAPA.investigation_id == investigation_id)
        if deviation_id is not None:
            stmt = stmt.where(CAPA.deviation_id == deviation_id)
        if site_id is not None:
            stmt = stmt.where(CAPA.site_id == site_id)
        if owner_membership_id is not None:
            stmt = stmt.where(CAPA.owner_membership_id == owner_membership_id)
        stmt = stmt.order_by(CAPA.created_at.desc())
        return paginate(db, stmt, page=page, page_size=page_size)

    def get_capa_action_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        capa_id: uuid.UUID,
        action_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> Optional[CAPAAction]:
        stmt = select(CAPAAction).where(
            CAPAAction.organization_id == organization_id,
            CAPAAction.capa_id == capa_id,
            CAPAAction.id == action_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return db.scalar(stmt)

    def list_capa_actions(
        self,
        db: Session,
        organization_id: uuid.UUID,
        capa_id: uuid.UUID,
    ) -> List[CAPAAction]:
        return list(
            db.scalars(
                select(CAPAAction)
                .where(
                    CAPAAction.organization_id == organization_id,
                    CAPAAction.capa_id == capa_id,
                )
                .order_by(CAPAAction.action_seq.asc())
            ).all()
        )

    def next_capa_action_seq(
        self,
        db: Session,
        organization_id: uuid.UUID,
        capa_id: uuid.UUID,
    ) -> int:
        max_seq = db.scalar(
            select(func.coalesce(func.max(CAPAAction.action_seq), 0)).where(
                CAPAAction.organization_id == organization_id,
                CAPAAction.capa_id == capa_id,
            )
        )
        return int(max_seq or 0) + 1

    def get_effectiveness_check_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        capa_id: uuid.UUID,
        check_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> Optional[CAPAEffectivenessCheck]:
        stmt = select(CAPAEffectivenessCheck).where(
            CAPAEffectivenessCheck.organization_id == organization_id,
            CAPAEffectivenessCheck.capa_id == capa_id,
            CAPAEffectivenessCheck.id == check_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return db.scalar(stmt)

    def list_effectiveness_checks(
        self,
        db: Session,
        organization_id: uuid.UUID,
        capa_id: uuid.UUID,
    ) -> List[CAPAEffectivenessCheck]:
        return list(
            db.scalars(
                select(CAPAEffectivenessCheck)
                .where(
                    CAPAEffectivenessCheck.organization_id == organization_id,
                    CAPAEffectivenessCheck.capa_id == capa_id,
                )
                .order_by(CAPAEffectivenessCheck.check_seq.asc())
            ).all()
        )

    def next_effectiveness_check_seq(
        self,
        db: Session,
        organization_id: uuid.UUID,
        capa_id: uuid.UUID,
    ) -> int:
        max_seq = db.scalar(
            select(func.coalesce(func.max(CAPAEffectivenessCheck.check_seq), 0)).where(
                CAPAEffectivenessCheck.organization_id == organization_id,
                CAPAEffectivenessCheck.capa_id == capa_id,
            )
        )
        return int(max_seq or 0) + 1

    def list_overdue_open_capas_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        *,
        now: datetime,
        tenant_ctx: Optional[object] = None,
        capa_id: Optional[uuid.UUID] = None,
        for_update: bool = False,
    ) -> List[CAPA]:
        stmt = select(CAPA).where(
            CAPA.organization_id == organization_id,
            CAPA.status != CAPAStatus.CLOSED.value,
            CAPA.due_at.is_not(None),
            CAPA.due_at < now,
            CAPA.overdue_notified_at.is_(None),
        )
        if tenant_ctx is not None:
            stmt = apply_tenant_filter(stmt, CAPA, tenant_ctx, allow_org_wide_site_null=True)
        if capa_id is not None:
            stmt = stmt.where(CAPA.id == capa_id)
        stmt = stmt.order_by(CAPA.due_at.asc())
        if for_update:
            stmt = stmt.with_for_update()
        return list(db.scalars(stmt).all())

    def list_open_mandatory_capas_for_complaint(
        self,
        db: Session,
        organization_id: uuid.UUID,
        complaint_id: uuid.UUID,
    ) -> List[CAPA]:
        inv_ids_stmt = select(Investigation.id).where(
            Investigation.organization_id == organization_id,
            Investigation.complaint_id == complaint_id,
        )
        dev_ids_stmt = select(Deviation.id).where(
            Deviation.organization_id == organization_id,
            or_(
                Deviation.complaint_id == complaint_id,
                Deviation.investigation_id.in_(inv_ids_stmt),
            ),
        )
        return list(
            db.scalars(
                select(CAPA).where(
                    CAPA.organization_id == organization_id,
                    CAPA.status != CAPAStatus.CLOSED.value,
                    CAPA.is_mandatory_for_complaint_closure.is_(True),
                    or_(
                        CAPA.complaint_id == complaint_id,
                        CAPA.investigation_id.in_(inv_ids_stmt),
                        CAPA.deviation_id.in_(dev_ids_stmt),
                    ),
                )
            ).all()
        )
