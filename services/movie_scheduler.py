"""Background scheduler for daily Metrograph and Letterboxd refreshes."""

import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from models import SessionLocal
from services.movie_service import build_schedule_payload, get_stored_schedule_payload, store_schedule_payload, update_letterboxd_table

SCHEDULER_TIMEZONE = os.getenv("MOVIE_SCHEDULER_TIMEZONE", "America/New_York")
SCHEDULER_ENABLED = os.getenv("ENABLE_MOVIE_SCHEDULER", "true").lower() == "true"

_scheduler: BackgroundScheduler | None = None


def _run_daily_refresh() -> None:
    db = SessionLocal()
    try:
        result = update_letterboxd_table(db)
        payload = build_schedule_payload(db)
        stored_payload = store_schedule_payload(db, payload)
        print(
            "🎬 Daily movie refresh completed: "
            f"{result['updated_movies']} films updated, schedule stored at {stored_payload['updated_at']}",
            flush=True,
        )
    except Exception as error:
        db.rollback()
        print(f"⚠️ Daily movie refresh failed: {error}", flush=True)
    finally:
        db.close()


def ensure_schedule_snapshot() -> None:
    db = SessionLocal()
    try:
        if get_stored_schedule_payload(db) is not None:
            return

        payload = build_schedule_payload(db)
        stored_payload = store_schedule_payload(db, payload)
        print(f"🎬 Created initial Metrograph schedule snapshot at {stored_payload['updated_at']}", flush=True)
    except Exception as error:
        db.rollback()
        print(f"⚠️ Failed to create initial Metrograph schedule snapshot: {error}", flush=True)
    finally:
        db.close()


def start_movie_scheduler() -> None:
    global _scheduler

    if not SCHEDULER_ENABLED:
        print("⏸️ Movie scheduler disabled by ENABLE_MOVIE_SCHEDULER=false", flush=True)
        return

    if _scheduler is not None:
        return

    scheduler = BackgroundScheduler(timezone=SCHEDULER_TIMEZONE)
    scheduler.add_job(
        _run_daily_refresh,
        trigger=CronTrigger(hour=8, minute=0),
        id="daily_movie_refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60 * 60,
    )
    scheduler.start()
    _scheduler = scheduler
    print(f"⏰ Movie scheduler started for daily 8:00 AM runs in {SCHEDULER_TIMEZONE}", flush=True)


def stop_movie_scheduler() -> None:
    global _scheduler

    if _scheduler is None:
        return

    _scheduler.shutdown(wait=False)
    _scheduler = None