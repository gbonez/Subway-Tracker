"""Movie route controllers — Metrograph schedule, Letterboxd sync, and SMS test tools."""

from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from models import get_db
from services.sms_utils import send_text_message
from services.movie_service import (
    build_movie_calendar_update_message_for_user,
    build_movie_setup_welcome_message,
    build_schedule_payload,
    clear_movie_user_friend_data,
    create_movie_user,
    enqueue_movie_sync_job,
    get_sync_job_status,
    get_movie_user,
    get_or_create_default_movie_user,
    get_schedule_payload_for_user,
    normalize_phone_number,
    run_daily_movie_user_update_cycle,
    run_movie_refresh_pipeline,
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


class MovieTextSendRequest(BaseModel):
    recipient: str
    message_kind: str = "custom"
    message_body: str = ""
    setup_username: str = ""


async def send_movie_test_text():
    """Send a fixed test SMS through Textbelt for manual verification."""
    try:
        result = send_text_message("+15132268634", "Hello")
    except ValueError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Text message send failed: {error}") from error

    return JSONResponse(content=result)


async def send_movie_custom_text(request: MovieTextSendRequest, db: Session = Depends(get_db)):
    """Send a custom or setup-welcome SMS to a saved movie user or a direct phone number."""
    recipient_value = (request.recipient or "").strip()
    if not recipient_value:
        raise HTTPException(status_code=400, detail="Enter a website username or phone number.")

    direct_phone_number = None
    movie_user = get_movie_user(db, recipient_value)

    if movie_user is not None:
        if not movie_user.phone_number:
            raise HTTPException(status_code=400, detail="That user does not have a saved phone number.")
        destination_phone = movie_user.phone_number
        resolved_username = movie_user.username
    else:
        try:
            direct_phone_number = normalize_phone_number(recipient_value)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        destination_phone = direct_phone_number
        resolved_username = ""

    message_kind = (request.message_kind or "custom").strip().lower()
    if message_kind == "setup-welcome":
        setup_username = (request.setup_username or resolved_username or "").strip()
        if not setup_username:
            raise HTTPException(status_code=400, detail="Enter a website username for the setup welcome text.")
        try:
            message_body = build_movie_setup_welcome_message(setup_username)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    elif message_kind == "calendar-update":
        if movie_user is None:
            raise HTTPException(status_code=400, detail="Calendar update texts must target a website username with a saved phone number.")
        try:
            message_body = build_movie_calendar_update_message_for_user(db, movie_user)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    elif message_kind == "custom":
        message_body = (request.message_body or "").strip()
        if not message_body:
            raise HTTPException(status_code=400, detail="Custom text message cannot be empty.")
    else:
        raise HTTPException(status_code=400, detail="Unsupported message type.")

    try:
        result = send_text_message(destination_phone, message_body)
    except ValueError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Text message send failed: {error}") from error

    response_payload = {
        "recipient": recipient_value,
        "resolved_phone_number": destination_phone,
        "message_kind": message_kind,
        "message_body": message_body,
        "textbelt": result,
    }
    if movie_user is not None:
        response_payload["resolved_username"] = movie_user.username

    return JSONResponse(content=response_payload)


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


async def run_daily_movie_refresh():
    """Run the shared daily Metrograph update cycle for all movie users."""
    try:
        results = run_daily_movie_user_update_cycle()
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Daily movie refresh failed: {error}") from error

    summary = {
        "users_processed": len(results),
        "texts_sent": sum(1 for result in results if result.get("text_sent")),
        "users_with_new_watchlist_films": sum(1 for result in results if result.get("new_watchlist_films", 0) > 0),
    }
    return JSONResponse(content={"results": results, "summary": summary})


async def setup_movie_user(request: MovieUserSetupRequest, db: Session = Depends(get_db)):
    """Create a movie user, start their initial Letterboxd sync, and return a pollable status response."""
    try:
        user = create_movie_user(db, request.username, request.letterboxd_username, request.phone_number)
        enqueue_movie_sync_job(
            db,
            user,
            job_type="setup",
            send_sms=True,
            sms_mode="setup-welcome",
            progress_logs=True,
            redirect_path=f"/movies/{user.username}?welcome=1",
        )
    except ValueError as error:
        db.rollback()
        detail = str(error)
        status_code = 409 if "already exists" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from error
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
    """Queue the Letterboxd refresh flow for a specific movie user."""
    user = get_movie_user(db, username)
    if user is None:
        raise HTTPException(status_code=404, detail="No user with that username exists. Sign up first.")
    if user.sync_in_progress:
        raise HTTPException(status_code=409, detail="A Letterboxd sync is already running for this user.")

    try:
        enqueue_movie_sync_job(
            db,
            user,
            job_type="sync",
            send_sms=True,
            sms_mode="summary",
            progress_logs=True,
        )
        payload = {
            "username": user.username,
            "message": "Letterboxd sync queued.",
            "status_path": f"/movies/users/{user.username}/setup-status",
        }
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Letterboxd sync failed: {error}") from error

    return JSONResponse(status_code=202, content=payload)


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
