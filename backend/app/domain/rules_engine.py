from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, Optional, Tuple

from app.domain.enums import (
    CalibrationValidityStatus,
    DeterministicCheckOutcome,
    DeterministicCheckType,
    SOPResolutionOutcome,
)

RULE_ENGINE_VERSION = "1.0.0"

RULE_PARAM_VS_SOP_LIMIT = "RULE_PARAM_VS_SOP_LIMIT"
RULE_UNIT_COMPATIBILITY = "RULE_UNIT_COMPATIBILITY"
RULE_REQUIRED_STEP_PRESENCE = "RULE_REQUIRED_STEP_PRESENCE"
RULE_REQUIRED_SIGNOFF_PRESENCE = "RULE_REQUIRED_SIGNOFF_PRESENCE"
RULE_SOP_REVISION_APPLICABILITY = "RULE_SOP_REVISION_APPLICABILITY"
RULE_EQUIPMENT_CALIBRATION_VALIDITY = "RULE_EQUIPMENT_CALIBRATION_VALIDITY"
RULE_QC_RESULT_VS_SPECIFICATION = "RULE_QC_RESULT_VS_SPECIFICATION"


@dataclass(frozen=True)
class DeterministicCheckEvaluation:
    check_type: str
    rule_identifier: str
    rule_version: str
    outcome: str
    actual_value_text: Optional[str]
    actual_numeric_value: Optional[Decimal]
    expected_requirement_text: Optional[str]
    expected_min_value: Optional[Decimal]
    expected_max_value: Optional[Decimal]
    unit_code: Optional[str]
    explanation: str
    metadata_json: Optional[Dict[str, Any]] = None


_UNIT_SYNONYMS: Dict[str, str] = {
    "°C": "C",
    "DEGC": "C",
    "DEG_C": "C",
    "CELSIUS": "C",
    "C": "C",
    "°F": "F",
    "DEGF": "F",
    "DEG_F": "F",
    "FAHRENHEIT": "F",
    "F": "F",
    "KELVIN": "K",
    "K": "K",
    "KG": "KG",
    "KILOGRAM": "KG",
    "KILOGRAMS": "KG",
    "G": "G",
    "GM": "G",
    "GRAM": "G",
    "GRAMS": "G",
    "MG": "MG",
    "MILLIGRAM": "MG",
    "MILLIGRAMS": "MG",
    "MCG": "MCG",
    "UG": "MCG",
    "µG": "MCG",
    "MICROGRAM": "MCG",
    "MICROGRAMS": "MCG",
    "L": "L",
    "LTR": "L",
    "LITER": "L",
    "LITERS": "L",
    "LITRE": "L",
    "ML": "ML",
    "MILLILITER": "ML",
    "MILLILITERS": "ML",
    "UL": "UL",
    "µL": "UL",
    "MICROLITER": "UL",
    "SEC": "SEC",
    "S": "SEC",
    "SECOND": "SEC",
    "SECONDS": "SEC",
    "MIN": "MIN",
    "MINS": "MIN",
    "MINUTE": "MIN",
    "MINUTES": "MIN",
    "HR": "HR",
    "HRS": "HR",
    "H": "HR",
    "HOUR": "HR",
    "HOURS": "HR",
    "%": "PCT",
    "PCT": "PCT",
    "PERCENT": "PCT",
    "%W/W": "%W/W",
    "%W/V": "%W/V",
    "%V/V": "%V/V",
    "RPM": "RPM",
    "PH": "PH",
    "BAR": "BAR",
    "KPA": "KPA",
    "MPA": "MPA",
    "PSI": "PSI",
    "UNIT": "UNIT",
    "UNITS": "UNIT",
    "TABLET": "TABLET",
    "TABLETS": "TABLET",
    "TAB": "TABLET",
    "CAPSULE": "CAPSULE",
    "CAPSULES": "CAPSULE",
    "CAP": "CAPSULE",
    "VIAL": "VIAL",
    "VIALS": "VIAL",
    "AMP": "AMP",
    "AMPOULE": "AMP",
}

# Built-in multiplicative unit families: unit -> (dimension, base_unit, factor_to_base)
_BUILTIN_MULTIPLICATIVE_UNITS: Dict[str, Tuple[str, str, Decimal]] = {
    "KG": ("MASS", "KG", Decimal("1")),
    "G": ("MASS", "KG", Decimal("0.001")),
    "MG": ("MASS", "KG", Decimal("0.000001")),
    "MCG": ("MASS", "KG", Decimal("0.000000001")),
    "L": ("VOLUME", "L", Decimal("1")),
    "ML": ("VOLUME", "L", Decimal("0.001")),
    "UL": ("VOLUME", "L", Decimal("0.000001")),
    "SEC": ("TIME", "SEC", Decimal("1")),
    "MIN": ("TIME", "SEC", Decimal("60")),
    "HR": ("TIME", "SEC", Decimal("3600")),
    "KPA": ("PRESSURE", "KPA", Decimal("1")),
    "BAR": ("PRESSURE", "KPA", Decimal("100")),
    "MPA": ("PRESSURE", "KPA", Decimal("1000")),
    "PSI": ("PRESSURE", "KPA", Decimal("6.894757")),
    "PCT": ("CONCENTRATION_PCT", "PCT", Decimal("1")),
    # Distinct concentration bases (%W/W, %W/V, %V/V) must not cross-convert 1:1 without density
    "%W/W": ("CONCENTRATION_WW", "%W/W", Decimal("1")),
    "%W/V": ("CONCENTRATION_WV", "%W/V", Decimal("1")),
    "%V/V": ("CONCENTRATION_VV", "%V/V", Decimal("1")),
    "RPM": ("ROTATION_SPEED", "RPM", Decimal("1")),
    "PH": ("ACIDITY", "PH", Decimal("1")),
    "UNIT": ("COUNT", "UNIT", Decimal("1")),
    "TABLET": ("DOSAGE_TABLET", "TABLET", Decimal("1")),
    "CAPSULE": ("DOSAGE_CAPSULE", "CAPSULE", Decimal("1")),
    "VIAL": ("PACK_VIAL", "VIAL", Decimal("1")),
    "AMP": ("PACK_AMP", "AMP", Decimal("1")),
}

_TEMPERATURE_UNITS = {"C", "F", "K"}


def _to_decimal(val: Any) -> Optional[Decimal]:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, Decimal):
        return val
    try:
        return Decimal(str(val).strip())
    except (InvalidOperation, ValueError):
        return None


