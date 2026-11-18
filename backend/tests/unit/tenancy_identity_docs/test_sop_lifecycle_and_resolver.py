from datetime import datetime, timezone
from decimal import Decimal

import pytest

import app.models.domain.sops  # noqa: F401
from app.core.database import UnitOfWork
from app.core.exceptions import ConflictError
from app.domain.state_machines import assert_transition
from app.models.domain.sops import SOP, SOPRevision, SOPStep
from app.services.sop_service import (
    SOP_REVISION_TRANSITIONS,
    SOP_STATUS_TRANSITIONS,
    SOPResolver,
)
from tests.conftest import create_seeded_tenant_actor


def test_sop_and_revision_state_machine_transitions() -> None:
    # Valid SOP status transitions
    assert_transition(SOP_STATUS_TRANSITIONS, "DRAFT", "ACTIVE", entity_name="SOP")
    assert_transition(SOP_STATUS_TRANSITIONS, "ACTIVE", "SUPERSEDED", entity_name="SOP")
    assert_transition(SOP_STATUS_TRANSITIONS, "ACTIVE", "OBSOLETE", entity_name="SOP")
    assert_transition(SOP_STATUS_TRANSITIONS, "OBSOLETE", "ARCHIVED", entity_name="SOP")

    with pytest.raises(ConflictError):
        assert_transition(SOP_STATUS_TRANSITIONS, "ARCHIVED", "ACTIVE", entity_name="SOP")

    # Valid SOPRevision transitions
    assert_transition(
        SOP_REVISION_TRANSITIONS, "DRAFT", "REVIEW", entity_name="SOPRevision"
    )
    assert_transition(
        SOP_REVISION_TRANSITIONS, "REVIEW", "APPROVED", entity_name="SOPRevision"
    )
    assert_transition(
        SOP_REVISION_TRANSITIONS, "REVIEW", "REJECTED", entity_name="SOPRevision"
    )
    assert_transition(
        SOP_REVISION_TRANSITIONS, "APPROVED", "EFFECTIVE", entity_name="SOPRevision"
    )
    assert_transition(
        SOP_REVISION_TRANSITIONS, "EFFECTIVE", "SUPERSEDED", entity_name="SOPRevision"
    )
    assert_transition(
        SOP_REVISION_TRANSITIONS, "EFFECTIVE", "OBSOLETE", entity_name="SOPRevision"
    )

    # Invalid skips or regressions
    with pytest.raises(ConflictError):
        assert_transition(
            SOP_REVISION_TRANSITIONS, "DRAFT", "EFFECTIVE", entity_name="SOPRevision"
        )
    with pytest.raises(ConflictError):
        assert_transition(
            SOP_REVISION_TRANSITIONS, "REVIEW", "EFFECTIVE", entity_name="SOPRevision"
        )
    with pytest.raises(ConflictError):
        assert_transition(
            SOP_REVISION_TRANSITIONS, "REJECTED", "APPROVED", entity_name="SOPRevision"
        )


