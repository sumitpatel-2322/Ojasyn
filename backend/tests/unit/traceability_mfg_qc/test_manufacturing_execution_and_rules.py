from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.domain.enums import (
    BatchExecutionStatus,
    CalibrationValidityStatus,
    DeterministicCheckOutcome,
    MasterBatchRecordStatus,
    SOPResolutionOutcome,
)
from app.domain.rules_engine import (
    convert_unit_value,
    evaluate_equipment_calibration_validity,
    evaluate_parameter_vs_sop_limit,
    evaluate_qc_result_vs_specification,
    evaluate_required_execution_step_presence,
    evaluate_required_signoff_presence,
    evaluate_sop_revision_applicability,
    evaluate_unit_compatibility,
)
from app.models.schemas.manufacturing_execution import ProcessParameterObservationCreateRequest
from app.services.manufacturing_execution_service import (
    EXECUTION_RECORD_TRANSITIONS,
    IMMUTABLE_EXECUTION_STATUSES,
    MBR_STATUS_TRANSITIONS,
)


def test_mbr_and_execution_record_state_machines() -> None:
    assert MasterBatchRecordStatus.UNDER_REVIEW.value in MBR_STATUS_TRANSITIONS[MasterBatchRecordStatus.DRAFT.value]
    assert MasterBatchRecordStatus.EFFECTIVE.value in MBR_STATUS_TRANSITIONS[MasterBatchRecordStatus.APPROVED.value]
    assert MBR_STATUS_TRANSITIONS[MasterBatchRecordStatus.SUPERSEDED.value] == set()

    assert BatchExecutionStatus.IN_PROGRESS.value in EXECUTION_RECORD_TRANSITIONS[BatchExecutionStatus.DRAFT.value]
    assert BatchExecutionStatus.VERIFIED.value in EXECUTION_RECORD_TRANSITIONS[BatchExecutionStatus.COMPLETED.value]
    assert BatchExecutionStatus.APPROVED.value in EXECUTION_RECORD_TRANSITIONS[BatchExecutionStatus.VERIFIED.value]
    assert BatchExecutionStatus.VERIFIED.value in IMMUTABLE_EXECUTION_STATUSES
    assert BatchExecutionStatus.APPROVED.value in IMMUTABLE_EXECUTION_STATUSES


def test_observation_schema_enforces_typed_value_presence() -> None:
    with pytest.raises(ValueError, match="numeric_value"):
        ProcessParameterObservationCreateRequest(
            parameter_code="TEMP",
            parameter_name="Temperature",
            value_type="NUMERIC",
            numeric_value=None,
        )
    with pytest.raises(ValueError, match="text_value"):
        ProcessParameterObservationCreateRequest(
            parameter_code="APPEARANCE",
            parameter_name="Appearance",
            value_type="TEXT",
            text_value="   ",
        )
    with pytest.raises(ValueError, match="boolean_value"):
        ProcessParameterObservationCreateRequest(
            parameter_code="SIEVE_INTEGRITY",
            parameter_name="Sieve Integrity",
            value_type="BOOLEAN",
            boolean_value=None,
        )


