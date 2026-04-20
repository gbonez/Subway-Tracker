"""
Movie route controllers — Metrograph schedule
"""
import json
import os
import subprocess
import sys

from fastapi import HTTPException
from fastapi.responses import JSONResponse

SCHEDULE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "metrograph_schedule.json")


async def get_schedule():
    """Return the cached Metrograph schedule JSON."""
    path = os.path.abspath(SCHEDULE_PATH)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Schedule not yet generated. Run the scraper first.")
    with open(path, "r") as f:
        data = json.load(f)
    return JSONResponse(content=data)


async def run_scraper():
    """Trigger the Metrograph scraper synchronously and return the result."""
    scraper = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "scripts", "scrape_metrograph.py")
    )
    if not os.path.exists(scraper):
        raise HTTPException(status_code=500, detail="Scraper script not found.")

    result = subprocess.run(
        [sys.executable, scraper],
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Scraper failed: {result.stderr or result.stdout}",
        )

    # Return refreshed schedule
    path = os.path.abspath(SCHEDULE_PATH)
    if not os.path.exists(path):
        raise HTTPException(status_code=500, detail="Scraper ran but produced no output file.")
    with open(path, "r") as f:
        data = json.load(f)
    return JSONResponse(content=data)
