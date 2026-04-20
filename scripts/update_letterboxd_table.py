"""Scan Letterboxd data for the current Metrograph slate and store it in the database."""

from models import SessionLocal, init_db
from services.movie_service import update_letterboxd_table


def main():
    init_db()
    db = SessionLocal()
    try:
        result = update_letterboxd_table(db)
        print(result["message"])
    finally:
        db.close()


if __name__ == "__main__":
    main()