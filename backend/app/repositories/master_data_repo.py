import uuid
from typing import List, Optional, Tuple

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.domain.master_data import ReferenceValue, UnitOfMeasure
from app.repositories.base import paginate


class MasterDataRepository:
    """Data access for ReferenceValues (global + tenant overrides) and UnitsOfMeasure."""

    def create_reference_value(self, db: Session, ref: ReferenceValue) -> ReferenceValue:
        db.add(ref)
        db.flush()
        return ref

    def get_reference_value_exact(
        self,
        db: Session,
        *,
        organization_id: Optional[uuid.UUID],
        category_code: str,
        code: str,
    ) -> Optional[ReferenceValue]:
        stmt = select(ReferenceValue).where(
            ReferenceValue.category_code == category_code.strip().upper(),
            ReferenceValue.code == code.strip().upper(),
        )
        if organization_id is None:
            stmt = stmt.where(ReferenceValue.organization_id.is_(None))
        else:
            stmt = stmt.where(ReferenceValue.organization_id == organization_id)
        return db.scalar(stmt)

    def list_reference_values(
        self,
        db: Session,
        *,
        organization_id: Optional[uuid.UUID],
        category_code: Optional[str] = None,
        active_only: bool = True,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[ReferenceValue], int]:
        stmt = select(ReferenceValue)
        if organization_id is None:
            stmt = stmt.where(ReferenceValue.organization_id.is_(None))
        else:
            stmt = stmt.where(
                or_(
                    ReferenceValue.organization_id.is_(None),
                    ReferenceValue.organization_id == organization_id,
                )
            )
        if category_code:
            stmt = stmt.where(ReferenceValue.category_code == category_code.strip().upper())
        if active_only:
            stmt = stmt.where(ReferenceValue.is_active.is_(True))
        stmt = stmt.order_by(
            ReferenceValue.category_code.asc(),
            ReferenceValue.sort_order.asc(),
            ReferenceValue.code.asc(),
        )
        return paginate(db, stmt, page=page, page_size=page_size)

    def create_uom(self, db: Session, uom: UnitOfMeasure) -> UnitOfMeasure:
        db.add(uom)
        db.flush()
        return uom

    def get_uom_by_code(self, db: Session, unit_code: str) -> Optional[UnitOfMeasure]:
        return db.scalar(select(UnitOfMeasure).where(UnitOfMeasure.unit_code == unit_code.strip().upper()))

    def list_uoms(
        self,
        db: Session,
        *,
        dimension: Optional[str] = None,
        active_only: bool = True,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[UnitOfMeasure], int]:
        stmt = select(UnitOfMeasure)
        if dimension:
            stmt = stmt.where(UnitOfMeasure.dimension == dimension.strip().upper())
        if active_only:
            stmt = stmt.where(UnitOfMeasure.is_active.is_(True))
        stmt = stmt.order_by(UnitOfMeasure.dimension.asc(), UnitOfMeasure.unit_code.asc())
        return paginate(db, stmt, page=page, page_size=page_size)
