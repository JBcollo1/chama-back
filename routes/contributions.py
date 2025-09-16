from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc, asc, and_
from typing import List, Optional
from uuid import UUID
from datetime import datetime
from database import get_db
from models import Contribution, Group, GroupMember, ContributionStatus
from schemas import (
    ContributionCreate, ContributionUpdate, ContributionResponse
)

class ContributionRoutes:
    def __init__(self):
        self.router = APIRouter(prefix="/contributions", tags=["contributions"])
        self._register_routes()
    
    def _register_routes(self):
        """Register all contribution-related routes"""
        self.router.add_api_route("/", self.create_contribution, methods=["POST"], response_model=ContributionResponse)
        self.router.add_api_route("/", self.get_contributions, methods=["GET"], response_model=List[ContributionResponse])
        self.router.add_api_route("/{contribution_id}", self.get_contribution, methods=["GET"], response_model=ContributionResponse)
        self.router.add_api_route("/{contribution_id}", self.update_contribution, methods=["PUT"], response_model=ContributionResponse)
        self.router.add_api_route("/{contribution_id}", self.delete_contribution, methods=["DELETE"])
        self.router.add_api_route("/{contribution_id}/pay", self.mark_as_paid, methods=["POST"], response_model=ContributionResponse)
        
        # Group-specific routes
        self.router.add_api_route("/group/{group_id}", self.get_group_contributions, methods=["GET"], response_model=List[ContributionResponse])
        self.router.add_api_route("/group/{group_id}/summary", self.get_group_contribution_summary, methods=["GET"])
        
        # User-specific routes
        self.router.add_api_route("/user/{user_id}", self.get_user_contributions, methods=["GET"], response_model=List[ContributionResponse])
        self.router.add_api_route("/user/{user_id}/overdue", self.get_user_overdue_contributions, methods=["GET"], response_model=List[ContributionResponse])
    
    def create_contribution(self, contribution_data: ContributionCreate, db: Session = Depends(get_db)) -> ContributionResponse:
        """Create a new contribution"""
        # Verify group exists
        group = db.query(Group).filter(Group.id == contribution_data.group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        # Verify member exists and is active
        member = db.query(GroupMember).filter(
            GroupMember.id == contribution_data.member_id,
            GroupMember.group_id == contribution_data.group_id
        ).first()
        if not member:
            raise HTTPException(status_code=404, detail="Member not found in this group")
        
        # Create contribution
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
        sort_order: str = Query("asc", pattern="^(asc|desc)$")
    ) -> List[ContributionResponse]:
        """Get all contributions with filtering and pagination"""
        query = db.query(Contribution)
        
        # Apply filters
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
        
        # Apply sorting
        order_func = asc if sort_order == "asc" else desc
        if sort_by == "amount":
            query = query.order_by(order_func(Contribution.amount))
        elif sort_by == "created_at":
            query = query.order_by(order_func(Contribution.created_at))
        elif sort_by == "status":
            query = query.order_by(order_func(Contribution.status))
        else:
            query = query.order_by(order_func(Contribution.due_date))
        
        contributions = query.offset(skip).limit(limit).all()
        return [ContributionResponse.model_validate(contrib) for contrib in contributions]
    
    def get_contribution(self, contribution_id: UUID, db: Session = Depends(get_db)) -> ContributionResponse:
        """Get a specific contribution"""
        contribution = db.query(Contribution).filter(Contribution.id == contribution_id).first()
        if not contribution:
            raise HTTPException(status_code=404, detail="Contribution not found")
        
        return ContributionResponse.model_validate(contribution)
    
    def update_contribution(self, contribution_id: UUID, contribution_data: ContributionUpdate, db: Session = Depends(get_db)) -> ContributionResponse:
        """Update a contribution"""
        db_contribution = db.query(Contribution).filter(Contribution.id == contribution_id).first()
        if not db_contribution:
            raise HTTPException(status_code=404, detail="Contribution not found")
        
        # Update fields
        update_data = contribution_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_contribution, field, value)
        
        # Auto-update status based on payment
        if update_data.get('paid_date'):
            setattr(db_contribution, 'status', ContributionStatus.completed)
        elif update_data.get('due_date') and update_data['due_date'] < datetime.utcnow() and db_contribution.paid_date is None:
            setattr(db_contribution, 'status', ContributionStatus.overdue)
        
        db.commit()
        db.refresh(db_contribution)
        
        return ContributionResponse.model_validate(db_contribution)
    
    def delete_contribution(self, contribution_id: UUID, db: Session = Depends(get_db)):
        """Delete a contribution"""
        db_contribution = db.query(Contribution).filter(Contribution.id == contribution_id).first()
        if not db_contribution:
            raise HTTPException(status_code=404, detail="Contribution not found")
        
        db.delete(db_contribution)
        db.commit()
        
        return {"message": "Contribution deleted successfully"}
    
    def mark_as_paid(self, contribution_id: UUID, transaction_hash: Optional[str] = None, db: Session = Depends(get_db)) -> ContributionResponse:
        """Mark a contribution as paid"""
        db_contribution = db.query(Contribution).filter(Contribution.id == contribution_id).first()
        if not db_contribution:
            raise HTTPException(status_code=404, detail="Contribution not found")
        
        # Check if already paid by querying the current status value
        current_contribution = db.query(Contribution).filter(
            Contribution.id == contribution_id,
            Contribution.status == ContributionStatus.completed
        ).first()
        if current_contribution:
            raise HTTPException(status_code=400, detail="Contribution is already paid")
        
        # Update contribution
        setattr(db_contribution, 'paid_date', datetime.utcnow())
        setattr(db_contribution, 'status', ContributionStatus.completed)
        if transaction_hash:
            setattr(db_contribution, 'transaction_hash', transaction_hash)
        
        db.commit()
        db.refresh(db_contribution)
        
        return ContributionResponse.model_validate(db_contribution)
    
    def get_group_contributions(
        self,
        group_id: UUID,
        db: Session = Depends(get_db),
        skip: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=100),
        status: Optional[ContributionStatus] = None,
        sort_by: str = Query("due_date", pattern="^(due_date|amount|created_at)$"),
        sort_order: str = Query("asc", pattern="^(asc|desc)$")
    ) -> List[ContributionResponse]:
        """Get all contributions for a specific group"""
        query = db.query(Contribution).filter(Contribution.group_id == group_id)
        
        if status:
            query = query.filter(Contribution.status == status)
        
        # Apply sorting
        order_func = asc if sort_order == "asc" else desc
        if sort_by == "amount":
            query = query.order_by(order_func(Contribution.amount))
        elif sort_by == "created_at":
            query = query.order_by(order_func(Contribution.created_at))
        else:
            query = query.order_by(order_func(Contribution.due_date))
        
        contributions = query.offset(skip).limit(limit).all()
        return [ContributionResponse.model_validate(contrib) for contrib in contributions]
    
    def get_group_contribution_summary(self, group_id: UUID, db: Session = Depends(get_db)):
        """Get contribution summary for a group"""
        # Total contributions
        total_contributions = db.query(func.count(Contribution.id)).filter(
            Contribution.group_id == group_id
        ).scalar()
        
        # Total amount expected
        total_expected = db.query(func.sum(Contribution.amount)).filter(
            Contribution.group_id == group_id
        ).scalar() or 0
        
        # Total amount paid
        total_paid = db.query(func.sum(Contribution.amount)).filter(
            and_(
                Contribution.group_id == group_id,
                Contribution.status == ContributionStatus.completed
            )
        ).scalar() or 0
        
        # Pending contributions
        pending_count = db.query(func.count(Contribution.id)).filter(
            and_(
                Contribution.group_id == group_id,
                Contribution.status == ContributionStatus.pending
            )
        ).scalar()
        
        # Overdue contributions
        overdue_count = db.query(func.count(Contribution.id)).filter(
            and_(
                Contribution.group_id == group_id,
                Contribution.status == ContributionStatus.overdue
            )
        ).scalar()
        
        return {
            "group_id": group_id,
            "total_contributions": total_contributions,
            "total_expected": total_expected,
            "total_paid": total_paid,
            "total_pending": total_expected - total_paid,
            "pending_count": pending_count,
            "overdue_count": overdue_count,
            "completion_rate": (total_paid / total_expected * 100) if total_expected > 0 else 0
        }
    
    def get_user_contributions(
        self,
        user_id: UUID,
        db: Session = Depends(get_db),
        skip: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=100),
        status: Optional[ContributionStatus] = None,
        group_id: Optional[UUID] = None
    ) -> List[ContributionResponse]:
        """Get all contributions for a specific user"""
        query = db.query(Contribution).join(GroupMember).filter(
            GroupMember.user_id == user_id
        )
        
        if status:
            query = query.filter(Contribution.status == status)
        if group_id:
            query = query.filter(Contribution.group_id == group_id)
        
        query = query.order_by(desc(Contribution.due_date))
        contributions = query.offset(skip).limit(limit).all()
        return [ContributionResponse.model_validate(contrib) for contrib in contributions]
    
    def get_user_overdue_contributions(self, user_id: UUID, db: Session = Depends(get_db)) -> List[ContributionResponse]:
        """Get all overdue contributions for a specific user"""
        contributions = db.query(Contribution).join(GroupMember).filter(
            and_(
                GroupMember.user_id == user_id,
                Contribution.status == ContributionStatus.overdue
            )
        ).order_by(asc(Contribution.due_date)).all()
        
        return [ContributionResponse.model_validate(contrib) for contrib in contributions]

# Create router instance
contribution_routes = ContributionRoutes()
router = contribution_routes.router