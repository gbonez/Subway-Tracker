"""Scan Letterboxd data for the current Metrograph slate and refresh the DB-backed schedule snapshot."""

from models import SessionLocal, init_db
from services.movie_service import run_movie_refresh_pipeline


def main():
    init_db()
    db = SessionLocal()
    try:
        result = run_movie_refresh_pipeline(db)
        print(result["message"])
    finally:
        db.close()


if __name__ == "__main__":
    main()