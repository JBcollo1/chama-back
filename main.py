from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from contextlib import asynccontextmanager
import uvicorn
import logging
import os

# Import database and models
from database import engine, Base
from models import *  # Import all models to ensure they're registered

# Import routes
from routes.groups import router as groups_router
from routes.contributions import router as contributions_router
from auth.auth_routes import router as auth_router
import socket

socket.setdefaulttimeout(30)
socket.has_ipv6 = False

# Configure logging  
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def check_environment_variables():
    """Check if all required environment variables are set"""
    required_vars = [
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY", 
        "SUPABASE_SERVICE_ROLE_KEY",
        "SECRET_KEY",
    ]
    
    missing_vars = []
    for var in required_vars:
        value = os.getenv(var)
        if not value:
            missing_vars.append(var)
        else:
            # Show partial value for debugging (don't expose full secrets)
            if "KEY" in var or "SECRET" in var:
                logger.info(f"{var}: {value[:10]}...{value[-4:] if len(value) > 14 else ''}")
            else:
                logger.info(f"{var}: {value}")
    
    if missing_vars:
        raise ValueError(f"Missing required environment variables: {missing_vars}")
    
    logger.info("All required environment variables are set")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    logger.info("Starting up FastAPI application...")
    try:
        # Check environment variables
        check_environment_variables()
        
        # Create tables if they don't exist
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully")
        logger.info(f"Available tables: {list(Base.metadata.tables.keys())}")
        
        # Log environment info
        env = os.getenv("ENV", "development")
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
        logger.info(f"Environment: {env}")
        logger.info(f"Frontend URL: {frontend_url}")
        
    except Exception as e:
        logger.error(f"Startup error: {e}")
        raise
    
    yield
    
    # Shutdown
    logger.info("Shutting down FastAPI application...")

# Create FastAPI app
app = FastAPI(
    title="Supabase FastAPI Backend",
    description="A FastAPI backend that replicates Supabase table structure using SQLAlchemy",
    version="1.0.0",
    lifespan=lifespan
)

# Environment-aware CORS configuration
ENV = os.getenv("ENV", "development")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

if ENV == "production":
    # Production CORS - be specific about origins
    allowed_origins = [
        "https://chama3.netlify.app",
        FRONTEND_URL,
        # Add any additional production domains here
    ]
    logger.info(f"Production CORS origins: {allowed_origins}")
else:
    # Development CORS - more permissive
    allowed_origins = [
        "http://localhost:3000",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://localhost:5173",  # Vite default
        "https://chama3.netlify.app",  # Allow Netlify in dev for testing
    ]
    logger.info(f"Development CORS origins: {allowed_origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,  # Essential for cookies
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=[
        "Accept",
        "Accept-Language",
        "Content-Language",
        "Content-Type",
        "Authorization",
        "X-Requested-With",
        "Origin",
        "User-Agent",
        "DNT",
        "Cache-Control",
        "X-Mx-ReqToken",
        "Keep-Alive",
        "X-CSRF-Token",
    ],
    expose_headers=["*"],
)

# Add request/response logging middleware for debugging
@app.middleware("http")
async def log_requests(request, call_next):
    """Log all requests for debugging"""
    if ENV == "production":  # Only log in production for debugging
        logger.info(f"Request: {request.method} {request.url}")
        logger.info(f"Headers: {dict(request.headers)}")
        logger.info(f"Cookies: {dict(request.cookies)}")
    
    response = await call_next(request)
    
    if ENV == "production" and response.status_code >= 400:
        logger.error(f"Error Response: {response.status_code}")
    
    return response

# Global exception handler
@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request, exc):
    logger.error(f"Database error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Database error occurred"}
    )

@app.exception_handler(ValueError)
async def value_error_handler(request, exc):
    logger.error(f"Value error: {exc}")
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc)}
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    logger.error(f"HTTP Exception: {exc.status_code} - {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy", 
        "message": "FastAPI backend is running",
        "environment": ENV,
        "timestamp": "2025-01-19T12:00:00Z"  # You can use actual timestamp
    }

# Root endpoint
@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Welcome to the Supabase FastAPI Backend",
        "version": "1.0.0",
        "environment": ENV,
        "docs": "/docs",
        "redoc": "/redoc"
    }

# Debug endpoint for production troubleshooting
@app.get("/debug/env")
async def debug_environment():
    """Debug endpoint to check environment (remove in production after fixing)"""
    if ENV != "production":
        return {
            "environment": ENV,
            "frontend_url": FRONTEND_URL,
            "supabase_url": os.getenv("SUPABASE_URL"),
            "has_secret_key": bool(os.getenv("SECRET_KEY")),
            "has_supabase_keys": bool(os.getenv("SUPABASE_ANON_KEY")) and bool(os.getenv("SUPABASE_SERVICE_ROLE_KEY")),
        }
    else:
        return {"message": "Debug endpoint disabled in production"}

# Register routers
app.include_router(groups_router, prefix="/api/v1")
app.include_router(contributions_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")

# Test auth endpoint for debugging
@app.get("/api/v1/test-auth")
async def test_auth():
    """Test endpoint to verify auth setup"""
    try:
        from auth.auth_service import auth_service
        # Test creating a token
        test_token = auth_service.create_access_token({"sub": "test", "email": "test@example.com"})
        return {
            "message": "Auth service is working",
            "token_created": bool(test_token),
            "environment": ENV
        }
    except Exception as e:
        logger.error(f"Auth test failed: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Auth setup error: {str(e)}"}
        )

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )