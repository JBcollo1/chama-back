from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc, asc
from typing import List, Optional
from uuid import UUID
from database import get_db
from models import Group, GroupMember, GroupAdmin, Profile, MemberStatus, GroupStatus
from schemas import (
    GroupCreate, GroupUpdate, GroupResponse, GroupWithDetails,
    GroupMemberCreate, GroupMemberUpdate, GroupMemberResponse,
    GroupAdminCreate, GroupAdminResponse
)

class GroupRoutes:
    def __init__(self):
        self.router = APIRouter(prefix="/groups", tags=["groups"])
        self._register_routes()
    
    def _register_routes(self):
        """Register all group-related routes"""
        self.router.add_api_route("/", self.create_group, methods=["POST"], response_model=GroupResponse)
        self.router.add_api_route("/", self.get_groups, methods=["GET"], response_model=List[GroupResponse])
        self.router.add_api_route("/{group_id}", self.get_group, methods=["GET"], response_model=GroupWithDetails)
        self.router.add_api_route("/{group_id}", self.update_group, methods=["PUT"], response_model=GroupResponse)
        self.router.add_api_route("/{group_id}", self.delete_group, methods=["DELETE"])
        
        # Member management
        self.router.add_api_route("/{group_id}/members", self.add_member, methods=["POST"], response_model=GroupMemberResponse)
        self.router.add_api_route("/{group_id}/members", self.get_group_members, methods=["GET"], response_model=List[GroupMemberResponse])
        self.router.add_api_route("/{group_id}/members/{member_id}", self.update_member, methods=["PUT"], response_model=GroupMemberResponse)
        self.router.add_api_route("/{group_id}/members/{member_id}", self.remove_member, methods=["DELETE"])
        
        # Admin management
        self.router.add_api_route("/{group_id}/admins", self.add_admin, methods=["POST"], response_model=GroupAdminResponse)
        self.router.add_api_route("/{group_id}/admins", self.get_group_admins, methods=["GET"], response_model=List[GroupAdminResponse])
        self.router.add_api_route("/{group_id}/admins/{admin_id}", self.remove_admin, methods=["DELETE"])
        
        # User-specific routes
        self.router.add_api_route("/user/{user_id}", self.get_user_groups, methods=["GET"], response_model=List[GroupResponse])
    
    def create_group(self, group_data: GroupCreate, db: Session = Depends(get_db)) -> GroupResponse:
        """Create a new group"""
        # Verify creator exists
        creator = db.query(Profile).filter(Profile.user_id == group_data.created_by).first()
        if not creator:
            raise HTTPException(status_code=404, detail="Creator profile not found")
        
        # Create group
        db_group = Group(**group_data.model_dump())
        db.add(db_group)
        db.commit()
        db.refresh(db_group)
        
        # Add creator as admin
        admin_data = GroupAdminCreate(
            group_id=db_group.id,
            user_id=group_data.created_by
        )
        db_admin = GroupAdmin(**admin_data.model_dump())
        db.add(db_admin)
        
        # Add creator as member
        member_data = GroupMemberCreate(
            group_id=db_group.id,
            user_id=group_data.created_by
        )
        db_member = GroupMember(**member_data.model_dump(), status=MemberStatus.active)
        db.add(db_member)
        
        db.commit()
        
        return GroupResponse.model_validate(db_group)
    
    def get_groups(
        self,
        db: Session = Depends(get_db),
        skip: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=100),
        status: Optional[GroupStatus] = None,
        search: Optional[str] = None,
        sort_by: str = Query("created_at", regex="^(created_at|name|start_date|contribution_amount)$"),
        sort_order: str = Query("desc", regex="^(asc|desc)$")
    ) -> List[GroupResponse]:
        """Get all groups with filtering and pagination"""
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
        
        # Add member count to each group
        group_responses = []
        for group in groups:
            member_count = db.query(GroupMember).filter(
                GroupMember.group_id == group.id,
                GroupMember.status == MemberStatus.active
            ).count()
            
            group_data = GroupResponse.model_validate(group)
            group_data.member_count = member_count
            group_responses.append(group_data)
        
        return group_responses
    
    def get_group(self, group_id: UUID, db: Session = Depends(get_db)) -> GroupWithDetails:
        """Get a specific group with full details"""
        group = db.query(Group).options(
            joinedload(Group.members).joinedload(GroupMember.user),
            joinedload(Group.admins).joinedload(GroupAdmin.user)
        ).filter(Group.id == group_id).first()
        
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        return GroupWithDetails.model_validate(group)
    
    def update_group(self, group_id: UUID, group_data: GroupUpdate, db: Session = Depends(get_db)) -> GroupResponse:
        """Update a group"""
        db_group = db.query(Group).filter(Group.id == group_id).first()
        if not db_group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        # Update fields
        update_data = group_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_group, field, value)
        
        db.commit()
        db.refresh(db_group)
        
        return GroupResponse.model_validate(db_group)
    
    def delete_group(self, group_id: UUID, db: Session = Depends(get_db)):
        """Delete a group"""
        db_group = db.query(Group).filter(Group.id == group_id).first()
        if not db_group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        db.delete(db_group)
        db.commit()
        
        return {"message": "Group deleted successfully"}
    
    def add_member(self, group_id: UUID, member_data: GroupMemberCreate, db: Session = Depends(get_db)) -> GroupMemberResponse:
        """Add a member to a group"""
        # Verify group exists
        group = db.query(Group).filter(Group.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        # Verify user exists
        user = db.query(Profile).filter(Profile.user_id == member_data.user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Check if user is already a member
        existing_member = db.query(GroupMember).filter(
            GroupMember.group_id == group_id,
            GroupMember.user_id == member_data.user_id
        ).first()
        if existing_member:
            raise HTTPException(status_code=400, detail="User is already a member of this group")
        
        # Check group capacity
        active_members = db.query(GroupMember).filter(
            GroupMember.group_id == group_id,
            GroupMember.status == MemberStatus.active
        ).count()
        if active_members >= group.max_members:
            raise HTTPException(status_code=400, detail="Group is at maximum capacity")
        
        # Create member
        db_member = GroupMember(
            group_id=group_id,
            user_id=member_data.user_id,
            status=MemberStatus.pending
        )
        db.add(db_member)
        db.commit()
        db.refresh(db_member)
        
        return GroupMemberResponse.model_validate(db_member)
    
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

# Create router instance
group_routes = GroupRoutes()
router = group_routes.router