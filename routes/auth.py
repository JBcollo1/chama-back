import os
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Request, Response, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field
from supabase import create_client, Client
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from passlib.context import CryptContext
import jwt
from jwt.exceptions import InvalidTokenError
from database import get_db

# Environment variables
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-here")

# Initialize Supabase clients
supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Security
security = HTTPBearer()

# Pydantic models for authentication
class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=100)
    display_name: Optional[str] = Field(None, max_length=100)
    phone_number: Optional[str] = Field(None, max_length=20)

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class AuthResponse(BaseModel):
    user_id: str
    email: str
    display_name: Optional[str] = None
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int

class TokenRefresh(BaseModel):
    refresh_token: str

class LogoutResponse(BaseModel):
    message: str

class UserProfile(BaseModel):
    user_id: str
    email: str
    display_name: Optional[str] = None
    phone_number: Optional[str] = None

class AuthRoutes:
    def __init__(self):
        self.router = APIRouter(prefix="/auth", tags=["authentication"])
        self._register_routes()
    
    def _register_routes(self):
        """Register all authentication-related routes"""
        self.router.add_api_route("/register", self.register, methods=["POST"], response_model=AuthResponse)
        self.router.add_api_route("/login", self.login, methods=["POST"], response_model=AuthResponse)
        self.router.add_api_route("/refresh", self.refresh_token, methods=["POST"], response_model=AuthResponse)
        self.router.add_api_route("/logout", self.logout, methods=["POST"], response_model=LogoutResponse)
        self.router.add_api_route("/me", self.get_current_user_profile, methods=["GET"], response_model=UserProfile)
        self.router.add_api_route("/verify-token", self.verify_token_endpoint, methods=["POST"])
    
    # Helper methods
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        return pwd_context.verify(plain_password, hashed_password)

    def get_password_hash(self, password: str) -> str:
        return pwd_context.hash(password)

    def create_access_token(self, data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=15)
        
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm="HS256")
        return encoded_jwt

    def create_refresh_token(self, data: Dict[str, Any]) -> str:
        to_encode = data.copy()
        expire = datetime.utcnow() + timedelta(days=30)
        to_encode.update({"exp": expire, "type": "refresh"})
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm="HS256")
        return encoded_jwt

    def verify_token(self, token: str) -> Dict[str, Any]:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
            return payload
        except InvalidTokenError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def set_auth_cookies(self, response: Response, access_token: str, refresh_token: str):
        """Set HTTP-only cookies for tokens"""
        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=True,
            secure=True,  # Use HTTPS in production
            samesite="strict",
            max_age=900,  # 15 minutes
        )
        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            httponly=True,
            secure=True,  # Use HTTPS in production
            samesite="strict",
            max_age=2592000,  # 30 days
        )

    def get_token_from_cookie_or_header(self, request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None) -> str:
        """Get token from cookie or Authorization header"""
        token = None
        
        # First, try to get token from Authorization header
        if credentials:
            token = credentials.credentials
        
        # If not found in header, try cookie
        if not token:
            token = request.cookies.get("access_token")
        
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No token provided",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        return token

    # Route handlers
    def register(self, user_data: UserRegister, response: Response, db: Session = Depends(get_db)) -> AuthResponse:
        """Register a new user with Supabase Auth and create profile"""
        try:
            # Create user in Supabase Auth
            auth_response = supabase.auth.sign_up({
                "email": user_data.email,
                "password": user_data.password,
            })
            
            if auth_response.user is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to create user account"
                )
            
            user_id = auth_response.user.id
            
            # Create profile in database
            from models import Profile  # Import your Profile model
            
            profile = Profile(
                user_id=uuid.UUID(user_id),
                display_name=user_data.display_name,
                phone_number=user_data.phone_number,
            )
            
            db.add(profile)
            db.commit()
            db.refresh(profile)
            
            # Create tokens
            access_token = self.create_access_token(
                data={"sub": user_id, "email": user_data.email},
                expires_delta=timedelta(minutes=15)
            )
            refresh_token = self.create_refresh_token(
                data={"sub": user_id, "email": user_data.email}
            )
            
            # Set HTTP-only cookies
            self.set_auth_cookies(response, access_token, refresh_token)
            
            return AuthResponse(
                user_id=user_id,
                email=user_data.email,
                display_name=user_data.display_name,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=900  # 15 minutes
            )
            
        except IntegrityError:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User with this email already exists"
            )
        except Exception as e:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Registration failed: {str(e)}"
            )

    def login(self, user_data: UserLogin, response: Response, db: Session = Depends(get_db)) -> AuthResponse:
        """Login user with Supabase Auth"""
        try:
            # Authenticate with Supabase
            auth_response = supabase.auth.sign_in_with_password({
                "email": user_data.email,
                "password": user_data.password,
            })
            
            if auth_response.user is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid email or password"
                )
            
            user_id = auth_response.user.id
            
            # Get user profile from database
            from models import Profile
            profile = db.query(Profile).filter(
                Profile.user_id == uuid.UUID(user_id)
            ).first()
            
            # Create tokens
            access_token = self.create_access_token(
                data={"sub": user_id, "email": user_data.email},
                expires_delta=timedelta(minutes=15)
            )
            refresh_token = self.create_refresh_token(
                data={"sub": user_id, "email": user_data.email}
            )
            
            # Set HTTP-only cookies
            self.set_auth_cookies(response, access_token, refresh_token)
            
            return AuthResponse(
                user_id=user_id,
                email=user_data.email,
                display_name=profile.display_name if profile else None,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=900  # 15 minutes
            )
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )

    def refresh_token(self, token_data: TokenRefresh, response: Response, db: Session = Depends(get_db)) -> AuthResponse:
        """Refresh access token using refresh token"""
        try:
            payload = self.verify_token(token_data.refresh_token)
            
            if payload.get("type") != "refresh":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid refresh token"
                )
            
            user_id = payload.get("sub")
            email = payload.get("email")
            
            # Get user profile
            from models import Profile
            profile = db.query(Profile).filter(
                Profile.user_id == uuid.UUID(user_id)
            ).first()
            
            # Create new tokens
            new_access_token = self.create_access_token(
                data={"sub": user_id, "email": email},
                expires_delta=timedelta(minutes=15)
            )
            new_refresh_token = self.create_refresh_token(
                data={"sub": user_id, "email": email}
            )
            
            # Set new HTTP-only cookies
            self.set_auth_cookies(response, new_access_token, new_refresh_token)
            
            return AuthResponse(
                user_id=user_id,
                email=email,
                display_name=profile.display_name if profile else None,
                access_token=new_access_token,
                refresh_token=new_refresh_token,
                expires_in=900
            )
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token"
            )

    def logout(self, response: Response) -> LogoutResponse:
        """Logout user by clearing cookies"""
        response.delete_cookie("access_token")
        response.delete_cookie("refresh_token")
        return LogoutResponse(message="Successfully logged out")

    def get_current_user_profile(
        self,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
        db: Session = Depends(get_db)
    ) -> UserProfile:
        """Get current authenticated user profile"""
        user_data = self.get_current_user(request, credentials, db)
        profile = user_data["profile"]
        
        return UserProfile(
            user_id=user_data["user_id"],
            email=user_data["email"],
            display_name=profile.display_name,
            phone_number=profile.phone_number
        )

    def verify_token_endpoint(
        self,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
    ):
        """Verify if the provided token is valid"""
        token = self.get_token_from_cookie_or_header(request, credentials)
        payload = self.verify_token(token)
        
        return {
            "valid": True,
            "user_id": payload.get("sub"),
            "email": payload.get("email"),
            "expires_at": payload.get("exp")
        }

    # Dependency methods (for use in other routes)
    def get_current_user(
        self,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        db: Session = None
    ) -> Dict[str, Any]:
        """Get current authenticated user"""
        
        # Try to get token from cookie first, then header
        token = None
        if not credentials:
            token = request.cookies.get("access_token")
        else:
            token = credentials.credentials
        
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No token provided",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        payload = self.verify_token(token)
        user_id = payload.get("sub")
        
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Get user profile from database
        from models import Profile
        profile = db.query(Profile).filter(
            Profile.user_id == uuid.UUID(user_id)
        ).first()
        
        if not profile:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User profile not found"
            )
        
        return {
            "user_id": user_id,
            "email": payload.get("email"),
            "profile": profile
        }

    def get_current_user_optional(
        self,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        db: Session = None
    ) -> Optional[Dict[str, Any]]:
        """Get current user if authenticated, otherwise None"""
        try:
            return self.get_current_user(request, credentials, db)
        except HTTPException:
            return None

# Create router instance
auth_routes = AuthRoutes()
router = auth_routes.router

# Export dependency functions for use in other routes
def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """Dependency function to get current authenticated user"""
    return auth_routes.get_current_user(request, credentials, db)

def get_current_user_optional(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> Optional[Dict[str, Any]]:
    """Dependency function to get current user if authenticated, otherwise None"""
    return auth_routes.get_current_user_optional(request, credentials, db)