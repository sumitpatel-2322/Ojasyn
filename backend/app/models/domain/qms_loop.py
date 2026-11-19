import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import DeviationClassification, DeviationSourceType, DeviationStatus
from app.models.domain.base import Base, TenantScopedMixin, TimestampVersionMixin, UUIDPKMixin, utcnow


class Deviation(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """
    Core Quality Deviation / Non-Conformance record (`E1`).
    Supports manual creation and human-confirmed creation from SOP compliance FAIL suggestions,
    with lifecycle `OPEN -> UNDER_INVESTIGATION -> CAPA_PENDING -> QA_REVIEW -> CLOSED` and
    dual `user_id` maker-checker QA closure.
    """

    __tablename__ = "deviations"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_deviations_org_id_id"),
        UniqueConstraint("organization_id", "deviation_number", name="uq_deviations_org_num"),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_deviations_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_deviations_org_complaint",
        ),
        ForeignKeyConstraint(
            ["organization_id", "investigation_id"],
            ["investigations.organization_id", "investigations.id"],
            ondelete="RESTRICT",
            name="fk_deviations_org_inv",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_deviations_org_batch",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_execution_step_id"],
            ["batch_execution_steps.organization_id", "batch_execution_steps.id"],
            ondelete="RESTRICT",
            name="fk_deviations_org_exec_step",
        ),
        ForeignKeyConstraint(
            ["organization_id", "sop_compliance_result_id"],
            ["sop_compliance_results.organization_id", "sop_compliance_results.id"],
            ondelete="RESTRICT",
            name="fk_deviations_org_sop_comp",
        ),
        ForeignKeyConstraint(
            ["organization_id", "qc_test_result_id"],
            ["qc_test_results.organization_id", "qc_test_results.id"],
            ondelete="RESTRICT",
            name="fk_deviations_org_qc_res",
        ),
        ForeignKeyConstraint(
            ["organization_id", "owner_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_deviations_org_owner",
        ),
        ForeignKeyConstraint(
            ["organization_id", "submitted_for_review_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_deviations_org_submitter",
        ),
        ForeignKeyConstraint(
            ["organization_id", "closed_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_deviations_org_closer",
        ),
        CheckConstraint(
            "closed_by_user_id IS NULL OR created_by_user_id IS NULL OR closed_by_user_id != created_by_user_id",
            name="ck_deviations_maker_checker_user",
        ),
        CheckConstraint(
            "closed_by_user_id IS NULL OR submitted_for_review_by_user_id IS NULL OR closed_by_user_id != submitted_for_review_by_user_id",
            name="ck_deviations_submit_close_user",
        ),
        CheckConstraint(
            "closed_by_membership_id IS NULL OR submitted_for_review_by_membership_id IS NULL OR closed_by_membership_id != submitted_for_review_by_membership_id",
            name="ck_deviations_maker_checker_member",
        ),
        Index("ix_deviations_org_status", "organization_id", "status"),
    )

    site_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    deviation_number: Mapped[str] = mapped_column(String(60), nullable=False)
    source_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default=DeviationSourceType.MANUAL.value, index=True
    )
    classification: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DeviationClassification.MAJOR.value, index=True
    )
    is_mandatory_for_closure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    immediate_containment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    impact_assessment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    root_cause_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    complaint_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("complaints.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    investigation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("investigations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    batch_execution_step_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batch_execution_steps.id", ondelete="RESTRICT"),
        nullable=True,
    )
    sop_compliance_result_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sop_compliance_results.id", ondelete="RESTRICT"),
        nullable=True,
    )
    qc_test_result_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("qc_test_results.id", ondelete="RESTRICT"),
        nullable=True,
    )

    owner_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=DeviationStatus.OPEN.value, index=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_for_review_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    submitted_for_review_by_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    submitted_for_review_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    closed_by_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    closure_comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approval_record_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("approval_records.id", ondelete="RESTRICT"),
        nullable=True,
    )
