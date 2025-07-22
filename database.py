from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import os
from dotenv import load_dotenv

load_dotenv()

# Supabase connection string format
# postgresql://[username]:[password]@[host]:[port]/[database]?options
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://username:password@db.supabase.co:5432/postgres"
).strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
# Create engine
engine = create_engine(
    DATABASE_URL,
    echo=True,  # Set to False in production
    pool_pre_ping=True,
     connect_args={"options": "-c client_encoding=utf8"},
    pool_recycle=300,
    pool_size=10,
    max_overflow=20
  

)
print("DATABASE_URL =", repr(DATABASE_URL))
# Create SessionLocal class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create Base class
Base = declarative_base()

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()