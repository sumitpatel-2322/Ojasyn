import pytest
import sqlalchemy as sa

from app.core.exceptions import ConflictError
from app.domain.enums import CAPAActionStatus, CAPAStatus
from app.domain.state_machines import assert_transition
from app.models.domain.qms_loop import CAPA, CAPAAction, CAPAEffectivenessCheck
from app.services.qms_action_service import CAPA_ACTION_TRANSITIONS, CAPA_TRANSITIONS


def test_capa_state_machine_transitions() -> None:
    # Valid transitions
    assert_transition(CAPA_TRANSITIONS, CAPAStatus.DRAFT.value, CAPAStatus.OPEN.value, entity_name="CAPA")
    assert_transition(
        CAPA_TRANSITIONS, CAPAStatus.OPEN.value, CAPAStatus.ACTIONS_IN_PROGRESS.value, entity_name="CAPA"
    )
    assert_transition(
        CAPA_TRANSITIONS,
        CAPAStatus.ACTIONS_IN_PROGRESS.value,
        CAPAStatus.EFFECTIVENESS_REVIEW.value,
        entity_name="CAPA",
    )
    assert_transition(
        CAPA_TRANSITIONS,
        CAPAStatus.EFFECTIVENESS_REVIEW.value,
        CAPAStatus.ACTIONS_IN_PROGRESS.value,
        entity_name="CAPA",
    )
    assert_transition(
        CAPA_TRANSITIONS,
        CAPAStatus.EFFECTIVENESS_REVIEW.value,
        CAPAStatus.CLOSED.value,
        entity_name="CAPA",
    )

    # Invalid transitions
    invalid_pairs = [
        (CAPAStatus.DRAFT.value, CAPAStatus.CLOSED.value),
        (CAPAStatus.DRAFT.value, CAPAStatus.EFFECTIVENESS_REVIEW.value),
        (CAPAStatus.OPEN.value, CAPAStatus.CLOSED.value),
        (CAPAStatus.ACTIONS_IN_PROGRESS.value, CAPAStatus.CLOSED.value),
        (CAPAStatus.CLOSED.value, CAPAStatus.OPEN.value),
        (CAPAStatus.CLOSED.value, CAPAStatus.ACTIONS_IN_PROGRESS.value),
    ]
    for current_state, target_state in invalid_pairs:
        with pytest.raises(ConflictError) as exc_info:
            assert_transition(CAPA_TRANSITIONS, current_state, target_state, entity_name="CAPA")
        assert exc_info.value.error_code == "INVALID_STATE_TRANSITION"


def test_capa_action_state_machine_transitions() -> None:
    assert_transition(
        CAPA_ACTION_TRANSITIONS,
        CAPAActionStatus.PENDING.value,
        CAPAActionStatus.IN_PROGRESS.value,
        entity_name="CAPAAction",
    )
    assert_transition(
        CAPA_ACTION_TRANSITIONS,
        CAPAActionStatus.IN_PROGRESS.value,
        CAPAActionStatus.COMPLETED.value,
        entity_name="CAPAAction",
    )
    assert_transition(
        CAPA_ACTION_TRANSITIONS,
        CAPAActionStatus.COMPLETED.value,
        CAPAActionStatus.VERIFIED.value,
        entity_name="CAPAAction",
    )
    assert_transition(
        CAPA_ACTION_TRANSITIONS,
        CAPAActionStatus.PENDING.value,
        CAPAActionStatus.CANCELLED.value,
        entity_name="CAPAAction",
    )

    for terminal in (CAPAActionStatus.VERIFIED.value, CAPAActionStatus.CANCELLED.value):
        with pytest.raises(ConflictError) as exc_info:
            assert_transition(
                CAPA_ACTION_TRANSITIONS,
                terminal,
                CAPAActionStatus.IN_PROGRESS.value,
                entity_name="CAPAAction",
            )
        assert exc_info.value.error_code == "INVALID_STATE_TRANSITION"


def test_capa_orm_matches_0007_migration_schema() -> None:
    capas_table = CAPA.__table__
    assert set(capas_table.columns.keys()) == {
        "id",
        "organization_id",
        "created_at",
        "updated_at",
        "created_by_user_id",
        "updated_by_user_id",
        "row_version",
        "site_id",
        "capa_number",
        "title",
        "description",
        "capa_type",
        "source_type",
        "complaint_id",
        "investigation_id",
        "deviation_id",
        "is_mandatory_for_complaint_closure",
        "severity",
        "status",
        "owner_membership_id",
        "approver_membership_id",
        "approved_by_user_id",
        "approved_at",
        "due_at",
        "overdue_notified_at",
        "closed_at",
        "closure_reason",
        "root_cause_summary",
    }
    capa_constraints = {c.name for c in capas_table.constraints if c.name}
    assert {
        "uq_capas_org_id_id",
        "uq_capas_org_capa_num",
        "fk_capas_org_site",
        "fk_capas_org_complaint",
        "fk_capas_org_inv",
        "fk_capas_org_dev",
        "fk_capas_org_owner",
        "fk_capas_org_approver",
        "ck_capas_approver_neq_owner",
    }.issubset(capa_constraints)
    assert "ix_capas_org_status_due" in {ix.name for ix in capas_table.indexes}

    actions_table = CAPAAction.__table__
    assert set(actions_table.columns.keys()) == {
        "id",
        "organization_id",
        "created_at",
        "updated_at",
        "created_by_user_id",
        "updated_by_user_id",
        "row_version",
        "capa_id",
        "action_seq",
        "action_type",
        "title",
        "description",
        "owner_membership_id",
        "status",
        "due_at",
        "completed_at",
        "completed_by_user_id",
        "implementation_notes",
        "evidence_document_id",
        "evidence_document_version_id",
    }
    action_constraints = {c.name for c in actions_table.constraints if c.name}
    assert {
        "uq_capa_actions_org_id_id",
        "uq_capa_actions_org_capa_seq",
        "fk_capa_actions_org_capa",
        "fk_capa_actions_org_owner",
        "fk_capa_actions_org_ev_doc",
        "ck_capa_actions_seq_pos",
    }.issubset(action_constraints)
    assert "ix_capa_actions_org_capa_status" in {ix.name for ix in actions_table.indexes}

    eff_table = CAPAEffectivenessCheck.__table__
    assert set(eff_table.columns.keys()) == {
        "id",
        "organization_id",
        "created_at",
        "updated_at",
        "created_by_user_id",
        "updated_by_user_id",
        "row_version",
        "capa_id",
        "check_seq",
        "criteria_description",
        "evaluation_method",
        "planned_check_date",
        "status",
        "reviewer_membership_id",
        "reviewer_user_id",
        "reviewed_at",
        "findings_summary",
        "evidence_document_id",
        "evidence_document_version_id",
    }
    assert isinstance(eff_table.columns["planned_check_date"].type, sa.Date)
    eff_constraints = {c.name for c in eff_table.constraints if c.name}
    assert {
        "uq_capa_eff_org_id_id",
        "uq_capa_eff_org_capa_seq",
        "fk_capa_eff_org_capa",
        "fk_capa_eff_org_reviewer",
        "fk_capa_eff_org_ev_doc",
        "ck_capa_eff_seq_pos",
    }.issubset(eff_constraints)
    assert {
        "ix_capa_eff_checks_organization_id",
        "ix_capa_eff_checks_capa_id",
        "ix_capa_eff_checks_status",
        "ix_capa_eff_checks_planned_date",
    }.issubset({ix.name for ix in eff_table.indexes})
