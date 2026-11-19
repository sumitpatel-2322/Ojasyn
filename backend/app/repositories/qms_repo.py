import uuid
from typing import List, Optional, Tuple

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.domain.enums import DeviationStatus
from app.models.domain.investigations import Investigation
from app.models.domain.qms_loop import Deviation
from app.repositories.base import apply_tenant_filter, paginate


class QMSRepository:
    """Tenant-scoped data access for QMS Loop records (Deviations in Phase 5)."""

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