def test_sop_resolver_historical_windows_and_all_outcomes(sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        actor = create_seeded_tenant_actor(
            uow.session,
            org_name="Chronos Pharma",
            site_code="CHR-01",
            email="qa@chronos.in",
            role_code="QA_HEAD",
            scope_type="SITE",
        )
        org_id = actor["organization"].id
        site_id = actor["site"].id
        user_id = actor["user"].id

        # 1. SOP_NOT_FOUND
        not_found = SOPResolver.resolve(
            "SOP-MISSING-99",
            site_id,
            at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            db=uow.session,
            organization_id=org_id,
        )
        assert not_found.outcome == "SOP_NOT_FOUND"
        assert not not_found.is_resolved

        # 2. PENDING_APPROVAL_ONLY
        sop_pending = SOP(
            organization_id=org_id,
            site_id=site_id,
            sop_code="SOP-PEND-01",
            title="Pending Procedure",
            sop_status="DRAFT",
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        uow.session.add(sop_pending)
        uow.session.flush()
        uow.session.add(
            SOPRevision(
                organization_id=org_id,
                sop_id=sop_pending.id,
                site_id=site_id,
                revision_number="1.0",
                revision_sequence=1,
                revision_status="APPROVED",
                approval_status="APPROVED",
                created_by_user_id=user_id,
                updated_by_user_id=user_id,
            )
        )
        uow.session.flush()

        pending_res = SOPResolver(uow.session, org_id).resolve(
            "SOP-PEND-01",
            site_id,
            at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        )
        assert pending_res.outcome == "PENDING_APPROVAL_ONLY"

        # 3. Historical timeline on SOP-GRAN-01:
        # Rev 1.0: [2026-01-01, 2026-04-01) SUPERSEDED (temp 20..25 C)
        # Rev 2.0: [2026-04-01, 2026-08-01) EFFECTIVE (temp 18..22 C)
        sop_gran = SOP(
            organization_id=org_id,
            site_id=site_id,
            sop_code="SOP-GRAN-01",
            title="Wet Granulation Procedure",
            sop_status="ACTIVE",
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        uow.session.add(sop_gran)
        uow.session.flush()

        rev1 = SOPRevision(
            organization_id=org_id,
            sop_id=sop_gran.id,
            site_id=site_id,
            revision_number="1.0",
            revision_sequence=1,
            effective_from=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
            effective_to=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
            revision_status="SUPERSEDED",
            approval_status="APPROVED",
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        rev2 = SOPRevision(
            organization_id=org_id,
            sop_id=sop_gran.id,
            site_id=site_id,
            revision_number="2.0",
            revision_sequence=2,
            effective_from=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
            effective_to=datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
            revision_status="EFFECTIVE",
            approval_status="APPROVED",
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        uow.session.add_all([rev1, rev2])
        uow.session.flush()

        uow.session.add(
            SOPStep(
                organization_id=org_id,
                sop_revision_id=rev1.id,
                step_code="STEP-TEMP",
                step_sequence=1,
                title="Granulation Temperature Check v1",
                instruction="Maintain temperature between 20 and 25 C",
                parameter_code="GRAN_TEMP",
                parameter_name="Granulation Temperature",
                expected_data_type="NUMERIC",
                unit_code="C",
                min_value=Decimal("20.0"),
                max_value=Decimal("25.0"),
                created_by_user_id=user_id,
                updated_by_user_id=user_id,
            )
        )
        uow.session.add(
            SOPStep(
                organization_id=org_id,
                sop_revision_id=rev2.id,
                step_code="STEP-TEMP",
                step_sequence=1,
                title="Granulation Temperature Check v2",
                instruction="Maintain temperature between 18 and 22 C",
                parameter_code="GRAN_TEMP",
                parameter_name="Granulation Temperature",
                expected_data_type="NUMERIC",
                unit_code="C",
                min_value=Decimal("18.0"),
                max_value=Decimal("22.0"),
                created_by_user_id=user_id,
                updated_by_user_id=user_id,
            )
        )
        uow.session.flush()

        # Before Rev 1.0 effective_from -> NO_APPLICABLE_REVISION
        before_res = SOPResolver.resolve(
            "SOP-GRAN-01",
            site_id,
            at=datetime(2025, 12, 15, tzinfo=timezone.utc),
            db=uow.session,
            organization_id=org_id,
        )
        assert before_res.outcome == "NO_APPLICABLE_REVISION"

        # During Rev 1.0 window -> RESOLVED to Rev 1.0 (NOT latest Rev 2.0)
        r1_res = SOPResolver.resolve(
            "SOP-GRAN-01",
            site_id,
            at=datetime(2026, 2, 15, tzinfo=timezone.utc),
            db=uow.session,
            organization_id=org_id,
        )
        assert r1_res.outcome == "RESOLVED"
        assert r1_res.revision_number == "1.0"
        step_v1 = r1_res.get_step(parameter_code="GRAN_TEMP")
        assert step_v1 is not None
        assert step_v1.max_value == Decimal("25.0")

        # At exact boundary 2026-04-01T00:00:00Z -> RESOLVED to Rev 2.0 ([from, to) half-open)
        r2_res = SOPResolver.resolve(
            "SOP-GRAN-01",
            site_id,
            at=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
            db=uow.session,
            organization_id=org_id,
        )
        assert r2_res.outcome == "RESOLVED"
        assert r2_res.revision_number == "2.0"
        step_v2 = r2_res.get_step(step_code="STEP-TEMP")
        assert step_v2 is not None
        assert step_v2.max_value == Decimal("22.0")

        # After Rev 2.0 effective_to -> EXPIRED_ONLY
        expired_res = SOPResolver.resolve(
            "SOP-GRAN-01",
            site_id,
            at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            db=uow.session,
            organization_id=org_id,
        )
        assert expired_res.outcome == "EXPIRED_ONLY"

        # 4. AMBIGUOUS_MULTIPLE_REVISIONS when two revisions cover the same timestamp
        sop_amb = SOP(
            organization_id=org_id,
            site_id=site_id,
            sop_code="SOP-AMB-01",
            title="Ambiguous SOP",
            sop_status="ACTIVE",
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        uow.session.add(sop_amb)
        uow.session.flush()
        uow.session.add_all(
            [
                SOPRevision(
                    organization_id=org_id,
                    sop_id=sop_amb.id,
                    site_id=site_id,
                    revision_number="1.0",
                    revision_sequence=1,
                    effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    effective_to=datetime(2026, 6, 1, tzinfo=timezone.utc),
                    revision_status="EFFECTIVE",
                    approval_status="APPROVED",
                    created_by_user_id=user_id,
                    updated_by_user_id=user_id,
                ),
                SOPRevision(
                    organization_id=org_id,
                    sop_id=sop_amb.id,
                    site_id=None,
                    revision_number="1.1",
                    revision_sequence=2,
                    effective_from=datetime(2026, 2, 1, tzinfo=timezone.utc),
                    effective_to=datetime(2026, 7, 1, tzinfo=timezone.utc),
                    revision_status="EFFECTIVE",
                    approval_status="APPROVED",
                    created_by_user_id=user_id,
                    updated_by_user_id=user_id,
                ),
            ]
        )
        uow.session.flush()

        amb_res = SOPResolver.resolve(
            "SOP-AMB-01",
            site_id,
            at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            db=uow.session,
            organization_id=org_id,
        )
        assert amb_res.outcome == "AMBIGUOUS_MULTIPLE_REVISIONS"
        assert len(amb_res.candidate_revision_ids) == 2
