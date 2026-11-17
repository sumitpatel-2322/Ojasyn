import uuid
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.domain.base import Base, TimestampVersionMixin, UUIDPKMixin

JSONType = JSON().with_variant(JSONB(), "postgresql")


class ReferenceValue(UUIDPKMixin, TimestampVersionMixin, Base):
    """Global (`organization_id IS NULL`) or tenant-scoped controlled lookup reference value."""

    __tablename__ = "reference_values"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "category_code",
            "code",
            name="uq_reference_values_org_cat_code",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_reference_values_org_cat", "organization_id", "category_code"),
    )

    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    category_code: Mapped[str] = mapped_column(String(60), nullable=False)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    display_name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)


class UnitOfMeasure(UUIDPKMixin, TimestampVersionMixin, Base):
    """Standardized global unit of measure catalog for batch, QC, and material operations."""

    __tablename__ = "units_of_measure"

    unit_code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True, index=True)
    unit_name: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    dimension: Mapped[str] = mapped_column(String(40), nullable=False)
    base_unit_code: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    conversion_factor: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False, default=Decimal("1.000000")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
