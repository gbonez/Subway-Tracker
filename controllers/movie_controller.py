"""Movie route controllers — Metrograph schedule and Letterboxd sync."""

from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models import get_db
from services.movie_service import (
    build_schedule_payload,
    create_movie_user,
    get_movie_user,
    get_or_create_default_movie_user,
    get_schedule_payload_for_user,
    run_movie_refresh_pipeline,
    store_schedule_payload,
    update_movie_user_letterboxd_username,
)


class MovieUserSetupRequest(BaseModel):
    username: str
    letterboxd_username: str
    phone_number: str


class MovieUserLoginRequest(BaseModel):
    username: str


class MovieUserLetterboxdUpdateRequest(BaseModel):
    letterboxd_username: str


async def get_schedule(db: Session = Depends(get_db)):
    """Return the default movie user's schedule payload."""
    user = get_or_create_default_movie_user(db)
    payload = get_schedule_payload_for_user(db, user)
    return JSONResponse(content=payload)


async def get_user_schedule(username: str, db: Session = Depends(get_db)):
    """Return the prepared movie schedule for a specific website user."""
    user = get_movie_user(db, username)
    if user is None:
        raise HTTPException(status_code=404, detail="No user with that username exists. Sign up first.")

    payload = get_schedule_payload_for_user(db, user)
    return JSONResponse(content=payload)


async def get_user_profile(username: str, db: Session = Depends(get_db)):
    """Return a movie user's profile details."""
    user = get_movie_user(db, username)
    if user is None:
        raise HTTPException(status_code=404, detail="No user with that username exists. Sign up first.")

    return {
        "username": user.username,
        "letterboxd_username": user.letterboxd_username,
        "phone_number": user.phone_number,
    }


async def run_scraper(db: Session = Depends(get_db)):
    """Fetch Metrograph, store the base payload, and return the default user's merged view."""
    try:
        user = get_or_create_default_movie_user(db)
        payload = build_schedule_payload(db)
        store_schedule_payload(db, payload)
        payload = get_schedule_payload_for_user(db, user)
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Metrograph scrape failed: {error}") from error

    return JSONResponse(content=payload)


async def run_letterboxd_scan(db: Session = Depends(get_db)):
    """Scan Letterboxd data for the default movie user and store it in the database."""
    try:
        payload = run_movie_refresh_pipeline(db, get_or_create_default_movie_user(db))
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Letterboxd scan failed: {error}") from error

    return JSONResponse(content=payload)


async def setup_movie_user(request: MovieUserSetupRequest, db: Session = Depends(get_db)):
    """Create a movie user, populate their Letterboxd data, and return their profile."""
    try:
        user = create_movie_user(db, request.username, request.letterboxd_username, request.phone_number)
        payload = run_movie_refresh_pipeline(db, user)
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Movie user setup failed: {error}") from error

    payload["redirect_path"] = f"/movies/{user.username}"
    return JSONResponse(content=payload)


async def login_movie_user(request: MovieUserLoginRequest, db: Session = Depends(get_db)):
    """Look up a movie user by their website username."""
    user = get_movie_user(db, request.username)
    if user is None:
        raise HTTPException(status_code=404, detail="No user with that username exists. Sign up first.")

    return {
        "username": user.username,
        "letterboxd_username": user.letterboxd_username,
        "phone_number": user.phone_number,
        "redirect_path": f"/movies/{user.username}",
    }


async def sync_movie_user(username: str, db: Session = Depends(get_db)):
    """Run the Letterboxd refresh flow for a specific movie user."""
    user = get_movie_user(db, username)
    if user is None:
        raise HTTPException(status_code=404, detail="No user with that username exists. Sign up first.")

    try:
        payload = run_movie_refresh_pipeline(db, user)
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Letterboxd sync failed: {error}") from error

    return JSONResponse(content=payload)


async def update_movie_user_letterboxd(username: str, request: MovieUserLetterboxdUpdateRequest, db: Session = Depends(get_db)):
    """Update a user's Letterboxd username and immediately refresh their movie data."""
    try:
        user = update_movie_user_letterboxd_username(db, username, request.letterboxd_username)
        payload = run_movie_refresh_pipeline(db, user)
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Letterboxd update failed: {error}") from error

    return JSONResponse(content=payload)
