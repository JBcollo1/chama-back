from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, ForeignKey, Enum, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
import enum
import uuid

Base = declarative_base()

# Enums

class PunishmentAction(enum.Enum):
    BAN = "ban"
    FINE = "fine"
    WARNING = "warning"

class PunishmentReason(enum.Enum):
    LATE_PAYMENT = "late_payment"
    MISSED_PAYMENT = "missed_payment"
    RULE_VIOLATION = "rule_violation"
class ContributionStatus(enum.Enum):
    pending = "pending"
    completed = "completed"
    overdue = "overdue"

class GroupStatus(enum.Enum):
    active = "active"
    inactive = "inactive"
    completed = "completed"

class MemberStatus(enum.Enum):
    active = "active"
    inactive = "inactive"
    pending = "pending"

class NotificationType(enum.Enum):
    contribution_due = "contribution_due"
    payment_received = "payment_received"
    group_update = "group_update"
    admin_message = "admin_message"

# Models
class AvalancheToken(Base):
    __tablename__ = "avalanche_tokens"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    price = Column(Float, nullable=True)
    market_cap = Column(Float, nullable=True)
    volume_24h = Column(Float, nullable=True)
    price_change_24h = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)
    last_updated = Column(DateTime, nullable=True)

class Profile(Base):
    __tablename__ = "profiles"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    display_name = Column(String, nullable=True)
    bio = Column(Text, nullable=True)
    avatar_url = Column(String, nullable=True)
    phone_number = Column(String, nullable=True)
    location = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    created_groups = relationship("Group", back_populates="creator")
    group_memberships = relationship("GroupMember", back_populates="user")
    admin_roles = relationship("GroupAdmin", back_populates="user")
    notifications = relationship("Notification", back_populates="user")

class Group(Base):
    __tablename__ = "groups"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    contribution_amount = Column(Float, nullable=False)
    contribution_frequency = Column(String, default="monthly")
    max_members = Column(Integer, default=20)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=True)
    status = Column(Enum(GroupStatus), default=GroupStatus.active)
    created_by = Column(UUID(as_uuid=True), ForeignKey("profiles.user_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    approval_required = Column(Boolean, default=False)
    emergency_withdraw_allowed = Column(Boolean, default=False)
    
    # Relationships
    member_punishments = relationship("MemberPunishment", back_populates="group", cascade="all, delete-orphan")
    creator = relationship("Profile", back_populates="created_groups")
    members = relationship("GroupMember", back_populates="group")
    admins = relationship("GroupAdmin", back_populates="group")
    contributions = relationship("Contribution", back_populates="group")
    notifications = relationship("Notification", back_populates="group")

class GroupMember(Base):
    __tablename__ = "group_members"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("profiles.user_id"), nullable=False)
    status = Column(Enum(MemberStatus), default=MemberStatus.pending)
    joined_at = Column(DateTime, default=datetime.utcnow)
    left_at = Column(DateTime, nullable=True)
    
    # Relationships
    group = relationship("Group", back_populates="members")
    user = relationship("Profile", back_populates="group_memberships")
    contributions = relationship("Contribution", back_populates="member")
    punishments = relationship("MemberPunishment", back_populates="member", cascade="all, delete-orphan")

class GroupAdmin(Base):
    __tablename__ = "group_admins"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("profiles.user_id"), nullable=False)
    assigned_by = Column(UUID(as_uuid=True), nullable=True)
    assigned_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    group = relationship("Group", back_populates="admins")
    user = relationship("Profile", back_populates="admin_roles")

class Contribution(Base):
    __tablename__ = "contributions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id"), nullable=False)
    member_id = Column(UUID(as_uuid=True), ForeignKey("group_members.id"), nullable=False)
    amount = Column(Float, nullable=False)
    due_date = Column(DateTime, nullable=False)
    paid_date = Column(DateTime, nullable=True)
    status = Column(Enum(ContributionStatus), default=ContributionStatus.pending)
    transaction_hash = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    group = relationship("Group", back_populates="contributions")
    member = relationship("GroupMember", back_populates="contributions")
    notifications = relationship("Notification", back_populates="contribution")

class Notification(Base):
    __tablename__ = "notifications"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("profiles.user_id"), nullable=False)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id"), nullable=True)
    contribution_id = Column(UUID(as_uuid=True), ForeignKey("contributions.id"), nullable=True)
    type = Column(Enum(NotificationType), nullable=False)
    title = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    user = relationship("Profile", back_populates="notifications")
    group = relationship("Group", back_populates="notifications")
    contribution = relationship("Contribution", back_populates="notifications")

class MemberPunishment(Base):
    __tablename__ = "member_punishments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    member_id = Column(UUID(as_uuid=True), ForeignKey("group_members.id", ondelete="CASCADE"), nullable=False)
    
    action = Column(Enum(PunishmentAction), nullable=False)
    reason = Column(Enum(PunishmentReason), nullable=False)
    
    description = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)

    # Relationships
    group = relationship("Group", back_populates="member_punishments")
    member = relationship("GroupMember", back_populates="punishments")