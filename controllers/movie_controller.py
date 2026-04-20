"""
Movie route controllers — Metrograph schedule and Letterboxd sync
"""
import json
import os

from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from models import get_db
from services.movie_service import SCHEDULE_PATH, build_schedule_payload, update_letterboxd_table, write_schedule_payload


async def get_schedule():
    """Return the cached Metrograph schedule JSON."""
    path = os.path.abspath(SCHEDULE_PATH)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Schedule not yet generated. Run the scraper first.")
    with open(path, "r") as f:
        data = json.load(f)
    return JSONResponse(content=data)


async def run_scraper(db: Session = Depends(get_db)):
    """Fetch Metrograph, merge stored Letterboxd data, cache the payload, and return it."""
    try:
        payload = build_schedule_payload(db)
        write_schedule_payload(payload)
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Metrograph scrape failed: {error}") from error

    return JSONResponse(content=payload)


async def run_letterboxd_scan(db: Session = Depends(get_db)):
    """Scan Letterboxd data for the current Metrograph slate and store it in the database."""
    try:
        payload = update_letterboxd_table(db)
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Letterboxd scan failed: {error}") from error

    return JSONResponse(content=payload)