def normalize_unit_code(unit: Optional[str]) -> Optional[str]:
    if unit is None:
        return None
    cleaned = str(unit).strip().upper().replace(" ", "")
    if not cleaned:
        return None
    return _UNIT_SYNONYMS.get(cleaned, cleaned)


def _convert_temperature(value: Decimal, from_u: str, to_u: str) -> Decimal:
    if from_u == to_u:
        return value
    # Convert to Celsius first
    if from_u == "C":
        celsius = value
    elif from_u == "F":
        celsius = (value - Decimal("32")) * Decimal("5") / Decimal("9")
    elif from_u == "K":
        celsius = value - Decimal("273.15")
    else:
        raise ValueError(f"Unsupported temperature unit '{from_u}'")

    if to_u == "C":
        res = celsius
    elif to_u == "F":
        res = (celsius * Decimal("9") / Decimal("5")) + Decimal("32")
    elif to_u == "K":
        res = celsius + Decimal("273.15")
    else:
        raise ValueError(f"Unsupported temperature unit '{to_u}'")
    return res.quantize(Decimal("0.000001"))


def convert_unit_value(
    value: Any,
    from_unit: Optional[str],
    to_unit: Optional[str],
    uom_lookup: Optional[Dict[str, Tuple[str, Optional[str], Decimal]]] = None,
) -> Tuple[Optional[Decimal], bool, str]:
    """
    Deterministically converts `value` from `from_unit` to `to_unit`.
    Returns `(converted_value, is_compatible, explanation)`.
    """
    dec_val = _to_decimal(value)
    if dec_val is None:
        return None, False, "Numeric value is missing or invalid for unit conversion."

    norm_from = normalize_unit_code(from_unit)
    norm_to = normalize_unit_code(to_unit)

    if norm_to is None and norm_from is None:
        return dec_val, True, "No unit specified on either side."
    if norm_to is None:
        return dec_val, True, f"No target unit required; kept '{norm_from}'."
    if norm_from is None:
        return None, False, f"Observed unit is missing while '{norm_to}' is required."
    if norm_from == norm_to:
        return dec_val, True, f"Units match directly ('{norm_to}')."

    if norm_from in _TEMPERATURE_UNITS or norm_to in _TEMPERATURE_UNITS:
        if norm_from in _TEMPERATURE_UNITS and norm_to in _TEMPERATURE_UNITS:
            converted = _convert_temperature(dec_val, norm_from, norm_to)
            return converted, True, f"Converted {dec_val} {norm_from} to {converted} {norm_to}."
        return (
            None,
            False,
            f"Incompatible unit dimensions: cannot convert '{norm_from}' to '{norm_to}'.",
        )

    combined_lookup: Dict[str, Tuple[str, Optional[str], Decimal]] = dict(_BUILTIN_MULTIPLICATIVE_UNITS)
    if uom_lookup:
        for raw_code, meta in uom_lookup.items():
            nc = normalize_unit_code(raw_code)
            if nc:
                dim, base_code, factor = meta
                combined_lookup[nc] = (str(dim).upper(), normalize_unit_code(base_code) or nc, Decimal(str(factor)))

    from_meta = combined_lookup.get(norm_from)
    to_meta = combined_lookup.get(norm_to)
    if from_meta is None or to_meta is None:
        return (
            None,
            False,
            f"Unrecognized or non-convertible units: '{norm_from}' -> '{norm_to}'.",
        )

    from_dim, from_base, from_factor = from_meta
    to_dim, to_base, to_factor = to_meta
    if from_dim != to_dim:
        return (
            None,
            False,
            f"Incompatible unit dimensions: '{norm_from}' ({from_dim}) vs '{norm_to}' ({to_dim}).",
        )

    # Support chained base-unit resolution (e.g. MCG -> MG -> KG in seed table)
    def _factor_to_root(unit_key: str, factor: Decimal, base_key: Optional[str]) -> Tuple[str, Decimal]:
        cur_base = base_key or unit_key
        acc = factor
        seen = {unit_key}
        while cur_base in combined_lookup and cur_base not in seen:
            seen.add(cur_base)
            _, next_base, next_factor = combined_lookup[cur_base]
            acc = acc * next_factor
            if not next_base or next_base == cur_base:
                break
            cur_base = next_base
        return cur_base, acc

    root_from, eff_from_factor = _factor_to_root(norm_from, from_factor, from_base)
    root_to, eff_to_factor = _factor_to_root(norm_to, to_factor, to_base)
    if root_from != root_to or eff_to_factor == 0:
        return (
            None,
            False,
            f"Incompatible base units for '{norm_from}' ({root_from}) and '{norm_to}' ({root_to}).",
        )

    converted = ((dec_val * eff_from_factor) / eff_to_factor).quantize(Decimal("0.000001"))
    return converted, True, f"Converted {dec_val} {norm_from} to {converted} {norm_to}."


def evaluate_unit_compatibility(
    *,
    actual_unit_code: Optional[str] = None,
    expected_unit_code: Optional[str] = None,
    actual_numeric_value: Any = None,
    from_unit: Optional[str] = None,
    to_unit: Optional[str] = None,
    value: Any = None,
    uom_lookup: Optional[Dict[str, Tuple[str, Optional[str], Decimal]]] = None,
) -> DeterministicCheckEvaluation:
    obs_unit = actual_unit_code if actual_unit_code is not None else from_unit
    req_unit = expected_unit_code if expected_unit_code is not None else to_unit
    raw_val = actual_numeric_value if actual_numeric_value is not None else (value if value is not None else Decimal("1"))
    dec_val = _to_decimal(raw_val) or Decimal("1")

    norm_obs = normalize_unit_code(obs_unit)
    norm_req = normalize_unit_code(req_unit)

    if norm_req is None:
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.UNIT_COMPATIBILITY_AND_CONVERSION.value,
            rule_identifier=RULE_UNIT_COMPATIBILITY,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.NOT_APPLICABLE.value,
            actual_value_text=norm_obs,
            actual_numeric_value=dec_val,
            expected_requirement_text=None,
            expected_min_value=None,
            expected_max_value=None,
            unit_code=norm_obs,
            explanation="SOP step / specification does not require a specific unit of measure.",
        )

    if norm_obs is None:
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.UNIT_COMPATIBILITY_AND_CONVERSION.value,
            rule_identifier=RULE_UNIT_COMPATIBILITY,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.MISSING.value,
            actual_value_text=None,
            actual_numeric_value=None,
            expected_requirement_text=f"Unit compatible with {norm_req}",
            expected_min_value=None,
            expected_max_value=None,
            unit_code=norm_req,
            explanation=f"Observed unit of measure is missing (expected '{norm_req}').",
        )

    converted, ok, msg = convert_unit_value(dec_val, norm_obs, norm_req, uom_lookup=uom_lookup)
    return DeterministicCheckEvaluation(
        check_type=DeterministicCheckType.UNIT_COMPATIBILITY_AND_CONVERSION.value,
        rule_identifier=RULE_UNIT_COMPATIBILITY,
        rule_version=RULE_ENGINE_VERSION,
        outcome=DeterministicCheckOutcome.PASS.value if ok else DeterministicCheckOutcome.FAIL.value,
        actual_value_text=f"{dec_val} {norm_obs}",
        actual_numeric_value=converted if ok else dec_val,
        expected_requirement_text=f"Unit compatible with {norm_req}",
        expected_min_value=None,
        expected_max_value=None,
        unit_code=norm_req if ok else norm_obs,
        explanation=msg,
        metadata_json={
            "from_unit": norm_obs,
            "to_unit": norm_req,
            "compatible": ok,
            "converted_value": str(converted) if converted is not None else None,
        },
    )


