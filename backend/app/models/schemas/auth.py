from pydantic import BaseModel, EmailStr, Field
from typing import Optional

# ==========================================
# LOGIN & TOKEN SCHEMAS
# ==========================================
class UserLogin(BaseModel):
    email: EmailStr
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    full_name: str
    email: Optional[str] = None
    user_id: Optional[int] = None
    organization_id: Optional[int] = None
    organization_name: Optional[str] = None
    manufacturing_unit_id: Optional[int] = None
    registration_code: Optional[str] = None

# ==========================================
# CUSTOMER / CHEMIST REGISTRATION (COMBINED)
# ==========================================
class CustomerRegister(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=8, description="Password must be at least 8 characters")
    mobile_number: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None

    # Chemist specific fields (combined Frontend + Backend fields)
    is_chemist: bool = False
    pharmacy_name: Optional[str] = None
    chemist_pharmacy_name: Optional[str] = None
    pharmacy_type: Optional[str] = None  # Retail, Wholesale, Hospital
    drug_license_number: Optional[str] = None
    drug_licence_number: Optional[str] = None
    gst_number: Optional[str] = None
    pharmacy_address: Optional[str] = None

# ==========================================
# ORGANIZATION REGISTRATION (COMBINED)
# ==========================================
class OrganizationRegister(BaseModel):
    # Organization legal & tax details
    organization_name: str = Field(..., min_length=2, max_length=200)
    trade_name: Optional[str] = None
    registration_number: str = Field(..., description="CIN / company registration number")
    gst_number: Optional[str] = None
    organization_type: str

    # Primary facility / unit details
    unit_name: Optional[str] = None
    unit_type: str = Field(default="Both", description="API, FDF, or Both")
    official_email: EmailStr
    company_mobile: str
    registered_address: str
    country: str = "India"
    state: str
    city: str
    postal_code: str

    # Primary organization administrator
    admin_full_name: str
    admin_designation: str
    admin_email: EmailStr
    admin_password: str = Field(..., min_length=8)

    # Employee invite / registration code for this organization
    registration_code: Optional[str] = None

# ==========================================
# EMPLOYEE REGISTRATION
# ==========================================
class EmployeeRegister(BaseModel):
    full_name: str
    email: EmailStr
    password: str = Field(..., min_length=8)
    mobile_number: Optional[str] = None

    organization_id: int
    registration_code: str  # The shared company code

    employee_id: str
    department: str
    designation: Optional[str] = None
    requested_role: str  # QA_PERSONNEL, PRODUCTION_PERSONNEL, QC_PERSONNEL
