from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Integer, Boolean
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()

# ==========================================
# MODULE 1: IDENTITY & ORGANIZATION ENTITIES
# ==========================================

class DBUser(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    mobile_number = Column(String, nullable=True)
    password_hash = Column(String, nullable=False)
    account_status = Column(String, default="Active") # Pending, Active, Suspended
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    memberships = relationship("DBOrganizationMembership", back_populates="user", cascade="all, delete-orphan")
    chemist_profile = relationship("DBChemistProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")


class DBOrganization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    legal_name = Column(String, nullable=False)
    trade_name = Column(String, nullable=True)
    organization_type = Column(String, nullable=False)
    country = Column(String, default="India")
    cin = Column(String, nullable=True)
    status = Column(String, default="Pending") # Draft, Submitted, Active, Suspended

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    units = relationship("DBManufacturingUnit", back_populates="organization", cascade="all, delete-orphan")
    memberships = relationship("DBOrganizationMembership", back_populates="organization", cascade="all, delete-orphan")
    registration_credentials = relationship("DBRegistrationCredential", back_populates="organization", cascade="all, delete-orphan")
    batches = relationship("DBBatch", back_populates="organization", cascade="all, delete-orphan")


class DBManufacturingUnit(Base):
    __tablename__ = "manufacturing_units"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    
    unit_code = Column(String, nullable=True)
    unit_name = Column(String, nullable=False)
    unit_type = Column(String, nullable=False) # API, FDF, Both, etc.
    
    address_line_1 = Column(String, nullable=False)
    address_line_2 = Column(String, nullable=True)
    city = Column(String, nullable=False)
    state = Column(String, nullable=False)
    pincode = Column(String, nullable=False)
    country = Column(String, default="India")
    
    is_primary = Column(Boolean, default=False)
    status = Column(String, default="Active")
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    organization = relationship("DBOrganization", back_populates="units")
    batches = relationship("DBBatch", back_populates="manufacturing_unit", cascade="all, delete-orphan")


class DBOrganizationMembership(Base):
    __tablename__ = "organization_memberships"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    
    employee_id = Column(String, nullable=False)
    department = Column(String, nullable=False)
    designation = Column(String, nullable=True)
    
    requested_role = Column(String, nullable=False)
    assigned_role = Column(String, nullable=True)
    
    approval_status = Column(String, default="Pending") # Pending, Approved, Rejected
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("DBUser", back_populates="memberships")
    organization = relationship("DBOrganization", back_populates="memberships")


class DBRegistrationCredential(Base):
    """Stores the shared employee-registration code metadata."""
    __tablename__ = "organization_registration_credentials"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    
    code_hash = Column(String, nullable=False) # Never store raw code
    status = Column(String, default="Active") # Active, Revoked
    
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    organization = relationship("DBOrganization", back_populates="registration_credentials")


class DBChemistProfile(Base):
    __tablename__ = "chemist_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    
    pharmacy_name = Column(String, nullable=False)
    pharmacy_type = Column(String, nullable=True)
    drug_license_number = Column(String, nullable=True)
    
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    pincode = Column(String, nullable=True)

    # Relationships
    user = relationship("DBUser", back_populates="chemist_profile")


# ==========================================
# MODULE 2: BATCH MANAGEMENT
# ==========================================

class DBBatch(Base):
    __tablename__ = "batches"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    manufacturing_unit_id = Column(Integer, ForeignKey("manufacturing_units.id", ondelete="CASCADE"), nullable=False)
    
    batch_number = Column(String, nullable=False, index=True)
    product_name = Column(String, nullable=False, index=True)
    manufacturing_date = Column(String, nullable=True)
    expiry_date = Column(String, nullable=True)
    
    status = Column(String, default="Active") # Active, Quarantined, Released
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    organization = relationship("DBOrganization", back_populates="batches")
    manufacturing_unit = relationship("DBManufacturingUnit", back_populates="batches")


# ==========================================
# MODULE 6 & 7: COMPLAINT & AI WORKFLOW
# ==========================================

class DBComplaint(Base):
    __tablename__ = "complaints"

    # We will use the conversation_id as the primary key for the MVP
    id = Column(String, primary_key=True, index=True)
    
    # -------------------------
    # Product Information
    # -------------------------
    product_name = Column(String, nullable=True, index=True)
    batch_number = Column(String, nullable=True, index=True)
    manufacturer = Column(String, nullable=True)
    strength = Column(String, nullable=True)
    dosage_form = Column(String, nullable=True)
    
    # NEW: Formal database link to the actual batch record
    linked_batch_id = Column(Integer, ForeignKey("batches.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # -------------------------
    # Complaint Information
    # -------------------------
    description = Column(Text, nullable=True)
    location = Column(String, nullable=True)
    customer_name = Column(String, nullable=True)
    customer_contact = Column(String, nullable=True)
    complaint_category = Column(String, nullable=True)
    originating_site_block = Column(String, nullable=True)
    impacted_non_product_material = Column(String, nullable=True)
    
    # -------------------------
    # AI Analysis
    # -------------------------
    severity = Column(String, nullable=True)
    priority = Column(String, nullable=True)
    summary = Column(Text, nullable=True)
    recommended_action = Column(String, nullable=True)
    risk_assessment = Column(Text, nullable=True)
    
    # -------------------------
    # Workflow Metadata
    # -------------------------
    status = Column(String, default="draft", index=True)
    source = Column(String, default="chat")
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    manufacturing_date = Column(String, nullable=True)
    expiry_date = Column(String, nullable=True)
    affected_quantity = Column(String, nullable=True)

    # -------------------------
    # Relationships
    # -------------------------
    chat_messages = relationship("DBChatMessage", back_populates="complaint", cascade="all, delete-orphan")
    
    # NEW: Allows easy access to production history when QA investigates
    linked_batch = relationship("DBBatch")


class DBChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    
    # Foreign key linking back to the complaints table
    complaint_id = Column(String, ForeignKey("complaints.id", ondelete="CASCADE"), index=True, nullable=False)
    
    # 'user', 'assistant', or 'system'
    role = Column(String, nullable=False)
    
    # The actual chat text
    message = Column(Text, nullable=False)
    
    timestamp = Column(DateTime, default=datetime.utcnow)

    # Relationship back to the parent complaint
    complaint = relationship("DBComplaint", back_populates="chat_messages")
    # ==========================================
# MODULE 3: PRODUCTION STAGES & DOCUMENTS
# ==========================================

class DBProductionStage(Base):
    __tablename__ = "production_stages"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # E.g., Dispensing, Granulation, Compression, Coating, Packaging
    stage_name = Column(String, nullable=False) 
    
    performed_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    
    # E.g., Pending, In Progress, Completed, Deviated
    status = Column(String, default="Pending") 
    notes = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    batch = relationship("DBBatch", backref="production_stages")
    performed_by = relationship("DBUser")


class DBBatchDocument(Base):
    __tablename__ = "batch_documents"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # E.g., BMR (Batch Manufacturing Record), Analytical Report, Logbook
    document_type = Column(String, nullable=False) 
    
    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False) # Path to where the file is saved locally or in cloud storage
    
    uploaded_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    batch = relationship("DBBatch", backref="documents")
    uploaded_by = relationship("DBUser")