"""Scan Letterboxd data for the current Metrograph slate, store it in the database, and refresh the cached schedule."""

from models import SessionLocal, init_db
from services.movie_service import build_schedule_payload, update_letterboxd_table, write_schedule_payload


def main():
    init_db()
    db = SessionLocal()
    try:
        result = update_letterboxd_table(db)
        schedule_payload = build_schedule_payload(db)
        write_schedule_payload(schedule_payload)
        print(result["message"])
    finally:
        db.close()


if __name__ == "__main__":
    main()