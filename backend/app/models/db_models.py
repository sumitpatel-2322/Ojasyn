from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Integer
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()

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
    
    # -------------------------
    # Complaint Information
    # -------------------------
    description = Column(Text, nullable=True)
    location = Column(String, nullable=True)
    customer_name = Column(String, nullable=True)
    customer_contact = Column(String, nullable=True)
    
    # -------------------------
    # AI Analysis
    # -------------------------
    severity = Column(String, nullable=True)
    priority = Column(String, nullable=True)
    summary = Column(Text, nullable=True)
    recommended_action = Column(String, nullable=True)
    
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
    
    # Add under Complaint Information
    complaint_category = Column(String, nullable=True)
    originating_site_block = Column(String, nullable=True)
    impacted_non_product_material = Column(String, nullable=True)

    # Add under AI Analysis
    risk_assessment = Column(Text, nullable=True) # To store AI reasoning

    # -------------------------
    # Relationships
    # -------------------------
    # This links the complaint to its chat history. 
    # cascade="all, delete-orphan" ensures if a complaint is deleted, its chat is too.
    chat_messages = relationship("DBChatMessage", back_populates="complaint", cascade="all, delete-orphan")


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