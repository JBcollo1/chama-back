import os
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, cast, Literal
from fastapi import HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from supabase._sync.client import create_client, SyncClient
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

# Ensure required Supabase environment variables are set
if not SUPABASE_URL or not SUPABASE_ANON_KEY or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("SUPABASE_URL, SUPABASE_ANON_KEY, and SUPABASE_SERVICE_ROLE_KEY must be set in environment variables.")

# Initialize Supabase clients
supabase: SyncClient = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
supabase_admin: SyncClient = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth provider types
OAuthProvider = Literal['google', 'github']


class AuthService:
    """Service class for authentication operations"""
    
    def __init__(self):
        self.supabase = supabase
        self.supabase_admin = supabase_admin
        self.pwd_context = pwd_context
    
    # Token utilities
    def create_access_token(self, data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(hours=24)
        
        to_encode.update({
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "access"
        })
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm="HS256")
        return encoded_jwt

    def create_refresh_token(self, data: Dict[str, Any]) -> str:
        to_encode = data.copy()
        expire = datetime.utcnow() + timedelta(days=30)
        jti = str(uuid.uuid4())  # Unique token ID for database storage
        
        to_encode.update({
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "refresh",
            "jti": jti
        })
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
    
    def validate_supabase_token(self, supabase_token: str) -> Dict[str, Any]:
        """Validate Supabase token and extract user info - only used during login/signup"""
        try:
            # Get user info from Supabase using the token
            user_response = self.supabase.auth.get_user(supabase_token)
            
            if not user_response or not hasattr(user_response, 'user') or not user_response.user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid Supabase token"
                )
            
            return {
                "user_id": user_response.user.id,
                "email": user_response.user.email,
                "user_metadata": user_response.user.user_metadata or {}
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Token validation failed: {str(e)}"
            )
    
    def store_refresh_token(self, user_id: str, refresh_token: str, db: Session):
        """Store refresh token in database"""
        from models import RefreshToken
        
        # Decode token to get JTI and expiration
        payload = self.verify_token(refresh_token)
        jti = payload.get("jti")
        exp_timestamp = payload.get("exp")
        if exp_timestamp is None:
            raise ValueError("Token missing expiration timestamp")
        expires_at = datetime.fromtimestamp(exp_timestamp)
        
        # Revoke existing tokens for this user
        db.query(RefreshToken).filter(
            RefreshToken.user_id == uuid.UUID(user_id),
            RefreshToken.is_revoked == False
        ).update({"is_revoked": True})
        
        # Store new refresh token
        token_record = RefreshToken(
            jti=jti,
            user_id=uuid.UUID(user_id),
            token_hash=self.pwd_context.hash(refresh_token),  # Store hashed token
            expires_at=expires_at,
            created_at=datetime.utcnow(),
            is_revoked=False
        )
        
        db.add(token_record)
        db.commit()
    
    def validate_refresh_token(self, refresh_token: str, db: Session) -> bool:
        """Validate refresh token against database"""
        from models import RefreshToken
        
        try:
            payload = self.verify_token(refresh_token)
            jti = payload.get("jti")
            
            if not jti:
                return False
            
            # Find token in database
            token_record = db.query(RefreshToken).filter(
                RefreshToken.jti == jti,
                RefreshToken.is_revoked == False,
                RefreshToken.expires_at > datetime.utcnow()
            ).first()
            
            if not token_record:
                return False
            
            # Verify token hash
            return self.pwd_context.verify(refresh_token, cast(str, token_record.token_hash))
            
        except Exception:
            return False
    
    def revoke_refresh_token(self, refresh_token: str, db: Session):
        """Revoke a refresh token"""
        from models import RefreshToken
        
        try:
            payload = self.verify_token(refresh_token)
            jti = payload.get("jti")
            
            if jti:
                db.query(RefreshToken).filter(
                    RefreshToken.jti == jti
                ).update({"is_revoked": True})
                db.commit()
        except Exception:
            pass  # Token might be invalid, that's ok for logout
    
    # Cookie utilities
    def set_auth_cookies(self, response: Response, access_token: str, refresh_token: str):
        """Set HTTP-only cookies for tokens"""
        # Only use secure cookies in production (HTTPS)
        is_prod = os.getenv("ENV") == "production"
        
        print(f"=== COOKIE DEBUG ===")
        print(f"Environment: {'production' if is_prod else 'development'}")
        print(f"Secure: {is_prod}")
        print(f"SameSite: {'none' if is_prod else 'lax'}")
        
        cookie_settings = {
            "httponly": True,
            "secure": is_prod,  
            "samesite": "None" if is_prod else "Lax",
            "max_age": 2592000,  # 30 days
            "path": "/",
            # REMOVE ALL DOMAIN LOGIC - Don't set domain for cross-origin
        }
        
        response.set_cookie(
            key="access_token",
            value=access_token,
            **cookie_settings
        )
        
        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            **cookie_settings
        )
        
        print("=== END COOKIE DEBUG ===")
    def get_token_from_cookie_or_header(self, request: Request, credentials: Optional[HTTPAuthorizationCredentials] = None) -> str:
        """Get token from cookie or Authorization header"""
        token = None
        
        print("=== TOKEN EXTRACTION DEBUG ===")
        print(f"Request cookies: {dict(request.cookies)}")
        print(f"Authorization credentials: {credentials.credentials if credentials else 'None'}")
        
        # IMPORTANT: Check cookies FIRST since that's your primary auth method
        token = request.cookies.get("access_token")
        print(f"Token from cookie: {token[:50] if token else 'None'}...")
        
        # Only fall back to header if no cookie token
        if not token and credentials:
            token = credentials.credentials
            print(f"Token from header: {token[:50] if token else 'None'}...")
        
        print("=== END TOKEN EXTRACTION DEBUG ===")
        
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No token provided - check cookies and Authorization header",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        return token

    
    # User profile utilities
    def create_or_update_profile(self, user_data: Dict[str, Any], db: Session) -> Any:
        """Create or update user profile"""
        from models import Profile
        
        user_id = uuid.UUID(user_data["user_id"])
        user_metadata = user_data.get("user_metadata", {})
        
        # Check if profile exists
        profile = db.query(Profile).filter(Profile.user_id == user_id).first()
        
        if profile:
            # Update existing profile
            if user_metadata.get("full_name"):
                profile.display_name = user_metadata["full_name"]
            elif user_metadata.get("name"):
                profile.display_name = user_metadata["name"]
        else:
            # Create new profile
            display_name = user_metadata.get("full_name") or user_metadata.get("name")
            
            profile = Profile(
                user_id=user_id,
                display_name=display_name,
                phone_number=None  # OAuth doesn't typically provide phone
            )
            db.add(profile)
        
        db.commit()
        db.refresh(profile)
        return profile
    
    # Authentication operations
    def register_user(self, email: str, password: str, display_name: Optional[str] = None, phone_number: Optional[str] = None, *, db: Session) -> Dict[str, Any]:
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
            print(f"=== SUPABASE LOGIN DEBUG ===")
            print(f"Attempting Supabase auth for: {email}")
            print(f"Supabase URL: {SUPABASE_URL}")
            print(f"Using anon key: {SUPABASE_ANON_KEY[:20]}..." if SUPABASE_ANON_KEY else "No anon key")
            
            
            # try:
            #     test_response = self.supabase.table("_supabase_migrations").select("*").limit(1).execute()
            #     print(f"Supabase connection test: {'SUCCESS' if test_response else 'FAILED'}")
            # except Exception as conn_error:
            #     print(f"Supabase connection test FAILED: {str(conn_error)}")
            #     raise HTTPException(
            #         status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            #         detail="Database connection failed"
            #     )
            
            
            print(f"Calling Supabase sign_in_with_password...")
            auth_response = self.supabase.auth.sign_in_with_password({
                "email": email,
                "password": password,
            })
            
            print(f"Supabase auth call completed")
            print(f"Auth response type: {type(auth_response)}")
            print(f"Has user: {hasattr(auth_response, 'user')}")
            print(f"Has session: {hasattr(auth_response, 'session')}")
            
            # Check if we got a proper response
            if not hasattr(auth_response, 'user') or not hasattr(auth_response, 'session'):
                print(f"Invalid auth response structure: {dir(auth_response)}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Authentication service error"
                )
            
            print(f"User: {auth_response.user.id if auth_response.user else 'None'}")
            print(f"Session: {'exists' if auth_response.session else 'None'}")
            
            if auth_response.user is None or auth_response.session is None:
                print("Invalid credentials - user or session is None")
                # Check if there's an error in the response
                error_msg = "Invalid email or password"
                
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=error_msg
                )
            
            user_id = auth_response.user.id
            user_email = auth_response.user.email
            print(f"User ID: {user_id}")
            print(f"User Email: {user_email}")
            
            # Get user profile from database
            from models import Profile
            profile = db.query(Profile).filter(
                Profile.user_id == uuid.UUID(user_id)
            ).first()
            
            print(f"Profile found: {profile.display_name if profile else 'None'}")
            print("=== END SUPABASE LOGIN DEBUG ===")
            
            return {
                "user_id": user_id,
                "email": user_email,
                "profile": profile
            }
            
        except HTTPException:
            print("=== END SUPABASE LOGIN DEBUG ===")
            raise
        except Exception as e:
            print(f"Supabase login error: {str(e)}")
            print(f"Error type: {type(e)}")
            print(f"Error args: {getattr(e, 'args', 'No args')}")
            
            # Check for specific Supabase errors
            if hasattr(e, 'message'):
                print(f"Error message: {getattr(e, 'message', 'Unknown')}")
            if hasattr(e, 'details'):
                print(f"Error details: {getattr(e, 'details', 'Unknown')}")
            if hasattr(e, 'code'):
                print(f"Error code: {getattr(e, 'code', 'Unknown')}")
                
            import traceback
            print(f"Traceback: {traceback.format_exc()}")
            print("=== END SUPABASE LOGIN DEBUG ===")
            
            # Provide more specific error message
            error_detail = "Authentication service unavailable"
            if "network" in str(e).lower() or "connection" in str(e).lower():
                error_detail = "Unable to connect to authentication service"
            elif "invalid" in str(e).lower():
                error_detail = "Invalid email or password"
                
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=error_detail
            )



    def refresh_user_token(self, refresh_token: str, db: Session) -> Dict[str, Any]:
        """Refresh access token using refresh token stored in database"""
        # Validate refresh token against database
        if not self.validate_refresh_token(refresh_token, db):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token"
            )
        
        try:
            payload = self.verify_token(refresh_token)
            user_id = payload.get("sub")
            email = payload.get("email")
            
            # Get user profile
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
        *,
        db: Session
    ) -> Dict[str, Any]:
        """Get current authenticated user using our own JWT tokens"""
        
        # Get token from cookie or header
        token = self.get_token_from_cookie_or_header(request, credentials)
        
        # Use the helper method
        return self.get_current_user_from_token(token, db)

    def get_current_user_optional(
        self,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = None,
        *,
        db: Session
    ) -> Optional[Dict[str, Any]]:
        """Get current user if authenticated, otherwise None"""
        try:
            return self.get_current_user(request, credentials, db=db)
        except HTTPException:
            return None
    def get_current_user_from_token(self, token: str, db: Session) -> Dict[str, Any]:
        """Get current authenticated user using JWT token directly"""
        
        print(f"=== GET CURRENT USER FROM TOKEN DEBUG ===")
        print(f"Token received: {token[:50]}...")
        
        try:
            # Verify our own JWT token
            payload = self.verify_token(token)
            print(f"Token payload: {payload}")
            
            # Ensure it's an access token
            if payload.get("type") != "access":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token type",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            user_id = payload.get("sub")
            
            if user_id is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token - no user ID",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            print(f"User ID from token: {user_id}")
            
            # Get user profile from database
            from models import Profile
            profile = db.query(Profile).filter(
                Profile.user_id == uuid.UUID(user_id)
            ).first()
            
            if not profile:
                print(f"No profile found for user_id: {user_id}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User profile not found"
                )
            
            print(f"Profile found: {profile.display_name}")
            print("=== END GET CURRENT USER FROM TOKEN DEBUG ===")
            
            return {
                "user_id": user_id,
                "email": payload.get("email"),
                "profile": profile
            }
            
        except HTTPException:
            print("=== END GET CURRENT USER FROM TOKEN DEBUG ===")
            raise
        except Exception as e:
            print(f"Unexpected error in get_current_user_from_token: {str(e)}")
            print(f"Error type: {type(e)}")
            import traceback
            print(f"Full traceback: {traceback.format_exc()}")
            print("=== END GET CURRENT USER FROM TOKEN DEBUG ===")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Token processing failed: {str(e)}",
                headers={"WWW-Authenticate": "Bearer"},
            )
    # OAuth operations
    def generate_oauth_url(self, provider: str, request: Request) -> Dict[str, Any]:
        """Generate OAuth URL for the specified provider"""
        try:
            # Validate provider is supported
            supported_providers = ['google', 'github']
            if provider not in supported_providers:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unsupported OAuth provider: {provider}. Supported providers: {', '.join(supported_providers)}"
                )
            
            # Generate state parameter for security
            state = str(uuid.uuid4())
            
          
            frontend_callback = f"{FRONTEND_URL}oauth-callback"
            
            print(f"=== OAuth URL Generation Debug ===")
            print(f"Provider: {provider}")
            print(f"Frontend callback: {frontend_callback}")
            
            # Create OAuth URL with Supabase
            response = self.supabase.auth.sign_in_with_oauth({
                "provider": cast(OAuthProvider, provider),
                "options": {
                    "redirect_to": frontend_callback,  # Frontend URL, not API URL
                    "query_params": {
                        "provider": provider  # Add provider to help identify
                    }
                }
            })
            
            print(f"Generated OAuth URL: {response.url}")
            print("=== End Debug ===")
            
            return {
                "url": response.url,
                "state": state
            }
            
        except Exception as e:
            print(f"OAuth URL generation error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to generate {provider} OAuth URL: {str(e)}"
            )

    # Keep the existing handle_oauth_callback method but update it for debugging:
    def handle_oauth_callback(self, code: str, db: Session) -> Dict[str, Any]:
        """Handle OAuth callback and return user data - DEPRECATED"""
        # This method is no longer used in the main flow
        # OAuth now goes through frontend exchange
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth callback should go through frontend exchange flow"
        )

    # Account management operations (still use Supabase)
    def reset_password(self, email: str):
        """Send password reset email via Supabase"""
        try:
            response = self.supabase.auth.reset_password_email(email, {
                "redirect_to": f"{FRONTEND_URL}/reset-password"
            })
            return {"message": "Password reset email sent"}
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to send reset email: {str(e)}"
            )
    
    def update_password(self, supabase_token: str, new_password: str):
        """Update password via Supabase"""
        try:
            # Validate the reset token with Supabase
            user_data = self.validate_supabase_token(supabase_token)
            
            # Update password
            response = self.supabase.auth.update_user({
                "password": new_password
            })
            
            return {"message": "Password updated successfully"}
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update password: {str(e)}"
            )
    
    def verify_email(self, supabase_token: str):
        """Verify email via Supabase"""
        try:
            user_data = self.validate_supabase_token(supabase_token)
            return {"message": "Email verified successfully"}
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Email verification failed: {str(e)}"
            )

    # OAuth token management (updated to use UserOAuthToken)
    def store_oauth_tokens(self, user_id: str, oauth_data: Dict[str, Any], db: Session):
        """Store OAuth provider tokens for API access"""
        from models import UserOAuthToken
        
        provider = oauth_data.get("provider")
        if not provider:
            return
        
        # Remove existing provider token
        db.query(UserOAuthToken).filter(
            UserOAuthToken.user_id == uuid.UUID(user_id),
            UserOAuthToken.provider == provider
        ).delete()
        
        # Calculate expiration (default to 1 hour if not provided)
        expires_at = None
        if oauth_data.get("expires_in"):
            expires_at = datetime.utcnow() + timedelta(seconds=oauth_data["expires_in"])
        else:
            expires_at = datetime.utcnow() + timedelta(hours=1)
        
        # Store new OAuth token
        oauth_token = UserOAuthToken(
            user_id=uuid.UUID(user_id),
            provider=provider,
            access_token=oauth_data.get("access_token"),
            refresh_token=oauth_data.get("refresh_token"),
            expires_at=expires_at,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        db.add(oauth_token)
        db.commit()

    def get_oauth_token(self, user_id: str, provider: str, db: Session) -> Optional[Dict[str, Any]]:
        """Get stored OAuth provider token"""
        from models import UserOAuthToken
        
        token = db.query(UserOAuthToken).filter(
            UserOAuthToken.user_id == uuid.UUID(user_id),
            UserOAuthToken.provider == provider
        ).first()
        
        if not token:
            return None
        
        is_expired = token.expires_at and token.expires_at < datetime.utcnow()
        
        return {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
            "expires_at": token.expires_at,
            "is_expired": is_expired
        }

    def refresh_oauth_token(self, user_id: str, provider: str, db: Session) -> Optional[Dict[str, Any]]:
        """Refresh OAuth token if possible (provider-specific implementation needed)"""
        # This would need provider-specific implementation
        # For now, return None to indicate refresh not available
        return None

    def revoke_oauth_token(self, user_id: str, provider: str, db: Session):
        """Remove OAuth token for a provider"""
        from models import UserOAuthToken
        
        db.query(UserOAuthToken).filter(
            UserOAuthToken.user_id == uuid.UUID(user_id),
            UserOAuthToken.provider == provider
        ).delete()
        db.commit()


# Create a singleton instance
auth_service = AuthService()