def evaluate_parameter_vs_sop_limit(
    *,
    parameter_name: str = "Parameter",
    parameter_code: Optional[str] = None,
    value_type: Optional[str] = None,
    numeric_value: Any = None,
    text_value: Optional[str] = None,
    boolean_value: Optional[bool] = None,
    actual_unit_code: Optional[str] = None,
    unit_code: Optional[str] = None,
    expected_data_type: Optional[str] = None,
    expected_unit_code: Optional[str] = None,
    min_value: Any = None,
    max_value: Any = None,
    min_limit: Any = None,
    max_limit: Any = None,
    target_value: Any = None,
    expected_text_value: Optional[str] = None,
    allowed_values: Optional[Iterable[str]] = None,
    acceptance_criteria: Optional[str] = None,
    verification_status: Optional[str] = None,
    require_verified: bool = False,
    uom_lookup: Optional[Dict[str, Tuple[str, Optional[str], Decimal]]] = None,
) -> DeterministicCheckEvaluation:
    label = parameter_code or parameter_name or "Parameter"
    obs_unit = actual_unit_code if actual_unit_code is not None else unit_code
    eff_min = _to_decimal(min_value if min_value is not None else min_limit)
    eff_max = _to_decimal(max_value if max_value is not None else max_limit)
    eff_target = _to_decimal(target_value)
    dec_num = _to_decimal(numeric_value)
    data_type = (expected_data_type or value_type or "NUMERIC").strip().upper()

    allowed_list = [str(v).strip() for v in (allowed_values or []) if str(v).strip()]
    has_numeric_req = eff_min is not None or eff_max is not None or eff_target is not None
    has_text_req = bool((expected_text_value and expected_text_value.strip()) or allowed_list)

    if data_type == "NONE" and not has_numeric_req and not has_text_req:
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.PROCESS_VALUE_VS_SOP_LIMIT.value,
            rule_identifier=RULE_PARAM_VS_SOP_LIMIT,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.NOT_APPLICABLE.value,
            actual_value_text=None,
            actual_numeric_value=None,
            expected_requirement_text=acceptance_criteria,
            expected_min_value=None,
            expected_max_value=None,
            unit_code=normalize_unit_code(expected_unit_code or obs_unit),
            explanation=f"SOP step defines no parameter requirement for '{label}'.",
        )

    if data_type == "NUMERIC" or (has_numeric_req and data_type not in {"TEXT", "ENUM", "BOOLEAN", "PASS_FAIL"}):
        norm_req_unit = normalize_unit_code(expected_unit_code)
        norm_obs_unit = normalize_unit_code(obs_unit)
        req_desc = (
            f"[{eff_min if eff_min is not None else '-inf'}, {eff_max if eff_max is not None else '+inf'}]"
            + (f" {norm_req_unit}" if norm_req_unit else "")
        )
        if dec_num is None:
            return DeterministicCheckEvaluation(
                check_type=DeterministicCheckType.PROCESS_VALUE_VS_SOP_LIMIT.value,
                rule_identifier=RULE_PARAM_VS_SOP_LIMIT,
                rule_version=RULE_ENGINE_VERSION,
                outcome=DeterministicCheckOutcome.MISSING.value,
                actual_value_text=None,
                actual_numeric_value=None,
                expected_requirement_text=acceptance_criteria or req_desc,
                expected_min_value=eff_min,
                expected_max_value=eff_max,
                unit_code=norm_req_unit or norm_obs_unit,
                explanation=f"Required numeric observation for '{label}' is missing (expected {req_desc}).",
            )

        eval_val = dec_num
        conv_note = ""
        if norm_req_unit is not None:
            converted, ok, conv_msg = convert_unit_value(
                dec_num, norm_obs_unit, norm_req_unit, uom_lookup=uom_lookup
            )
            if not ok or converted is None:
                return DeterministicCheckEvaluation(
                    check_type=DeterministicCheckType.PROCESS_VALUE_VS_SOP_LIMIT.value,
                    rule_identifier=RULE_PARAM_VS_SOP_LIMIT,
                    rule_version=RULE_ENGINE_VERSION,
                    outcome=DeterministicCheckOutcome.FAIL.value,
                    actual_value_text=f"{dec_num} {norm_obs_unit or ''}".strip(),
                    actual_numeric_value=dec_num,
                    expected_requirement_text=acceptance_criteria or req_desc,
                    expected_min_value=eff_min,
                    expected_max_value=eff_max,
                    unit_code=norm_req_unit,
                    explanation=f"Unit incompatibility for '{label}': {conv_msg}",
                )
            eval_val = converted
            if norm_obs_unit != norm_req_unit:
                conv_note = f" ({conv_msg})"

        below_min = eff_min is not None and eval_val < eff_min
        above_max = eff_max is not None and eval_val > eff_max
        if below_min or above_max:
            return DeterministicCheckEvaluation(
                check_type=DeterministicCheckType.PROCESS_VALUE_VS_SOP_LIMIT.value,
                rule_identifier=RULE_PARAM_VS_SOP_LIMIT,
                rule_version=RULE_ENGINE_VERSION,
                outcome=DeterministicCheckOutcome.FAIL.value,
                actual_value_text=f"{eval_val} {norm_req_unit or norm_obs_unit or ''}".strip(),
                actual_numeric_value=eval_val,
                expected_requirement_text=acceptance_criteria or req_desc,
                expected_min_value=eff_min,
                expected_max_value=eff_max,
                unit_code=norm_req_unit or norm_obs_unit,
                explanation=f"Observed '{label}' value {eval_val} is outside SOP limits {req_desc}.{conv_note}",
            )

        if require_verified and (verification_status or "").upper() in {"UNVERIFIED", "FLAGGED"}:
            return DeterministicCheckEvaluation(
                check_type=DeterministicCheckType.PROCESS_VALUE_VS_SOP_LIMIT.value,
                rule_identifier=RULE_PARAM_VS_SOP_LIMIT,
                rule_version=RULE_ENGINE_VERSION,
                outcome=DeterministicCheckOutcome.UNVERIFIED.value,
                actual_value_text=f"{eval_val} {norm_req_unit or norm_obs_unit or ''}".strip(),
                actual_numeric_value=eval_val,
                expected_requirement_text=acceptance_criteria or req_desc,
                expected_min_value=eff_min,
                expected_max_value=eff_max,
                unit_code=norm_req_unit or norm_obs_unit,
                explanation=f"Observed '{label}' value {eval_val} is within {req_desc} but observation is {verification_status}.{conv_note}",
            )

        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.PROCESS_VALUE_VS_SOP_LIMIT.value,
            rule_identifier=RULE_PARAM_VS_SOP_LIMIT,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.PASS.value,
            actual_value_text=f"{eval_val} {norm_req_unit or norm_obs_unit or ''}".strip(),
            actual_numeric_value=eval_val,
            expected_requirement_text=acceptance_criteria or req_desc,
            expected_min_value=eff_min,
            expected_max_value=eff_max,
            unit_code=norm_req_unit or norm_obs_unit,
            explanation=f"Observed '{label}' value {eval_val} satisfies SOP limits {req_desc}.{conv_note}",
        )

    if data_type in {"BOOLEAN", "PASS_FAIL"}:
        actual_bool: Optional[bool] = boolean_value
        if actual_bool is None and text_value is not None:
            t_up = text_value.strip().upper()
            if t_up in {"TRUE", "PASS", "YES", "CONFORMS", "OK"}:
                actual_bool = True
            elif t_up in {"FALSE", "FAIL", "NO", "NON_CONFORMING"}:
                actual_bool = False
        if actual_bool is None:
            return DeterministicCheckEvaluation(
                check_type=DeterministicCheckType.PROCESS_VALUE_VS_SOP_LIMIT.value,
                rule_identifier=RULE_PARAM_VS_SOP_LIMIT,
                rule_version=RULE_ENGINE_VERSION,
                outcome=DeterministicCheckOutcome.MISSING.value,
                actual_value_text=None,
                actual_numeric_value=None,
                expected_requirement_text=acceptance_criteria or expected_text_value or "TRUE / PASS",
                expected_min_value=None,
                expected_max_value=None,
                unit_code=None,
                explanation=f"Required boolean/pass-fail observation for '{label}' is missing.",
            )
        expected_bool = True
        if expected_text_value and expected_text_value.strip().upper() in {"FALSE", "FAIL", "NO"}:
            expected_bool = False
        passed = actual_bool is expected_bool
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.PROCESS_VALUE_VS_SOP_LIMIT.value,
            rule_identifier=RULE_PARAM_VS_SOP_LIMIT,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.PASS.value if passed else DeterministicCheckOutcome.FAIL.value,
            actual_value_text=str(actual_bool),
            actual_numeric_value=None,
            expected_requirement_text=acceptance_criteria or str(expected_bool),
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation=(
                f"Observed '{label}' boolean/pass-fail value '{actual_bool}' matches requirement."
                if passed
                else f"Observed '{label}' value '{actual_bool}' failed requirement '{expected_bool}'."
            ),
        )

    # TEXT or ENUM
    cleaned_text = (text_value or "").strip()
    if not cleaned_text:
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.PROCESS_VALUE_VS_SOP_LIMIT.value,
            rule_identifier=RULE_PARAM_VS_SOP_LIMIT,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.MISSING.value,
            actual_value_text=None,
            actual_numeric_value=None,
            expected_requirement_text=acceptance_criteria or expected_text_value or ", ".join(allowed_list),
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation=f"Required text/enum observation for '{label}' is missing.",
        )

    if allowed_list:
        allowed_upper = {a.upper() for a in allowed_list}
        passed = cleaned_text.upper() in allowed_upper
        req_str = f"One of {allowed_list}"
    elif expected_text_value and expected_text_value.strip():
        passed = cleaned_text.upper() == expected_text_value.strip().upper()
        req_str = expected_text_value.strip()
    else:
        passed = True
        req_str = acceptance_criteria or "Non-empty text"

    return DeterministicCheckEvaluation(
        check_type=DeterministicCheckType.PROCESS_VALUE_VS_SOP_LIMIT.value,
        rule_identifier=RULE_PARAM_VS_SOP_LIMIT,
        rule_version=RULE_ENGINE_VERSION,
        outcome=DeterministicCheckOutcome.PASS.value if passed else DeterministicCheckOutcome.FAIL.value,
        actual_value_text=cleaned_text,
        actual_numeric_value=None,
        expected_requirement_text=req_str,
        expected_min_value=None,
        expected_max_value=None,
        unit_code=None,
        explanation=(
            f"Observed '{label}' text '{cleaned_text}' satisfies '{req_str}'."
            if passed
            else f"Observed '{label}' text '{cleaned_text}' does not match '{req_str}'."
        ),
    )


