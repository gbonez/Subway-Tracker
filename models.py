"""
Database models and configuration for the NYC Subway Tracker
"""
import os
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, relationship, sessionmaker

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


def init_db():
    Base.metadata.create_all(bind=engine)

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


class MovieUser(Base):
    __tablename__ = "movie_users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, nullable=False, unique=True, index=True)
    letterboxd_username = Column(String, nullable=False, index=True)
    phone_number = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    movies = relationship(
        "MovieLetterboxdData",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    friends = relationship(
        "MovieUserFriend",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class MovieUserFriend(Base):
    __tablename__ = "movie_user_friends"
    __table_args__ = (
        UniqueConstraint("user_id", "friend_username", name="uq_movie_user_friend_username"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("movie_users.id", ondelete="CASCADE"), nullable=False, index=True)
    friend_username = Column(String, nullable=False)
    friend_display_name = Column(String, nullable=True)
    profile_url = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    user = relationship("MovieUser", back_populates="friends")


class MovieLetterboxdData(Base):
    __tablename__ = "movie_letterboxd_data"
    __table_args__ = (
        UniqueConstraint("user_id", "normalized_title", "year", name="uq_movie_letterboxd_user_title_year"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("movie_users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String, nullable=False)
    normalized_title = Column(String, nullable=False, index=True)
    year = Column(Integer, nullable=True, index=True)
    letterboxd_rating = Column(Float, nullable=True)
    on_watchlist = Column(Boolean, default=False, nullable=False)
    watched = Column(Boolean, default=False, nullable=False)
    personal_rating = Column(Float, nullable=True)
    last_scanned_at = Column(DateTime, nullable=True)

    user = relationship("MovieUser", back_populates="movies")
    friend_ratings = relationship(
        "MovieFriendRating",
        back_populates="movie",
        cascade="all, delete-orphan",
    )


class MovieFriendRating(Base):
    __tablename__ = "movie_friend_ratings"
    __table_args__ = (
        UniqueConstraint("movie_id", "friend_username", name="uq_movie_friend_username"),
    )

    id = Column(Integer, primary_key=True, index=True)
    movie_id = Column(Integer, ForeignKey("movie_letterboxd_data.id", ondelete="CASCADE"), nullable=False, index=True)
    friend_username = Column(String, nullable=False)
    friend_display_name = Column(String, nullable=True)
    rating = Column(Float, nullable=True)

    movie = relationship("MovieLetterboxdData", back_populates="friend_ratings")


class MovieScheduleSnapshot(Base):
    __tablename__ = "movie_schedule_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    snapshot_key = Column(String, nullable=False, unique=True, index=True)
    payload = Column(JSON, nullable=False)
    updated_at = Column(DateTime, nullable=False)
