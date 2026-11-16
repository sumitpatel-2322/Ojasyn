import secrets
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.core.security import get_password_hash, verify_password, create_access_token
from app.models.schemas.auth import CustomerRegister, OrganizationRegister, EmployeeRegister, UserLogin, Token
from app.models.domain.db_models import (
    DBUser,
    DBChemistProfile,
    DBOrganization,
    DBManufacturingUnit,
    DBOrganizationMembership,
    DBRegistrationCredential,
)

router = APIRouter()

# Dependency to get the database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/organizations")
def list_organizations(db: Session = Depends(get_db)):
    """Lists active organizations and their manufacturing units for onboarding & batch creation."""
    orgs = db.query(DBOrganization).all()
    result = []
    for org in orgs:
        units = [
            {
                "id": u.id,
                "unit_name": u.unit_name,
                "unit_type": u.unit_type,
                "city": u.city,
                "state": u.state,
                "is_primary": u.is_primary,
            }
            for u in org.units
        ]
        result.append(
            {
                "id": org.id,
                "legal_name": org.legal_name,
                "trade_name": org.trade_name,
                "organization_type": org.organization_type,
                "city": org.city,
                "state": org.state,
                "status": org.status,
                "units": units,
            }
        )
    return result


@router.post("/register/customer", response_model=Token)
def register_customer(user_data: CustomerRegister, db: Session = Depends(get_db)):
    # 1. Check if user already exists
    if db.query(DBUser).filter(DBUser.email == user_data.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    # 2. Create the User with combined location fields
    new_user = DBUser(
        full_name=user_data.full_name,
        email=user_data.email,
        mobile_number=user_data.mobile_number,
        city=user_data.city,
        state=user_data.state,
        pincode=user_data.pincode,
        password_hash=get_password_hash(user_data.password),
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # 3. Create Chemist Profile if applicable (combining frontend & backend fields)
    role = "customer"
    if user_data.is_chemist:
        role = "chemist"
        resolved_pharmacy_name = (
            user_data.pharmacy_name
            or user_data.chemist_pharmacy_name
            or f"{user_data.full_name} Pharmacy"
        )
        resolved_licence = user_data.drug_license_number or user_data.drug_licence_number
        chemist_profile = DBChemistProfile(
            user_id=new_user.id,
            pharmacy_name=resolved_pharmacy_name,
            pharmacy_type=user_data.pharmacy_type or "Retail",
            drug_license_number=resolved_licence,
            gst_number=user_data.gst_number,
            pharmacy_address=user_data.pharmacy_address,
            city=user_data.city,
            state=user_data.state,
            pincode=user_data.pincode,
        )
        db.add(chemist_profile)
        db.commit()

    # 4. Generate Token
    access_token = create_access_token(data={"sub": new_user.email, "role": role})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": role,
        "full_name": new_user.full_name,
        "email": new_user.email,
        "user_id": new_user.id,
    }


@router.post("/register/organization", response_model=Token)
def register_organization(org_data: OrganizationRegister, db: Session = Depends(get_db)):
    """
    Registers a new pharmaceutical organization, its primary manufacturing unit,
    its employee registration credential, and its primary Organization Administrator.
    """
    # 1. Check if admin email already exists
    if db.query(DBUser).filter(DBUser.email == org_data.admin_email).first():
        raise HTTPException(status_code=400, detail="Administrator email is already registered")

    # 2. Create the Organization
    new_org = DBOrganization(
        legal_name=org_data.organization_name,
        trade_name=org_data.trade_name or org_data.organization_name,
        organization_type=org_data.organization_type,
        country=org_data.country,
        cin=org_data.registration_number,
        gst_number=org_data.gst_number,
        official_email=org_data.official_email,
        company_mobile=org_data.company_mobile,
        registered_address=org_data.registered_address,
        state=org_data.state,
        city=org_data.city,
        postal_code=org_data.postal_code,
        status="Active",
    )
    db.add(new_org)
    db.commit()
    db.refresh(new_org)

    # 3. Create Primary Manufacturing Unit
    primary_unit = DBManufacturingUnit(
        organization_id=new_org.id,
        unit_code=f"UNIT-{new_org.id}-01",
        unit_name=org_data.unit_name or f"{org_data.city} Main Plant",
        unit_type=org_data.unit_type or "Both",
        address_line_1=org_data.registered_address,
        city=org_data.city,
        state=org_data.state,
        pincode=org_data.postal_code,
        country=org_data.country,
        is_primary=True,
        status="Active",
    )
    db.add(primary_unit)
    db.commit()
    db.refresh(primary_unit)

    # 4. Create Organization Employee Registration Code
    invite_code = (
        org_data.registration_code.strip()
        if org_data.registration_code and org_data.registration_code.strip()
        else f"OJASYN-{new_org.id}-{secrets.token_hex(2).upper()}"
    )
    credential = DBRegistrationCredential(
        organization_id=new_org.id,
        code_hash=invite_code,
        status="Active",
    )
    db.add(credential)
    db.commit()

    # 5. Create Primary Administrator User
    admin_user = DBUser(
        full_name=org_data.admin_full_name,
        email=org_data.admin_email,
        mobile_number=org_data.company_mobile,
        city=org_data.city,
        state=org_data.state,
        pincode=org_data.postal_code,
        password_hash=get_password_hash(org_data.admin_password),
        account_status="Active",
    )
    db.add(admin_user)
    db.commit()
    db.refresh(admin_user)

    # 6. Create Approved Organization Admin Membership
    membership = DBOrganizationMembership(
        user_id=admin_user.id,
        organization_id=new_org.id,
        employee_id="ADMIN-001",
        department="Quality & Executive Management",
        designation=org_data.admin_designation,
        requested_role="ORG_ADMIN",
        assigned_role="ORG_ADMIN",
        approval_status="Approved",
    )
    db.add(membership)
    db.commit()

    access_token = create_access_token(data={"sub": admin_user.email, "role": "ORG_ADMIN"})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": "ORG_ADMIN",
        "full_name": admin_user.full_name,
        "email": admin_user.email,
        "user_id": admin_user.id,
        "organization_id": new_org.id,
        "organization_name": new_org.legal_name,
        "manufacturing_unit_id": primary_unit.id,
        "registration_code": invite_code,
    }


