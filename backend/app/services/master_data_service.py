import uuid
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.database import UnitOfWork
from app.core.deps import Principal, TenantContext
from app.core.exceptions import ConflictError, PermissionDeniedError
from app.domain.enums import ActorType, SecurityEventOutcome, SecurityEventSeverity
from app.models.domain.master_data import ReferenceValue, UnitOfMeasure
from app.models.schemas.master_documents import ReferenceValueCreateRequest, UnitOfMeasureCreateRequest
from app.repositories.master_data_repo import MasterDataRepository
from app.services.audit_service import AuditService

TENANT_MASTER_DATA_ROLES = {"ORG_ADMIN", "QMS_HEAD", "ORG_OWNER"}


class MasterDataService:
    """Controlled ReferenceValue (global + tenant) and global UnitOfMeasure catalog service."""

    def __init__(
        self,
        repo: Optional[MasterDataRepository] = None,
        audit_service: Optional[AuditService] = None,
    ) -> None:
        self._repo = repo or MasterDataRepository()
        self._audit = audit_service or AuditService()

    def list_reference_values(
        self,
        db: Session,
        *,
        organization_id: Optional[uuid.UUID],
        category_code: Optional[str] = None,
        active_only: bool = True,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[ReferenceValue], int]:
        return self._repo.list_reference_values(
            db,
            organization_id=organization_id,
            category_code=category_code,
            active_only=active_only,
            page=page,
            page_size=page_size,
        )

    def create_reference_value(
        self,
        db: Session,
        *,
        principal: Principal,
        tenant_ctx: Optional[TenantContext],
        payload: ReferenceValueCreateRequest,
    ) -> ReferenceValue:
        if payload.is_global:
            if principal.platform_role != "PLATFORM_SUPER_ADMIN":
                self._audit.record_security_event(
                    event_type="MASTER_DATA_GLOBAL_WRITE_DENIED",
                    outcome=SecurityEventOutcome.DENIED.value,
                    severity=SecurityEventSeverity.HIGH.value,
                    user_id=principal.user_id,
                    target_resource="/api/v1/reference-values",
                )
                raise PermissionDeniedError("Only PLATFORM_SUPER_ADMIN may create global reference values.")
            target_org_id = None
        else:
            if tenant_ctx is None:
                raise PermissionDeniedError("Tenant context is required for tenant reference values.")
            if not (set(tenant_ctx.role_codes) & TENANT_MASTER_DATA_ROLES):
                raise PermissionDeniedError("Tenant reference value overrides require ORG_ADMIN or QMS_HEAD.")
            target_org_id = tenant_ctx.organization_id

        cat = payload.category_code.strip().upper()
        code = payload.code.strip().upper()
        if self._repo.get_reference_value_exact(
            db, organization_id=target_org_id, category_code=cat, code=code
        ) is not None:
            raise ConflictError(f"Reference value {cat}:{code} already exists in this scope.")

        with UnitOfWork(db):
            ref = ReferenceValue(
                organization_id=target_org_id,
                category_code=cat,
                code=code,
                display_name=payload.display_name.strip(),
                description=payload.description,
                sort_order=payload.sort_order,
                is_active=payload.is_active,
                metadata_json=payload.metadata_json,
                created_by_user_id=principal.user_id,
                updated_by_user_id=principal.user_id,
            )
            self._repo.create_reference_value(db, ref)
            self._audit.record(
                db,
                organization_id=target_org_id,
                actor_type=ActorType.USER.value,
                actor_id=principal.user_id,
                action="REFERENCE_VALUE_CREATED",
                entity_type="reference_values",
                entity_id=ref.id,
                after_state={"category_code": cat, "code": code, "is_global": target_org_id is None},
            )
        db.refresh(ref)
        return ref

    def list_units_of_measure(
        self,
        db: Session,
        *,
        dimension: Optional[str] = None,
        active_only: bool = True,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[UnitOfMeasure], int]:
        return self._repo.list_uoms(db, dimension=dimension, active_only=active_only, page=page, page_size=page_size)

    def create_unit_of_measure(
        self,
        db: Session,
        *,
        principal: Principal,
        payload: UnitOfMeasureCreateRequest,
    ) -> UnitOfMeasure:
        if principal.platform_role != "PLATFORM_SUPER_ADMIN":
            raise PermissionDeniedError("Only PLATFORM_SUPER_ADMIN may manage global units of measure.")

        unit_code = payload.unit_code.strip().upper()
        if self._repo.get_uom_by_code(db, unit_code) is not None:
            raise ConflictError(f"UnitOfMeasure '{unit_code}' already exists.")

        with UnitOfWork(db):
            uom = UnitOfMeasure(
                unit_code=unit_code,
                unit_name=payload.unit_name.strip(),
                symbol=payload.symbol.strip(),
                dimension=payload.dimension.strip().upper(),
                base_unit_code=payload.base_unit_code.strip().upper() if payload.base_unit_code else None,
                conversion_factor=payload.conversion_factor,
                is_active=payload.is_active,
                created_by_user_id=principal.user_id,
                updated_by_user_id=principal.user_id,
            )
            self._repo.create_uom(db, uom)
            self._audit.record(
                db,
                organization_id=None,
                actor_type=ActorType.USER.value,
                actor_id=principal.user_id,
                action="UOM_CREATED",
                entity_type="units_of_measure",
                entity_id=uom.id,
                after_state={"unit_code": unit_code, "dimension": uom.dimension},
            )
        db.refresh(uom)
        return uom
