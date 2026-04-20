"""Fetch the Metrograph schedule and merge stored Letterboxd table data."""

from models import SessionLocal, init_db
from services.movie_service import build_schedule_payload, write_schedule_payload


def main():
    init_db()
    db = SessionLocal()
    try:
        payload = build_schedule_payload(db)
        out_path = write_schedule_payload(payload)
        print(f"✅ Wrote {len(payload['films'])} films to {out_path}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
