from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime
from uuid import UUID
from models import ContributionStatus, GroupStatus, MemberStatus, NotificationType

# Base schemas
class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

# Profile schemas
class ProfileBase(BaseSchema):
    display_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    phone_number: Optional[str] = None
    location: Optional[str] = None

class ProfileCreate(ProfileBase):
    user_id: UUID

class ProfileUpdate(ProfileBase):
    pass

class ProfileResponse(ProfileBase):
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime

# Group schemas
class GroupBase(BaseSchema):
    name: str
    description: Optional[str] = None
    contribution_amount: float = Field(gt=0)
    contribution_frequency: str = "monthly"
    max_members: int = Field(default=20, gt=0)
    start_date: datetime
    end_date: Optional[datetime] = None

class GroupCreate(GroupBase):
    created_by: UUID

class GroupUpdate(BaseSchema):
    name: Optional[str] = None
    description: Optional[str] = None
    contribution_amount: Optional[float] = Field(None, gt=0)
    contribution_frequency: Optional[str] = None
    max_members: Optional[int] = Field(None, gt=0)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    status: Optional[GroupStatus] = None

class GroupResponse(GroupBase):
    id: UUID
    status: GroupStatus
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    member_count: Optional[int] = None

class GroupWithDetails(GroupResponse):
    members: List['GroupMemberResponse'] = []
    admins: List['GroupAdminResponse'] = []

# Group Member schemas
class GroupMemberBase(BaseSchema):
    group_id: UUID
    user_id: UUID

class GroupMemberCreate(GroupMemberBase):
    pass

class GroupMemberUpdate(BaseSchema):
    status: Optional[MemberStatus] = None

class GroupMemberResponse(GroupMemberBase):
    id: UUID
    status: MemberStatus
    joined_at: datetime
    left_at: Optional[datetime] = None

# Group Admin schemas
class GroupAdminBase(BaseSchema):
    group_id: UUID
    user_id: UUID

class GroupAdminCreate(GroupAdminBase):
    assigned_by: Optional[UUID] = None

class GroupAdminResponse(GroupAdminBase):
    id: UUID
    assigned_by: Optional[UUID] = None
    assigned_at: datetime

# Contribution schemas
class ContributionBase(BaseSchema):
    group_id: UUID
    member_id: UUID
    amount: float = Field(gt=0)
    due_date: datetime
    notes: Optional[str] = None

class ContributionCreate(ContributionBase):
    pass

class ContributionUpdate(BaseSchema):
    amount: Optional[float] = Field(None, gt=0)
    due_date: Optional[datetime] = None
    paid_date: Optional[datetime] = None
    status: Optional[ContributionStatus] = None
    transaction_hash: Optional[str] = None
    notes: Optional[str] = None

class ContributionResponse(ContributionBase):
    id: UUID
    paid_date: Optional[datetime] = None
    status: ContributionStatus
    transaction_hash: Optional[str] = None
    created_at: datetime
    updated_at: datetime

# Notification schemas
class NotificationBase(BaseSchema):
    user_id: UUID
    type: NotificationType
    title: str
    message: str
    group_id: Optional[UUID] = None
    contribution_id: Optional[UUID] = None

class NotificationCreate(NotificationBase):
    pass

class NotificationUpdate(BaseSchema):
    is_read: Optional[bool] = None

class NotificationResponse(NotificationBase):
    id: UUID
    is_read: bool
    created_at: datetime

# Avalanche Token schemas
class AvalancheTokenBase(BaseSchema):
    name: str
    symbol: str
    price: Optional[float] = None
    market_cap: Optional[float] = None
    volume_24h: Optional[float] = None
    price_change_24h: Optional[float] = None

class AvalancheTokenCreate(AvalancheTokenBase):
    pass

class AvalancheTokenUpdate(BaseSchema):
    price: Optional[float] = None
    market_cap: Optional[float] = None
    volume_24h: Optional[float] = None
    price_change_24h: Optional[float] = None
    last_updated: Optional[datetime] = None

class AvalancheTokenResponse(AvalancheTokenBase):
    id: UUID
    created_at: Optional[datetime] = None
    last_updated: Optional[datetime] = None

# Update forward references
GroupWithDetails.model_rebuild()