"""
Database models and configuration for the NYC Subway Tracker
"""
import os
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, create_engine, inspect, text
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

DEFAULT_MOVIE_USERNAME = os.getenv("DEFAULT_MOVIE_USERNAME", "grayson")
DEFAULT_MOVIE_LETTERBOXD_USERNAME = os.getenv("DEFAULT_MOVIE_LETTERBOXD_USERNAME") or os.getenv("LETTERBOXD_USERNAME", "gbonez100")


def _normalize_default_phone_number(raw_value: str | None) -> str:
    digits = "".join(char for char in (raw_value or "") if char.isdigit())
    if not digits:
        return ""
    if len(digits) == 10:
        digits = f"1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return ""


def _ensure_default_movie_user_row(connection) -> int:
    existing_user_id = connection.execute(
        text("SELECT id FROM movie_users WHERE username = :username LIMIT 1"),
        {"username": DEFAULT_MOVIE_USERNAME},
    ).scalar()
    if existing_user_id is not None:
        return int(existing_user_id)

    now = datetime.now(timezone.utc)
    phone_number = _normalize_default_phone_number(os.getenv("DEFAULT_MOVIE_PHONE_NUMBER") or os.getenv("MY_PHONE_NUMBER"))
    connection.execute(
        text(
            """
            INSERT INTO movie_users (username, letterboxd_username, phone_number, created_at, updated_at)
            VALUES (:username, :letterboxd_username, :phone_number, :created_at, :updated_at)
            """
        ),
        {
            "username": DEFAULT_MOVIE_USERNAME,
            "letterboxd_username": DEFAULT_MOVIE_LETTERBOXD_USERNAME,
            "phone_number": phone_number,
            "created_at": now,
            "updated_at": now,
        },
    )
    inserted_user_id = connection.execute(
        text("SELECT id FROM movie_users WHERE username = :username LIMIT 1"),
        {"username": DEFAULT_MOVIE_USERNAME},
    ).scalar()
    return int(inserted_user_id)


def _run_movie_schema_migrations() -> None:
    with engine.begin() as connection:
        inspector = inspect(connection)
        table_names = set(inspector.get_table_names())

        if "movie_users" in table_names and connection.dialect.name == "postgresql":
            movie_user_columns = {column["name"]: column for column in inspector.get_columns("movie_users")}
            phone_number_column = movie_user_columns.get("phone_number")
            if phone_number_column and not phone_number_column.get("nullable", True):
                connection.execute(text("ALTER TABLE movie_users ALTER COLUMN phone_number DROP NOT NULL"))

            if "sync_in_progress" not in movie_user_columns:
                connection.execute(text("ALTER TABLE movie_users ADD COLUMN sync_in_progress BOOLEAN DEFAULT FALSE"))
                connection.execute(text("UPDATE movie_users SET sync_in_progress = FALSE WHERE sync_in_progress IS NULL"))
                connection.execute(text("ALTER TABLE movie_users ALTER COLUMN sync_in_progress SET NOT NULL"))

            if "friend_sync_pending" not in movie_user_columns:
                connection.execute(text("ALTER TABLE movie_users ADD COLUMN friend_sync_pending BOOLEAN DEFAULT FALSE"))
                connection.execute(text("UPDATE movie_users SET friend_sync_pending = FALSE WHERE friend_sync_pending IS NULL"))
                connection.execute(text("ALTER TABLE movie_users ALTER COLUMN friend_sync_pending SET NOT NULL"))

        if "movie_letterboxd_data" in table_names:
            movie_columns = {column["name"]: column for column in inspector.get_columns("movie_letterboxd_data")}
            if "user_id" not in movie_columns:
                default_user_id = _ensure_default_movie_user_row(connection)
                connection.execute(text("ALTER TABLE movie_letterboxd_data ADD COLUMN user_id INTEGER"))
                connection.execute(
                    text("UPDATE movie_letterboxd_data SET user_id = :user_id WHERE user_id IS NULL"),
                    {"user_id": default_user_id},
                )
                if connection.dialect.name == "postgresql":
                    connection.execute(text("ALTER TABLE movie_letterboxd_data ALTER COLUMN user_id SET NOT NULL"))

            if connection.dialect.name == "postgresql":
                refreshed_inspector = inspect(connection)
                unique_constraints = {
                    constraint["name"]
                    for constraint in refreshed_inspector.get_unique_constraints("movie_letterboxd_data")
                    if constraint.get("name")
                }
                if "uq_movie_letterboxd_title_year" in unique_constraints:
                    connection.execute(text("ALTER TABLE movie_letterboxd_data DROP CONSTRAINT uq_movie_letterboxd_title_year"))
                    unique_constraints.remove("uq_movie_letterboxd_title_year")
                if "uq_movie_letterboxd_user_title_year" not in unique_constraints:
                    connection.execute(
                        text(
                            "ALTER TABLE movie_letterboxd_data ADD CONSTRAINT uq_movie_letterboxd_user_title_year UNIQUE (user_id, normalized_title, year)"
                        )
                    )


def init_db():
    Base.metadata.create_all(bind=engine)
    _run_movie_schema_migrations()

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
    phone_number = Column(String, nullable=True)
    sync_in_progress = Column(Boolean, nullable=False, default=False)
    friend_sync_pending = Column(Boolean, nullable=False, default=False)
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


class MovieMemberFilmCache(Base):
    __tablename__ = "movie_member_film_cache"
    __table_args__ = (
        UniqueConstraint("member_username", "normalized_title", "year", name="uq_movie_member_film_cache"),
    )

    id = Column(Integer, primary_key=True, index=True)
    member_username = Column(String, nullable=False, index=True)
    normalized_title = Column(String, nullable=False, index=True)
    year = Column(Integer, nullable=True, index=True)
    watched = Column(Boolean, default=False, nullable=False)
    personal_rating = Column(Float, nullable=True)
    last_scanned_at = Column(DateTime, nullable=True)


class MovieScheduleSnapshot(Base):
    __tablename__ = "movie_schedule_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    snapshot_key = Column(String, nullable=False, unique=True, index=True)
    payload = Column(JSON, nullable=False)
    updated_at = Column(DateTime, nullable=False)
