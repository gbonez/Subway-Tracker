"""Movie route controllers — Metrograph schedule and Letterboxd sync."""

from fastapi import BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from models import get_db
from services.movie_service import (
    build_schedule_payload,
    clear_movie_user_friend_data,
    create_movie_user,
    finish_sync_job,
    get_sync_job_status,
    get_movie_user,
    get_or_create_default_movie_user,
    get_schedule_payload_for_user,
    initialize_sync_job,
    run_movie_refresh_pipeline,
    run_movie_refresh_pipeline_for_username,
    set_movie_user_sync_state,
    store_schedule_payload,
    update_movie_user_letterboxd_username,
    update_movie_user_phone_number,
)


class MovieUserSetupRequest(BaseModel):
    username: str
    letterboxd_username: str
    phone_number: str = ""


class MovieUserLoginRequest(BaseModel):
    username: str


class MovieUserProfileUpdateRequest(BaseModel):
    letterboxd_username: Optional[str] = None
    phone_number: Optional[str] = None


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
        "sync_in_progress": user.sync_in_progress,
        "friend_sync_pending": user.friend_sync_pending,
    }


async def get_user_setup_status(username: str, db: Session = Depends(get_db)):
    """Return current setup/sync status and accumulated logs for a movie user."""
    user = get_movie_user(db, username)
    if user is None:
        raise HTTPException(status_code=404, detail="No user with that username exists. Sign up first.")

    job_status = get_sync_job_status(username)
    return {
        "username": user.username,
        "sync_in_progress": user.sync_in_progress,
        "friend_sync_pending": user.friend_sync_pending,
        "job": job_status,
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


async def setup_movie_user(request: MovieUserSetupRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Create a movie user, start their initial Letterboxd sync, and return a pollable status response."""
    try:
        user = create_movie_user(db, request.username, request.letterboxd_username, request.phone_number)
        user = set_movie_user_sync_state(db, user, sync_in_progress=True, friend_sync_pending=True)
        initialize_sync_job(user.username, "setup")
        background_tasks.add_task(
            run_movie_refresh_pipeline_for_username,
            user.username,
            send_sms=True,
            sms_mode="setup-welcome",
            progress_logs=True,
            redirect_path=f"/movies/{user.username}?welcome=1",
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Movie user setup failed: {error}") from error

    return JSONResponse(
        status_code=202,
        content={
            "username": user.username,
            "message": "Account created. Initial friend sync started.",
            "status_path": f"/movies/users/{user.username}/setup-status",
        },
    )


async def login_movie_user(request: MovieUserLoginRequest, db: Session = Depends(get_db)):
    """Look up a movie user by their website username."""
    user = get_movie_user(db, request.username)
    if user is None:
        raise HTTPException(status_code=404, detail="No user with that username exists. Sign up first.")

    return {
        "username": user.username,
        "letterboxd_username": user.letterboxd_username,
        "phone_number": user.phone_number,
        "redirect_path": "/movies" if user.username == "grayson" else f"/movies/{user.username}",
    }


async def sync_movie_user(username: str, db: Session = Depends(get_db)):
    """Run the Letterboxd refresh flow for a specific movie user."""
    user = get_movie_user(db, username)
    if user is None:
        raise HTTPException(status_code=404, detail="No user with that username exists. Sign up first.")
    if user.sync_in_progress:
        raise HTTPException(status_code=409, detail="A Letterboxd sync is already running for this user.")

    try:
        payload = run_movie_refresh_pipeline(db, user)
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Letterboxd sync failed: {error}") from error

    return JSONResponse(content=payload)


async def update_movie_user_letterboxd(username: str, request: MovieUserProfileUpdateRequest, db: Session = Depends(get_db)):
    """Update a user's Letterboxd username and/or phone number."""
    try:
        user = get_movie_user(db, username)
        if user is None:
            raise LookupError("User not found.")
        if request.letterboxd_username is not None and user.sync_in_progress:
            raise ValueError("A Letterboxd sync is currently running for this username. Try again after it finishes.")

        payload = None
        details = []

        if request.phone_number is not None:
            user = update_movie_user_phone_number(db, username, request.phone_number)
            details.append("phone number updated")

        if request.letterboxd_username is not None:
            user = update_movie_user_letterboxd_username(db, username, request.letterboxd_username)
            user = clear_movie_user_friend_data(db, username)
            details.append("Letterboxd username updated")
            payload = {
                "user": {
                    "username": user.username,
                    "letterboxd_username": user.letterboxd_username,
                    "phone_number": user.phone_number,
                    "sync_in_progress": user.sync_in_progress,
                    "friend_sync_pending": user.friend_sync_pending,
                },
                "show_friend_sync_popup": True,
                "message": "Letterboxd username updated. Friend data was cleared and will refresh within 24 hours.",
            }

        if payload is None:
            payload = {
                "user": {
                    "username": user.username,
                    "letterboxd_username": user.letterboxd_username,
                    "phone_number": user.phone_number,
                    "sync_in_progress": user.sync_in_progress,
                    "friend_sync_pending": user.friend_sync_pending,
                },
                "message": "Updated profile." if not details else f"Updated profile: {', '.join(details)}.",
            }
        elif details and not payload.get("show_friend_sync_popup"):
            payload["message"] = f"{payload.get('message', 'Updated profile.')} Also {', '.join(details)}."
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
