"""
Database models and configuration for the NYC Subway Tracker
"""
import os
from sqlalchemy import create_engine, Column, Integer, String, Date, Boolean, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# -------------------------------
# DATABASE CONFIGURATION
# -------------------------------
# Railway provides DATABASE_URL, but we can also construct it from individual vars
DATABASE_URL = os.getenv("DATABASE_URL")

# If DATABASE_URL is not available, construct it from Railway's PostgreSQL environment variables
if not DATABASE_URL:
    PGHOST = os.getenv("PGHOST", "localhost")
    PGPORT = os.getenv("PGPORT", "5432")
    PGUSER = os.getenv("PGUSER", "postgres")
    PGPASSWORD = os.getenv("PGPASSWORD", "")
    PGDATABASE = os.getenv("PGDATABASE", "railway")
    
    if PGPASSWORD:
        DATABASE_URL = f"postgresql://{PGUSER}:{PGPASSWORD}@{PGHOST}:{PGPORT}/{PGDATABASE}"
    else:
        DATABASE_URL = "sqlite:///./rides.db"

# Log which database we're using
if DATABASE_URL.startswith("postgresql"):
    print("🐘 Using PostgreSQL database from Railway")
    engine = create_engine(DATABASE_URL)
elif DATABASE_URL.startswith("sqlite"):
    print("🗄️ Using SQLite database for local development")
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    print(f"🤔 Using unknown database: {DATABASE_URL}")
    engine = create_engine(DATABASE_URL)

Base = declarative_base()
SessionLocal = sessionmaker(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# -------------------------------
# DATABASE MODELS
# -------------------------------
class SubwayRide(Base):
    __tablename__ = "rides"
    
    id = Column(Integer, primary_key=True, index=True)
    ride_number = Column(Integer, nullable=False)
    line = Column(String, nullable=False)
    board_stop = Column(String, nullable=False)
    depart_stop = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    transferred = Column(Boolean, default=False)
