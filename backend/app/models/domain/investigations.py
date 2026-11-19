import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.domain.enums import (
    HypothesisStatus,
    InvestigationRunStatus,
    InvestigationStatus,
    InvestigationStepStatus,
    InvestigationTriggerType,
    ReportStatus,
    SOPComplianceResultStatus,
    UncertaintyLevel,
)
from app.models.domain.base import Base, TenantScopedMixin, TimestampVersionMixin, UUIDPKMixin, utcnow

JSONType = JSON().with_variant(JSONB(), "postgresql")


class Investigation(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """
    Durable Investigation case aggregate root (`D1`).
    Supports complaint-triggered, deviation-triggered (`complaint_id` nullable), or combined investigations.
    """

    __tablename__ = "investigations"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_investigations_org_id_id"),
        UniqueConstraint("organization_id", "investigation_number", name="uq_investigations_org_num"),
        ForeignKeyConstraint(
            ["organization_id", "complaint_id"],
            ["complaints.organization_id", "complaints.id"],
            ondelete="RESTRICT",
            name="fk_investigations_org_complaint",
        ),
        ForeignKeyConstraint(
            ["organization_id", "deviation_id"],
            ["deviations.organization_id", "deviations.id"],
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_investigations_org_dev",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_investigations_org_batch",
        ),
        ForeignKeyConstraint(
            ["organization_id", "product_id"],
            ["products.organization_id", "products.id"],
            ondelete="RESTRICT",
            name="fk_investigations_org_product",
        ),
        ForeignKeyConstraint(
            ["organization_id", "site_id"],
            ["sites.organization_id", "sites.id"],
            ondelete="RESTRICT",
            name="fk_investigations_org_site",
        ),
        ForeignKeyConstraint(
            ["organization_id", "owner_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_investigations_org_owner",
        ),
        ForeignKeyConstraint(
            ["organization_id", "qa_reviewed_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_investigations_org_qa_reviewer",
        ),
        Index("ix_investigations_org_status", "organization_id", "status"),
    )

    complaint_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("complaints.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    deviation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "deviations.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_investigations_deviation_id",
        ),
        nullable=True,
        index=True,
    )
    batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    product_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    site_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sites.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    investigation_number: Mapped[str] = mapped_column(String(60), nullable=False)
    trigger_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default=InvestigationTriggerType.COMPLAINT.value
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    scope_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=InvestigationStatus.QUEUED.value, index=True
    )
    owner_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    qa_reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    qa_reviewed_by_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    qa_reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    qa_decision: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    qa_decision_comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approval_record_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("approval_records.id", ondelete="RESTRICT"),
        nullable=True,
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    closure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    runs: Mapped[List["InvestigationRun"]] = relationship(
        "InvestigationRun",
        back_populates="investigation",
        foreign_keys="InvestigationRun.investigation_id",
        order_by="InvestigationRun.run_number.asc()",
    )
    evidence_references: Mapped[List["EvidenceReference"]] = relationship(
        "EvidenceReference",
        back_populates="investigation",
        foreign_keys="EvidenceReference.investigation_id",
        order_by="EvidenceReference.created_at.asc()",
    )
    compliance_results: Mapped[List["SOPComplianceResult"]] = relationship(
        "SOPComplianceResult",
        back_populates="investigation",
        foreign_keys="SOPComplianceResult.investigation_id",
        order_by="SOPComplianceResult.created_at.asc()",
    )
    findings: Mapped[List["InvestigationFinding"]] = relationship(
        "InvestigationFinding",
        back_populates="investigation",
        foreign_keys="InvestigationFinding.investigation_id",
        order_by="InvestigationFinding.created_at.asc()",
    )
    hypotheses: Mapped[List["RootCauseHypothesis"]] = relationship(
        "RootCauseHypothesis",
        back_populates="investigation",
        foreign_keys="RootCauseHypothesis.investigation_id",
        order_by="RootCauseHypothesis.created_at.asc()",
    )
    reports: Mapped[List["GeneratedReport"]] = relationship(
        "GeneratedReport",
        back_populates="investigation",
        foreign_keys="GeneratedReport.investigation_id",
        order_by="GeneratedReport.version_number.asc()",
    )