def evaluate_required_execution_step_presence(
    *,
    step_code: str,
    is_mandatory: bool = True,
    execution_step_present: Optional[bool] = None,
    step_exists: Optional[bool] = None,
    step_status: Optional[str] = None,
    executed_at: Optional[Any] = None,
) -> DeterministicCheckEvaluation:
    present = (
        execution_step_present
        if execution_step_present is not None
        else (step_exists if step_exists is not None else (step_status is not None))
    )
    norm_status = (step_status or "").strip().upper() if step_status else None

    if not is_mandatory and (not present or norm_status == "SKIPPED"):
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.REQUIRED_EXECUTION_STEP_PRESENCE.value,
            rule_identifier=RULE_REQUIRED_STEP_PRESENCE,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.NOT_APPLICABLE.value,
            actual_value_text=norm_status or "OMITTED",
            actual_numeric_value=None,
            expected_requirement_text=f"Optional step {step_code}",
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation=f"Optional SOP step '{step_code}' was skipped or not required.",
        )

    if not present or norm_status is None:
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.REQUIRED_EXECUTION_STEP_PRESENCE.value,
            rule_identifier=RULE_REQUIRED_STEP_PRESENCE,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.MISSING.value,
            actual_value_text="MISSING",
            actual_numeric_value=None,
            expected_requirement_text=f"Mandatory step {step_code} COMPLETED or VERIFIED",
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation=f"Mandatory execution step '{step_code}' is missing from the batch execution record.",
        )

    if norm_status in {"PENDING", "IN_PROGRESS", "SKIPPED"}:
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.REQUIRED_EXECUTION_STEP_PRESENCE.value,
            rule_identifier=RULE_REQUIRED_STEP_PRESENCE,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.MISSING.value,
            actual_value_text=norm_status,
            actual_numeric_value=None,
            expected_requirement_text=f"Mandatory step {step_code} COMPLETED or VERIFIED",
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation=f"Mandatory execution step '{step_code}' is incomplete (status='{norm_status}').",
        )

    if norm_status == "EXCEPTION":
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.REQUIRED_EXECUTION_STEP_PRESENCE.value,
            rule_identifier=RULE_REQUIRED_STEP_PRESENCE,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.FAIL.value,
            actual_value_text=norm_status,
            actual_numeric_value=None,
            expected_requirement_text=f"Mandatory step {step_code} COMPLETED or VERIFIED",
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation=f"Mandatory execution step '{step_code}' recorded an execution EXCEPTION.",
        )

    return DeterministicCheckEvaluation(
        check_type=DeterministicCheckType.REQUIRED_EXECUTION_STEP_PRESENCE.value,
        rule_identifier=RULE_REQUIRED_STEP_PRESENCE,
        rule_version=RULE_ENGINE_VERSION,
        outcome=DeterministicCheckOutcome.PASS.value,
        actual_value_text=norm_status,
        actual_numeric_value=None,
        expected_requirement_text=f"Mandatory step {step_code} COMPLETED or VERIFIED",
        expected_min_value=None,
        expected_max_value=None,
        unit_code=None,
        explanation=f"Mandatory execution step '{step_code}' was executed and recorded ('{norm_status}').",
    )


