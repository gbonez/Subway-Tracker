"""Scan Letterboxd data for the current Metrograph slate and refresh the DB-backed schedule snapshot."""

from models import SessionLocal, init_db
from services.movie_service import build_schedule_payload, store_schedule_payload, update_letterboxd_table


def main():
    init_db()
    db = SessionLocal()
    try:
        result = update_letterboxd_table(db)
        schedule_payload = build_schedule_payload(db)
        store_schedule_payload(db, schedule_payload)
        print(result["message"])
    finally:
        db.close()


if __name__ == "__main__":
    main()