import uuid
from typing import List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import TenantContext
from app.models.domain.sops import SOP, SOPRevision, SOPStep
from app.repositories.base import apply_tenant_filter, paginate


class SOPRepository:
    """Tenant-scoped data access for SOPs, SOPRevisions, and SOPSteps (`C5`)."""

    # --- SOP Master ---

    def create_sop(self, db: Session, sop: SOP) -> SOP:
        db.add(sop)
        db.flush()
        return sop

    def get_sop_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        sop_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> Optional[SOP]:
        stmt = select(SOP).where(
            SOP.organization_id == organization_id,
            SOP.id == sop_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return db.scalar(stmt)

    def get_sop_by_code(
        self,
        db: Session,
        organization_id: uuid.UUID,
        sop_code: str,
        *,
        for_update: bool = False,
    ) -> Optional[SOP]:
        stmt = select(SOP).where(
            SOP.organization_id == organization_id,
            func.lower(SOP.sop_code) == sop_code.strip().lower(),
        )
        if for_update:
            stmt = stmt.with_for_update()
        return db.scalar(stmt)

    def list_sops_for_tenant(
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
        stmt = select(SOP)
        stmt = apply_tenant_filter(
            stmt,
            SOP,
            tenant_ctx,
            site_column="site_id",
            allow_org_wide_site_null=True,
        )
        if status:
            stmt = stmt.where(SOP.sop_status == status.strip().upper())
        if site_id is not None:
            stmt = stmt.where(or_(SOP.site_id == site_id, SOP.site_id.is_(None)))
        if category:
            stmt = stmt.where(func.lower(SOP.category) == category.strip().lower())
        if search:
            pattern = f"%{search.strip().lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(SOP.sop_code).like(pattern),
                    func.lower(SOP.title).like(pattern),
                )
            )
        stmt = stmt.order_by(SOP.sop_code.asc(), SOP.created_at.desc())
        return paginate(db, stmt, page=page, page_size=page_size)

    # --- SOP Revisions ---

    def create_revision(self, db: Session, revision: SOPRevision) -> SOPRevision:
        db.add(revision)
        db.flush()
        return revision

    def get_revision_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        revision_id: uuid.UUID,
        *,
        sop_id: Optional[uuid.UUID] = None,
        for_update: bool = False,
    ) -> Optional[SOPRevision]:
        stmt = select(SOPRevision).where(
            SOPRevision.organization_id == organization_id,
            SOPRevision.id == revision_id,
        )
        if sop_id is not None:
            stmt = stmt.where(SOPRevision.sop_id == sop_id)
        if for_update:
            stmt = stmt.with_for_update()
        return db.scalar(stmt)

    def get_revision_by_number(
        self,
        db: Session,
        organization_id: uuid.UUID,
        sop_id: uuid.UUID,
        revision_number: str,
    ) -> Optional[SOPRevision]:
        return db.scalar(
            select(SOPRevision).where(
                SOPRevision.organization_id == organization_id,
                SOPRevision.sop_id == sop_id,
                func.lower(SOPRevision.revision_number) == revision_number.strip().lower(),
            )
        )

    def get_next_revision_sequence(
        self,
        db: Session,
        organization_id: uuid.UUID,
        sop_id: uuid.UUID,
    ) -> int:
        max_seq = db.scalar(
            select(func.max(SOPRevision.revision_sequence)).where(
                SOPRevision.organization_id == organization_id,
                SOPRevision.sop_id == sop_id,
            )
        )
        return int(max_seq or 0) + 1

    def list_revisions_for_sop(
        self,
        db: Session,
        organization_id: uuid.UUID,
        sop_id: uuid.UUID,
    ) -> List[SOPRevision]:
        return list(
            db.scalars(
                select(SOPRevision)
                .where(
                    SOPRevision.organization_id == organization_id,
                    SOPRevision.sop_id == sop_id,
                )
                .order_by(SOPRevision.revision_sequence.asc(), SOPRevision.created_at.asc())
            ).all()
        )

    # --- SOP Steps ---

    def create_step(self, db: Session, step: SOPStep) -> SOPStep:
        db.add(step)
        db.flush()
        return step

    def get_step_for_org(
        self,
        db: Session,
        organization_id: uuid.UUID,
        step_id: uuid.UUID,
        *,
        sop_revision_id: Optional[uuid.UUID] = None,
        for_update: bool = False,
    ) -> Optional[SOPStep]:
        stmt = select(SOPStep).where(
            SOPStep.organization_id == organization_id,
            SOPStep.id == step_id,
        )
        if sop_revision_id is not None:
            stmt = stmt.where(SOPStep.sop_revision_id == sop_revision_id)
        if for_update:
            stmt = stmt.with_for_update()
        return db.scalar(stmt)

    def get_step_by_code(
        self,
        db: Session,
        organization_id: uuid.UUID,
        sop_revision_id: uuid.UUID,
        step_code: str,
    ) -> Optional[SOPStep]:
        return db.scalar(
            select(SOPStep).where(
                SOPStep.organization_id == organization_id,
                SOPStep.sop_revision_id == sop_revision_id,
                func.lower(SOPStep.step_code) == step_code.strip().lower(),
            )
        )

    def get_step_by_sequence(
        self,
        db: Session,
        organization_id: uuid.UUID,
        sop_revision_id: uuid.UUID,
        step_sequence: int,
    ) -> Optional[SOPStep]:
        return db.scalar(
            select(SOPStep).where(
                SOPStep.organization_id == organization_id,
                SOPStep.sop_revision_id == sop_revision_id,
                SOPStep.step_sequence == step_sequence,
            )
        )

    def list_steps_for_revision(
        self,
        db: Session,
        organization_id: uuid.UUID,
        sop_revision_id: uuid.UUID,
    ) -> List[SOPStep]:
        return list(
            db.scalars(
                select(SOPStep)
                .where(
                    SOPStep.organization_id == organization_id,
                    SOPStep.sop_revision_id == sop_revision_id,
                )
                .order_by(SOPStep.step_sequence.asc(), SOPStep.created_at.asc())
            ).all()
        )