def evaluate_required_signoff_presence(
    *,
    step_code: str = "STEP",
    requires_operator_signoff: bool = True,
    requires_checker_signoff: bool = False,
    requires_qa_signoff: bool = False,
    operator_membership_id: Optional[Any] = None,
    checker_membership_id: Optional[Any] = None,
    qa_membership_id: Optional[Any] = None,
    operator_user_id: Optional[Any] = None,
    checker_user_id: Optional[Any] = None,
    has_operator_signoff: Optional[bool] = None,
    has_checker_signoff: Optional[bool] = None,
    has_qa_signoff: Optional[bool] = None,
) -> DeterministicCheckEvaluation:
    if not requires_operator_signoff and not requires_checker_signoff and not requires_qa_signoff:
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.REQUIRED_SIGNOFF_PRESENCE.value,
            rule_identifier=RULE_REQUIRED_SIGNOFF_PRESENCE,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.NOT_APPLICABLE.value,
            actual_value_text="NONE_REQUIRED",
            actual_numeric_value=None,
            expected_requirement_text="No signoff required",
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation=f"No signoff is required for '{step_code}'.",
        )

    op_present = has_operator_signoff if has_operator_signoff is not None else (operator_membership_id is not None)
    chk_present = has_checker_signoff if has_checker_signoff is not None else (checker_membership_id is not None)
    qa_present = has_qa_signoff if has_qa_signoff is not None else (qa_membership_id is not None)

    if (
        operator_membership_id is not None
        and checker_membership_id is not None
        and str(operator_membership_id) == str(checker_membership_id)
    ) or (
        operator_user_id is not None
        and checker_user_id is not None
        and str(operator_user_id) == str(checker_user_id)
    ):
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.REQUIRED_SIGNOFF_PRESENCE.value,
            rule_identifier=RULE_REQUIRED_SIGNOFF_PRESENCE,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.FAIL.value,
            actual_value_text="SAME_ACTOR_OPERATOR_AND_CHECKER",
            actual_numeric_value=None,
            expected_requirement_text=f"Independent maker-checker signoff for {step_code}",
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation=f"Maker-checker violation on '{step_code}': operator and checker must be distinct.",
        )

    missing_roles = []
    if requires_operator_signoff and not op_present:
        missing_roles.append("operator")
    if requires_checker_signoff and not chk_present:
        missing_roles.append("checker")
    if requires_qa_signoff and not qa_present:
        missing_roles.append("qa")

    req_roles = [
        r
        for r, req in (
            ("operator", requires_operator_signoff),
            ("checker", requires_checker_signoff),
            ("qa", requires_qa_signoff),
        )
        if req
    ]
    present_roles = [
        r
        for r, pr in (("operator", op_present), ("checker", chk_present), ("qa", qa_present))
        if pr
    ]

    if missing_roles:
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.REQUIRED_SIGNOFF_PRESENCE.value,
            rule_identifier=RULE_REQUIRED_SIGNOFF_PRESENCE,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.MISSING.value,
            actual_value_text=",".join(present_roles) or "NONE",
            actual_numeric_value=None,
            expected_requirement_text=",".join(req_roles),
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation=f"Missing required signoff(s) on '{step_code}': {', '.join(missing_roles)}.",
        )

    return DeterministicCheckEvaluation(
        check_type=DeterministicCheckType.REQUIRED_SIGNOFF_PRESENCE.value,
        rule_identifier=RULE_REQUIRED_SIGNOFF_PRESENCE,
        rule_version=RULE_ENGINE_VERSION,
        outcome=DeterministicCheckOutcome.PASS.value,
        actual_value_text=",".join(present_roles),
        actual_numeric_value=None,
        expected_requirement_text=",".join(req_roles),
        expected_min_value=None,
        expected_max_value=None,
        unit_code=None,
        explanation=f"All required signoffs ({', '.join(req_roles)}) are present on '{step_code}'.",
    )


