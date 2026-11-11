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

# ==========================================
# CUSTOMER / CHEMIST REGISTRATION
# ==========================================
class CustomerRegister(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=8, description="Password must be at least 8 characters")
    mobile_number: Optional[str] = None
    
    # Chemist specific fields (optional for standard customers)
    is_chemist: bool = False
    pharmacy_name: Optional[str] = None
    pharmacy_type: Optional[str] = None # Retail, Wholesale, Hospital
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None

# ==========================================
# EMPLOYEE REGISTRATION
# ==========================================
class EmployeeRegister(BaseModel):
    full_name: str
    email: EmailStr
    password: str = Field(..., min_length=8)
    mobile_number: Optional[str] = None
    
    organization_id: int
    registration_code: str # The shared company code
    
    employee_id: str
    department: str
    requested_role: str # QA_PERSONNEL, PRODUCTION_PERSONNEL