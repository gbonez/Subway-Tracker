"""Fetch the Metrograph schedule and merge stored Letterboxd table data."""

from models import SessionLocal, init_db
from services.movie_service import build_schedule_payload, store_schedule_payload


def main():
    init_db()
    db = SessionLocal()
    try:
        payload = build_schedule_payload(db)
        stored_payload = store_schedule_payload(db, payload)
        print(f"✅ Stored {len(stored_payload['films'])} films in the Metrograph schedule snapshot")
    finally:
        db.close()


if __name__ == "__main__":
    main()