@router.post("/register/employee", response_model=Token)
def register_employee(user_data: EmployeeRegister, db: Session = Depends(get_db)):
    # 1. Check if user already exists
    if db.query(DBUser).filter(DBUser.email == user_data.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    # 2. Verify the Organization exists
    org = db.query(DBOrganization).filter(DBOrganization.id == user_data.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # 3. Verify the Organization's Registration Code (if one is configured for this org)
    active_creds = (
        db.query(DBRegistrationCredential)
        .filter(
            DBRegistrationCredential.organization_id == user_data.organization_id,
            DBRegistrationCredential.status == "Active",
        )
        .all()
    )
    if active_creds:
        matching = next((c for c in active_creds if c.code_hash == user_data.registration_code), None)
        if not matching:
            raise HTTPException(status_code=403, detail="Invalid or revoked employee registration code")

    # 4. Create the User
    new_user = DBUser(
        full_name=user_data.full_name,
        email=user_data.email,
        mobile_number=user_data.mobile_number,
        password_hash=get_password_hash(user_data.password),
        account_status="Active",
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # 5. Create the Organization Membership (Approved with valid registration code so employee can work immediately)
    membership = DBOrganizationMembership(
        user_id=new_user.id,
        organization_id=user_data.organization_id,
        employee_id=user_data.employee_id,
        department=user_data.department,
        designation=user_data.designation,
        requested_role=user_data.requested_role,
        assigned_role=user_data.requested_role,
        approval_status="Approved",
    )
    db.add(membership)
    db.commit()

    primary_unit = org.units[0] if org.units else None
    access_token = create_access_token(data={"sub": new_user.email, "role": user_data.requested_role})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user_data.requested_role,
        "full_name": new_user.full_name,
        "email": new_user.email,
        "user_id": new_user.id,
        "organization_id": org.id,
        "organization_name": org.legal_name,
        "manufacturing_unit_id": primary_unit.id if primary_unit else None,
    }


@router.post("/login", response_model=Token)
def login(login_data: UserLogin, db: Session = Depends(get_db)):
    # 1. Find the user
    user = db.query(DBUser).filter(DBUser.email == login_data.email).first()
    if not user or not verify_password(login_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if user.account_status != "Active":
        raise HTTPException(status_code=403, detail="Account is disabled or suspended")

    # 2. Determine Role (Customer, Chemist, or Employee Role)
    role = "customer"
    org_id = None
    org_name = None
    unit_id = None
    reg_code = None

    if user.chemist_profile:
        role = "chemist"
    elif user.memberships:
        active_membership = next((m for m in user.memberships if m.approval_status == "Approved"), None)
        if not active_membership:
            # Fallback to first membership if pending
            first_m = user.memberships[0]
            active_membership = first_m
        role = active_membership.assigned_role or active_membership.requested_role
        org_id = active_membership.organization_id
        if active_membership.organization:
            org_name = active_membership.organization.legal_name
            if active_membership.organization.units:
                unit_id = active_membership.organization.units[0].id
            if active_membership.organization.registration_credentials:
                active_cred = next(
                    (c for c in active_membership.organization.registration_credentials if c.status == "Active"),
                    None,
                )
                if active_cred:
                    reg_code = active_cred.code_hash

    # 3. Generate Token
    access_token = create_access_token(data={"sub": user.email, "role": role})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": role,
        "full_name": user.full_name,
        "email": user.email,
        "user_id": user.id,
        "organization_id": org_id,
        "organization_name": org_name,
        "manufacturing_unit_id": unit_id,
        "registration_code": reg_code,
    }
