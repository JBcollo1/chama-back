from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Optional, List
from datetime import datetime
from uuid import UUID
from decimal import Decimal
from models import ContributionStatus, GroupStatus, MemberStatus, NotificationType
from web3 import Web3

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
    contribution_amount: Decimal = Field(gt=0)
    contribution_frequency: Optional[str] = "monthly"
    max_members: int = Field(default=20, gt=0)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None

class GroupCreate(GroupBase):
    name: str = Field(..., min_length=1, max_length=255)
    max_members: int = Field(default=20, ge=3, le=100)
    approval_required: Optional[bool] = False
    wallet_address: Optional[str] = Field(None, pattern="^0x[a-fA-F0-9]{40}$")
    network_info: Optional[dict] = None
    created_by: UUID
class GroupUpdate(BaseSchema):
    name: Optional[str] = None
    description: Optional[str] = None
    contribution_amount: Optional[Decimal] = Field(None, gt=0)
    contribution_frequency: Optional[str] = None
    max_members: Optional[int] = Field(None, gt=0)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    status: Optional[GroupStatus] = None

class BlockchainInfo(BaseModel):
    contract_address: Optional[str] = None
    tx_hash: Optional[str] = None
    block_number: Optional[int] = None
    gas_used: Optional[int] = None
    verified: Optional[bool] = None
class TransactionResponse(BaseModel):
    requires_signature: bool
    transaction: dict
    message: str
    group_id: UUID
    user_id: UUID


class GroupResponse(GroupBase):
    id: UUID
    member_count: Optional[int] = 0
    status: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    
    # Blockchain fields
    contract_address: Optional[str] = None
    creation_tx_hash: Optional[str] = None
    creation_block_number: Optional[int] = None
    blockchain_verified: Optional[bool] = None
    blockchain_info: Optional[BlockchainInfo] = None

class GroupWithDetails(GroupResponse):
    members: List['GroupMemberResponse'] = []
    admins: List['GroupAdminResponse'] = []

# Group Member schemas
class GroupMemberBase(BaseSchema):
    group_id: UUID
    user_id: UUID
    # wallet_address: Optional[str] = Field(None, pattern="^0x[a-fA-F0-9]{40}$")
class GroupCreateWithTransaction(BaseModel):
    # All GroupCreate fields
    name: str
    description: Optional[str] = None
    contribution_amount: float
    contribution_cycle: str
    max_members: int
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    category: str
    created_by: str
    status: str
    wallet_address: str
    network_info: Optional[dict] = None
    
    # Transaction hash from signed transaction
    signed_tx_hash: str
class GroupMemberCreate(BaseSchema):
    group_id: UUID
    user_id: UUID
    wallet_address: Optional[str] = Field(None, pattern="^0x[a-fA-F0-9]{40}$")

    @field_validator("wallet_address")
    def checksum_address(cls, v):
        try:
            return Web3.to_checksum_address(v)
        except Exception:
            raise ValueError("Invalid Ethereum address")

class GroupMemberUpdate(BaseSchema):
    status: Optional[MemberStatus] = None

class GroupMemberResponse(GroupMemberBase):
    id: UUID
    status: MemberStatus
    joined_at: datetime
    left_at: Optional[datetime] = None

class GroupMemberBlockchainInfo(BaseModel):
    wallet_address: str
    tx_hash: str
    block_number: int
    gas_used: int
    joined_on_blockchain: bool

class GroupMemberConfirmationResponse(GroupMemberResponse):
    blockchain_info: GroupMemberBlockchainInfo
    
class ConfirmMemberJoinRequest(BaseModel):
    user_id: UUID
    tx_hash: str

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
    amount: Decimal = Field(gt=0)
    due_date: datetime
    notes: Optional[str] = None

class ContributionCreate(ContributionBase):
    pass

class ContributionUpdate(BaseSchema):
    amount: Optional[Decimal] = Field(None, gt=0)
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

    
class WalletConnect(BaseModel):
    wallet_address: str = Field(..., pattern="^0x[a-fA-F0-9]{40}$")
    wallet_provider: Optional[str] = None


class BlockchainSyncResponse(BaseModel):
    total_blockchain_groups: int
    synced_count: int
    errors: List[str] = []
# Update forward references
GroupWithDetails.model_rebuild()