class InvestigationRun(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Durable, checkpointed execution run for an Investigation (`D1`, `D2`)."""

    __tablename__ = "investigation_runs"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_inv_runs_org_id_id"),
        UniqueConstraint(
            "organization_id",
            "investigation_id",
            "run_number",
            name="uq_inv_runs_org_inv_num",
        ),
        ForeignKeyConstraint(
            ["organization_id", "investigation_id"],
            ["investigations.organization_id", "investigations.id"],
            ondelete="RESTRICT",
            name="fk_inv_runs_org_inv",
        ),
        CheckConstraint("run_number >= 1", name="ck_inv_runs_num_pos"),
        CheckConstraint("attempt_count >= 0", name="ck_inv_runs_attempt_non_neg"),
        CheckConstraint("max_attempts >= 1", name="ck_inv_runs_max_attempts_pos"),
        CheckConstraint(
            "resume_from_step_seq IS NULL OR resume_from_step_seq >= 1",
            name="ck_inv_runs_resume_seq_pos",
        ),
        Index(
            "ix_inv_runs_org_idemp",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where="idempotency_key IS NOT NULL",
        ),
        Index("ix_inv_runs_org_inv_status", "organization_id", "investigation_id", "status"),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("investigations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    run_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    workflow_version: Mapped[str] = mapped_column(String(40), nullable=False, default="phase5-v1")
    model_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    rule_engine_version: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=InvestigationRunStatus.QUEUED.value, index=True
    )
    waiting_reason: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    resume_from_step_seq: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    checkpoint_state_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    triggered_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)

    investigation: Mapped["Investigation"] = relationship(
        "Investigation",
        back_populates="runs",
        foreign_keys=[investigation_id],
    )
    steps: Mapped[List["InvestigationStep"]] = relationship(
        "InvestigationStep",
        back_populates="run",
        foreign_keys="InvestigationStep.investigation_run_id",
        order_by="InvestigationStep.step_seq.asc()",
    )


class InvestigationStep(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """Per-node execution audit & checkpoint log inside an InvestigationRun (`D1`)."""

    __tablename__ = "investigation_steps"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_inv_steps_org_id_id"),
        UniqueConstraint(
            "organization_id",
            "investigation_run_id",
            "step_seq",
            name="uq_inv_steps_org_run_seq",
        ),
        ForeignKeyConstraint(
            ["organization_id", "investigation_id"],
            ["investigations.organization_id", "investigations.id"],
            ondelete="RESTRICT",
            name="fk_inv_steps_org_inv",
        ),
        ForeignKeyConstraint(
            ["organization_id", "investigation_run_id"],
            ["investigation_runs.organization_id", "investigation_runs.id"],
            ondelete="RESTRICT",
            name="fk_inv_steps_org_run",
        ),
        CheckConstraint("step_seq >= 1", name="ck_inv_steps_seq_pos"),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("investigations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    investigation_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("investigation_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    step_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    node_name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=InvestigationStepStatus.STARTED.value, index=True
    )
    input_refs_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    output_refs_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped["InvestigationRun"] = relationship(
        "InvestigationRun",
        back_populates="steps",
        foreign_keys=[investigation_run_id],
    )


class EvidenceReference(UUIDPKMixin, TenantScopedMixin, Base):
    """
    Immutable provenance pointer persisted by retrieval nodes (`D4`).
    Downstream RCA hypotheses, findings, and reports may cite ONLY persisted EvidenceReference IDs.
    """

    __tablename__ = "evidence_references"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_ev_refs_org_id_id"),
        ForeignKeyConstraint(
            ["organization_id", "investigation_id"],
            ["investigations.organization_id", "investigations.id"],
            ondelete="RESTRICT",
            name="fk_ev_refs_org_inv",
        ),
        ForeignKeyConstraint(
            ["organization_id", "investigation_run_id"],
            ["investigation_runs.organization_id", "investigation_runs.id"],
            ondelete="RESTRICT",
            name="fk_ev_refs_org_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_ev_refs_org_doc",
        ),
        CheckConstraint("page_number IS NULL OR page_number >= 1", name="ck_ev_refs_page_pos"),
        Index("ix_ev_refs_inv_source", "investigation_id", "source_table", "source_record_id"),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("investigations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    investigation_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("investigation_runs.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    source_table: Mapped[str] = mapped_column(String(80), nullable=False)
    source_record_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    source_row_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    document_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    document_checksum_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    section_ref: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    field_path: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    quoted_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provenance_metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    collected_by: Mapped[str] = mapped_column(String(40), nullable=False, default="SYSTEM_WORKER")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )

    investigation: Mapped["Investigation"] = relationship(
        "Investigation",
        back_populates="evidence_references",
        foreign_keys=[investigation_id],
    )


class SOPComplianceResult(UUIDPKMixin, TenantScopedMixin, Base):
    """
    Deterministic SOP, manufacturing, QC, and calibration compliance evaluation (`D3`).
    Zero LLM involvement; FAIL results store suggested Deviation draft payloads (never auto-created).
    """

    __tablename__ = "sop_compliance_results"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_sop_comp_org_id_id"),
        ForeignKeyConstraint(
            ["organization_id", "investigation_id"],
            ["investigations.organization_id", "investigations.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_inv",
        ),
        ForeignKeyConstraint(
            ["organization_id", "investigation_run_id"],
            ["investigation_runs.organization_id", "investigation_runs.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["batches.organization_id", "batches.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_batch",
        ),
        ForeignKeyConstraint(
            ["organization_id", "sop_id"],
            ["sops.organization_id", "sops.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_sop",
        ),
        ForeignKeyConstraint(
            ["organization_id", "sop_revision_id"],
            ["sop_revisions.organization_id", "sop_revisions.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_sop_rev",
        ),
        ForeignKeyConstraint(
            ["organization_id", "sop_step_id"],
            ["sop_steps.organization_id", "sop_steps.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_sop_step",
        ),
        ForeignKeyConstraint(
            ["organization_id", "deterministic_check_id"],
            ["deterministic_evidence_checks.organization_id", "deterministic_evidence_checks.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_det_chk",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_execution_record_id"],
            ["batch_execution_records.organization_id", "batch_execution_records.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_exec_rec",
        ),
        ForeignKeyConstraint(
            ["organization_id", "batch_execution_step_id"],
            ["batch_execution_steps.organization_id", "batch_execution_steps.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_exec_step",
        ),
        ForeignKeyConstraint(
            ["organization_id", "observation_id"],
            ["process_parameter_observations.organization_id", "process_parameter_observations.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_obs",
        ),
        ForeignKeyConstraint(
            ["organization_id", "equipment_id"],
            ["equipment.organization_id", "equipment.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_equip",
        ),
        ForeignKeyConstraint(
            ["organization_id", "calibration_record_id"],
            ["calibration_records.organization_id", "calibration_records.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_cal",
        ),
        ForeignKeyConstraint(
            ["organization_id", "qc_test_result_id"],
            ["qc_test_results.organization_id", "qc_test_results.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_qc_res",
        ),
        ForeignKeyConstraint(
            ["organization_id", "evidence_ref_id"],
            ["evidence_references.organization_id", "evidence_references.id"],
            ondelete="RESTRICT",
            name="fk_sop_comp_org_ev_ref",
        ),
        Index("ix_sop_comp_inv_status", "investigation_id", "status"),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("investigations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    investigation_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("investigation_runs.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    sop_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sops.id", ondelete="RESTRICT"),
        nullable=True,
    )
    sop_revision_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sop_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    sop_step_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sop_steps.id", ondelete="RESTRICT"),
        nullable=True,
    )
    deterministic_check_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("deterministic_evidence_checks.id", ondelete="RESTRICT"),
        nullable=True,
    )
    batch_execution_record_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batch_execution_records.id", ondelete="RESTRICT"),
        nullable=True,
    )
    batch_execution_step_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("batch_execution_steps.id", ondelete="RESTRICT"),
        nullable=True,
    )
    observation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("process_parameter_observations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    equipment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("equipment.id", ondelete="RESTRICT"),
        nullable=True,
    )
    calibration_record_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("calibration_records.id", ondelete="RESTRICT"),
        nullable=True,
    )
    qc_test_result_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("qc_test_results.id", ondelete="RESTRICT"),
        nullable=True,
    )
    evidence_ref_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("evidence_references.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    check_type: Mapped[str] = mapped_column(String(60), nullable=False)
    rule_identifier: Mapped[str] = mapped_column(String(80), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(40), nullable=False, default="1.0.0")
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=SOPComplianceResultStatus.PASS.value, index=True
    )
    actual_value_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    actual_numeric_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    expected_requirement_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expected_min_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    expected_max_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    unit_code: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    deviation_suggested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suggested_deviation_payload_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONType, nullable=True
    )
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )

    investigation: Mapped["Investigation"] = relationship(
        "Investigation",
        back_populates="compliance_results",
        foreign_keys=[investigation_id],
    )


class InvestigationFinding(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """
    Structured investigation finding distinguishing verified deterministic observations
    (`is_verified_observation=True`) from analyst/AI inferences (`False`) (`D5`).
    """

    __tablename__ = "investigation_findings"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_inv_findings_org_id_id"),
        UniqueConstraint(
            "organization_id",
            "investigation_id",
            "finding_code",
            name="uq_inv_findings_org_inv_code",
        ),
        ForeignKeyConstraint(
            ["organization_id", "investigation_id"],
            ["investigations.organization_id", "investigations.id"],
            ondelete="RESTRICT",
            name="fk_inv_findings_org_inv",
        ),
        ForeignKeyConstraint(
            ["organization_id", "investigation_run_id"],
            ["investigation_runs.organization_id", "investigation_runs.id"],
            ondelete="RESTRICT",
            name="fk_inv_findings_org_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "sop_compliance_result_id"],
            ["sop_compliance_results.organization_id", "sop_compliance_results.id"],
            ondelete="RESTRICT",
            name="fk_inv_findings_org_comp",
        ),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("investigations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    investigation_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("investigation_runs.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    finding_code: Mapped[str] = mapped_column(String(60), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    is_verified_observation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    severity: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    evidence_ref_ids_json: Mapped[List[str]] = mapped_column(JSONType, nullable=False, default=list)
    sop_compliance_result_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sop_compliance_results.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_origin: Mapped[str] = mapped_column(String(30), nullable=False, default="DETERMINISTIC")

    investigation: Mapped["Investigation"] = relationship(
        "Investigation",
        back_populates="findings",
        foreign_keys=[investigation_id],
    )


class RootCauseHypothesis(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """
    Evidence-gated root cause hypothesis subject to QA review (`D5`, `D7`).
    Never creates CAPA records directly.
    """

    __tablename__ = "root_cause_hypotheses"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_rc_hyp_org_id_id"),
        UniqueConstraint(
            "organization_id",
            "investigation_id",
            "hypothesis_code",
            name="uq_rc_hyp_org_inv_code",
        ),
        ForeignKeyConstraint(
            ["organization_id", "investigation_id"],
            ["investigations.organization_id", "investigations.id"],
            ondelete="RESTRICT",
            name="fk_rc_hyp_org_inv",
        ),
        ForeignKeyConstraint(
            ["organization_id", "investigation_run_id"],
            ["investigation_runs.organization_id", "investigation_runs.id"],
            ondelete="RESTRICT",
            name="fk_rc_hyp_org_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "reviewed_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_rc_hyp_org_reviewer",
        ),
        CheckConstraint(
            "confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)",
            name="ck_rc_hyp_conf_range",
        ),
        CheckConstraint(
            "reviewed_by_user_id IS NULL OR created_by_user_id IS NULL OR reviewed_by_user_id != created_by_user_id",
            name="ck_rc_hyp_maker_checker_user",
        ),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("investigations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    investigation_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("investigation_runs.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    hypothesis_code: Mapped[str] = mapped_column(String(60), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_evidence_ids_json: Mapped[List[str]] = mapped_column(
        JSONType, nullable=False, default=list
    )
    contradicting_evidence_ids_json: Mapped[List[str]] = mapped_column(
        JSONType, nullable=False, default=list
    )
    uncertainty_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default=UncertaintyLevel.MEDIUM.value
    )
    confidence_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)
    recommended_verification: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    limitations: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=HypothesisStatus.PROPOSED.value, index=True
    )
    model_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reviewed_by_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    review_comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approval_record_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("approval_records.id", ondelete="RESTRICT"),
        nullable=True,
    )

    investigation: Mapped["Investigation"] = relationship(
        "Investigation",
        back_populates="hypotheses",
        foreign_keys=[investigation_id],
    )


class GeneratedReport(UUIDPKMixin, TenantScopedMixin, TimestampVersionMixin, Base):
    """
    Versioned, checksum-verified DOCX Investigation Report stored in the document vault (`D6`, `D7`).
    """

    __tablename__ = "generated_reports"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_gen_reports_org_id_id"),
        UniqueConstraint("organization_id", "report_number", name="uq_gen_reports_org_num"),
        UniqueConstraint(
            "organization_id",
            "investigation_id",
            "version_number",
            name="uq_gen_reports_org_inv_ver",
        ),
        ForeignKeyConstraint(
            ["organization_id", "investigation_id"],
            ["investigations.organization_id", "investigations.id"],
            ondelete="RESTRICT",
            name="fk_gen_reports_org_inv",
        ),
        ForeignKeyConstraint(
            ["organization_id", "investigation_run_id"],
            ["investigation_runs.organization_id", "investigation_runs.id"],
            ondelete="RESTRICT",
            name="fk_gen_reports_org_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="RESTRICT",
            name="fk_gen_reports_org_doc",
        ),
        ForeignKeyConstraint(
            ["organization_id", "reviewed_by_membership_id"],
            ["organization_memberships.organization_id", "organization_memberships.id"],
            ondelete="RESTRICT",
            name="fk_gen_reports_org_reviewer",
        ),
        ForeignKeyConstraint(
            ["organization_id", "supersedes_report_id"],
            ["generated_reports.organization_id", "generated_reports.id"],
            ondelete="RESTRICT",
            name="fk_gen_reports_org_supersedes",
        ),
        CheckConstraint("version_number >= 1", name="ck_gen_reports_ver_pos"),
        CheckConstraint(
            "reviewed_by_user_id IS NULL OR generated_by_user_id IS NULL OR reviewed_by_user_id != generated_by_user_id",
            name="ck_gen_reports_maker_checker_user",
        ),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("investigations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    investigation_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("investigation_runs.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    report_number: Mapped[str] = mapped_column(String(60), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=ReportStatus.GENERATED.value, index=True
    )
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    document_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    checksum_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    report_snapshot_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    generated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reviewed_by_membership_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    review_comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approval_record_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("approval_records.id", ondelete="RESTRICT"),
        nullable=True,
    )
    supersedes_report_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("generated_reports.id", ondelete="RESTRICT"),
        nullable=True,
    )

    investigation: Mapped["Investigation"] = relationship(
        "Investigation",
        back_populates="reports",
        foreign_keys=[investigation_id],
    )
