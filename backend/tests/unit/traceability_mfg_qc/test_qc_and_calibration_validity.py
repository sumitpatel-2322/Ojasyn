from datetime import date, datetime, timezone
from decimal import Decimal
import uuid

import pytest

try:
    import app.models.domain.sops  # noqa: F401
except ImportError:
    pass
try:
    import app.models.domain.manufacturing_execution  # noqa: F401
except ImportError:
    pass
import app.models.domain.qc_equipment  # noqa: F401

from app.core.database import UnitOfWork
from app.domain.rules_engine import evaluate_qc_result_vs_specification
from app.models.domain.qc_equipment import CalibrationRecord, Equipment
from app.models.schemas.qc_equipment import (
    CalibrationRecordCreateRequest,
    EquipmentUsageCreateRequest,
    QCTestDefinitionCreateRequest,
    QCTestResultCreateRequest,
)
from app.services.qc_equipment_service import evaluate_calibration_validity
from tests.conftest import create_seeded_tenant_actor


def test_qc_and_calibration_schema_validators_enforce_invariants() -> None:
    # 1. QCTestDefinitionCreateRequest rejects min_limit > max_limit
    with pytest.raises(ValueError, match="min_limit"):
        QCTestDefinitionCreateRequest(
            test_code="ASSAY-01",
            test_name="Assay by HPLC",
            test_method_reference="USP <621>",
            test_type="NUMERIC_RANGE",
            min_limit=Decimal("105.0"),
            max_limit=Decimal("95.0"),
            acceptance_criteria="95.0 - 105.0 %",
        )

    # 2. QCTestDefinitionCreateRequest rejects effective_to <= effective_from
    t0 = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="effective_to"):
        QCTestDefinitionCreateRequest(
            test_code="ASSAY-01",
            test_name="Assay by HPLC",
            test_method_reference="USP <621>",
            acceptance_criteria="95.0 - 105.0 %",
            effective_from=t0,
            effective_to=t0,
        )

    # 3. QCTestResultCreateRequest enforces typed value presence
    with pytest.raises(ValueError, match="numeric_result"):
        QCTestResultCreateRequest(
            test_definition_id=uuid.uuid4(),
            result_type="NUMERIC",
            numeric_result=None,
        )
    with pytest.raises(ValueError, match="text_result"):
        QCTestResultCreateRequest(
            test_definition_id=uuid.uuid4(),
            result_type="TEXT",
            text_result="   ",
        )
    with pytest.raises(ValueError, match="boolean_result"):
        QCTestResultCreateRequest(
            test_definition_id=uuid.uuid4(),
            result_type="BOOLEAN",
            boolean_result=None,
        )

    # 4. CalibrationRecordCreateRequest rejects due_at <= calibrated_at
    with pytest.raises(ValueError, match="due_at"):
        CalibrationRecordCreateRequest(
            calibration_code="CAL-2026-01",
            calibrated_at=t0,
            due_at=t0,
            calibration_provider="NABL Metrology Labs",
        )

    # 5. EquipmentUsageCreateRequest rejects usage_end_at < usage_start_at
    with pytest.raises(ValueError, match="usage_end_at"):
        EquipmentUsageCreateRequest(
            equipment_id=uuid.uuid4(),
            usage_start_at=datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc),
            usage_end_at=datetime(2026, 5, 2, 11, 0, tzinfo=timezone.utc),
            usage_purpose="Blending stage",
        )


