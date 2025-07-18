import os
from datetime import timedelta
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Request, Response, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from database import get_db
from auth_service import auth_service

# Environment variables
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

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

class AuthResponseWithProvider(BaseModel):
    user_id: str
    email: str
    display_name: Optional[str] = None
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    provider: Optional[str] = None
    provider_access_token: Optional[str] = None
    provider_refresh_token: Optional[str] = None

class TokenRefresh(BaseModel):
    refresh_token: str

class LogoutResponse(BaseModel):
    message: str

class UserProfile(BaseModel):
    user_id: str
    email: str
    display_name: Optional[str] = None
    phone_number: Optional[str] = None

class OAuthCallbackData(BaseModel):
    code: str
    state: Optional[str] = None

class OAuthUrlResponse(BaseModel):
    url: str
    state: Optional[str] = None

class AuthRoutes:
    def __init__(self):
        self.router = APIRouter(prefix="/auth", tags=["authentication"])
        self.auth_service = auth_service
        self._register_routes()
    
    def _register_routes(self):
        """Register all authentication-related routes"""
        # Regular auth routes
        self.router.add_api_route("/register", self.register, methods=["POST"], response_model=AuthResponseWithProvider)
        self.router.add_api_route("/login", self.login, methods=["POST"], response_model=AuthResponseWithProvider)
        self.router.add_api_route("/refresh", self.refresh_token, methods=["POST"], response_model=AuthResponseWithProvider)
        self.router.add_api_route("/logout", self.logout, methods=["POST"], response_model=LogoutResponse)
        self.router.add_api_route("/me", self.get_current_user_profile, methods=["GET"], response_model=UserProfile)
        self.router.add_api_route("/verify-token", self.verify_token_endpoint, methods=["POST"])
        
        # OAuth routes
        self.router.add_api_route("/oauth/google", self.google_oauth_url, methods=["GET"], response_model=OAuthUrlResponse)
        self.router.add_api_route("/oauth/github", self.github_oauth_url, methods=["GET"], response_model=OAuthUrlResponse)
        self.router.add_api_route("/oauth/callback", self.oauth_callback, methods=["GET"])
        
        # Add route for getting provider tokens
        self.router.add_api_route("/provider-tokens/{provider}", self.get_provider_token, methods=["GET"])
    
    def _create_auth_response(self, user_data: Dict[str, Any], response: Response) -> AuthResponseWithProvider:
        """Helper method to create auth response with tokens"""
        user_id = user_data["user_id"]
        email = user_data["email"]
        display_name = user_data.get("display_name") or (user_data.get("profile") and user_data["profile"].display_name)
        
        # Create tokens
        access_token = self.auth_service.create_access_token(
            data={"sub": user_id, "email": email},
            expires_delta=timedelta(minutes=15)
        )
        refresh_token = self.auth_service.create_refresh_token(
            data={"sub": user_id, "email": email}
        )
        
        # Set HTTP-only cookies
        self.auth_service.set_auth_cookies(response, access_token, refresh_token)
        
        return  AuthResponseWithProvider(
            user_id=user_id,
            email=email,
            display_name=display_name,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=900  # 15 minutes
        )
    
    # Regular auth route handlers
    def register(self, user_data: UserRegister, response: Response, db: Session = Depends(get_db)) ->  AuthResponseWithProvider:
        """Register a new user with Supabase Auth and create profile"""
        result = self.auth_service.register_user(
            email=user_data.email,
            password=user_data.password,
            display_name=user_data.display_name,
            phone_number=user_data.phone_number,
            db=db
        )
        
        return self._create_auth_response(result, response)

    def login(self, user_data: UserLogin, response: Response, db: Session = Depends(get_db)) ->  AuthResponseWithProvider:
        """Login user with Supabase Auth"""
        result = self.auth_service.login_user(
            email=user_data.email,
            password=user_data.password,
            db=db
        )
        
        return self._create_auth_response(result, response)

    def refresh_token(self, token_data: TokenRefresh, response: Response, db: Session = Depends(get_db)) ->  AuthResponseWithProvider:
        """Refresh access token using refresh token"""
        result = self.auth_service.refresh_user_token(
            refresh_token=token_data.refresh_token,
            db=db
        )
        
        return self._create_auth_response(result, response)

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
        user_data = self.auth_service.get_current_user(request, credentials, db)
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
        """Generate Google OAuth URL"""
        result = self.auth_service.generate_oauth_url("google", request)
        return OAuthUrlResponse(url=result["url"], state=result["state"])

    def github_oauth_url(self, request: Request) -> OAuthUrlResponse:
        """Generate GitHub OAuth URL"""
        result = self.auth_service.generate_oauth_url("github", request)
        return OAuthUrlResponse(url=result["url"], state=result["state"])

    def oauth_callback(
        self,
        request: Request,
        response: Response,
        provider: str,
        code: Optional[str] = None,
        state: Optional[str] = None,
        error: Optional[str] = None,
        db: Session = Depends(get_db)
    ):
        """Handle OAuth callback from Google/GitHub"""
        try:
            # Check for error in callback
            if error:
                return RedirectResponse(
                    url=f"{FRONTEND_URL}/login?error={error}",
                    status_code=status.HTTP_302_FOUND
                )
            
            if not code:
                return RedirectResponse(
                    url=f"{FRONTEND_URL}/login?error=no_code",
                    status_code=status.HTTP_302_FOUND
                )
            
            # Handle OAuth callback
            result = self.auth_service.handle_oauth_callback(code, db)
            
            # Store provider tokens in database for future API calls
            if result.get("provider_access_token"):
                self.auth_service.store_provider_tokens(
                    user_id=result["user_id"],
                    provider_data=result,
                    db=db
                )
            
            # Create our own application tokens
            access_token = self.auth_service.create_access_token(
                data={"sub": result["user_id"], "email": result["email"]},
                expires_delta=timedelta(minutes=15)
            )
            refresh_token = self.auth_service.create_refresh_token(
                data={"sub": result["user_id"], "email": result["email"]}
            )
            
            # Set HTTP-only cookies with our tokens
            self.auth_service.set_auth_cookies(response, access_token, refresh_token)
            
            # Redirect to frontend success page with provider info
            redirect_url = f"{FRONTEND_URL}/login/success"
            if result.get("provider"):
                redirect_url += f"?provider={result['provider']}"
            
            return RedirectResponse(
                url=redirect_url,
                status_code=status.HTTP_302_FOUND
            )
            
        except Exception as e:
            print(f"OAuth callback error: {str(e)}")
            return RedirectResponse(
                url=f"{FRONTEND_URL}/login?error=callback_failed",
                status_code=status.HTTP_302_FOUND
            )

    def get_provider_token(
        self,
        provider: str,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
        db: Session = Depends(get_db)
    ):
        """Get stored provider token for API calls"""
        user_data = self.auth_service.get_current_user(request, credentials, db)
        user_id = user_data["user_id"]
        
        token_data = self.auth_service.get_provider_token(user_id, provider, db)
        
        if not token_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No {provider} token found for user"
            )
        
        return {
            "provider": provider,
            "access_token": token_data["access_token"],
            "expires_at": token_data["expires_at"],
            "is_expired": token_data["is_expired"]
        }


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
    return auth_service.get_current_user(request, credentials, db)

def get_current_user_optional(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> Optional[Dict[str, Any]]:
    """Dependency function to get current user if authenticated, otherwise None"""
    return auth_service.get_current_user_optional(request, credentials, db)