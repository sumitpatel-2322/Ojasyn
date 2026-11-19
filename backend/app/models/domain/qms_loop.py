import uuid
from datetime import date, datetime
from typing import List, Optional

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import (
    CAPAActionStatus,
    CAPAActionType,
    CAPASourceType,
    CAPAStatus,
    CAPAType,
    DeviationClassification,
    DeviationSourceType,
    DeviationStatus,
    EffectivenessCheckStatus,
    Severity,
)
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


class CAPA(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """
    Corrective and Preventive Action aggregate root (`E3`).
    Supports lifecycle `DRAFT -> OPEN -> ACTIONS_IN_PROGRESS -> EFFECTIVENESS_REVIEW -> CLOSED`
    with dual `user_id` maker-checker on opening approval, effectiveness review, and closure.
    """

    __tablename__ = "capas"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_capas_org_id_id"),
        UniqueConstraint("organization_id", "capa_number", name="uq_capas_org_capa_num"),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_capas_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_capas_org_complaint",
        ),
        ForeignKeyConstraint(
            ["organization_id", "investigation_id"],
            ["investigations.organization_id", "investigations.id"],
            ondelete="RESTRICT",
            name="fk_capas_org_inv",
        ),
        ForeignKeyConstraint(
            ["organization_id", "deviation_id"],
            ["deviations.organization_id", "deviations.id"],
            ondelete="RESTRICT",
            name="fk_capas_org_dev",
        ),
        ForeignKeyConstraint(
            ["organization_id", "owner_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_capas_org_owner",
        ),
        ForeignKeyConstraint(
            ["organization_id", "approver_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_capas_org_approver",
        ),
        CheckConstraint(
            "approver_membership_id IS NULL OR approver_membership_id != owner_membership_id",
            name="ck_capas_approver_neq_owner",
        ),
        Index("ix_capas_org_status_due", "organization_id", "status", "due_at"),
    )

    site_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    capa_number: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    capa_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=CAPAType.CORRECTIVE.value,
        server_default=CAPAType.CORRECTIVE.value,
    )
    source_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=CAPASourceType.MANUAL.value,
        server_default=CAPASourceType.MANUAL.value,
    )
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
    deviation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("deviations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    is_mandatory_for_complaint_closure: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=sa.true(),
    )
    severity: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=Severity.MEDIUM.value,
        server_default=Severity.MEDIUM.value,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=CAPAStatus.DRAFT.value,
        server_default=CAPAStatus.DRAFT.value,
        index=True,
    )
    owner_membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    approver_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    overdue_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    closure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    root_cause_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    actions: Mapped[List["CAPAAction"]] = relationship(
        "CAPAAction",
        back_populates="capa",
        order_by="CAPAAction.action_seq.asc()",
        foreign_keys="CAPAAction.capa_id",
    )
    effectiveness_checks: Mapped[List["CAPAEffectivenessCheck"]] = relationship(
        "CAPAEffectivenessCheck",
        back_populates="capa",
        order_by="CAPAEffectivenessCheck.check_seq.asc()",
        foreign_keys="CAPAEffectivenessCheck.capa_id",
    )


class CAPAAction(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Sequential CAPA remediation / containment / preventive action (`E3`)."""

    __tablename__ = "capa_actions"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_capa_actions_org_id_id"),
        UniqueConstraint(
            "organization_id",
            "capa_id",
            "action_seq",
            name="uq_capa_actions_org_capa_seq",
        ),
        ForeignKeyConstraint(
            ["organization_id", "capa_id"],
            ["capas.organization_id", "capas.id"],
            ondelete="RESTRICT",
            name="fk_capa_actions_org_capa",
        ),
        ForeignKeyConstraint(
            ["organization_id", "owner_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_capa_actions_org_owner",
        ),
        ForeignKeyConstraint(
            ["organization_id", "evidence_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_capa_actions_org_ev_doc",
        ),
        CheckConstraint("action_seq >= 1", name="ck_capa_actions_seq_pos"),
        Index("ix_capa_actions_org_capa_status", "organization_id", "capa_id", "status"),
    )

    capa_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("capas.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    action_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=CAPAActionType.CORRECTIVE.value,
        server_default=CAPAActionType.CORRECTIVE.value,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    owner_membership_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=CAPAActionStatus.PENDING.value,
        server_default=CAPAActionStatus.PENDING.value,
        index=True,
    )
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    implementation_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    evidence_document_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )

    capa: Mapped["CAPA"] = relationship(
        "CAPA",
        back_populates="actions",
        foreign_keys=[capa_id],
    )


class CAPAEffectivenessCheck(UUIDPKMixin, TimestampVersionMixin, Base):
    """Formal CAPA effectiveness verification check (`E3`)."""

    __tablename__ = "capa_effectiveness_checks"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_capa_eff_org_id_id"),
        UniqueConstraint(
            "organization_id",
            "capa_id",
            "check_seq",
            name="uq_capa_eff_org_capa_seq",
        ),
        ForeignKeyConstraint(
            ["organization_id", "capa_id"],
            ["capas.organization_id", "capas.id"],
            ondelete="RESTRICT",
            name="fk_capa_eff_org_capa",
        ),
        ForeignKeyConstraint(
            ["organization_id", "reviewer_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_capa_eff_org_reviewer",
        ),
        ForeignKeyConstraint(
            ["organization_id", "evidence_document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_capa_eff_org_ev_doc",
        ),
        CheckConstraint("check_seq >= 1", name="ck_capa_eff_seq_pos"),
        Index("ix_capa_eff_checks_organization_id", "organization_id"),
        Index("ix_capa_eff_checks_capa_id", "capa_id"),
        Index("ix_capa_eff_checks_status", "status"),
        Index("ix_capa_eff_checks_planned_date", "planned_check_date"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    capa_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("capas.id", ondelete="RESTRICT"),
        nullable=False,
    )
    check_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    criteria_description: Mapped[str] = mapped_column(Text, nullable=False)
    evaluation_method: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    planned_check_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=EffectivenessCheckStatus.PENDING.value,
        server_default=EffectivenessCheckStatus.PENDING.value,
    )
    reviewer_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reviewer_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    findings_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    evidence_document_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )

    capa: Mapped["CAPA"] = relationship(
        "CAPA",
        back_populates="effectiveness_checks",
        foreign_keys=[capa_id],
    )
