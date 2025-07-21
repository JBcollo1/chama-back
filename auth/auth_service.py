import os
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from fastapi import HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from supabase import create_client, Client
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from passlib.context import CryptContext
import jwt
from jwt.exceptions import InvalidTokenError

# Environment variables
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-here")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

# Initialize Supabase clients
supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthService:
    """Service class for authentication operations"""
    
    def __init__(self):
        self.supabase = supabase
        self.supabase_admin = supabase_admin
        self.pwd_context = pwd_context
    
    # Password utilities
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        return self.pwd_context.verify(plain_password, hashed_password)

    def get_password_hash(self, password: str) -> str:
        return self.pwd_context.hash(password)
    
    # Token utilities
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
    
    # Cookie utilities
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
    
    # User profile utilities
    def create_or_update_profile(self, user_data: Dict[str, Any], db: Session) -> Any:
        """Create or update user profile from OAuth data"""
        from models import Profile
        
        user_id = uuid.UUID(user_data["id"])
        
        # Check if profile exists
        profile = db.query(Profile).filter(Profile.user_id == user_id).first()
        
        if profile:
            # Update existing profile
            if user_data.get("user_metadata", {}).get("full_name"):
                profile.display_name = user_data["user_metadata"]["full_name"]
            elif user_data.get("user_metadata", {}).get("name"):
                profile.display_name = user_data["user_metadata"]["name"]
            
            if user_data.get("phone"):
                profile.phone_number = user_data["phone"]
        else:
            # Create new profile
            display_name = None
            if user_data.get("user_metadata", {}).get("full_name"):
                display_name = user_data["user_metadata"]["full_name"]
            elif user_data.get("user_metadata", {}).get("name"):
                display_name = user_data["user_metadata"]["name"]
            
            profile = Profile(
                user_id=user_id,
                display_name=display_name,
                phone_number=user_data.get("phone")
            )
            db.add(profile)
        
        db.commit()
        db.refresh(profile)
        return profile
    
    # Authentication operations
    def register_user(self, email: str, password: str, display_name: Optional[str] = None, phone_number: Optional[str] = None, db: Session = None) -> Dict[str, Any]:
        """Register a new user with Supabase Auth and create profile"""
        try:
            # Create user in Supabase Auth
            auth_response = self.supabase.auth.sign_up({
                "email": email,
                "password": password,
            })
            
            if auth_response.user is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to create user account"
                )
            
            user_id = auth_response.user.id
            
            # Create profile in database
            from models import Profile
            
            profile = Profile(
                user_id=uuid.UUID(user_id),
                display_name=display_name,
                phone_number=phone_number,
            )
            
            db.add(profile)
            db.commit()
            db.refresh(profile)
            
            return {
                "user_id": user_id,
                "email": email,
                "display_name": display_name,
                "profile": profile
            }
            
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

    def login_user(self, email: str, password: str, db: Session) -> Dict[str, Any]:
        """Login user with Supabase Auth"""
        try:
            # Authenticate with Supabase
            auth_response = self.supabase.auth.sign_in_with_password({
                "email": email,
                "password": password,
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
            
            return {
                "user_id": user_id,
                "email": email,
                "profile": profile
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )

    def refresh_user_token(self, refresh_token: str, db: Session) -> Dict[str, Any]:
        """Refresh access token using refresh token"""
        try:
            payload = self.verify_token(refresh_token)
            
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
            
            return {
                "user_id": user_id,
                "email": email,
                "profile": profile
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token"
            )

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

    # OAuth operations
    def generate_oauth_url(self, provider: str, request: Request) -> Dict[str, Any]:
        """Generate OAuth URL for the specified provider"""
        try:
            # Generate state parameter for security
            state = str(uuid.uuid4())
            
            # Create OAuth URL with Supabase
            response = self.supabase.auth.sign_in_with_oauth({
                "provider": provider,
                "options": {
                    "redirect_to": f"{request.base_url}api/v1/auth/oauth/callback?provider={provider}"
                }
            })
            
            return {
                "url": response.url,
                "state": state
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to generate {provider} OAuth URL: {str(e)}"
            )

    def handle_oauth_callback(self, code: str, db: Session) -> Dict[str, Any]:
        """Handle OAuth callback and return user data"""
        try:
            # Exchange code for session with Supabase
            auth_response = self.supabase.auth.exchange_code_for_session({
                "auth_code": code
            })
            
            if not auth_response.user:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="OAuth authentication failed"
                )
            
            user = auth_response.user
            user_id = user.id
            email = user.email
            
            # Create or update profile
            profile = self.create_or_update_profile(user.model_dump(), db)
            
            return {
                "user_id": user_id,
                "email": email,
                "profile": profile
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"OAuth callback failed: {str(e)}"
            )


# Create a singleton instance
auth_service = AuthService()