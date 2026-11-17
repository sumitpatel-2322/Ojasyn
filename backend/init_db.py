from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import UnitOfWork, get_session_factory
from app.domain.rbac_catalog import seed_rbac_catalog
from app.models.domain.master_data import ReferenceValue, UnitOfMeasure

_DEFAULT_UNITS_OF_MEASURE: Tuple[Tuple[str, str, str, str, Optional[str], Decimal], ...] = (
    ("KG", "Kilogram", "kg", "MASS", "KG", Decimal("1.000000")),
    ("G", "Gram", "g", "MASS", "KG", Decimal("0.001000")),
    ("MG", "Milligram", "mg", "MASS", "KG", Decimal("0.000001")),
    ("MCG", "Microgram", "mcg", "MASS", "MG", Decimal("0.001000")),
    ("L", "Liter", "L", "VOLUME", "L", Decimal("1.000000")),
    ("ML", "Milliliter", "mL", "VOLUME", "L", Decimal("0.001000")),
    ("PCT", "Percent", "%", "CONCENTRATION", None, Decimal("1.000000")),
    ("UNIT", "Unit", "unit", "COUNT", None, Decimal("1.000000")),
    ("TABLET", "Tablet", "tab", "DOSAGE_FORM", None, Decimal("1.000000")),
    ("CAPSULE", "Capsule", "cap", "DOSAGE_FORM", None, Decimal("1.000000")),
    ("VIAL", "Vial", "vial", "PACK_UNIT", None, Decimal("1.000000")),
    ("AMP", "Ampoule", "amp", "PACK_UNIT", None, Decimal("1.000000")),
)

_DEFAULT_GLOBAL_REFERENCE_VALUES: Tuple[Tuple[str, str, str, int], ...] = (
    ("LICENSE_TYPE", "WHO_GMP", "WHO-GMP Certification", 10),
    ("LICENSE_TYPE", "US_FDA", "US FDA Establishment Registration", 20),
    ("LICENSE_TYPE", "EU_GMP", "EU-GMP Certificate", 30),
    ("LICENSE_TYPE", "CDSCO_MFG", "CDSCO Manufacturing License", 40),
    ("LICENSE_TYPE", "STATE_FDA", "State FDA Drug License", 50),
    ("DOSAGE_FORM", "TABLET", "Oral Tablet", 10),
    ("DOSAGE_FORM", "CAPSULE", "Oral Capsule", 20),
    ("DOSAGE_FORM", "INJECTABLE", "Sterile Injectable", 30),
    ("DOSAGE_FORM", "SYRUP", "Oral Liquid / Syrup", 40),
    ("DOSAGE_FORM", "OINTMENT", "Topical Ointment", 50),
    ("COMPLAINT_CATEGORY", "QUALITY_DEFECT", "Quality Defect", 10),
    ("COMPLAINT_CATEGORY", "PACKAGING_DEFECT", "Packaging Defect", 20),
    ("COMPLAINT_CATEGORY", "CONTAMINATION", "Contamination / Foreign Matter", 30),
    ("COMPLAINT_CATEGORY", "LABELING_ERROR", "Labeling / Artwork Error", 40),
    ("COMPLAINT_CATEGORY", "ADVERSE_EVENT", "Suspected Adverse Event", 50),
)


def seed_master_data_catalog(db: Session) -> Dict[str, int]:
    """Idempotently seeds global Units of Measure and starter global ReferenceValues."""
    uom_created = 0
    for unit_code, unit_name, symbol, dimension, base_unit_code, factor in _DEFAULT_UNITS_OF_MEASURE:
        existing = db.scalar(select(UnitOfMeasure).where(UnitOfMeasure.unit_code == unit_code))
        if existing is None:
            db.add(
                UnitOfMeasure(
                    unit_code=unit_code,
                    unit_name=unit_name,
                    symbol=symbol,
                    dimension=dimension,
                    base_unit_code=base_unit_code,
                    conversion_factor=factor,
                    is_active=True,
                )
            )
            uom_created += 1

    ref_created = 0
    for category_code, code, display_name, sort_order in _DEFAULT_GLOBAL_REFERENCE_VALUES:
        existing_ref = db.scalar(
            select(ReferenceValue).where(
                ReferenceValue.organization_id.is_(None),
                ReferenceValue.category_code == category_code,
                ReferenceValue.code == code,
            )
        )
        if existing_ref is None:
            db.add(
                ReferenceValue(
                    organization_id=None,
                    category_code=category_code,
                    code=code,
                    display_name=display_name,
                    sort_order=sort_order,
                    is_active=True,
                )
            )
            ref_created += 1

    db.flush()
    return {"units_of_measure_created": uom_created, "reference_values_created": ref_created}


def seed_foundation() -> None:
    """
    Seeds the canonical RBAC roles, atomic permissions, role-permission mappings,
    and global Phase 1 master data catalogs (units_of_measure, reference_values).
    Schema creation is managed exclusively via Alembic (`alembic upgrade head`), never create_all().
    """
    factory = get_session_factory()
    with UnitOfWork(factory) as uow:
        assert uow.session is not None
        rbac_summary = seed_rbac_catalog(uow.session)
        master_summary = seed_master_data_catalog(uow.session)
        print(f"Foundation seeded cleanly: rbac={rbac_summary}, master={master_summary}")


if __name__ == "__main__":
    seed_foundation()