def evaluate_sop_revision_applicability(
    *,
    sop_code: Optional[str] = None,
    resolution_outcome: str = SOPResolutionOutcome.RESOLVED.value,
    resolved_revision_id: Optional[Any] = None,
    resolved_revision_number: Optional[str] = None,
    referenced_revision_id: Optional[Any] = None,
    referenced_revision_number: Optional[str] = None,
    execution_timestamp: Optional[Any] = None,
) -> DeterministicCheckEvaluation:
    if not sop_code and referenced_revision_id is None:
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.SOP_REVISION_APPLICABILITY.value,
            rule_identifier=RULE_SOP_REVISION_APPLICABILITY,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.NOT_APPLICABLE.value,
            actual_value_text=None,
            actual_numeric_value=None,
            expected_requirement_text=None,
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation="Step does not reference an SOP.",
        )

    norm_outcome = (resolution_outcome or "").strip().upper()
    label = sop_code or str(referenced_revision_id)

    if norm_outcome == SOPResolutionOutcome.RESOLVED.value:
        if (
            referenced_revision_id is not None
            and resolved_revision_id is not None
            and str(referenced_revision_id) != str(resolved_revision_id)
        ):
            return DeterministicCheckEvaluation(
                check_type=DeterministicCheckType.SOP_REVISION_APPLICABILITY.value,
                rule_identifier=RULE_SOP_REVISION_APPLICABILITY,
                rule_version=RULE_ENGINE_VERSION,
                outcome=DeterministicCheckOutcome.FAIL.value,
                actual_value_text=referenced_revision_number or str(referenced_revision_id),
                actual_numeric_value=None,
                expected_requirement_text=resolved_revision_number or str(resolved_revision_id),
                expected_min_value=None,
                expected_max_value=None,
                unit_code=None,
                explanation=(
                    f"SOP '{label}' execution referenced revision "
                    f"'{referenced_revision_number or referenced_revision_id}', but revision "
                    f"'{resolved_revision_number or resolved_revision_id}' was effective at execution time."
                ),
            )
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.SOP_REVISION_APPLICABILITY.value,
            rule_identifier=RULE_SOP_REVISION_APPLICABILITY,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.PASS.value,
            actual_value_text=resolved_revision_number or str(resolved_revision_id or ""),
            actual_numeric_value=None,
            expected_requirement_text=f"Effective revision for {label}",
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation=(
                f"Historical SOP revision '{resolved_revision_number or resolved_revision_id}' "
                f"was effective for '{label}' at execution time."
            ),
        )

    if norm_outcome == SOPResolutionOutcome.PENDING_APPROVAL_ONLY.value:
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.SOP_REVISION_APPLICABILITY.value,
            rule_identifier=RULE_SOP_REVISION_APPLICABILITY,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.UNVERIFIED.value,
            actual_value_text=norm_outcome,
            actual_numeric_value=None,
            expected_requirement_text=f"Effective revision for {label}",
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation=f"SOP '{label}' has only draft/pending-approval revisions at execution time.",
        )

    if norm_outcome in {
        SOPResolutionOutcome.SOP_NOT_FOUND.value,
        SOPResolutionOutcome.NO_APPLICABLE_REVISION.value,
    }:
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.SOP_REVISION_APPLICABILITY.value,
            rule_identifier=RULE_SOP_REVISION_APPLICABILITY,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.MISSING.value,
            actual_value_text=norm_outcome,
            actual_numeric_value=None,
            expected_requirement_text=f"Effective revision for {label}",
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation=f"No applicable SOP revision existed for '{label}' at execution time ({norm_outcome}).",
        )

    return DeterministicCheckEvaluation(
        check_type=DeterministicCheckType.SOP_REVISION_APPLICABILITY.value,
        rule_identifier=RULE_SOP_REVISION_APPLICABILITY,
        rule_version=RULE_ENGINE_VERSION,
        outcome=DeterministicCheckOutcome.FAIL.value,
        actual_value_text=norm_outcome,
        actual_numeric_value=None,
        expected_requirement_text=f"Single effective revision for {label}",
        expected_min_value=None,
        expected_max_value=None,
        unit_code=None,
        explanation=f"Historical SOP resolution failed for '{label}' at execution time ({norm_outcome}).",
    )


def evaluate_equipment_calibration_validity(
    *,
    equipment_code: Optional[str] = None,
    calibration_required: bool = True,
    calibration_validity_status: Optional[str] = None,
    calibration_code: Optional[str] = None,
    usage_timestamp: Optional[Any] = None,
) -> DeterministicCheckEvaluation:
    label = equipment_code or "Equipment"
    if not calibration_required:
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.EQUIPMENT_CALIBRATION_VALIDITY.value,
            rule_identifier=RULE_EQUIPMENT_CALIBRATION_VALIDITY,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.NOT_APPLICABLE.value,
            actual_value_text="CALIBRATION_NOT_REQUIRED",
            actual_numeric_value=None,
            expected_requirement_text="Calibration not required",
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation=f"Equipment '{label}' does not require calibration.",
        )

    norm_status = (calibration_validity_status or "").strip().upper()
    if norm_status == CalibrationValidityStatus.VALID.value:
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.EQUIPMENT_CALIBRATION_VALIDITY.value,
            rule_identifier=RULE_EQUIPMENT_CALIBRATION_VALIDITY,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.PASS.value,
            actual_value_text=calibration_code or norm_status,
            actual_numeric_value=None,
            expected_requirement_text=f"Approved valid calibration at usage time for {label}",
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation=f"Equipment '{label}' had a valid approved calibration at the time of use.",
        )

    if norm_status == CalibrationValidityStatus.MISSING_RECORD.value:
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.EQUIPMENT_CALIBRATION_VALIDITY.value,
            rule_identifier=RULE_EQUIPMENT_CALIBRATION_VALIDITY,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.MISSING.value,
            actual_value_text=norm_status,
            actual_numeric_value=None,
            expected_requirement_text=f"Approved valid calibration at usage time for {label}",
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation=f"Equipment '{label}' has no calibration records on file.",
        )

    if norm_status in {CalibrationValidityStatus.UNVERIFIED.value, ""}:
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.EQUIPMENT_CALIBRATION_VALIDITY.value,
            rule_identifier=RULE_EQUIPMENT_CALIBRATION_VALIDITY,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.UNVERIFIED.value,
            actual_value_text=norm_status or "UNVERIFIED",
            actual_numeric_value=None,
            expected_requirement_text=f"Approved valid calibration at usage time for {label}",
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation=f"Calibration record covering usage of '{label}' is unverified / pending approval.",
        )

    return DeterministicCheckEvaluation(
        check_type=DeterministicCheckType.EQUIPMENT_CALIBRATION_VALIDITY.value,
        rule_identifier=RULE_EQUIPMENT_CALIBRATION_VALIDITY,
        rule_version=RULE_ENGINE_VERSION,
        outcome=DeterministicCheckOutcome.FAIL.value,
        actual_value_text=norm_status,
        actual_numeric_value=None,
        expected_requirement_text=f"Approved valid calibration at usage time for {label}",
        expected_min_value=None,
        expected_max_value=None,
        unit_code=None,
        explanation=f"Equipment '{label}' calibration was '{norm_status}' at the time of use.",
    )