def test_deterministic_unit_conversion_and_seven_rule_evaluators() -> None:
    # 1. Temperature & mass conversions + incompatible dimension check
    c_val, ok, _ = convert_unit_value(Decimal("149"), "F", "C")
    assert ok is True
    assert c_val == Decimal("65.000000")

    kg_val, ok_m, _ = convert_unit_value(Decimal("2500"), "G", "KG")
    assert ok_m is True
    assert kg_val == Decimal("2.500000")

    bad_val, ok_bad, _ = convert_unit_value(Decimal("10"), "KG", "C")
    assert ok_bad is False
    assert bad_val is None

    # 2. Unit compatibility check
    u_pass = evaluate_unit_compatibility(
        actual_unit_code="F",
        expected_unit_code="C",
        actual_numeric_value=Decimal("149"),
    )
    assert u_pass.outcome == DeterministicCheckOutcome.PASS.value

    u_fail = evaluate_unit_compatibility(
        actual_unit_code="LITERS",
        expected_unit_code="KG",
        actual_numeric_value=Decimal("10"),
    )
    assert u_fail.outcome == DeterministicCheckOutcome.FAIL.value

    # 3. Parameter vs SOP limit (with F -> C conversion, out-of-range FAIL, UNVERIFIED, MISSING)
    p_pass = evaluate_parameter_vs_sop_limit(
        parameter_code="DRY_TEMP",
        value_type="NUMERIC",
        numeric_value=Decimal("149"),
        actual_unit_code="F",
        expected_data_type="NUMERIC",
        expected_unit_code="C",
        min_value=Decimal("60"),
        max_value=Decimal("70"),
        verification_status="VERIFIED",
        require_verified=True,
    )
    assert p_pass.outcome == DeterministicCheckOutcome.PASS.value
    assert p_pass.actual_numeric_value == Decimal("65.000000")

    p_unver = evaluate_parameter_vs_sop_limit(
        parameter_code="DRY_TEMP",
        value_type="NUMERIC",
        numeric_value=Decimal("65"),
        actual_unit_code="C",
        expected_data_type="NUMERIC",
        expected_unit_code="C",
        min_value=Decimal("60"),
        max_value=Decimal("70"),
        verification_status="UNVERIFIED",
        require_verified=True,
    )
    assert p_unver.outcome == DeterministicCheckOutcome.UNVERIFIED.value

    p_fail = evaluate_parameter_vs_sop_limit(
        parameter_code="DRY_TEMP",
        value_type="NUMERIC",
        numeric_value=Decimal("185"),
        actual_unit_code="F",
        expected_data_type="NUMERIC",
        expected_unit_code="C",
        min_value=Decimal("60"),
        max_value=Decimal("70"),
    )
    assert p_fail.outcome == DeterministicCheckOutcome.FAIL.value

    p_miss = evaluate_parameter_vs_sop_limit(
        parameter_code="DRY_TEMP",
        expected_data_type="NUMERIC",
        expected_unit_code="C",
        min_value=Decimal("60"),
        max_value=Decimal("70"),
    )
    assert p_miss.outcome == DeterministicCheckOutcome.MISSING.value

    # 4. Required execution step presence
    s_pass = evaluate_required_execution_step_presence(
        step_code="STEP-01",
        is_mandatory=True,
        execution_step_present=True,
        step_status="VERIFIED",
    )
    assert s_pass.outcome == DeterministicCheckOutcome.PASS.value

    s_miss = evaluate_required_execution_step_presence(
        step_code="STEP-02",
        is_mandatory=True,
        execution_step_present=False,
    )
    assert s_miss.outcome == DeterministicCheckOutcome.MISSING.value

    # 5. Required signoff presence
    sig_miss = evaluate_required_signoff_presence(
        step_code="STEP-01",
        requires_operator_signoff=True,
        requires_checker_signoff=True,
        operator_membership_id="m1",
        checker_membership_id=None,
    )
    assert sig_miss.outcome == DeterministicCheckOutcome.MISSING.value

    sig_same = evaluate_required_signoff_presence(
        step_code="STEP-01",
        requires_operator_signoff=True,
        requires_checker_signoff=True,
        operator_membership_id="m1",
        checker_membership_id="m1",
    )
    assert sig_same.outcome == DeterministicCheckOutcome.FAIL.value

    # 6. SOP revision applicability
    sop_ok = evaluate_sop_revision_applicability(
        sop_code="SOP-MFG-01",
        resolution_outcome=SOPResolutionOutcome.RESOLVED.value,
        resolved_revision_id="rev-1",
        resolved_revision_number="1.0",
        referenced_revision_id="rev-1",
    )
    assert sop_ok.outcome == DeterministicCheckOutcome.PASS.value

    sop_mismatch = evaluate_sop_revision_applicability(
        sop_code="SOP-MFG-01",
        resolution_outcome=SOPResolutionOutcome.RESOLVED.value,
        resolved_revision_id="rev-1",
        resolved_revision_number="1.0",
        referenced_revision_id="rev-2",
        referenced_revision_number="2.0",
    )
    assert sop_mismatch.outcome == DeterministicCheckOutcome.FAIL.value

    # 7. Equipment calibration & QC result evaluators
    eq_exp = evaluate_equipment_calibration_validity(
        equipment_code="EQ-BAL-01",
        calibration_required=True,
        calibration_validity_status=CalibrationValidityStatus.EXPIRED.value,
        usage_timestamp=datetime.now(timezone.utc),
    )
    assert eq_exp.outcome == DeterministicCheckOutcome.FAIL.value

    qc_oos = evaluate_qc_result_vs_specification(
        test_code="ASSAY-01",
        test_type="NUMERIC_RANGE",
        result_type="NUMERIC",
        numeric_result=Decimal("88.5"),
        unit_code="PCT",
        expected_unit_code="PCT",
        min_limit=Decimal("95.0"),
        max_limit=Decimal("105.0"),
        review_status="APPROVED",
        require_approved_review=True,
    )
    assert qc_oos.outcome == DeterministicCheckOutcome.FAIL.value
