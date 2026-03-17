from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, asc, and_
from typing import List, Optional
from uuid import UUID
from datetime import datetime

from database import get_db
from models import Contribution, Group, GroupMember, ContributionStatus
from schemas import ContributionCreate, ContributionUpdate, ContributionResponse
from web3_files.web3_contribution import ContributionContractService
from web3_files.web3_main import Web3Service


def get_contract_service() -> ContributionContractService:
    """FastAPI dependency — returns a shared ContributionContractService instance."""
    return ContributionContractService(Web3Service())


class ContributionRoutes:
    def __init__(self):
        self.router = APIRouter(prefix="/contributions", tags=["contributions"])
        self._register_routes()

    def _register_routes(self):
        # off-chain CRUD
        self.router.add_api_route(
            "/", self.create_contribution, methods=["POST"],
            response_model=ContributionResponse,
        )
        self.router.add_api_route(
            "/", self.get_contributions, methods=["GET"],
            response_model=List[ContributionResponse],
        )
        self.router.add_api_route(
            "/{contribution_id}", self.get_contribution, methods=["GET"],
            response_model=ContributionResponse,
        )
        self.router.add_api_route(
            "/{contribution_id}", self.update_contribution, methods=["PUT"],
            response_model=ContributionResponse,
        )
        self.router.add_api_route(
            "/{contribution_id}", self.delete_contribution, methods=["DELETE"],
        )

        # member payment flow 
        self.router.add_api_route(
            "/{contribution_id}/build-tx", self.build_contribute_tx, methods=["POST"],
            summary="Build unsigned contribute() tx for member wallet to sign",
        )
        self.router.add_api_route(
            "/{contribution_id}/confirm", self.confirm_contribution, methods=["POST"],
            response_model=ContributionResponse,
            summary="Verify on-chain tx and mark contribution as paid",
        )
        # Legacy / manual pay (off-chain only, no on-chain verification)
        self.router.add_api_route(
            "/{contribution_id}/pay", self.mark_as_paid, methods=["POST"],
            response_model=ContributionResponse,
        )

        # fine payment flow 
        self.router.add_api_route(
            "/{contribution_id}/fine/build-tx", self.build_pay_fine_tx, methods=["POST"],
            summary="Build unsigned payFine() tx for member wallet to sign",
        )

        # group routes
        self.router.add_api_route(
            "/group/{group_id}", self.get_group_contributions, methods=["GET"],
            response_model=List[ContributionResponse],
        )
        self.router.add_api_route(
            "/group/{group_id}/summary", self.get_group_contribution_summary, methods=["GET"],
        )
        self.router.add_api_route(
            "/group/{group_id}/on-chain-summary", self.get_group_on_chain_summary, methods=["GET"],
            summary="Live on-chain stats: period, balance, window open, active members",
        )
        self.router.add_api_route(
            "/group/{group_id}/process-payout", self.process_rotation_payout, methods=["POST"],
            summary="Backend-signed: trigger processRotationPayout() for current period",
        )
        self.router.add_api_route(
            "/group/{group_id}/check-missed", self.batch_check_missed_contributions, methods=["POST"],
            summary="Backend-signed: batch check missed contributions for all group members",
        )
        self.router.add_api_route(
            "/group/{group_id}/set-payout-queue", self.set_payout_queue, methods=["POST"],
            summary="Backend-signed: set rotation payout queue (one-time, creator only)",
        )

        # user routes 
        self.router.add_api_route(
            "/user/{user_id}", self.get_user_contributions, methods=["GET"],
            response_model=List[ContributionResponse],
        )
        self.router.add_api_route(
            "/user/{user_id}/overdue", self.get_user_overdue_contributions, methods=["GET"],
            response_model=List[ContributionResponse],
        )

        # member-level on-chain state 
        self.router.add_api_route(
            "/member/{member_wallet}/status", self.get_member_on_chain_status, methods=["GET"],
            summary="On-chain member status: contributed, active, missed, punishment",
        )
        self.router.add_api_route(
            "/member/{member_wallet}/reset-period", self.reset_last_checked_period, methods=["POST"],
            summary="Backend-signed: emergency reset of lastCheckedPeriod for a member",
        )

    
    # Off-chain CRUD
   

    def create_contribution(
        self,
        contribution_data: ContributionCreate,
        db: Session = Depends(get_db),
    ) -> ContributionResponse:
        """Create a new off-chain contribution record."""
        group = db.query(Group).filter(Group.id == contribution_data.group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")

        member = db.query(GroupMember).filter(
            GroupMember.id == contribution_data.member_id,
            GroupMember.group_id == contribution_data.group_id,
        ).first()
        if not member:
            raise HTTPException(status_code=404, detail="Member not found in this group")

        db_contribution = Contribution(**contribution_data.model_dump())
        db.add(db_contribution)
        db.commit()
        db.refresh(db_contribution)
        return ContributionResponse.model_validate(db_contribution)

    def get_contributions(
        self,
        db: Session = Depends(get_db),
        skip: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=100),
        status: Optional[ContributionStatus] = None,
        group_id: Optional[UUID] = None,
        member_id: Optional[UUID] = None,
        due_date_from: Optional[datetime] = None,
        due_date_to: Optional[datetime] = None,
        sort_by: str = Query("due_date", pattern="^(due_date|amount|created_at|status)$"),
        sort_order: str = Query("asc", pattern="^(asc|desc)$"),
    ) -> List[ContributionResponse]:
        """Get all contributions with filtering and pagination."""
        query = db.query(Contribution)

        if status:
            query = query.filter(Contribution.status == status)
        if group_id:
            query = query.filter(Contribution.group_id == group_id)
        if member_id:
            query = query.filter(Contribution.member_id == member_id)
        if due_date_from:
            query = query.filter(Contribution.due_date >= due_date_from)
        if due_date_to:
            query = query.filter(Contribution.due_date <= due_date_to)

        order_func = asc if sort_order == "asc" else desc
        sort_map = {
            "amount": Contribution.amount,
            "created_at": Contribution.created_at,
            "status": Contribution.status,
        }
        query = query.order_by(order_func(sort_map.get(sort_by, Contribution.due_date)))

        return [ContributionResponse.model_validate(c) for c in query.offset(skip).limit(limit).all()]

    def get_contribution(
        self, contribution_id: UUID, db: Session = Depends(get_db)
    ) -> ContributionResponse:
        """Get a specific contribution."""
        contribution = db.query(Contribution).filter(Contribution.id == contribution_id).first()
        if not contribution:
            raise HTTPException(status_code=404, detail="Contribution not found")
        return ContributionResponse.model_validate(contribution)

    def update_contribution(
        self,
        contribution_id: UUID,
        contribution_data: ContributionUpdate,
        db: Session = Depends(get_db),
    ) -> ContributionResponse:
        """Update a contribution record."""
        db_contribution = db.query(Contribution).filter(Contribution.id == contribution_id).first()
        if not db_contribution:
            raise HTTPException(status_code=404, detail="Contribution not found")

        update_data = contribution_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_contribution, field, value)

        if update_data.get("paid_date"):
            db_contribution.status = ContributionStatus.completed
        elif (
            update_data.get("due_date")
            and update_data["due_date"] < datetime.utcnow()
            and db_contribution.paid_date is None
        ):
            db_contribution.status = ContributionStatus.overdue

        db.commit()
        db.refresh(db_contribution)
        return ContributionResponse.model_validate(db_contribution)

    def delete_contribution(
        self, contribution_id: UUID, db: Session = Depends(get_db)
    ):
        """Delete a contribution record."""
        db_contribution = db.query(Contribution).filter(Contribution.id == contribution_id).first()
        if not db_contribution:
            raise HTTPException(status_code=404, detail="Contribution not found")

        db.delete(db_contribution)
        db.commit()
        return {"message": "Contribution deleted successfully"}

    def mark_as_paid(
        self,
        contribution_id: UUID,
        transaction_hash: Optional[str] = None,
        db: Session = Depends(get_db),
    ) -> ContributionResponse:
      
        db_contribution = db.query(Contribution).filter(Contribution.id == contribution_id).first()
        if not db_contribution:
            raise HTTPException(status_code=404, detail="Contribution not found")

        if db_contribution.status == ContributionStatus.completed:
            raise HTTPException(status_code=400, detail="Contribution is already paid")

        db_contribution.paid_date = datetime.utcnow()
        db_contribution.status = ContributionStatus.completed
        if transaction_hash:
            db_contribution.transaction_hash = transaction_hash

        db.commit()
        db.refresh(db_contribution)
        return ContributionResponse.model_validate(db_contribution)

    
    # Member payment flow  (on-chain)
   

    def build_contribute_tx(
        self,
        contribution_id: UUID,
        db: Session = Depends(get_db),
        contract_svc: ContributionContractService = Depends(get_contract_service),
    ) -> dict:
       
        db_contribution = db.query(Contribution).filter(Contribution.id == contribution_id).first()
        if not db_contribution:
            raise HTTPException(status_code=404, detail="Contribution not found")

        if db_contribution.status == ContributionStatus.completed:
            raise HTTPException(status_code=400, detail="Contribution is already paid")

        # Fetch group contract address and member wallet from DB
        group = db.query(Group).filter(Group.id == db_contribution.group_id).first()
        if not group or not group.contract_address:
            raise HTTPException(status_code=400, detail="Group has no deployed contract address")

        member = db.query(GroupMember).filter(GroupMember.id == db_contribution.member_id).first()
        if not member or not member.wallet_address:
            raise HTTPException(status_code=400, detail="Member has no wallet address on record")

        return contract_svc.build_contribute_tx(
            group_contract_address=group.contract_address,
            member_wallet=member.wallet_address,
            contribution_amount_wei=int(db_contribution.amount),
            is_token_based=group.is_token_based,
        )

    def confirm_contribution(
        self,
        contribution_id: UUID,
        tx_hash: str = Body(..., embed=True),
        db: Session = Depends(get_db),
        contract_svc: ContributionContractService = Depends(get_contract_service),
    ) -> ContributionResponse:
       
        db_contribution = db.query(Contribution).filter(Contribution.id == contribution_id).first()
        if not db_contribution:
            raise HTTPException(status_code=404, detail="Contribution not found")

        if db_contribution.status == ContributionStatus.completed:
            raise HTTPException(status_code=400, detail="Contribution is already paid")

        group = db.query(Group).filter(Group.id == db_contribution.group_id).first()
        member = db.query(GroupMember).filter(GroupMember.id == db_contribution.member_id).first()

        if not group or not group.contract_address:
            raise HTTPException(status_code=400, detail="Group has no deployed contract address")
        if not member or not member.wallet_address:
            raise HTTPException(status_code=400, detail="Member has no wallet address on record")

        # Block until receipt is confirmed and timestamp is written on-chain
        verified = contract_svc.verify_contribution_on_chain(
            group_contract_address=group.contract_address,
            member_wallet=member.wallet_address,
            expected_period=contract_svc.get_current_period(group.contract_address),
            tx_hash=tx_hash,
        )

        # Sync off-chain record
        db_contribution.paid_date = datetime.utcfromtimestamp(verified["contribution_timestamp"])
        db_contribution.status = ContributionStatus.completed
        db_contribution.transaction_hash = verified["tx_hash"]
        db.commit()
        db.refresh(db_contribution)
        return ContributionResponse.model_validate(db_contribution)

    # Fine payment flow  (on-chain)
   

    def build_pay_fine_tx(
        self,
        contribution_id: UUID,
        db: Session = Depends(get_db),
        contract_svc: ContributionContractService = Depends(get_contract_service),
    ) -> dict:
       
        db_contribution = db.query(Contribution).filter(Contribution.id == contribution_id).first()
        if not db_contribution:
            raise HTTPException(status_code=404, detail="Contribution not found")

        group = db.query(Group).filter(Group.id == db_contribution.group_id).first()
        member = db.query(GroupMember).filter(GroupMember.id == db_contribution.member_id).first()

        if not group or not group.contract_address:
            raise HTTPException(status_code=400, detail="Group has no deployed contract address")
        if not member or not member.wallet_address:
            raise HTTPException(status_code=400, detail="Member has no wallet address on record")

        return contract_svc.build_pay_fine_tx(
            group_contract_address=group.contract_address,
            member_wallet=member.wallet_address,
            is_token_based=group.is_token_based,
        )

    
    # Group routes
   
    def get_group_contributions(
        self,
        group_id: UUID,
        db: Session = Depends(get_db),
        skip: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=100),
        status: Optional[ContributionStatus] = None,
        sort_by: str = Query("due_date", pattern="^(due_date|amount|created_at)$"),
        sort_order: str = Query("asc", pattern="^(asc|desc)$"),
    ) -> List[ContributionResponse]:
        """Get all contributions for a specific group."""
        query = db.query(Contribution).filter(Contribution.group_id == group_id)

        if status:
            query = query.filter(Contribution.status == status)

        order_func = asc if sort_order == "asc" else desc
        sort_map = {"amount": Contribution.amount, "created_at": Contribution.created_at}
        query = query.order_by(order_func(sort_map.get(sort_by, Contribution.due_date)))

        return [ContributionResponse.model_validate(c) for c in query.offset(skip).limit(limit).all()]

    def get_group_contribution_summary(
        self, group_id: UUID, db: Session = Depends(get_db)
    ) -> dict:
        """Get off-chain contribution summary for a group."""
        total_contributions = db.query(func.count(Contribution.id)).filter(
            Contribution.group_id == group_id
        ).scalar()

        total_expected = db.query(func.sum(Contribution.amount)).filter(
            Contribution.group_id == group_id
        ).scalar() or 0

        total_paid = db.query(func.sum(Contribution.amount)).filter(
            and_(Contribution.group_id == group_id, Contribution.status == ContributionStatus.completed)
        ).scalar() or 0

        pending_count = db.query(func.count(Contribution.id)).filter(
            and_(Contribution.group_id == group_id, Contribution.status == ContributionStatus.pending)
        ).scalar()

        overdue_count = db.query(func.count(Contribution.id)).filter(
            and_(Contribution.group_id == group_id, Contribution.status == ContributionStatus.overdue)
        ).scalar()

        return {
            "group_id": group_id,
            "total_contributions": total_contributions,
            "total_expected": total_expected,
            "total_paid": total_paid,
            "total_pending": total_expected - total_paid,
            "pending_count": pending_count,
            "overdue_count": overdue_count,
            "completion_rate": (total_paid / total_expected * 100) if total_expected > 0 else 0,
        }

    def get_group_on_chain_summary(
        self,
        group_id: UUID,
        db: Session = Depends(get_db),
        contract_svc: ContributionContractService = Depends(get_contract_service),
    ) -> dict:
        
        group = db.query(Group).filter(Group.id == group_id).first()
        if not group or not group.contract_address:
            raise HTTPException(status_code=400, detail="Group has no deployed contract address")

        return contract_svc.get_group_on_chain_summary(group.contract_address)

    def process_rotation_payout(
        self,
        group_id: UUID,
        db: Session = Depends(get_db),
        contract_svc: ContributionContractService = Depends(get_contract_service),
    ) -> dict:
       
        group = db.query(Group).filter(Group.id == group_id).first()
        if not group or not group.contract_address:
            raise HTTPException(status_code=400, detail="Group has no deployed contract address")

        tx_hash = contract_svc.process_rotation_payout(group.contract_address)
        return {"tx_hash": tx_hash, "group_id": group_id}

    def batch_check_missed_contributions(
        self,
        group_id: UUID,
        db: Session = Depends(get_db),
        contract_svc: ContributionContractService = Depends(get_contract_service),
    ) -> dict:
       
        group = db.query(Group).filter(Group.id == group_id).first()
        if not group or not group.contract_address:
            raise HTTPException(status_code=400, detail="Group has no deployed contract address")

        # Fetch all active member wallets for this group
        members = db.query(GroupMember).filter(
            GroupMember.group_id == group_id,
            GroupMember.is_active == True,
        ).all()

        wallets = [m.wallet_address for m in members if m.wallet_address]
        if not wallets:
            raise HTTPException(status_code=400, detail="No active members with wallet addresses found")

        tx_hash = contract_svc.batch_check_missed_contributions(group.contract_address, wallets)
        return {"tx_hash": tx_hash, "group_id": group_id, "members_checked": len(wallets)}

    def set_payout_queue(
        self,
        group_id: UUID,
        ordered_wallets: List[str] = Body(..., embed=True),
        db: Session = Depends(get_db),
        contract_svc: ContributionContractService = Depends(get_contract_service),
    ) -> dict:
        
        group = db.query(Group).filter(Group.id == group_id).first()
        if not group or not group.contract_address:
            raise HTTPException(status_code=400, detail="Group has no deployed contract address")

        tx_hash = contract_svc.set_payout_queue(group.contract_address, ordered_wallets)
        return {"tx_hash": tx_hash, "group_id": group_id}

    # User routes
    

    def get_user_contributions(
        self,
        user_id: UUID,
        db: Session = Depends(get_db),
        skip: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=100),
        status: Optional[ContributionStatus] = None,
        group_id: Optional[UUID] = None,
    ) -> List[ContributionResponse]:
   
        query = db.query(Contribution).join(GroupMember).filter(GroupMember.user_id == user_id)

        if status:
            query = query.filter(Contribution.status == status)
        if group_id:
            query = query.filter(Contribution.group_id == group_id)

        contributions = query.order_by(desc(Contribution.due_date)).offset(skip).limit(limit).all()
        return [ContributionResponse.model_validate(c) for c in contributions]

    def get_user_overdue_contributions(
        self, user_id: UUID, db: Session = Depends(get_db)
    ) -> List[ContributionResponse]:
        
        contributions = db.query(Contribution).join(GroupMember).filter(
            and_(
                GroupMember.user_id == user_id,
                Contribution.status == ContributionStatus.overdue,
            )
        ).order_by(asc(Contribution.due_date)).all()

        return [ContributionResponse.model_validate(c) for c in contributions]

 
    # Member on-chain state


    def get_member_on_chain_status(
        self,
        member_wallet: str,
        group_contract_address: str = Query(..., description="On-chain group contract address"),
        period: Optional[int] = Query(None, description="Period index — defaults to current"),
        contract_svc: ContributionContractService = Depends(get_contract_service),
    ) -> dict:
       
        return contract_svc.sync_contribution_status(
            group_contract_address=group_contract_address,
            member_wallet=member_wallet,
            period=period,
        )

    def reset_last_checked_period(
        self,
        member_wallet: str,
        period: int = Body(..., embed=True),
        group_contract_address: str = Body(..., embed=True),
        contract_svc: ContributionContractService = Depends(get_contract_service),
    ) -> dict:
       
        tx_hash = contract_svc.reset_last_checked_period(
            group_contract_address=group_contract_address,
            member_wallet=member_wallet,
            period=period,
        )
        return {"tx_hash": tx_hash, "member_wallet": member_wallet, "reset_to_period": period}


# Create router instance
contribution_routes = ContributionRoutes()
router = contribution_routes.router