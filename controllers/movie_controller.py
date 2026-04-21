"""Movie route controllers — Metrograph schedule and Letterboxd sync."""

from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from models import get_db
from services.movie_service import build_schedule_payload, get_stored_schedule_payload, store_schedule_payload, update_letterboxd_table


async def get_schedule(db: Session = Depends(get_db)):
    """Return the prepared Metrograph schedule snapshot from the database."""
    payload = get_stored_schedule_payload(db)
    if payload is None:
        raise HTTPException(status_code=404, detail="Schedule not yet generated. Run the scraper first.")
    return JSONResponse(content=payload)


async def run_scraper(db: Session = Depends(get_db)):
    """Fetch Metrograph, merge stored Letterboxd data, store the payload, and return it."""
    try:
        payload = build_schedule_payload(db)
        payload = store_schedule_payload(db, payload)
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Metrograph scrape failed: {error}") from error

    return JSONResponse(content=payload)


async def run_letterboxd_scan(db: Session = Depends(get_db)):
    """Scan Letterboxd data for the current Metrograph slate and store it in the database."""
    try:
        payload = update_letterboxd_table(db)
        schedule_payload = build_schedule_payload(db)
        stored_schedule = store_schedule_payload(db, schedule_payload)
        payload["schedule_updated_at"] = stored_schedule.get("updated_at")
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Letterboxd scan failed: {error}") from error

    return JSONResponse(content=payload)
