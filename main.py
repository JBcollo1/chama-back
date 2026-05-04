from dotenv import load_dotenv
load_dotenv()

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
from models import *

# Import routes
from routes.groups import router as groups_router
from routes.contributions import router as contributions_router
from auth.auth_routes import router as auth_router
from web3_files.schedular import build_scheduler   # ← import only, don't call yet

import socket
socket.setdefaulttimeout(30)
socket.has_ipv6 = False

logging.basicConfig(level=logging.INFO)
logging.getLogger("apscheduler").setLevel(logging.DEBUG)  # ← see scheduler errors
logger = logging.getLogger(__name__)


def check_environment_variables():
    required_vars = [
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SECRET_KEY",
    ]
    missing_vars = [v for v in required_vars if not os.getenv(v)]
    if missing_vars:
        raise ValueError(f"Missing required environment variables: {missing_vars}")
    logger.info("All required environment variables are set")


# ── ONE lifespan, scheduler started here ──────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up FastAPI application...")
    try:
        check_environment_variables()
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables ready")

        # ✅ Build AND start scheduler here, inside lifespan
        scheduler = build_scheduler()
        scheduler.start()
        logger.info("✅ Scheduler started — jobs: %s", [j.id for j in scheduler.get_jobs()])

    except Exception as e:
        logger.error(f"Startup error: {e}")
        raise

    yield

    logger.info("Shutting down...")
    scheduler.shutdown(wait=False)
    logger.info("🛑 Scheduler stopped")


app = FastAPI(
    title="Supabase FastAPI Backend",
    version="1.0.0",
    lifespan=lifespan,   # ← only one lifespan
)
@app.post("/debug/force-create-records")
async def force_create_records():
    from database import SessionLocal
    from models import Group, GroupMember, Contribution, ContributionStatus
    from web3_files.schedular import _active_groups, _period_due_date
    from web3_files.initialize import contribution_contract_svc
    import datetime

    db = SessionLocal()
    results = []
    try:
        groups = _active_groups(db)
        results.append(f"Found {len(groups)} active groups")

        for group in groups:
            members = db.query(GroupMember).filter(
                GroupMember.group_id == group.id,
                GroupMember.status == "active",
            ).all()
            results.append(f"Group {group.name}: {len(members)} active members")
            
            for m in members:
                results.append(f"  member {m.id} wallet={m.wallet_address}")

            period = contribution_contract_svc.get_current_period(group.contract_address)
            results.append(f"  on-chain period: {period}")

            for member in members:
                if not member.wallet_address:
                    results.append(f"  SKIP {member.id} — no wallet")
                    continue
                exists = db.query(Contribution).filter(
                    Contribution.group_id == group.id,
                    Contribution.member_id == member.id,
                    Contribution.period == period,
                ).first()
                if exists:
                    results.append(f"  SKIP {member.id} — record exists")
                    continue
                db.add(Contribution(
                    group_id=group.id,
                    member_id=member.id,
                    amount=group.contribution_amount,
                    status=ContributionStatus.pending,
                    due_date=_period_due_date(group, period),
                    period=period,
                ))
                results.append(f"  CREATED record for {member.id}")
        
        db.commit()
        return {"steps": results}
    except Exception as e:
        db.rollback()
        return {"error": str(e), "steps": results}
    finally:
        db.close()
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
        "http://172.17.129.34:8080",
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