def test_evaluate_calibration_validity_across_all_historical_states(sqlite_db) -> None:
    session_factory = sqlite_db["session_factory"]

    with UnitOfWork(session_factory) as uow:
        assert uow.session is not None
        seed = create_seeded_tenant_actor(
            uow.session,
            org_name="Metrology Pharma Ltd",
            site_code="MET-S1",
            email="qc.head@metrology.in",
            role_code="QC_HEAD",
            scope_type="SITE",
        )
        org = seed["organization"]
        site = seed["site"]
        member = seed["membership"]
        user = seed["user"]

        # Case A: calibration_required = False -> VALID even with 0 records
        eq_no_cal = Equipment(
            organization_id=org.id,
            site_id=site.id,
            equipment_code="EQ-STIRRER-01",
            equipment_name="Manual Stainless Paddle",
            equipment_category="ACCESSORY",
            operational_status="ACTIVE",
            qualification_status="NOT_REQUIRED",
            calibration_required=False,
        )
        # Case B: calibration_required = True, 0 records -> MISSING_RECORD
        eq_missing = Equipment(
            organization_id=org.id,
            site_id=site.id,
            equipment_code="EQ-BAL-MISSING",
            equipment_name="Analytical Balance Uncalibrated",
            equipment_category="BALANCE",
            operational_status="ACTIVE",
            qualification_status="QUALIFIED",
            calibration_required=True,
        )
        # Case C: Equipment with historical calibration timeline
        # Even if operational_status is later OUT_OF_SERVICE, historical validity at t_valid is VALID!
        eq_timeline = Equipment(
            organization_id=org.id,
            site_id=site.id,
            equipment_code="EQ-HPLC-01",
            equipment_name="HPLC System 01",
            equipment_category="CHROMATOGRAPHY",
            operational_status="OUT_OF_SERVICE",
            qualification_status="QUALIFIED",
            calibration_required=True,
            calibration_frequency_days=180,
        )
        # Case D: Equipment with DRAFT (unapproved) calibration record
        eq_unverified = Equipment(
            organization_id=org.id,
            site_id=site.id,
            equipment_code="EQ-PH-01",
            equipment_name="pH Meter 01",
            equipment_category="ANALYTICAL",
            operational_status="ACTIVE",
            qualification_status="QUALIFIED",
            calibration_required=True,
        )
        # Case E: Equipment with FAILED calibration record
        eq_failed = Equipment(
            organization_id=org.id,
            site_id=site.id,
            equipment_code="EQ-THERM-FAIL",
            equipment_name="Reference Thermometer",
            equipment_category="THERMOMETER",
            operational_status="ACTIVE",
            qualification_status="QUALIFIED",
            calibration_required=True,
        )
        uow.session.add_all([eq_no_cal, eq_missing, eq_timeline, eq_unverified, eq_failed])
        uow.session.flush()

        # Approved calibration for eq_timeline: 2026-01-01 to 2026-07-01
        cal_approved = CalibrationRecord(
            organization_id=org.id,
            site_id=site.id,
            equipment_id=eq_timeline.id,
            calibration_code="CAL-HPLC-2026-H1",
            calibrated_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
            due_at=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
            calibration_provider="Agilent Metrology",
            calibration_result="PASS",
            record_status="APPROVED",
            recorded_by_user_id=user.id,
            recorded_by_membership_id=member.id,
            approved_at=datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc),
        )
        # Draft calibration for eq_unverified: 2026-01-01 to 2026-07-01
        cal_draft = CalibrationRecord(
            organization_id=org.id,
            site_id=site.id,
            equipment_id=eq_unverified.id,
            calibration_code="CAL-PH-2026-DRAFT",
            calibrated_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
            due_at=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
            calibration_provider="Mettler Service",
            calibration_result="PASS",
            record_status="DRAFT",
            recorded_by_user_id=user.id,
            recorded_by_membership_id=member.id,
        )
        # Failed calibration for eq_failed: 2026-01-01 to 2026-07-01
        cal_failed = CalibrationRecord(
            organization_id=org.id,
            site_id=site.id,
            equipment_id=eq_failed.id,
            calibration_code="CAL-THERM-2026-FAIL",
            calibrated_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
            due_at=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
            calibration_provider="Fluke Calibration",
            calibration_result="OUT_OF_TOLERANCE",
            record_status="APPROVED",
            recorded_by_user_id=user.id,
            recorded_by_membership_id=member.id,
        )
        uow.session.add_all([cal_approved, cal_draft, cal_failed])
        uow.session.flush()

        t_before = datetime(2025, 12, 15, 12, 0, tzinfo=timezone.utc)
        t_during = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
        t_after = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

        # 1. Calibration not required -> VALID
        res_no_cal = evaluate_calibration_validity(
            uow.session,
            organization_id=org.id,
            equipment_id=eq_no_cal.id,
            at=t_during,
        )
        assert res_no_cal.validity_status == "VALID"

        # 2. Zero calibration records -> MISSING_RECORD
        res_missing = evaluate_calibration_validity(
            uow.session,
            organization_id=org.id,
            equipment_id=eq_missing.id,
            at=t_during,
        )
        assert res_missing.validity_status == "MISSING_RECORD"
        assert res_missing.applicable_calibration_record_id is None

        # 3. Timestamp before first calibration -> NOT_CALIBRATED
        res_before = evaluate_calibration_validity(
            uow.session,
            organization_id=org.id,
            equipment_id=eq_timeline.id,
            at=t_before,
        )
        assert res_before.validity_status == "NOT_CALIBRATED"

        # 4. Timestamp inside approved window -> VALID (even though equipment.operational_status == OUT_OF_SERVICE)
        res_during = evaluate_calibration_validity(
            uow.session,
            organization_id=org.id,
            equipment_id=eq_timeline.id,
            at=t_during,
        )
        assert res_during.validity_status == "VALID"
        assert res_during.applicable_calibration_record_id == cal_approved.id

        # 5. Timestamp after due_at -> EXPIRED
        res_after = evaluate_calibration_validity(
            uow.session,
            organization_id=org.id,
            equipment_id=eq_timeline.id,
            at=t_after,
        )
        assert res_after.validity_status == "EXPIRED"
        assert res_after.applicable_calibration_record_id == cal_approved.id

        # 6. Timestamp covered by unapproved DRAFT calibration -> UNVERIFIED
        res_unverified = evaluate_calibration_validity(
            uow.session,
            organization_id=org.id,
            equipment_id=eq_unverified.id,
            at=t_during,
        )
        assert res_unverified.validity_status == "UNVERIFIED"
        assert res_unverified.applicable_calibration_record_id == cal_draft.id

        # 7. Timestamp covered by OUT_OF_TOLERANCE / FAIL calibration -> NOT_CALIBRATED
        res_failed = evaluate_calibration_validity(
            uow.session,
            organization_id=org.id,
            equipment_id=eq_failed.id,
            at=t_during,
        )
        assert res_failed.validity_status == "NOT_CALIBRATED"
        assert res_failed.applicable_calibration_record_id == cal_failed.id


def test_qc_result_vs_specification_deterministic_evaluation_and_unit_conversion() -> None:
    # 1. Numeric range with unit conversion (0.5 G == 500 MG within [495, 505] MG)
    pass_conv = evaluate_qc_result_vs_specification(
        test_code="ASSAY-MG",
        test_type="NUMERIC_RANGE",
        result_type="NUMERIC",
        numeric_result=Decimal("0.500"),
        actual_unit_code="G",
        expected_unit_code="MG",
        min_limit=Decimal("495.0"),
        max_limit=Decimal("505.0"),
        acceptance_criteria="495.0 to 505.0 mg",
        review_status="APPROVED",
        require_approved_review=True,
    )
    assert pass_conv.outcome == "PASS"
    assert pass_conv.actual_numeric_value == Decimal("500.000000")

    # 2. Out-of-specification numeric result -> FAIL
    fail_oos = evaluate_qc_result_vs_specification(
        test_code="ASSAY-PCT",
        test_type="NUMERIC_RANGE",
        result_type="NUMERIC",
        numeric_result=Decimal("88.4"),
        actual_unit_code="%W/W",
        expected_unit_code="%W/W",
        min_limit=Decimal("95.0"),
        max_limit=Decimal("105.0"),
        acceptance_criteria="95.0% to 105.0%",
    )
    assert fail_oos.outcome == "FAIL"

    # 3. Within range but pending review when require_approved_review=True -> UNVERIFIED
    unverified_qc = evaluate_qc_result_vs_specification(
        test_code="ASSAY-PCT",
        test_type="NUMERIC_RANGE",
        result_type="NUMERIC",
        numeric_result=Decimal("99.8"),
        actual_unit_code="%W/W",
        expected_unit_code="%W/W",
        min_limit=Decimal("95.0"),
        max_limit=Decimal("105.0"),
        review_status="PENDING_REVIEW",
        require_approved_review=True,
    )
    assert unverified_qc.outcome == "UNVERIFIED"
