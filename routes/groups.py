from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc, asc
from typing import List, Optional, Union
from uuid import UUID
import asyncio
from datetime import datetime

from database import get_db
from models import Group, GroupMember, GroupAdmin, Profile, MemberStatus, GroupStatus
from schemas import (
    GroupCreate, GroupUpdate, GroupResponse, GroupWithDetails,
    GroupMemberCreate, GroupMemberUpdate, GroupMemberResponse,
    GroupAdminCreate, GroupAdminResponse, BlockchainSyncResponse, TransactionResponse,
    GroupMemberConfirmationResponse, BlockchainInfo, GroupMemberBlockchainInfo, ConfirmMemberJoinRequest
)
from web3_service import Web3Service

class GroupRoutes:
    def __init__(self):
        self.router = APIRouter(prefix="/groups", tags=["groups"])
        self.web3_service = Web3Service()
        self._register_routes()
    
    def _register_routes(self):
        """Register all group-related routes"""
        # Core group routes
        self.router.add_api_route("/create-with-transaction", self.create_group_with_transaction, methods=["POST"], response_model=GroupResponse)
        self.router.add_api_route("/", self.get_groups, methods=["GET"], response_model=List[GroupResponse])
        self.router.add_api_route("/{group_id}", self.get_group, methods=["GET"], response_model=GroupWithDetails)
        self.router.add_api_route("/{group_id}", self.update_group, methods=["PUT"], response_model=GroupResponse)
        self.router.add_api_route("/{group_id}", self.delete_group, methods=["DELETE"])
        
        # Member management with Web3 integration
        self.router.add_api_route("/{group_id}/members", self.add_member, methods=["POST"], response_model=Union[GroupMemberResponse, TransactionResponse])
        self.router.add_api_route("/{group_id}/members/confirm", self.confirm_member_join, methods=["POST"], response_model=GroupMemberConfirmationResponse)
        self.router.add_api_route("/{group_id}/members", self.get_group_members, methods=["GET"], response_model=List[GroupMemberResponse])
        self.router.add_api_route("/{group_id}/members/{member_id}", self.update_member, methods=["PUT"], response_model=GroupMemberResponse)
        self.router.add_api_route("/{group_id}/members/{member_id}", self.remove_member, methods=["DELETE"])
        
        # NEW: Web3 transaction preparation endpoints
        self.router.add_api_route("/prepare-transaction", self.prepare_group_creation_transaction, methods=["POST"])
        self.router.add_api_route("/{group_id}/join-transaction", self.prepare_join_transaction, methods=["POST"])
        self.router.add_api_route("/{group_id}/contribute-transaction", self.prepare_contribute_transaction, methods=["POST"])
        
        # NEW: Web3 transaction verification endpoints
        self.router.add_api_route("/{group_id}/verify-creation", self.verify_group_creation, methods=["POST"])
        self.router.add_api_route("/{group_id}/verify-join", self.verify_join_transaction, methods=["POST"])
        self.router.add_api_route("/{group_id}/verify-contribution", self.verify_contribution_transaction, methods=["POST"])
        
        # Admin management with Web3 integration
        self.router.add_api_route("/{group_id}/admins", self.add_admin, methods=["POST"], response_model=GroupAdminResponse)
        self.router.add_api_route("/{group_id}/admins", self.get_group_admins, methods=["GET"], response_model=List[GroupAdminResponse])
        self.router.add_api_route("/{group_id}/admins/{admin_id}", self.remove_admin, methods=["DELETE"])
        
        # NEW: Admin approval system for blockchain groups
        self.router.add_api_route("/{group_id}/admin/approve-join", self.admin_approve_join_request, methods=["POST"])
        self.router.add_api_route("/{group_id}/pending-members", self.get_pending_members, methods=["GET"])
        
        # User-specific routes
        self.router.add_api_route("/user/{user_id}", self.get_user_groups, methods=["GET"], response_model=List[GroupResponse])
        
        # Web3/Blockchain routes
        self.router.add_api_route("/blockchain/sync", self.sync_blockchain_groups, methods=["POST"], response_model=BlockchainSyncResponse)
        self.router.add_api_route("/blockchain/stats", self.get_blockchain_stats, methods=["GET"])
        self.router.add_api_route("/blockchain/gas-estimates", self.get_gas_estimates, methods=["GET"])
        self.router.add_api_route("/creator/{creator_address}/blockchain", self.get_creator_groups_blockchain, methods=["GET"])
    
    # NEW: Web3 Transaction Preparation Endpoints
    async def prepare_group_creation_transaction(
        self, 
        group_data: GroupCreate, 
        db: Session = Depends(get_db)
    ) -> dict:
        """Phase 1: Prepare transaction data for frontend signing"""
        # Verify creator exists
        creator = db.query(Profile).filter(
            Profile.user_id == group_data.created_by
        ).first()
        if not creator:
            raise HTTPException(status_code=404, detail="Creator profile not found")
        
        creator_address = group_data.wallet_address
        if not creator_address:
            raise HTTPException(status_code=400, detail="Wallet address is required")
        
        if not creator_address.startswith('0x') or len(creator_address) != 42:
            raise HTTPException(
                status_code=400, 
                detail="Invalid wallet address format"
            )
        
        try:
            result = await self.web3_service.prepare_group_creation_transaction(
                group_data, 
                creator_address
            )
            if not result['success']:
                raise HTTPException(status_code=500, detail=result['error'])
            
            # Include group data for frontend reference
            result['group_data'] = group_data.model_dump(mode='json')
            return result
            
        except Exception as e:
            raise HTTPException(
                status_code=500, 
                detail=f"Failed to prepare transaction: {str(e)}"
            )

    
    async def prepare_join_transaction(self, group_id: UUID, user_address: str, db: Session = Depends(get_db)) -> dict:
        """Prepare a join group transaction for user to sign"""
        # Verify group exists and get contract address
        group = db.query(Group).filter(Group.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        contract_address = getattr(group, 'contract_address', None)
        if not contract_address:
            raise HTTPException(status_code=400, detail="Group does not have a blockchain contract")
        
        try:
            result = await self.web3_service.prepare_join_group_transaction(contract_address, user_address)
            if not result['success']:
                raise HTTPException(status_code=500, detail=result['error'])
            
            return result
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to prepare join transaction: {str(e)}")
    
    async def prepare_contribute_transaction(
        self, 
        group_id: UUID, 
        user_address: str, 
        contribution_amount: int, 
        db: Session = Depends(get_db)
    ) -> dict:
        """Prepare a contribution transaction for user to sign"""
        # Verify group exists
        group = db.query(Group).filter(Group.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        contract_address = getattr(group, 'contract_address', None)
        if not contract_address:
            raise HTTPException(status_code=400, detail="Group does not have a blockchain contract")
        
        try:
            result = await self.web3_service.prepare_contribute_transaction(
                contract_address, user_address, contribution_amount
            )
            if not result['success']:
                raise HTTPException(status_code=500, detail=result['error'])
            
            return result
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to prepare contribution transaction: {str(e)}")
    
    # NEW: Web3 Transaction Verification Endpoints
    async def verify_group_creation(
        self, 
        group_id: UUID, 
        tx_hash: str, 
        creator_address: str, 
        db: Session = Depends(get_db)
    ) -> dict:
        """Verify group creation transaction and update database"""
        try:
            result = await self.web3_service.verify_group_creation_transaction(tx_hash, creator_address)
            if not result['success']:
                raise HTTPException(status_code=400, detail=result['error'])
            
            # Update group in database with blockchain info
            group = db.query(Group).filter(Group.id == group_id).first()
            if group:
                setattr(group, 'contract_address', result['group_address'])
                setattr(group, 'creation_tx_hash', result['tx_hash'])
                setattr(group, 'creation_block_number', result['block_number'])
                setattr(group, 'is_blockchain_synced', True)
                setattr(group, 'last_blockchain_sync', datetime.utcnow())
                db.commit()
            
            return result
            
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Verification failed: {str(e)}")
    
    async def verify_join_transaction(
        self, 
        group_id: UUID, 
        tx_hash: str, 
        user_address: str, 
        user_id: UUID,
        db: Session = Depends(get_db)
    ) -> dict:
        """Verify join transaction and update member status"""
        # Get group contract address
        group = db.query(Group).filter(Group.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        contract_address = getattr(group, 'contract_address', None)
        if not contract_address:
            raise HTTPException(status_code=400, detail="Group does not have a blockchain contract")
        
        try:
            result = await self.web3_service.verify_join_transaction(tx_hash, contract_address, user_address)
            if not result['success']:
                raise HTTPException(status_code=400, detail=result['error'])
            
            # Update or create member record
            existing_member = db.query(GroupMember).filter(
                GroupMember.group_id == group_id,
                GroupMember.user_id == user_id
            ).first()
            
            if existing_member:
                setattr(existing_member, 'status', MemberStatus.active)
            else:
                db_member = GroupMember(
                    group_id=group_id,
                    user_id=user_id,
                    status=MemberStatus.active
                )
                db.add(db_member)
            
            db.commit()
            return result
            
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Join verification failed: {str(e)}")
    
    async def verify_contribution_transaction(
        self, 
        group_id: UUID, 
        tx_hash: str, 
        user_address: str, 
        expected_amount: Optional[int] = None,
        db: Session = Depends(get_db)
    ) -> dict:
        """Verify contribution transaction"""
        # Get group contract address
        group = db.query(Group).filter(Group.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        contract_address = getattr(group, 'contract_address', None)
        if not contract_address:
            raise HTTPException(status_code=400, detail="Group does not have a blockchain contract")
        
        try:
            result = await self.web3_service.verify_contribution_transaction(
                tx_hash, contract_address, user_address, expected_amount
            )
            if not result['success']:
                raise HTTPException(status_code=400, detail=result['error'])
            
            return result
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Contribution verification failed: {str(e)}")
    
    # NEW: Admin Approval System
    async def admin_approve_join_request(
        self, 
        group_id: UUID, 
        applicant_address: str, 
        admin_user_id: UUID,
        db: Session = Depends(get_db)
    ) -> dict:
        """Admin approves a join request on the blockchain (requires admin private key in Web3Service)"""
        # Verify group exists
        group = db.query(Group).filter(Group.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        # Verify admin permissions
        admin = db.query(GroupAdmin).filter(
            GroupAdmin.group_id == group_id,
            GroupAdmin.user_id == admin_user_id
        ).first()
        if not admin:
            raise HTTPException(status_code=403, detail="User is not an admin of this group")
        
        contract_address = getattr(group, 'contract_address', None)
        if not contract_address:
            raise HTTPException(status_code=400, detail="Group does not have a blockchain contract")
        
        try:
            result = await self.web3_service.admin_approve_join_request(contract_address, applicant_address)
            if not result['success']:
                raise HTTPException(status_code=500, detail=result['error'])
            
            return result
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Admin approval failed: {str(e)}")
    
    def get_pending_members(self, group_id: UUID, db: Session = Depends(get_db)) -> List[GroupMemberResponse]:
        """Get members with pending status (waiting for admin approval)"""
        pending_members = db.query(GroupMember).filter(
            GroupMember.group_id == group_id,
            GroupMember.status == MemberStatus.pending
        ).all()
        
        return [GroupMemberResponse.model_validate(member) for member in pending_members]
    
    # NEW: Gas estimates endpoint
    async def get_gas_estimates(self) -> dict:
        """Get current gas price estimates for frontend"""
        try:
            result = await self.web3_service.get_gas_estimates()
            if not result['success']:
                raise HTTPException(status_code=500, detail=result['error'])
            
            return result
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to get gas estimates: {str(e)}")
    
    async def create_group_with_transaction(
        self,
        group_data: GroupCreate = Body(...),
        signed_tx_hash: str = Body(...),
        db: Session = Depends(get_db)
    ) -> GroupResponse:
        """Phase 2: Create group after transaction is signed and submitted"""
        # Verify creator exists
        creator = db.query(Profile).filter(
            Profile.user_id == group_data.created_by
        ).first()
        if not creator:
            raise HTTPException(status_code=404, detail="Creator profile not found")
        
        try:
            # Wait for transaction confirmation
            tx_result = await self.web3_service.wait_for_transaction_confirmation(
                signed_tx_hash
            )
            
            if not tx_result['success']:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Transaction failed: {tx_result.get('error')}"
                )
            
            # Extract contract address from transaction receipt
            contract_address = tx_result.get('contract_address')
            if not contract_address:
                raise HTTPException(
                    status_code=500, 
                    detail="Contract address not found in transaction receipt"
                )
            
            # Create group in database with blockchain info
            group_dict = group_data.model_dump()
            group_dict.pop('wallet_address', None)
            group_dict.pop('network_info', None)
            
            group_dict.update({
                'contract_address': contract_address,
                'creation_tx_hash': signed_tx_hash,
                'creation_block_number': tx_result.get('block_number'),
                'is_blockchain_synced': True,
                'last_blockchain_sync': datetime.utcnow()
            })
            
            db_group = Group(**group_dict)
            db.add(db_group)
            db.flush()  # Get the group ID without committing
            
            # Add creator as admin
            admin_data = GroupAdminCreate(
                group_id=db_group.id,
                user_id=group_data.created_by
            )
            db_admin = GroupAdmin(**admin_data.model_dump())
            db.add(db_admin)
            
            # Add creator as active member
            member_data = GroupMemberCreate(
                group_id=db_group.id,
                user_id=group_data.created_by,
                wallet_address=group_data.wallet_address
            )
            db_member = GroupMember(
                **member_data.model_dump(), 
                status=MemberStatus.active
            )
            db.add(db_member)
            
            # Commit everything together
            db.commit()
            db.refresh(db_group)
            
            return GroupResponse.model_validate(db_group)
            
        except HTTPException:
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(
                status_code=500, 
                detail=f"Group creation failed: {str(e)}"
            )
    
    def get_groups(
        self,
        db: Session = Depends(get_db),
        skip: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=100),
        status: Optional[GroupStatus] = None,
        search: Optional[str] = None,
        sort_by: str = Query("created_at", pattern="^(created_at|name|start_date|contribution_amount)$"),
        sort_order: str = Query("desc", pattern="^(asc|desc)$"),
        include_blockchain: bool = Query(False, description="Include blockchain verification")
    ) -> List[GroupResponse]:
        """Get all groups with filtering, pagination, and optional blockchain verification"""
        query = db.query(Group)
        
        # Apply filters
        if status:
            query = query.filter(Group.status == status)
        if search:
            query = query.filter(Group.name.ilike(f"%{search}%"))
        
        # Apply sorting
        order_func = asc if sort_order == "asc" else desc
        if sort_by == "name":
            query = query.order_by(order_func(Group.name))
        elif sort_by == "start_date":
            query = query.order_by(order_func(Group.start_date))
        elif sort_by == "contribution_amount":
            query = query.order_by(order_func(Group.contribution_amount))
        else:
            query = query.order_by(order_func(Group.created_at))
        
        groups = query.offset(skip).limit(limit).all()
        
        # Add member count and blockchain info to each group
        group_responses = []
        for group in groups:
            member_count = db.query(GroupMember).filter(
                GroupMember.group_id == group.id,
                GroupMember.status == MemberStatus.active
            ).count()
            
            group_data = GroupResponse.model_validate(group)
            group_data.member_count = member_count
            
            # Add blockchain verification if requested
            if include_blockchain and group.contract_address is not None:
                try:
                    # Verify group still exists on blockchain
                    blockchain_groups = asyncio.run(self.web3_service.get_blockchain_groups())
                    contract_addr = getattr(group, 'contract_address', None)
                    group_data.blockchain_verified = contract_addr in blockchain_groups if contract_addr else False
                    group_data.blockchain_info = BlockchainInfo(
                        contract_address=getattr(group, 'contract_address', None),
                        tx_hash=getattr(group, 'creation_tx_hash', None),
                        block_number=getattr(group, 'creation_block_number', None),
                        verified=group_data.blockchain_verified
                    )
                except Exception as e:
                    print(f"Blockchain verification error: {e}")
                    group_data.blockchain_verified = False
            
            group_responses.append(group_data)
        
        return group_responses
    
    def get_group(self, group_id: UUID, db: Session = Depends(get_db)) -> GroupWithDetails:
        """Get a specific group with full details including blockchain info"""
        group = db.query(Group).options(
            joinedload(Group.members).joinedload(GroupMember.user),
            joinedload(Group.admins).joinedload(GroupAdmin.user)
        ).filter(Group.id == group_id).first()
        
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        group_details = GroupWithDetails.model_validate(group)
        
        # Add blockchain verification
        if group.contract_address is not None:
            try:
                blockchain_groups = asyncio.run(self.web3_service.get_blockchain_groups())
                contract_addr = getattr(group, 'contract_address', None)
                group_details.blockchain_verified = contract_addr in blockchain_groups if contract_addr else False
                group_details.blockchain_info = BlockchainInfo(
                    contract_address=getattr(group, 'contract_address', None),
                    tx_hash=getattr(group, 'creation_tx_hash', None),
                    block_number=getattr(group, 'creation_block_number', None),
                    verified=group_details.blockchain_verified
                )
            except Exception as e:
                print(f"Blockchain verification error: {e}")
                group_details.blockchain_verified = False
        
        return group_details
    
    def update_group(self, group_id: UUID, group_data: GroupUpdate, db: Session = Depends(get_db)) -> GroupResponse:
        """Update a group"""
        db_group = db.query(Group).filter(Group.id == group_id).first()
        if not db_group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        # Update fields
        update_data = group_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_group, field, value)
        
        # Update sync status
        setattr(db_group, 'last_blockchain_sync', datetime.utcnow())
        
        db.commit()
        db.refresh(db_group)
        
        return GroupResponse.model_validate(db_group)
    
    def delete_group(self, group_id: UUID, db: Session = Depends(get_db)):
        """Delete a group (database only - blockchain groups are immutable)"""
        db_group = db.query(Group).filter(Group.id == group_id).first()
        if not db_group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        # Note: We only delete from database. Blockchain groups are immutable.
        # In practice, you might want to mark the group as inactive instead
        setattr(db_group, 'status', GroupStatus.inactive)
        db.commit()
        
        return {"message": "Group marked as inactive (blockchain groups cannot be deleted)"}
    
    # Updated add_member method with prepare/verify pattern
    async def add_member(self, group_id: UUID, member_data: GroupMemberCreate, db: Session = Depends(get_db)) -> Union[GroupMemberResponse, TransactionResponse]:
        """Add a member to a group - for blockchain groups, this prepares the transaction"""
        # Verify group exists
        group = db.query(Group).filter(Group.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        # Verify user exists
        user = db.query(Profile).filter(Profile.user_id == member_data.user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Check if wallet address is required and provided
        wallet_address = member_data.wallet_address
        if wallet_address:
            setattr(user, 'wallet_address', wallet_address)
            db.add(user)
            db.commit()
            db.refresh(user)
            
        if group.contract_address is not None:  # This is a blockchain group
            if not wallet_address:
                raise HTTPException(
                    status_code=400, 
                    detail="Wallet address is required to join blockchain-enabled groups."
                )
            
            # Validate wallet address format
            if not wallet_address.startswith('0x') or len(wallet_address) != 42:
                raise HTTPException(status_code=400, detail="Invalid wallet address format.")
        
        # Check if user is already a member
        existing_member = db.query(GroupMember).filter(
            GroupMember.group_id == group_id,
            GroupMember.user_id == member_data.user_id
        ).first()
        if existing_member:
            raise HTTPException(status_code=400, detail="User is already a member of this group")
        
        # Check group capacity
        active_members_count = db.query(GroupMember).filter(
            GroupMember.group_id == group_id,
            GroupMember.status == MemberStatus.active
        ).count()
        max_members = getattr(group, 'max_members', 20)
        if active_members_count >= max_members:
            raise HTTPException(status_code=400, detail="Group is at maximum capacity")
        
        try:
            # For blockchain groups, prepare the transaction
            contract_address = getattr(group, 'contract_address', None)
            if contract_address and wallet_address:
                # Prepare blockchain join transaction
                blockchain_result = await self.web3_service.prepare_join_group_transaction(
                    contract_address, 
                    wallet_address
                )
                
                if not blockchain_result['success']:
                    raise HTTPException(
                        status_code=500, 
                        detail=f"Failed to prepare blockchain transaction: {blockchain_result['error']}"
                    )
                
                # Create pending member record in database
                db_member = GroupMember(
                    group_id=group_id,
                    user_id=member_data.user_id,
                    status=MemberStatus.pending  # Will be activated after transaction verification
                )
                db.add(db_member)
                db.commit()
                
                return TransactionResponse(
                    requires_signature=True,
                    transaction=blockchain_result['transaction'],
                    message="Please sign the transaction with your wallet to complete joining the group.",
                    group_id=group_id,
                    user_id=member_data.user_id
                )
            
            else:
                # Create member in database directly (non-blockchain group)
                db_member = GroupMember(
                    group_id=group_id,
                    user_id=member_data.user_id,
                    status=MemberStatus.active
                )
                
                db.add(db_member)
                db.commit()
                db.refresh(db_member)
                
                return GroupMemberResponse.model_validate(db_member)
            
        except HTTPException:
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to add member: {str(e)}")

    async def confirm_member_join(
        self,
        group_id: UUID,
        body: ConfirmMemberJoinRequest,
        db: Session = Depends(get_db)
    ) -> GroupMemberConfirmationResponse:
        """Confirm member join after successful blockchain transaction."""
        user_id = body.user_id
        tx_hash = body.tx_hash
        group = db.query(Group).filter(Group.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        user = db.query(Profile).filter(Profile.user_id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        wallet_address = getattr(user, 'wallet_address', None)
        if not wallet_address:
            raise HTTPException(status_code=400, detail="User wallet address not found")
        
        try:
            # Verify blockchain transaction
            contract_address = getattr(group, 'contract_address', None)
            if not contract_address:
                raise HTTPException(status_code=400, detail="Group does not have a blockchain contract")
                
            verification_result = await self.web3_service.verify_join_transaction(
                tx_hash, 
                contract_address, 
                wallet_address
            )
            
            if not verification_result['success']:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Transaction verification failed: {verification_result['error']}"
                )
            
            existing_member = db.query(GroupMember).filter(
                GroupMember.group_id == group_id,
                GroupMember.user_id == user_id
            ).first()
            
            if existing_member:
                # Update existing member status
                setattr(existing_member, 'status', MemberStatus.active)
                db_member = existing_member
            else:
                db_member = GroupMember(
                    group_id=group_id,
                    user_id=user_id,
                    status=MemberStatus.active
                )
                db.add(db_member)
            
            db.commit()
            db.refresh(db_member)
            
            # Create blockchain info
            blockchain_info = GroupMemberBlockchainInfo(
                wallet_address=wallet_address,
                tx_hash=verification_result['tx_hash'],
                block_number=verification_result['block_number'],
                gas_used=verification_result['gas_used'],
                joined_on_blockchain=True
            )
            
            # Create the response
            member_response = GroupMemberResponse.model_validate(db_member)
            response = GroupMemberConfirmationResponse(
                **member_response.model_dump(),
                blockchain_info=blockchain_info
            )
            
            return response
            
        except HTTPException:
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to confirm member join: {str(e)}")
    
    # Rest of the methods remain the same...
    def get_group_members(self, group_id: UUID, db: Session = Depends(get_db)) -> List[GroupMemberResponse]:
        """Get all members of a group"""
        members = db.query(GroupMember).filter(GroupMember.group_id == group_id).all()
        return [GroupMemberResponse.model_validate(member) for member in members]
    
    def update_member(self, group_id: UUID, member_id: UUID, member_data: GroupMemberUpdate, db: Session = Depends(get_db)) -> GroupMemberResponse:
        """Update a group member"""
        db_member = db.query(GroupMember).filter(
            GroupMember.id == member_id,
            GroupMember.group_id == group_id
        ).first()
        
        if not db_member:
            raise HTTPException(status_code=404, detail="Member not found")
        
        # Update fields
        update_data = member_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_member, field, value)
        
        db.commit()
        db.refresh(db_member)
        
        return GroupMemberResponse.model_validate(db_member)
    
    def remove_member(self, group_id: UUID, member_id: UUID, db: Session = Depends(get_db)):
        """Remove a member from a group"""
        db_member = db.query(GroupMember).filter(
            GroupMember.id == member_id,
            GroupMember.group_id == group_id
        ).first()
        
        if not db_member:
            raise HTTPException(status_code=404, detail="Member not found")
        
        db.delete(db_member)
        db.commit()
        
        return {"message": "Member removed successfully"}
    
    def add_admin(self, group_id: UUID, admin_data: GroupAdminCreate, db: Session = Depends(get_db)) -> GroupAdminResponse:
        """Add an admin to a group"""
        # Verify group exists
        group = db.query(Group).filter(Group.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        # Verify user exists
        user = db.query(Profile).filter(Profile.user_id == admin_data.user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Check if user is already an admin
        existing_admin = db.query(GroupAdmin).filter(
            GroupAdmin.group_id == group_id,
            GroupAdmin.user_id == admin_data.user_id
        ).first()
        if existing_admin:
            raise HTTPException(status_code=400, detail="User is already an admin of this group")
        
        # Create admin
        db_admin = GroupAdmin(
            group_id=group_id,
            user_id=admin_data.user_id,
            assigned_by=admin_data.assigned_by
        )
        db.add(db_admin)
        db.commit()
        db.refresh(db_admin)
        
        return GroupAdminResponse.model_validate(db_admin)
    
    def get_group_admins(self, group_id: UUID, db: Session = Depends(get_db)) -> List[GroupAdminResponse]:
        """Get all admins of a group"""
        admins = db.query(GroupAdmin).filter(GroupAdmin.group_id == group_id).all()
        return [GroupAdminResponse.model_validate(admin) for admin in admins]
    
    def remove_admin(self, group_id: UUID, admin_id: UUID, db: Session = Depends(get_db)):
        """Remove an admin from a group"""
        db_admin = db.query(GroupAdmin).filter(
            GroupAdmin.id == admin_id,
            GroupAdmin.group_id == group_id
        ).first()
        
        if not db_admin:
            raise HTTPException(status_code=404, detail="Admin not found")
        
        db.delete(db_admin)
        db.commit()
        
        return {"message": "Admin removed successfully"}
    
    def get_user_groups(self, user_id: UUID, db: Session = Depends(get_db)) -> List[GroupResponse]:
        """Get all groups for a specific user"""
        groups = db.query(Group).join(GroupMember).filter(
            GroupMember.user_id == user_id,
            GroupMember.status == MemberStatus.active
        ).all()
        
        return [GroupResponse.model_validate(group) for group in groups]
    
    # Web3/Blockchain methods
    async def sync_blockchain_groups(self, db: Session = Depends(get_db)) -> BlockchainSyncResponse:
        """Sync groups from blockchain to database"""
        try:
            blockchain_groups = await self.web3_service.get_blockchain_groups()
            
            synced_count = 0
            errors = []
            
            for group_address in blockchain_groups:
                try:
                    # Check if group already exists in database
                    existing_group = db.query(Group).filter(
                        Group.contract_address == group_address
                    ).first()
                    
                    if existing_group:
                        # Update sync timestamp
                        setattr(existing_group, 'last_blockchain_sync', datetime.utcnow())
                        setattr(existing_group, 'is_blockchain_synced', True)
                    else:
                        # Log unsynced group (you might want to implement full group data retrieval)
                        print(f"Found unsynced group: {group_address}")
                        synced_count += 1
                        
                except Exception as e:
                    errors.append(f"Error syncing group {group_address}: {str(e)}")
            
            db.commit()
            
            return BlockchainSyncResponse(
                total_blockchain_groups=len(blockchain_groups),
                synced_count=synced_count,
                errors=errors
            )
            
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Blockchain sync failed: {str(e)}")
    
    async def get_blockchain_stats(self):
        """Get blockchain statistics"""
        try:
            all_groups = await self.web3_service.get_blockchain_groups()
            group_counter = self.web3_service.get_group_counter()
            network_info = await self.web3_service.get_network_info()
            
            return {
                "total_groups": len(all_groups),
                "group_counter": group_counter,
                "factory_address": self.web3_service.factory_address,
                "network_connected": self.web3_service.is_connected(),
                "latest_block": self.web3_service.get_latest_block_number(),
                "network_info": network_info
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error fetching blockchain stats: {str(e)}")
    
    async def get_creator_groups_blockchain(self, creator_address: str):
        """Get groups created by a specific wallet address from blockchain"""
        try:
            # Validate Ethereum address format
            if not creator_address.startswith('0x') or len(creator_address) != 42:
                raise HTTPException(status_code=400, detail="Invalid Ethereum address format")
            
            groups = await self.web3_service.get_creator_groups_from_blockchain(creator_address)
            return {
                "creator_address": creator_address, 
                "groups": groups,
                "count": len(groups)
            }
        except HTTPException:
            raise

# Create router instance
group_routes = GroupRoutes()
router = group_routes.router