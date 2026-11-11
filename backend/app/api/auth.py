from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.core.security import get_password_hash, verify_password, create_access_token
from app.models.schemas.auth import CustomerRegister, EmployeeRegister, UserLogin, Token
from app.models.domain.db_models import DBUser, DBChemistProfile, DBOrganizationMembership, DBRegistrationCredential

router = APIRouter()

# Dependency to get the database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/register/customer", response_model=Token)
def register_customer(user_data: CustomerRegister, db: Session = Depends(get_db)):
    # 1. Check if user already exists
    if db.query(DBUser).filter(DBUser.email == user_data.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    # 2. Create the User
    new_user = DBUser(
        full_name=user_data.full_name,
        email=user_data.email,
        mobile_number=user_data.mobile_number,
        password_hash=get_password_hash(user_data.password)
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # 3. Create Chemist Profile if applicable
    role = "customer"
    if user_data.is_chemist:
        role = "chemist"
        chemist_profile = DBChemistProfile(
            user_id=new_user.id,
            pharmacy_name=user_data.pharmacy_name,
            pharmacy_type=user_data.pharmacy_type,
            city=user_data.city,
            state=user_data.state,
            pincode=user_data.pincode
        )
        db.add(chemist_profile)
        db.commit()

    # 4. Generate Token
    access_token = create_access_token(data={"sub": new_user.email, "role": role})
    return {"access_token": access_token, "token_type": "bearer", "role": role, "full_name": new_user.full_name}


@router.post("/register/employee")
def register_employee(user_data: EmployeeRegister, db: Session = Depends(get_db)):
    # 1. Check if user already exists
    if db.query(DBUser).filter(DBUser.email == user_data.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    # 2. Verify the Organization's Registration Code
    # In a production app, we would hash the input and compare it. For MVP, we do a direct match.
    credential = db.query(DBRegistrationCredential).filter(
        DBRegistrationCredential.organization_id == user_data.organization_id,
        DBRegistrationCredential.code_hash == user_data.registration_code,
        DBRegistrationCredential.status == "Active"
    ).first()
    
    if not credential:
        raise HTTPException(status_code=403, detail="Invalid or revoked employee registration code")

    # 3. Create the User
    new_user = DBUser(
        full_name=user_data.full_name,
        email=user_data.email,
        mobile_number=user_data.mobile_number,
        password_hash=get_password_hash(user_data.password)
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # 4. Create the Organization Membership (Pending Admin Approval)
    membership = DBOrganizationMembership(
        user_id=new_user.id,
        organization_id=user_data.organization_id,
        employee_id=user_data.employee_id,
        department=user_data.department,
        requested_role=user_data.requested_role,
        approval_status="Pending"
    )
    db.add(membership)
    db.commit()

    return {"message": "Registration request submitted. Awaiting organization administrator approval."}


@router.post("/login", response_model=Token)
def login(login_data: UserLogin, db: Session = Depends(get_db)):
    # 1. Find the user
    user = db.query(DBUser).filter(DBUser.email == login_data.email).first()
    if not user or not verify_password(login_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
        
    if user.account_status != "Active":
        raise HTTPException(status_code=403, detail="Account is disabled or suspended")

    # 2. Determine Role (Customer, Chemist, or Approved Employee Role)
    role = "customer"
    if user.chemist_profile:
        role = "chemist"
    elif user.memberships:
        active_membership = next((m for m in user.memberships if m.approval_status == "Approved"), None)
        if active_membership:
            role = active_membership.assigned_role
        else:
            raise HTTPException(status_code=403, detail="Employee account is still pending admin approval")

    # 3. Generate Token
    access_token = create_access_token(data={"sub": user.email, "role": role})
    return {"access_token": access_token, "token_type": "bearer", "role": role, "full_name": user.full_name}