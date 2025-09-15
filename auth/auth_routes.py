import os
from datetime import timedelta
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Request, Response, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from database import get_db
from auth.auth_service import auth_service

# Environment variables
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

# Security
security = HTTPBearer(auto_error=False)

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

class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None

class LogoutResponse(BaseModel):
    message: str

class UserProfile(BaseModel):
    user_id: str
    email: str
    display_name: Optional[str] = None
    phone_number: Optional[str] = None

class OAuthUrlResponse(BaseModel):
    url: str
    state: Optional[str] = None

class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordUpdateRequest(BaseModel):
    token: str  # Supabase token from reset email
    new_password: str = Field(..., min_length=8, max_length=100)

class EmailVerificationRequest(BaseModel):
    token: str  # Supabase token from verification email

class OAuthTokenResponse(BaseModel):
    provider: str
    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[str] = None
    is_expired: bool

class AuthRoutes:
    def __init__(self):
        self.router = APIRouter(prefix="/auth", tags=["authentication"])
        self.auth_service = auth_service
        self._register_routes()
    
    def _register_routes(self):
        """Register all authentication-related routes"""
        # Regular auth routes
        self.router.add_api_route("/register", self.register, methods=["POST"], response_model=AuthResponse)
        self.router.add_api_route("/login", self.login, methods=["POST"], response_model=AuthResponse)
        self.router.add_api_route("/refresh", self.refresh_token, methods=["POST"], response_model=AuthResponse)
        self.router.add_api_route("/logout", self.logout, methods=["POST"], response_model=LogoutResponse)
        self.router.add_api_route("/me", self.get_current_user_profile, methods=["GET"], response_model=UserProfile)
        self.router.add_api_route("/verify-token", self.verify_token_endpoint, methods=["POST"])
        
        # OAuth routes
        self.router.add_api_route("/oauth/google", self.google_oauth_url, methods=["GET"], response_model=OAuthUrlResponse)
        self.router.add_api_route("/oauth/github", self.github_oauth_url, methods=["GET"], response_model=OAuthUrlResponse)
        self.router.add_api_route("/oauth/callback", self.oauth_callback, methods=["GET"])
        self.router.add_api_route("/oauth/exchange", self.oauth_token_exchange, methods=["POST"], response_model=AuthResponse)
        
        # Account management routes (uses Supabase)
        self.router.add_api_route("/reset-password", self.reset_password, methods=["POST"])
        self.router.add_api_route("/update-password", self.update_password, methods=["POST"])
        self.router.add_api_route("/verify-email", self.verify_email, methods=["POST"])
        
        # OAuth token management routes
        self.router.add_api_route("/oauth-tokens/{provider}", self.get_oauth_token, methods=["GET"], response_model=OAuthTokenResponse)
        self.router.add_api_route("/oauth-tokens/{provider}", self.revoke_oauth_token, methods=["DELETE"])
    
    def _create_auth_response(self, user_data: Dict[str, Any], response: Response, db: Session) -> AuthResponse:
        """Helper method to create auth response with tokens"""
        user_id = user_data["user_id"]
        email = user_data["email"]
        display_name = user_data.get("display_name") or (user_data.get("profile") and user_data["profile"].display_name)
        
        # Create our own JWT tokens
        access_token = self.auth_service.create_access_token(
            data={"sub": user_id, "email": email},
            expires_delta=timedelta(hours=24)
        )
        refresh_token = self.auth_service.create_refresh_token(
            data={"sub": user_id, "email": email}
        )
        print(f"Generated access token: {access_token[:50]}...")
        print(f"Generated refresh token: {refresh_token[:50]}...")
        # Store refresh token in database
        self.auth_service.store_refresh_token(user_id, refresh_token, db)
        
        # Set HTTP-only cookies
        self.auth_service.set_auth_cookies(response, access_token, refresh_token)
        print("Cookies set successfully")
        print("=== END CREATE AUTH RESPONSE DEBUG ===")
        return AuthResponse(
            user_id=user_id,
            email=email,
            display_name=display_name,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=900  # 15 minutes
        )
    
    # Regular auth route handlers
    def register(self, user_data: UserRegister, response: Response, db: Session = Depends(get_db)) -> AuthResponse:
        """Register a new user with Supabase Auth and create profile"""
        # Register user with Supabase (one-time validation)
        result = self.auth_service.register_user(
            email=user_data.email,
            password=user_data.password,
            display_name=user_data.display_name,
            phone_number=user_data.phone_number,
            db=db
        )
        
        # Create our own tokens and return them
        return self._create_auth_response(result, response, db)

    def login(self, user_data: UserLogin, response: Response, db: Session = Depends(get_db)) -> AuthResponse:
        """Login user with Supabase Auth and return our own tokens"""
        try:
            print(f"=== LOGIN DEBUG ===")
            print(f"Login attempt for email: {user_data.email}")
            print(f"Environment: {os.getenv('ENV', 'development')}")
            
            # Authenticate with Supabase (one-time validation)
            result = self.auth_service.login_user(
                email=user_data.email,
                password=user_data.password,
                db=db
            )
            
            print(f"Supabase authentication successful for user: {result.get('user_id')}")
            
            # Create our own tokens and return them
            auth_response = self._create_auth_response(result, response, db)
            
            print(f"Auth response created successfully")
            print("=== END LOGIN DEBUG ===")
            
            return auth_response
            
        except HTTPException as e:
            print(f"HTTPException during login: {e.detail}")
            print("=== END LOGIN DEBUG ===")
            raise
        except Exception as e:
            print(f"Unexpected error during login: {str(e)}")
            import traceback
            print(f"Traceback: {traceback.format_exc()}")
            print("=== END LOGIN DEBUG ===")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Login failed: {str(e)}"
            )

    def refresh_token(self, token_data: TokenRefresh, response: Response, db: Session = Depends(get_db)) -> AuthResponse:
        """Refresh access token using our own refresh token stored in database"""
        # Validate refresh token against our database (no Supabase call)
        result = self.auth_service.refresh_user_token(
            refresh_token=token_data.refresh_token,
            db=db
        )
        
        # Create new tokens
        return self._create_auth_response(result, response, db)

    def logout(self, logout_data: LogoutRequest, response: Response, db: Session = Depends(get_db)) -> LogoutResponse:
        """Logout user by clearing cookies and revoking refresh token"""
        # Revoke refresh token if provided
        if logout_data.refresh_token:
            self.auth_service.revoke_refresh_token(logout_data.refresh_token, db)
        
        # Clear cookies
        response.delete_cookie("access_token")
        response.delete_cookie("refresh_token")
        
        return LogoutResponse(message="Successfully logged out")

    def get_current_user_profile(
        self,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
        db: Session = Depends(get_db)
    ) -> UserProfile:
        """Get current authenticated user profile using our own tokens"""
        
        # Debug logging
        print("=== GET CURRENT USER PROFILE DEBUG ===")
        print(f"Cookies: {dict(request.cookies)}")
        print(f"Credentials: {credentials.credentials if credentials else 'None'}")
        
        access_token = request.cookies.get("access_token")

        # Fall back to Authorization header (for API clients)
        if not access_token and credentials:
            access_token = credentials.credentials
        
        print(f"Final access_token: {access_token[:50] if access_token else 'None'}...")
        
        if not access_token:
            raise HTTPException(
                status_code=401, 
                detail="Not authenticated - no token found"
            )
        
        try:
            # FIX: Call the method with correct parameters
            # Instead of: user_data = self.auth_service.get_current_user(access_token, db)
            # Use a helper method that takes just the token:
            user_data = self.auth_service.get_current_user_from_token(access_token, db)
            
            profile = user_data["profile"]
            
            print(f"User data retrieved successfully for user: {user_data.get('email')}")
            print("=== END DEBUG ===")
            
            return UserProfile(
                user_id=user_data["user_id"],
                email=user_data["email"],
                display_name=profile.display_name,
                phone_number=profile.phone_number
            )
            
        except Exception as e:
            print(f"Error in get_current_user_profile: {str(e)}")
            print(f"Error type: {type(e)}")
            import traceback
            print(f"Full traceback: {traceback.format_exc()}")
            print("=== END DEBUG ===")
            raise

    def verify_token_endpoint(
        self,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False))
    ):
        """Verify if the provided token is valid (our own JWT)"""
        token = self.auth_service.get_token_from_cookie_or_header(request, credentials)
        payload = self.auth_service.verify_token(token)
        
        return {
            "valid": True,
            "user_id": payload.get("sub"),
            "email": payload.get("email"),
            "expires_at": payload.get("exp")
    }
    # OAuth route handlers
    def google_oauth_url(self, request: Request) -> OAuthUrlResponse:
        """Generate Google OAuth URL via Supabase"""
        result = self.auth_service.generate_oauth_url("google", request)
        return OAuthUrlResponse(url=result["url"], state=result["state"])

    def github_oauth_url(self, request: Request) -> OAuthUrlResponse:
        """Generate GitHub OAuth URL via Supabase"""
        result = self.auth_service.generate_oauth_url("github", request)
        return OAuthUrlResponse(url=result["url"], state=result["state"])

    def oauth_token_exchange(
        self,
        request: Request,
        response: Response,
        db: Session = Depends(get_db),
        credentials: HTTPAuthorizationCredentials = Depends(security)
    ) -> AuthResponse:
        """Exchange Supabase OAuth token for app tokens"""
        try:
            supabase_token = credentials.credentials
            
            print("=== TOKEN EXCHANGE DEBUG ===")
            print(f"Received Supabase token: {supabase_token[:50]}...")
            
            # Validate Supabase token and get user info
            user_data = self.auth_service.validate_supabase_token(supabase_token)
            print(f"User data from Supabase: {user_data}")
            
            # Create or update profile
            profile = self.auth_service.create_or_update_profile(user_data, db)
            print(f"Profile created/updated: {profile}")
            
            # Create response with your app's tokens
            auth_response = self._create_auth_response({
                "user_id": user_data["user_id"],
                "email": user_data["email"],
                "profile": profile
            }, response, db)
            
            print(f"Auth response created successfully")
            print("=== END TOKEN EXCHANGE DEBUG ===")
            
            return auth_response
            
        except Exception as e:
            print(f"Token exchange error: {str(e)}")
            print("=== END TOKEN EXCHANGE DEBUG ===")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Token exchange failed: {str(e)}"
            )

    # You can remove or simplify the oauth_callback method since it won't be used
    # But keep it for debugging purposes:
    def oauth_callback(
        self,
        request: Request,
        response: Response,
        code: Optional[str] = None,
        state: Optional[str] = None,
        error: Optional[str] = None,
        provider: Optional[str] = None,
        db: Session = Depends(get_db)
    ):
        """Debug endpoint - OAuth flow now goes through frontend"""
        return RedirectResponse(
            url=f"{FRONTEND_URL}/login?error=deprecated_endpoint",
            status_code=status.HTTP_302_FOUND
        )

    # Account management routes (still use Supabase for these operations)
    def reset_password(self, reset_data: PasswordResetRequest):
        """Send password reset email via Supabase"""
        return self.auth_service.reset_password(reset_data.email)

    def update_password(self, update_data: PasswordUpdateRequest):
        """Update password via Supabase token"""
        return self.auth_service.update_password(
            supabase_token=update_data.token,
            new_password=update_data.new_password
        )

    def verify_email(self, verify_data: EmailVerificationRequest):
        """Verify email via Supabase token"""
        return self.auth_service.verify_email(verify_data.token)

    # OAuth token management routes
    def get_oauth_token(
        self,
        provider: str,
        request: Request,
        db: Session = Depends(get_db),
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False))
    ) -> OAuthTokenResponse:
        """Get stored OAuth provider token for API calls"""
        # Uses our own JWT tokens to authenticate
        token = self.auth_service.get_token_from_cookie_or_header(request, credentials)
        user_data = self.auth_service.get_current_user_from_token(token, db)
        user_id = user_data["user_id"]
        
        token_data = self.auth_service.get_oauth_token(user_id, provider, db)
        
        if not token_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No {provider} token found for user"
            )
        
        return OAuthTokenResponse(
            provider=provider,
            access_token=token_data["access_token"],
            refresh_token=token_data["refresh_token"],
            expires_at=token_data["expires_at"].isoformat() if token_data["expires_at"] else None,
            is_expired=token_data["is_expired"]
        )
    
    def revoke_oauth_token(
        self,
        provider: str,
        request: Request,
        db: Session = Depends(get_db),
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False))
    ):
        """Remove OAuth provider token"""
        # Uses our own JWT tokens to authenticate
        token = self.auth_service.get_token_from_cookie_or_header(request, credentials)
        user_data = self.auth_service.get_current_user_from_token(token, db)
        user_id = user_data["user_id"]
        
        self.auth_service.revoke_oauth_token(user_id, provider, db)
        
        return {"message": f"{provider} token revoked successfully"}


# Create router instance
auth_routes = AuthRoutes()
router = auth_routes.router

# Export dependency functions for use in other routes
def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False))
) -> Dict[str, Any]:
    """Dependency function to get current authenticated user using our own JWT tokens"""
    token = auth_service.get_token_from_cookie_or_header(request, credentials)
    return auth_service.get_current_user_from_token(token, db)

def get_current_user_optional(
    request: Request,
    db: Session = Depends(get_db),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False))
) -> Optional[Dict[str, Any]]:
    """Dependency function to get current user if authenticated, otherwise None"""
    try:
        token = auth_service.get_token_from_cookie_or_header(request, credentials)
        if token:
            return auth_service.get_current_user_from_token(token, db)
        return None
    except:
        return None