def evaluate_qc_result_vs_specification(
    *,
    test_code: str = "QC_TEST",
    test_type: str = "NUMERIC_RANGE",
    result_type: Optional[str] = None,
    numeric_result: Any = None,
    text_result: Optional[str] = None,
    boolean_result: Optional[bool] = None,
    actual_unit_code: Optional[str] = None,
    unit_code: Optional[str] = None,
    expected_unit_code: Optional[str] = None,
    min_limit: Any = None,
    max_limit: Any = None,
    expected_text_specification: Optional[str] = None,
    acceptance_criteria: Optional[str] = None,
    review_status: Optional[str] = None,
    require_approved_review: bool = False,
    uom_lookup: Optional[Dict[str, Tuple[str, Optional[str], Decimal]]] = None,
) -> DeterministicCheckEvaluation:
    norm_test_type = (test_type or "NUMERIC_RANGE").strip().upper()
    norm_review = (review_status or "").strip().upper()
    obs_unit = actual_unit_code if actual_unit_code is not None else unit_code
    eff_min = _to_decimal(min_limit)
    eff_max = _to_decimal(max_limit)
    dec_num = _to_decimal(numeric_result)

    if norm_test_type in {"NUMERIC_RANGE", "NUMERIC_MIN", "NUMERIC_MAX"}:
        norm_req_unit = normalize_unit_code(expected_unit_code)
        norm_obs_unit = normalize_unit_code(obs_unit)
        req_desc = (
            f"[{eff_min if eff_min is not None else '-inf'}, {eff_max if eff_max is not None else '+inf'}]"
            + (f" {norm_req_unit}" if norm_req_unit else "")
        )
        if dec_num is None:
            return DeterministicCheckEvaluation(
                check_type=DeterministicCheckType.QC_RESULT_VS_SPECIFICATION.value,
                rule_identifier=RULE_QC_RESULT_VS_SPECIFICATION,
                rule_version=RULE_ENGINE_VERSION,
                outcome=DeterministicCheckOutcome.MISSING.value,
                actual_value_text=None,
                actual_numeric_value=None,
                expected_requirement_text=acceptance_criteria or req_desc,
                expected_min_value=eff_min,
                expected_max_value=eff_max,
                unit_code=norm_req_unit or norm_obs_unit,
                explanation=f"QC numeric result for '{test_code}' is missing.",
            )

        eval_val = dec_num
        if norm_req_unit is not None:
            converted, ok, conv_msg = convert_unit_value(
                dec_num, norm_obs_unit, norm_req_unit, uom_lookup=uom_lookup
            )
            if not ok or converted is None:
                return DeterministicCheckEvaluation(
                    check_type=DeterministicCheckType.QC_RESULT_VS_SPECIFICATION.value,
                    rule_identifier=RULE_QC_RESULT_VS_SPECIFICATION,
                    rule_version=RULE_ENGINE_VERSION,
                    outcome=DeterministicCheckOutcome.FAIL.value,
                    actual_value_text=f"{dec_num} {norm_obs_unit or ''}".strip(),
                    actual_numeric_value=dec_num,
                    expected_requirement_text=acceptance_criteria or req_desc,
                    expected_min_value=eff_min,
                    expected_max_value=eff_max,
                    unit_code=norm_req_unit,
                    explanation=f"QC test '{test_code}' unit mismatch: {conv_msg}",
                )
            eval_val = converted

        if (eff_min is not None and eval_val < eff_min) or (eff_max is not None and eval_val > eff_max):
            return DeterministicCheckEvaluation(
                check_type=DeterministicCheckType.QC_RESULT_VS_SPECIFICATION.value,
                rule_identifier=RULE_QC_RESULT_VS_SPECIFICATION,
                rule_version=RULE_ENGINE_VERSION,
                outcome=DeterministicCheckOutcome.FAIL.value,
                actual_value_text=f"{eval_val} {norm_req_unit or norm_obs_unit or ''}".strip(),
                actual_numeric_value=eval_val,
                expected_requirement_text=acceptance_criteria or req_desc,
                expected_min_value=eff_min,
                expected_max_value=eff_max,
                unit_code=norm_req_unit or norm_obs_unit,
                explanation=f"QC test '{test_code}' result {eval_val} is out of specification {req_desc}.",
            )

        if norm_review == "REJECTED":
            return DeterministicCheckEvaluation(
                check_type=DeterministicCheckType.QC_RESULT_VS_SPECIFICATION.value,
                rule_identifier=RULE_QC_RESULT_VS_SPECIFICATION,
                rule_version=RULE_ENGINE_VERSION,
                outcome=DeterministicCheckOutcome.FAIL.value,
                actual_value_text=f"{eval_val} {norm_req_unit or norm_obs_unit or ''}".strip(),
                actual_numeric_value=eval_val,
                expected_requirement_text=acceptance_criteria or req_desc,
                expected_min_value=eff_min,
                expected_max_value=eff_max,
                unit_code=norm_req_unit or norm_obs_unit,
                explanation=f"QC test '{test_code}' result was rejected during review.",
            )

        if require_approved_review and norm_review != "APPROVED":
            return DeterministicCheckEvaluation(
                check_type=DeterministicCheckType.QC_RESULT_VS_SPECIFICATION.value,
                rule_identifier=RULE_QC_RESULT_VS_SPECIFICATION,
                rule_version=RULE_ENGINE_VERSION,
                outcome=DeterministicCheckOutcome.UNVERIFIED.value,
                actual_value_text=f"{eval_val} {norm_req_unit or norm_obs_unit or ''}".strip(),
                actual_numeric_value=eval_val,
                expected_requirement_text=acceptance_criteria or req_desc,
                expected_min_value=eff_min,
                expected_max_value=eff_max,
                unit_code=norm_req_unit or norm_obs_unit,
                explanation=f"QC test '{test_code}' result {eval_val} is within {req_desc} but review is '{norm_review or 'PENDING_REVIEW'}'.",
            )

        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.QC_RESULT_VS_SPECIFICATION.value,
            rule_identifier=RULE_QC_RESULT_VS_SPECIFICATION,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.PASS.value,
            actual_value_text=f"{eval_val} {norm_req_unit or norm_obs_unit or ''}".strip(),
            actual_numeric_value=eval_val,
            expected_requirement_text=acceptance_criteria or req_desc,
            expected_min_value=eff_min,
            expected_max_value=eff_max,
            unit_code=norm_req_unit or norm_obs_unit,
            explanation=f"QC test '{test_code}' result {eval_val} conforms to specification {req_desc}.",
        )

    if norm_test_type == "PASS_FAIL" or (result_type or "").upper() == "BOOLEAN":
        actual_bool = boolean_result
        if actual_bool is None and text_result is not None:
            t_up = text_result.strip().upper()
            if t_up in {"TRUE", "PASS", "CONFORMS", "YES"}:
                actual_bool = True
            elif t_up in {"FALSE", "FAIL", "NO"}:
                actual_bool = False
        if actual_bool is None:
            return DeterministicCheckEvaluation(
                check_type=DeterministicCheckType.QC_RESULT_VS_SPECIFICATION.value,
                rule_identifier=RULE_QC_RESULT_VS_SPECIFICATION,
                rule_version=RULE_ENGINE_VERSION,
                outcome=DeterministicCheckOutcome.MISSING.value,
                actual_value_text=None,
                actual_numeric_value=None,
                expected_requirement_text=acceptance_criteria or "PASS / TRUE",
                expected_min_value=None,
                expected_max_value=None,
                unit_code=None,
                explanation=f"QC pass/fail result for '{test_code}' is missing.",
            )
        if not actual_bool or norm_review == "REJECTED":
            return DeterministicCheckEvaluation(
                check_type=DeterministicCheckType.QC_RESULT_VS_SPECIFICATION.value,
                rule_identifier=RULE_QC_RESULT_VS_SPECIFICATION,
                rule_version=RULE_ENGINE_VERSION,
                outcome=DeterministicCheckOutcome.FAIL.value,
                actual_value_text=str(actual_bool),
                actual_numeric_value=None,
                expected_requirement_text=acceptance_criteria or "PASS / TRUE",
                expected_min_value=None,
                expected_max_value=None,
                unit_code=None,
                explanation=f"QC pass/fail result for '{test_code}' failed.",
            )
        if require_approved_review and norm_review != "APPROVED":
            return DeterministicCheckEvaluation(
                check_type=DeterministicCheckType.QC_RESULT_VS_SPECIFICATION.value,
                rule_identifier=RULE_QC_RESULT_VS_SPECIFICATION,
                rule_version=RULE_ENGINE_VERSION,
                outcome=DeterministicCheckOutcome.UNVERIFIED.value,
                actual_value_text=str(actual_bool),
                actual_numeric_value=None,
                expected_requirement_text=acceptance_criteria or "PASS / TRUE",
                expected_min_value=None,
                expected_max_value=None,
                unit_code=None,
                explanation=f"QC result for '{test_code}' passed but review is '{norm_review or 'PENDING_REVIEW'}'.",
            )
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.QC_RESULT_VS_SPECIFICATION.value,
            rule_identifier=RULE_QC_RESULT_VS_SPECIFICATION,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.PASS.value,
            actual_value_text=str(actual_bool),
            actual_numeric_value=None,
            expected_requirement_text=acceptance_criteria or "PASS / TRUE",
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation=f"QC pass/fail result for '{test_code}' conforms to specification.",
        )

    # TEXT_MATCH / QUALITATIVE
    cleaned_txt = (text_result or "").strip()
    if not cleaned_txt:
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.QC_RESULT_VS_SPECIFICATION.value,
            rule_identifier=RULE_QC_RESULT_VS_SPECIFICATION,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.MISSING.value,
            actual_value_text=None,
            actual_numeric_value=None,
            expected_requirement_text=expected_text_specification or acceptance_criteria,
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation=f"QC text result for '{test_code}' is missing.",
        )

    exp_txt = (expected_text_specification or "").strip()
    passed = True if not exp_txt else (cleaned_txt.upper() == exp_txt.upper())
    if not passed or norm_review == "REJECTED":
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.QC_RESULT_VS_SPECIFICATION.value,
            rule_identifier=RULE_QC_RESULT_VS_SPECIFICATION,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.FAIL.value,
            actual_value_text=cleaned_txt,
            actual_numeric_value=None,
            expected_requirement_text=exp_txt or acceptance_criteria,
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation=f"QC text result '{cleaned_txt}' for '{test_code}' does not match '{exp_txt}'.",
        )
    if require_approved_review and norm_review != "APPROVED":
        return DeterministicCheckEvaluation(
            check_type=DeterministicCheckType.QC_RESULT_VS_SPECIFICATION.value,
            rule_identifier=RULE_QC_RESULT_VS_SPECIFICATION,
            rule_version=RULE_ENGINE_VERSION,
            outcome=DeterministicCheckOutcome.UNVERIFIED.value,
            actual_value_text=cleaned_txt,
            actual_numeric_value=None,
            expected_requirement_text=exp_txt or acceptance_criteria,
            expected_min_value=None,
            expected_max_value=None,
            unit_code=None,
            explanation=f"QC text result for '{test_code}' matches but review is '{norm_review or 'PENDING_REVIEW'}'.",
        )
    return DeterministicCheckEvaluation(
        check_type=DeterministicCheckType.QC_RESULT_VS_SPECIFICATION.value,
        rule_identifier=RULE_QC_RESULT_VS_SPECIFICATION,
        rule_version=RULE_ENGINE_VERSION,
        outcome=DeterministicCheckOutcome.PASS.value,
        actual_value_text=cleaned_txt,
        actual_numeric_value=None,
        expected_requirement_text=exp_txt or acceptance_criteria,
        expected_min_value=None,
        expected_max_value=None,
        unit_code=None,
        explanation=f"QC text result '{cleaned_txt}' for '{test_code}' conforms to specification.",
    )
