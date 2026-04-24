"""Movie services for Metrograph schedule scraping and stored Letterboxd data."""

import json
import importlib
import os
import re
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from threading import Lock
from urllib.parse import quote, urljoin
from typing import Callable, Optional

import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session, selectinload

from models import MovieFriendRating, MovieLetterboxdData, MovieMemberFilmCache, MovieScheduleSnapshot, MovieUser, MovieUserFriend, SessionLocal
from services.sms_utils import send_text_message

SCHEDULE_SNAPSHOT_KEY = "metrograph_schedule"
LETTERBOXD_FRIENDS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "letterboxd_friends.json")
LETTERBOXD_USERNAME = "gbonez100"
DEFAULT_MOVIE_USERNAME = os.getenv("DEFAULT_MOVIE_USERNAME", "grayson")
DEFAULT_MOVIE_PHONE_NUMBER = os.getenv("DEFAULT_MOVIE_PHONE_NUMBER") or os.getenv("MY_PHONE_NUMBER") or ""
ENABLE_LETTERBOXD = os.getenv("ENABLE_LETTERBOXD", "true").lower() == "true"
LETTERBOXD_REFRESH_SKIP_DAYS = int(os.getenv("LETTERBOXD_REFRESH_SKIP_DAYS", "7"))
METROGRAPH_CALENDAR_URL = "https://metrograph.com/nyc/"
LETTERBOXD_BASE_URL = "https://letterboxd.com"
LETTERBOXD_FRIEND_USERNAMES_ENV = [
    username.strip()
    for username in os.getenv("LETTERBOXD_FRIEND_USERNAMES", "").split(",")
    if username.strip()
]

SYNC_JOB_LOCK = Lock()
SYNC_JOB_STATUS: dict[str, dict] = {}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

IMDB_SUGGESTION_BASE_URL = "https://v2.sg.media-imdb.com/suggestion"
IMDB_MATCH_CACHE: dict[tuple[str, Optional[int], Optional[str]], Optional[dict]] = {}
IMDB_RATING_CACHE: dict[str, Optional[float]] = {}
LETTERBOXD_SEARCH_CACHE: dict[tuple[str, Optional[int], Optional[str]], list[str]] = {}

SPECIAL_EVENT_TITLE_PATTERNS = [
    (re.compile(r"\[[^\]]+\]", re.IGNORECASE), "Special screening format"),
    (re.compile(r"\bq\s*&\s*a\b|\bq and a\b", re.IGNORECASE), "Q&A event"),
    (re.compile(r"\binterview\b|\bconversation\b|\bdiscussion\b|\btalk\b|\bmasterclass\b", re.IGNORECASE), "Interview or discussion event"),
    (re.compile(r"\bin person\b|\bwith special guest\b|\bguest\b", re.IGNORECASE), "Guest appearance event"),
    (re.compile(r"\bpresented by\b|\bhosted by\b|\bintroduced by\b|\bintro by\b", re.IGNORECASE), "Presented or introduced screening"),
    (re.compile(r"\blive score\b|\blive accompaniment\b|\blive performance\b", re.IGNORECASE), "Live performance screening"),
]

SPECIAL_EVENT_DESCRIPTION_PATTERNS = [
    (re.compile(r"\bcast member\b|\bactor in person\b|\bactress in person\b", re.IGNORECASE), "Cast member appearance"),
    (re.compile(r"\bdirector in person\b|\bfilmmaker in person\b|\bwriter in person\b", re.IGNORECASE), "Filmmaker appearance"),
    (re.compile(r"\bpost-screening\b|\bafter the screening\b|\bfollowed by\b.*\b(q\s*&\s*a|discussion|conversation|interview)\b", re.IGNORECASE), "Post-screening event"),
    (re.compile(r"\bmoderated by\b|\bjoined by\b|\bfeaturing\b", re.IGNORECASE), "Hosted guest event"),
    (re.compile(r"\blive music\b|\baccompaniment\b", re.IGNORECASE), "Live accompaniment event"),
]


def _log(message: str) -> None:
    print(message, flush=True)


def _job_status_key(username: str) -> str:
    return normalize_app_username(username)


def initialize_sync_job(username: str, job_type: str) -> None:
    with SYNC_JOB_LOCK:
        SYNC_JOB_STATUS[_job_status_key(username)] = {
            "job_type": job_type,
            "state": "running",
            "logs": [f"Starting {job_type} for {username}..."],
            "redirect_path": None,
            "error": None,
            "completed": False,
        }


def append_sync_job_log(username: str, message: str) -> None:
    with SYNC_JOB_LOCK:
        job = SYNC_JOB_STATUS.setdefault(
            _job_status_key(username),
            {"job_type": "sync", "state": "running", "logs": [], "redirect_path": None, "error": None, "completed": False},
        )
        job["logs"].append(message)


def finish_sync_job(username: str, redirect_path: Optional[str] = None, error: Optional[str] = None) -> None:
    with SYNC_JOB_LOCK:
        job = SYNC_JOB_STATUS.setdefault(
            _job_status_key(username),
            {"job_type": "sync", "state": "running", "logs": [], "redirect_path": None, "error": None, "completed": False},
        )
        job["completed"] = True
        job["state"] = "failed" if error else "completed"
        job["error"] = error
        job["redirect_path"] = redirect_path
        if error:
            job["logs"].append(f"Sync failed: {error}")
        else:
            job["logs"].append("Done!")


def get_sync_job_status(username: str) -> Optional[dict]:
    with SYNC_JOB_LOCK:
        job = SYNC_JOB_STATUS.get(_job_status_key(username))
        if job is None:
            return None
        return {
            "job_type": job.get("job_type"),
            "state": job.get("state"),
            "logs": list(job.get("logs", [])),
            "redirect_path": job.get("redirect_path"),
            "error": job.get("error"),
            "completed": job.get("completed", False),
        }


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", value.lower()).strip()


def normalize_app_username(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]", "", (value or "").strip().lower())
    if not normalized:
        raise ValueError("Username is required.")
    return normalized


def normalize_letterboxd_username(value: str) -> str:
    normalized = (value or "").strip().lower()
    profile_match = re.match(r"https?://(?:www\.)?letterboxd\.com/([^/?#]+)/?", normalized)
    if profile_match:
        normalized = profile_match.group(1)

    normalized = normalized.lstrip("@")
    normalized = normalized.strip("/")
    if not normalized:
        raise ValueError("Letterboxd username is required.")
    if not re.fullmatch(r"[a-z0-9_-]+", normalized):
        raise ValueError("Letterboxd username may only contain letters, numbers, hyphens, and underscores.")
    return normalized


def normalize_phone_number(value: str | None) -> str:
    raw_value = (value or "").strip()
    if not raw_value:
        return ""
    if not re.fullmatch(r"\+[1-9]\d{7,14}", raw_value):
        raise ValueError("Phone number must be entered exactly like +11234567890, including the plus sign and country code.")
    return raw_value


def get_movie_user(db: Session, username: str) -> Optional[MovieUser]:
    normalized_username = normalize_app_username(username)
    return db.query(MovieUser).filter(MovieUser.username == normalized_username).first()


def list_movie_users(db: Session) -> list[MovieUser]:
    return db.query(MovieUser).order_by(MovieUser.username.asc()).all()


def set_movie_user_sync_state(db: Session, user: MovieUser, *, sync_in_progress: bool, friend_sync_pending: Optional[bool] = None) -> MovieUser:
    user.sync_in_progress = sync_in_progress
    if friend_sync_pending is not None:
        user.friend_sync_pending = friend_sync_pending
    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


def get_or_create_default_movie_user(db: Session) -> MovieUser:
    existing_user = db.query(MovieUser).filter(MovieUser.username == DEFAULT_MOVIE_USERNAME).first()
    if existing_user is not None:
        return existing_user

    default_user = MovieUser(
        username=DEFAULT_MOVIE_USERNAME,
        letterboxd_username=normalize_letterboxd_username(LETTERBOXD_USERNAME),
        phone_number=normalize_phone_number(DEFAULT_MOVIE_PHONE_NUMBER),
    )
    db.add(default_user)
    db.commit()
    db.refresh(default_user)
    return default_user


def create_movie_user(db: Session, username: str, letterboxd_username: str, phone_number: str) -> MovieUser:
    normalized_username = normalize_app_username(username)
    if db.query(MovieUser).filter(MovieUser.username == normalized_username).first() is not None:
        raise ValueError("That username already exists. Log in with that username instead.")

    user = MovieUser(
        username=normalized_username,
        letterboxd_username=normalize_letterboxd_username(letterboxd_username),
        phone_number=normalize_phone_number(phone_number),
        sync_in_progress=False,
        friend_sync_pending=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_movie_user_letterboxd_username(db: Session, username: str, letterboxd_username: str) -> MovieUser:
    user = get_movie_user(db, username)
    if user is None:
        raise LookupError("User not found.")

    user.letterboxd_username = normalize_letterboxd_username(letterboxd_username)
    user.friend_sync_pending = True
    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


def update_movie_user_phone_number(db: Session, username: str, phone_number: str | None) -> MovieUser:
    user = get_movie_user(db, username)
    if user is None:
        raise LookupError("User not found.")

    user.phone_number = normalize_phone_number(phone_number)
    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


def clear_movie_user_friend_data(db: Session, username: str) -> MovieUser:
    user = get_movie_user(db, username)
    if user is None:
        raise LookupError("User not found.")

    user.friends.clear()
    for entry in db.query(MovieLetterboxdData).options(selectinload(MovieLetterboxdData.friend_ratings)).filter(
        MovieLetterboxdData.user_id == user.id
    ).all():
        entry.friend_ratings.clear()

    user.friend_sync_pending = True
    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


def _serialize_movie_user(user: MovieUser) -> dict:
    return {
        "username": user.username,
        "letterboxd_username": user.letterboxd_username,
        "phone_number": user.phone_number,
        "sync_in_progress": user.sync_in_progress,
        "friend_sync_pending": user.friend_sync_pending,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    }


def _find_cached_member_film_data(db: Session, username: str, normalized_title: str, year: Optional[int]) -> Optional[dict]:
    cache_query = db.query(MovieMemberFilmCache).filter(
        MovieMemberFilmCache.member_username == username,
        MovieMemberFilmCache.normalized_title == normalized_title,
    )
    cache_entry = None
    if year is None:
        cache_entry = cache_query.filter(MovieMemberFilmCache.year.is_(None)).first()
    else:
        cache_entry = cache_query.filter(MovieMemberFilmCache.year == year).first() or cache_query.filter(MovieMemberFilmCache.year.is_(None)).first()

    if cache_entry is not None and _was_cache_scanned_recently(cache_entry.last_scanned_at):
        return {
            "watched": cache_entry.watched,
            "personal_rating": cache_entry.personal_rating,
        }

    matching_user = db.query(MovieUser).filter(MovieUser.letterboxd_username == username).first()
    if matching_user is None:
        return None

    user_entry = _find_movie_entry(db, matching_user.id, normalized_title, year)
    if user_entry is None or not _was_scanned_recently(user_entry):
        return None

    cached_info = {
        "watched": user_entry.watched,
        "personal_rating": user_entry.personal_rating,
    }
    _store_member_film_cache(db, username, normalized_title, year, cached_info)
    return cached_info


def _store_member_film_cache(db: Session, username: str, normalized_title: str, year: Optional[int], info: dict) -> None:
    cache_query = db.query(MovieMemberFilmCache).filter(
        MovieMemberFilmCache.member_username == username,
        MovieMemberFilmCache.normalized_title == normalized_title,
    )
    cache_entry = None
    if year is None:
        cache_entry = cache_query.filter(MovieMemberFilmCache.year.is_(None)).first()
    else:
        cache_entry = cache_query.filter(MovieMemberFilmCache.year == year).first()

    if cache_entry is None:
        cache_entry = MovieMemberFilmCache(
            member_username=username,
            normalized_title=normalized_title,
            year=year,
        )
        db.add(cache_entry)

    cache_entry.watched = bool(info.get("watched"))
    cache_entry.personal_rating = info.get("personal_rating")
    cache_entry.last_scanned_at = datetime.now(timezone.utc)
    db.flush()


def _was_cache_scanned_recently(last_scanned_at: Optional[datetime]) -> bool:
    if last_scanned_at is None:
        return False

    timestamp = last_scanned_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    return timestamp >= datetime.now(timezone.utc) - timedelta(days=LETTERBOXD_REFRESH_SKIP_DAYS)


def _extract_letterboxd_username_from_url(profile_url: str) -> str:
    path = requests.utils.urlparse(profile_url).path.strip("/")
    return path.split("/")[0] if path else ""


def _collect_letterboxd_following_profiles(page, playwright_timeout_error) -> list[dict]:
    page.wait_for_timeout(4000)

    selectors = [
        ".person-summary",
        "a.name",
        "a.avatar",
    ]

    table_found = False
    for selector in selectors:
        try:
            page.locator(selector).first.wait_for(state="attached", timeout=15000)
            table_found = True
            break
        except playwright_timeout_error:
            continue

    if not table_found and page.locator("a.name").count() > 0:
        table_found = True

    if not table_found:
        raise RuntimeError("Could not find the Letterboxd following table on the page.")

    profiles = {}
    summaries = page.locator(".person-summary")
    for index in range(summaries.count()):
        summary = summaries.nth(index)
        name_link = summary.locator("a.name").first
        if name_link.count() == 0:
            continue

        href = name_link.get_attribute("href")
        display_name = name_link.inner_text().strip()
        if not href:
            continue
        if href.startswith("/sign-in") or href.startswith("/search"):
            continue
        if any(segment in href for segment in ["/film/", "/list/", "/reviews/", "/likes/", "/diary/", "/following/", "/followers/"]):
            continue

        full_url = f"{LETTERBOXD_BASE_URL}{href}" if href.startswith("/") else href
        profile_username = _extract_letterboxd_username_from_url(full_url)
        if not profile_username:
            continue

        profiles[profile_username] = {
            "username": profile_username,
            "display_name": display_name or profile_username,
            "profile_url": full_url,
        }

    return sorted(profiles.values(), key=lambda profile: profile["username"])


def _fetch_letterboxd_following_profiles(username: str) -> list[dict]:
    try:
        playwright_sync_api = importlib.import_module("playwright.sync_api")
        PlaywrightTimeoutError = getattr(playwright_sync_api, "TimeoutError")
        sync_playwright = getattr(playwright_sync_api, "sync_playwright")
    except Exception as error:
        _log(f"  ⚠️  Playwright is not available for Letterboxd following fetches: {error}")
        return []

    profiles: dict[str, dict] = {}
    visited_pages = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )

        try:
            page_number = 1
            while True:
                page_url = f"{LETTERBOXD_BASE_URL}/{username}/following/"
                if page_number > 1:
                    page_url = f"{LETTERBOXD_BASE_URL}/{username}/following/page/{page_number}/"

                page = browser.new_page(
                    user_agent=HEADERS["User-Agent"],
                    locale="en-US",
                    viewport={"width": 1440, "height": 1200},
                )
                page.add_init_script(
                    """
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                    Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });
                    """
                )

                try:
                    page.goto(page_url, wait_until="domcontentloaded", timeout=120000)
                    if "/sign-in/" in page.url:
                        raise RuntimeError("Letterboxd redirected the following page to sign-in.")

                    page_profiles = _collect_letterboxd_following_profiles(page, PlaywrightTimeoutError)
                    if not page_profiles:
                        break

                    visited_pages.append(page.url)
                    for profile in page_profiles:
                        profiles[profile["username"]] = profile

                    if page.locator("a.next").count() == 0:
                        break
                    page_number += 1
                finally:
                    page.close()
        except Exception as error:
            visited = ", ".join(visited_pages) if visited_pages else "none"
            _log(f"  ⚠️  Letterboxd following fetch failed for {username} via Playwright: {error}. Visited pages: {visited}")
            return []
        finally:
            browser.close()

    return sorted(profiles.values(), key=lambda profile: profile["username"])


def _refresh_movie_user_friends(db: Session, user: MovieUser, progress_callback: Optional[Callable[[str], None]] = None) -> list[dict]:
    profiles = _fetch_letterboxd_following_profiles(user.letterboxd_username)

    if progress_callback is not None:
        progress_callback(f"Found {len(profiles)} following accounts for @{user.letterboxd_username}.")

    user.friends.clear()
    db.flush()

    for index, profile in enumerate(profiles, start=1):
        user.friends.append(
            MovieUserFriend(
                friend_username=profile["username"],
                friend_display_name=profile.get("display_name"),
                profile_url=profile.get("profile_url"),
            )
        )
        if progress_callback is not None:
            progress_callback(f"Stored friend {index}/{len(profiles)}: {profile['username']}")

    db.flush()
    return profiles


def _slugify_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"\[[^\]]*\]|\([^\)]*\)", " ", normalized)
    normalized = normalized.replace("&", " and ")
    normalized = normalized.replace("'", "")
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return re.sub(r"-+", "-", normalized)


def _generate_slug_candidates(title: str, year: Optional[int]) -> list[str]:
    cleaned = re.sub(r"\[[^\]]*\]|\([^\)]*\)", " ", title).strip()
    variants = [cleaned]

    no_leading_article = re.sub(r"^(the|an|a)\s+", "", cleaned, flags=re.IGNORECASE).strip()
    if no_leading_article and no_leading_article != cleaned:
        variants.append(no_leading_article)

    if ":" in cleaned:
        variants.append(cleaned.split(":", 1)[0].strip())

    if " - " in cleaned:
        variants.append(cleaned.split(" - ", 1)[0].strip())

    candidates = []
    seen = set()
    for variant in variants:
        slug = _slugify_title(variant)
        if not slug:
            continue
        paths = [f"/film/{slug}/"]
        if year is not None:
            paths.append(f"/film/{slug}-{year}/")
        for path in paths:
            if path not in seen:
                seen.add(path)
                candidates.append(path)

    return candidates


def _clean_title_for_external_search(title: str) -> str:
    cleaned = re.sub(r"\[[^\]]*\]|\([^\)]*\)", " ", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _strip_trailing_byline(title: str) -> str:
    return re.sub(r"\s*\((?:by|presented by|introduced by|intro by)\s+[^\)]+\)\s*$", "", title, flags=re.IGNORECASE).strip()


def _parse_title_context(title: str) -> dict:
    base_title = _strip_trailing_byline(title)
    components: list[dict] = []
    structural_reasons: list[str] = []

    preceded_by_parts = [part.strip() for part in re.split(r"\s+preceded by\s+", base_title, maxsplit=1, flags=re.IGNORECASE) if part.strip()]
    if len(preceded_by_parts) == 2:
        components = [{"title": part, "search_title": _clean_title_for_external_search(part)} for part in preceded_by_parts]
        structural_reasons.append("Double feature")
        return {"components": components, "structural_reasons": structural_reasons}

    plus_parts = [part.strip() for part in re.split(r"\s+\+\s+", base_title) if part.strip()]
    if len(plus_parts) > 1:
        components = [{"title": part, "search_title": _clean_title_for_external_search(part)} for part in plus_parts]
        structural_reasons.append("Double feature")
        return {"components": components, "structural_reasons": structural_reasons}

    presenter_match = re.match(r"^.+?\s+(?:presents|presented by|introduces|introduced)\s+(.+)$", base_title, flags=re.IGNORECASE)
    if presenter_match:
        presented_title = presenter_match.group(1).strip()
        components = [{"title": presented_title, "search_title": _clean_title_for_external_search(presented_title)}]
        structural_reasons.append("Presented screening")
        return {"components": components, "structural_reasons": structural_reasons}

    components = [{"title": base_title, "search_title": _clean_title_for_external_search(base_title)}]
    return {"components": components, "structural_reasons": structural_reasons}


def _fetch_imdb_movie_match(title: str, year: Optional[int], director: Optional[str] = None) -> Optional[dict]:
    cache_key = (title, year, director)
    if cache_key in IMDB_MATCH_CACHE:
        return IMDB_MATCH_CACHE[cache_key]

    cleaned_title = _clean_title_for_external_search(title)
    title_norm = _norm(cleaned_title)
    director_norm = _norm((director or "").split(",")[0]) if director else ""
    queries = [cleaned_title]
    if director:
        queries.append(f"{cleaned_title} {director}")
    if year is not None:
        queries.append(f"{cleaned_title} {year}")

    best_match = None
    best_score = float("-inf")

    for query in queries:
        search_query = re.sub(r"\s+", "-", query.lower())
        first_char = next((char for char in search_query if char.isalnum()), "t")
        url = f"{IMDB_SUGGESTION_BASE_URL}/{first_char}/{requests.utils.quote(search_query)}.json"

        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            continue

        for candidate in payload.get("d", []):
            qid = (candidate.get("qid") or "").lower()
            q = (candidate.get("q") or "").lower()
            if qid not in {"movie", "feature"} and q not in {"feature", "movie"}:
                continue

            candidate_title = candidate.get("l") or ""
            candidate_year = candidate.get("y")
            candidate_norm = _norm(candidate_title)
            similarity = SequenceMatcher(None, title_norm, candidate_norm).ratio()
            score = similarity

            if year is not None and candidate_year is not None:
                year_delta = abs(candidate_year - year)
                if year_delta == 0:
                    score += 0.45
                elif year_delta == 1:
                    score += 0.2
                elif year_delta <= 5:
                    score -= 0.05 * year_delta
                else:
                    score -= 0.35

            cast_or_credit = _norm(candidate.get("s") or "")
            if director_norm and director_norm and director_norm in cast_or_credit:
                score += 0.35

            if similarity < 0.72 and not (
                year is not None
                and candidate_year is not None
                and abs(candidate_year - year) <= 1
                and score >= 0.35
            ):
                continue

            if score > best_score:
                best_score = score
                best_match = {
                    "title": candidate_title,
                    "year": candidate_year,
                    "id": candidate.get("id"),
                }

    IMDB_MATCH_CACHE[cache_key] = best_match
    return best_match


def _fetch_imdb_public_rating(imdb_id: Optional[str]) -> Optional[float]:
    if not imdb_id:
        return None

    if imdb_id in IMDB_RATING_CACHE:
        return IMDB_RATING_CACHE[imdb_id]

    try:
        response = requests.get(f"https://www.imdb.com/title/{imdb_id}/", headers=HEADERS, timeout=15)
        response.raise_for_status()
    except requests.RequestException:
        IMDB_RATING_CACHE[imdb_id] = None
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.get_text(strip=True))
        except json.JSONDecodeError:
            continue

        if not isinstance(payload, dict):
            continue

        aggregate_rating = payload.get("aggregateRating")
        if isinstance(aggregate_rating, dict) and aggregate_rating.get("ratingValue") is not None:
            try:
                IMDB_RATING_CACHE[imdb_id] = float(aggregate_rating["ratingValue"])
                return IMDB_RATING_CACHE[imdb_id]
            except (TypeError, ValueError):
                continue

    IMDB_RATING_CACHE[imdb_id] = None
    return None


def _build_letterboxd_search_queries(title: str, year: Optional[int], director: Optional[str]) -> list[str]:
    clean_title = _clean_title_for_external_search(title)
    primary_director = (director or "").split(",")[0].strip()
    candidates = []

    if clean_title:
        candidates.append(clean_title)
        if year is not None:
            candidates.append(f"{clean_title} {year}")
        if primary_director:
            candidates.append(f"{clean_title} {primary_director}")
        if year is not None and primary_director:
            candidates.append(f"{clean_title} {primary_director} {year}")

    deduped = []
    seen = set()
    for candidate in candidates:
        key = candidate.lower().strip()
        if key and key not in seen:
            seen.add(key)
            deduped.append(candidate)
    return deduped


def _has_external_movie_match(title: str, year: Optional[int], director: Optional[str] = None) -> bool:
    if _search_letterboxd_film_paths(title, year, director):
        return True
    return _fetch_imdb_movie_match(title, year, director) is not None


def _has_external_movie_match(title: str, year: Optional[int], director: Optional[str] = None) -> bool:
    if _search_letterboxd_film_paths(title, year, director):
        return True

    imdb_match = _fetch_imdb_movie_match(title, year, director)
    return imdb_match is not None


def _search_letterboxd_film_paths(title: str, year: Optional[int] = None, director: Optional[str] = None) -> list[str]:
    cache_key = (title, year, director)
    if cache_key in LETTERBOXD_SEARCH_CACHE:
        return LETTERBOXD_SEARCH_CACHE[cache_key]

    paths = []
    seen_paths = set()
    for query in _build_letterboxd_search_queries(title, year, director):
        search_url = f"{LETTERBOXD_BASE_URL}/search/{quote(query)}/"
        try:
            response = requests.get(search_url, headers=HEADERS, timeout=15)
            response.raise_for_status()
        except requests.RequestException:
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        for link in soup.select('a[href^="/film/"]'):
            href = link.get("href", "")
            match = re.match(r"^(/film/[^/?#]+/)$", href)
            if not match:
                continue
            path = match.group(1)
            if path in seen_paths:
                continue
            seen_paths.add(path)
            paths.append(path)
            if len(paths) >= 10:
                LETTERBOXD_SEARCH_CACHE[cache_key] = paths
                return paths

    LETTERBOXD_SEARCH_CACHE[cache_key] = paths
    return paths


def fetch_calendar_page() -> BeautifulSoup:
    try:
        resp = requests.get(METROGRAPH_CALENDAR_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as error:
        raise RuntimeError(f"Failed to fetch Metrograph calendar: {error}") from error

    return BeautifulSoup(resp.text, "html.parser")


def parse_day_block(day_block) -> list[dict]:
    day_id = day_block.get("id", "")
    match = re.search(r"calendar-list-day-(\d{4}-\d{2}-\d{2})", day_id)
    if not match:
        return []
    target_date = match.group(1)

    if "closed" in (day_block.get("class") or []):
        return []

    showings = []

    for film_block in day_block.select("div.item.film-thumbnail.homepage-in-theater-movie"):
        h4 = film_block.find("h4")
        if not h4:
            continue
        link_tag = h4.find("a", href=True)
        if not link_tag:
            continue
        film_url = link_tag["href"]
        title = link_tag.get_text(strip=True)
        if not title:
            continue

        film_id_match = re.search(r"vista_film_id=(\d+)", film_url)
        film_id = film_id_match.group(1) if film_id_match else None

        meta_el = film_block.find("div", class_="film-metadata")
        meta_text = meta_el.get_text(" ", strip=True) if meta_el else ""

        description_el = film_block.find("div", class_="film-description")
        description = description_el.get_text(" ", strip=True) if description_el else None

        showtimes_el = film_block.find("div", class_="showtimes")
        times = []
        ticket_links = []
        if showtimes_el:
            time_links = showtimes_el.find_all("a", href=True)
            if time_links:
                for link in time_links:
                    text = link.get_text(strip=True)
                    href = link["href"]
                    if text:
                        times.append(text)
                        ticket_links.append(href)
            else:
                raw_text = showtimes_el.get_text(" ", strip=True)
                for match_time in re.findall(r"\d{1,2}:\d{2}(?:am|pm)", raw_text, re.IGNORECASE):
                    times.append(match_time)
                    ticket_links.append(None)

        director = year = runtime = fmt = None
        parts = [part.strip() for part in meta_text.split("/")]
        if len(parts) >= 1:
            director = parts[0]
        if len(parts) >= 2:
            year_match = re.search(r"\d{4}", parts[1])
            year = int(year_match.group()) if year_match else None
        if len(parts) >= 3:
            runtime = parts[2]
        if len(parts) >= 4:
            fmt = "/".join(parts[3:]).strip()

        for index, showtime in enumerate(times):
            ticket_url = ticket_links[index] if index < len(ticket_links) else None
            showings.append(
                {
                    "date": target_date,
                    "title": title,
                    "film_id": film_id,
                    "film_url": f"https://metrograph.com{film_url}" if film_url.startswith("/") else film_url,
                    "director": director,
                    "year": year,
                    "runtime": runtime,
                    "format": fmt,
                    "description": description,
                    "time": showtime,
                    "ticket_url": ticket_url,
                    "sold_out": ticket_url is None,
                }
            )

    return showings


def scrape_schedule() -> list[dict]:
    soup = fetch_calendar_page()
    all_showings = []
    seen = set()

    day_blocks = soup.select("div.calendar-list-day")
    _log(f"  Found {len(day_blocks)} calendar day blocks.")
    for day_block in day_blocks:
        for showing in parse_day_block(day_block):
            key = (showing["date"], showing["title"], showing["time"])
            if key in seen:
                continue
            seen.add(key)
            all_showings.append(showing)

    return all_showings


def _fetch_letterboxd_watchlist(username: str) -> dict[str, str]:
    watchlist_titles: dict[str, str] = {}
    page = 1
    while True:
        url = f"https://letterboxd.com/{username}/watchlist/page/{page}/"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 404:
                break
            resp.raise_for_status()
        except requests.RequestException as error:
            _log(f"  ⚠️  Letterboxd watchlist fetch failed (page {page}): {error}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        films = soup.select("li.griditem div.react-component[data-component-class='LazyPoster']")
        if not films:
            break

        for film in films:
            item_name = film.get("data-item-name", "")
            item_link = film.get("data-item-link", "")
            if not item_name or not item_link:
                continue
            clean_name = re.sub(r"\s*\(\d{4}\)$", "", item_name).strip()
            watchlist_titles[_norm(clean_name)] = item_link

        if not soup.select_one("a.next"):
            break

        page += 1
        time.sleep(0.4)

    return watchlist_titles


def _load_friend_profiles() -> list[dict]:
    if os.path.exists(LETTERBOXD_FRIENDS_PATH):
        try:
            with open(LETTERBOXD_FRIENDS_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)

            profiles = []
            for profile in data.get("profiles", []):
                username = profile.get("username", "").strip()
                if username:
                    profiles.append(
                        {
                            "username": username,
                            "display_name": profile.get("display_name", "").strip() or None,
                        }
                    )
            if profiles:
                return profiles
        except (OSError, json.JSONDecodeError) as error:
            _log(f"  ⚠️  Failed to load {LETTERBOXD_FRIENDS_PATH}: {error}")

    return [{"username": username, "display_name": None} for username in LETTERBOXD_FRIEND_USERNAMES_ENV]


def _extract_rating_from_film_page(soup: BeautifulSoup) -> Optional[float]:
    meta_rating = soup.select_one('meta[name="twitter:data2"]')
    if meta_rating:
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)", meta_rating.get("content", ""))
        if match:
            return float(match.group(1))

    for script in soup.select('script[type="application/ld+json"]'):
        text = script.get_text(strip=True)
        match = re.search(r'"ratingValue"\s*:\s*([0-9]+(?:\.[0-9]+)?)', text)
        if match:
            return float(match.group(1))

    return None


def _extract_star_rating(star_text: str) -> Optional[float]:
    if not star_text:
        return None

    star_text = star_text.strip()
    full_stars = star_text.count("★")
    half_star = 0.5 if "½" in star_text else 0.0
    if full_stars == 0 and half_star == 0:
        return None
    return full_stars + half_star


def _validate_film_match(soup: BeautifulSoup, title: str, year: Optional[int], allow_year_mismatch: bool = False) -> bool:
    og_title = soup.select_one('meta[property="og:title"]')
    if not og_title:
        return False

    page_title = og_title.get("content", "")
    page_title_norm = _norm(re.sub(r"\s*\(\d{4}\)$", "", page_title))
    title_norm = _norm(re.sub(r"\[[^\]]*\]|\([^\)]*\)", " ", title))
    similarity = SequenceMatcher(None, page_title_norm, title_norm).ratio()
    if page_title_norm != title_norm and similarity < 0.72:
        return False

    if year is not None:
        year_match = re.search(r"\((\d{4})\)$", page_title)
        if year_match and int(year_match.group(1)) != year and not allow_year_mismatch:
            return False

    return True


def _validate_member_film_match(soup: BeautifulSoup, title: str, year: Optional[int], allow_year_mismatch: bool = False) -> bool:
    og_title = soup.select_one('meta[property="og:title"]')
    if not og_title:
        return False

    raw_title = og_title.get("content", "")
    year_match = re.search(r"\((\d{4})\)", raw_title)
    extracted_year = int(year_match.group(1)) if year_match else None

    raw_title = re.sub(r"^A\s+[★½\s]*review\s+of\s+", "", raw_title)
    raw_title = re.sub(r"^A\s+review\s+of\s+", "", raw_title)
    raw_title = re.sub(r"^Watched\s+", "", raw_title)
    raw_title = re.sub(r"\s*\(\d{4}\).*$", "", raw_title).strip()

    title_norm = _norm(re.sub(r"\[[^\]]*\]|\([^\)]*\)", " ", title))
    raw_norm = _norm(raw_title)
    similarity = SequenceMatcher(None, raw_norm, title_norm).ratio()
    if raw_norm != title_norm and similarity < 0.72:
        return False

    if year is not None and extracted_year is not None and extracted_year != year and not allow_year_mismatch:
        return False

    return True


def _try_letterboxd_paths(paths: list[str], title: str, year: Optional[int], allow_year_mismatch: bool = False) -> Optional[float]:
    seen_paths = set()
    for path in paths:
        if path in seen_paths:
            continue
        seen_paths.add(path)

        try:
            resp = requests.get(f"{LETTERBOXD_BASE_URL}{path}", headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
        except requests.RequestException:
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        if not _validate_film_match(soup, title, year, allow_year_mismatch=allow_year_mismatch):
            continue

        rating = _extract_rating_from_film_page(soup)
        if rating is not None:
            return rating

    return None


def _try_member_letterboxd_paths(username: str, paths: list[str], title: str, year: Optional[int], allow_year_mismatch: bool = False) -> dict:
    seen_paths = set()
    for path in paths:
        if path in seen_paths:
            continue
        seen_paths.add(path)

        try:
            resp = requests.get(f"{LETTERBOXD_BASE_URL}/{username}{path}", headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
        except requests.RequestException:
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        if not _validate_member_film_match(soup, title, year, allow_year_mismatch=allow_year_mismatch):
            continue

        rating_meta = soup.select_one('meta[name="twitter:data2"]')
        personal_rating = _extract_star_rating(rating_meta.get("content", "") if rating_meta else "")
        return {"watched": True, "personal_rating": personal_rating}

    return {"watched": False, "personal_rating": None}


def _fetch_letterboxd_rating(title: str, year: Optional[int], film_path: Optional[str] = None, director: Optional[str] = None) -> Optional[float]:
    candidate_paths = []
    if film_path:
        candidate_paths.append(film_path)
    candidate_paths.extend(_generate_slug_candidates(title, year))

    rating = _try_letterboxd_paths(candidate_paths, title, year)
    if rating is not None:
        return rating

    rating = _try_letterboxd_paths(_search_letterboxd_film_paths(title, year, director), title, year)
    if rating is not None:
        return rating

    imdb_match = _fetch_imdb_movie_match(title, year)
    if imdb_match:
        rating = _try_letterboxd_paths(
            _generate_slug_candidates(imdb_match["title"], imdb_match.get("year") or year),
            imdb_match["title"],
            imdb_match.get("year") or year,
            allow_year_mismatch=True,
        )
        if rating is not None:
            return rating

        rating = _try_letterboxd_paths(
            _search_letterboxd_film_paths(imdb_match["title"], imdb_match.get("year") or year, director),
            imdb_match["title"],
            imdb_match.get("year") or year,
            allow_year_mismatch=True,
        )
        if rating is not None:
            return rating

        imdb_rating = _fetch_imdb_public_rating(imdb_match.get("id"))
        if imdb_rating is not None:
            return imdb_rating

    return None


def _fetch_member_film_data(db: Session, username: str, title: str, year: Optional[int], director: Optional[str] = None) -> dict:
    normalized_title = _norm(title)
    cached_info = _find_cached_member_film_data(db, username, normalized_title, year)
    if cached_info is not None:
        return cached_info

    info = _try_member_letterboxd_paths(username, _generate_slug_candidates(title, year), title, year)
    if info["watched"]:
        _store_member_film_cache(db, username, normalized_title, year, info)
        return info

    info = _try_member_letterboxd_paths(username, _search_letterboxd_film_paths(title, year, director), title, year)
    if info["watched"]:
        _store_member_film_cache(db, username, normalized_title, year, info)
        return info

    imdb_match = _fetch_imdb_movie_match(title, year)
    if imdb_match:
        info = _try_member_letterboxd_paths(
            username,
            _generate_slug_candidates(imdb_match["title"], imdb_match.get("year") or year),
            imdb_match["title"],
            imdb_match.get("year") or year,
            allow_year_mismatch=True,
        )
        if info["watched"]:
            _store_member_film_cache(db, username, normalized_title, year, info)
            return info

        info = _try_member_letterboxd_paths(
            username,
            _search_letterboxd_film_paths(imdb_match["title"], imdb_match.get("year") or year, director),
            imdb_match["title"],
            imdb_match.get("year") or year,
            allow_year_mismatch=True,
        )
        if info["watched"]:
            _store_member_film_cache(db, username, normalized_title, year, info)
            return info

    not_watched_info = {"watched": False, "personal_rating": None}
    _store_member_film_cache(db, username, normalized_title, year, not_watched_info)
    return not_watched_info


def _find_movie_entry(db: Session, user_id: int, normalized_title: str, year: Optional[int]) -> Optional[MovieLetterboxdData]:
    query = db.query(MovieLetterboxdData).options(selectinload(MovieLetterboxdData.friend_ratings)).filter(
        MovieLetterboxdData.user_id == user_id,
        MovieLetterboxdData.normalized_title == normalized_title
    )
    if year is None:
        return query.filter(MovieLetterboxdData.year.is_(None)).first()

    exact_match = query.filter(MovieLetterboxdData.year == year).first()
    if exact_match:
        return exact_match

    return query.filter(MovieLetterboxdData.year.is_(None)).first()


def _was_scanned_recently(entry: Optional[MovieLetterboxdData]) -> bool:
    if entry is None or entry.last_scanned_at is None:
        return False

    last_scanned_at = entry.last_scanned_at
    if last_scanned_at.tzinfo is None:
        last_scanned_at = last_scanned_at.replace(tzinfo=timezone.utc)

    return last_scanned_at >= datetime.now(timezone.utc) - timedelta(days=LETTERBOXD_REFRESH_SKIP_DAYS)


def _find_movie_entry_from_lookups(
    search_title: str,
    year: Optional[int],
    entries_by_key: dict,
    entries_by_title: defaultdict,
) -> Optional[MovieLetterboxdData]:
    normalized_title = _norm(search_title)
    entry = entries_by_key.get((normalized_title, year))
    if entry is not None:
        return entry

    matching_entries = entries_by_title.get(normalized_title, [])
    if len(matching_entries) == 1:
        return matching_entries[0]

    return next((candidate for candidate in matching_entries if candidate.year is None), None)


def _serialize_friend_rows(friend_rows: list[MovieFriendRating]) -> list[dict]:
    return [
        {
            "username": row.friend_username,
            "display_name": row.friend_display_name,
            "rating": row.rating,
        }
        for row in sorted(friend_rows, key=lambda row: (row.friend_display_name or row.friend_username or "").lower())
    ]


def _entry_has_letterboxd_signals(entry: Optional[MovieLetterboxdData]) -> bool:
    if entry is None:
        return False

    return any(
        [
            entry.letterboxd_rating is not None,
            entry.on_watchlist,
            entry.watched,
            entry.personal_rating is not None,
            bool(entry.friend_ratings),
        ]
    )


def _build_component_payload(component: dict, year: Optional[int], director: Optional[str], entry: Optional[MovieLetterboxdData]) -> dict:
    payload = {
        "title": component["title"],
        "search_title": component["search_title"],
        "year": year,
    }
    _apply_entry_to_film(payload, entry)
    payload["has_external_match"] = _entry_has_letterboxd_signals(entry) or _has_external_movie_match(component["search_title"], year, director)
    return payload


def _apply_entry_to_film(film: dict, entry: Optional[MovieLetterboxdData]) -> None:
    if entry is None:
        film["on_watchlist"] = False
        film["letterboxd_rating"] = None
        film["watched"] = False
        film["personal_rating"] = None
        film["friend_watch_count"] = 0
        film["friend_avg_rating"] = None
        film["friend_watchers"] = []
        return

    rated_friend_values = [friend.rating for friend in entry.friend_ratings if friend.rating is not None]
    film["on_watchlist"] = entry.on_watchlist
    film["letterboxd_rating"] = entry.letterboxd_rating
    film["watched"] = entry.watched
    film["personal_rating"] = entry.personal_rating
    film["friend_watch_count"] = len(entry.friend_ratings)
    film["friend_avg_rating"] = round(sum(rated_friend_values) / len(rated_friend_values), 2) if rated_friend_values else None
    film["friend_watchers"] = _serialize_friend_rows(entry.friend_ratings)


def _apply_components_to_film(film: dict, component_payloads: list[dict]) -> None:
    film["rating_components"] = [
        {
            "title": component["title"],
            "search_title": component["search_title"],
            "letterboxd_rating": component.get("letterboxd_rating"),
            "on_watchlist": component.get("on_watchlist", False),
            "watched": component.get("watched", False),
            "personal_rating": component.get("personal_rating"),
            "friend_watch_count": component.get("friend_watch_count", 0),
            "friend_avg_rating": component.get("friend_avg_rating"),
            "friend_watchers": component.get("friend_watchers", []),
        }
        for component in component_payloads
    ]

    if not component_payloads:
        _apply_entry_to_film(film, None)
        return

    public_ratings = [component["letterboxd_rating"] for component in component_payloads if component.get("letterboxd_rating") is not None]
    personal_ratings = [component["personal_rating"] for component in component_payloads if component.get("personal_rating") is not None]
    friend_counts = [component.get("friend_watch_count", 0) for component in component_payloads]
    friend_avgs = [component["friend_avg_rating"] for component in component_payloads if component.get("friend_avg_rating") is not None]

    best_component = max(
        component_payloads,
        key=lambda component: (
            1 if component.get("on_watchlist") else 0,
            component.get("friend_watch_count", 0),
            component.get("friend_avg_rating") if component.get("friend_avg_rating") is not None else -1,
            component.get("letterboxd_rating") if component.get("letterboxd_rating") is not None else -1,
            component.get("watched", False),
        ),
    )

    film["on_watchlist"] = any(component.get("on_watchlist") for component in component_payloads)
    film["watched"] = any(component.get("watched") for component in component_payloads)
    film["personal_rating"] = max(personal_ratings) if personal_ratings else None
    film["friend_watch_count"] = max(friend_counts) if friend_counts else 0
    film["friend_avg_rating"] = max(friend_avgs) if friend_avgs else None
    film["letterboxd_rating"] = max(public_ratings) if public_ratings else None
    film["friend_watchers"] = best_component.get("friend_watchers", [])


def _get_special_event_reasons(showing: dict, entry: Optional[MovieLetterboxdData]) -> list[str]:
    reasons = []
    title = showing.get("title") or ""
    description = showing.get("description") or ""
    director = showing.get("director")
    title_context = _parse_title_context(title)

    for reason in title_context["structural_reasons"]:
        reasons.append(reason)

    for pattern, reason in SPECIAL_EVENT_TITLE_PATTERNS:
        if pattern.search(title):
            reasons.append(reason)

    for pattern, reason in SPECIAL_EVENT_DESCRIPTION_PATTERNS:
        if pattern.search(description):
            reasons.append(reason)

    if not reasons:
        has_component_match = False
        for component in title_context["components"]:
            if _has_external_movie_match(component["search_title"], showing.get("year"), director):
                has_component_match = True
                break
        if not has_component_match:
            reasons.append("No IMDb title found")

    deduped_reasons = []
    for reason in reasons:
        if reason not in deduped_reasons:
            deduped_reasons.append(reason)

    return deduped_reasons


def _enrich_film_from_components(film: dict, entries_by_key: dict, entries_by_title: defaultdict) -> None:
    title_context = _parse_title_context(film.get("title", ""))
    component_payloads = []
    director = film.get("director")

    for component in title_context["components"]:
        entry = _find_movie_entry_from_lookups(component["search_title"], film.get("year"), entries_by_key, entries_by_title)
        component_payloads.append(_build_component_payload(component, film.get("year"), director, entry))

    _apply_components_to_film(film, component_payloads)
    special_event_reasons = _get_special_event_reasons(film, None)
    film["special_event"] = bool(special_event_reasons)
    film["special_event_reason"] = "; ".join(special_event_reasons) if special_event_reasons else None


def enrich_showings_from_db(showings: list[dict], db: Session, user: Optional[MovieUser] = None) -> list[dict]:
    if user is None:
        entries = []
    else:
        entries = db.query(MovieLetterboxdData).options(selectinload(MovieLetterboxdData.friend_ratings)).filter(
            MovieLetterboxdData.user_id == user.id
        ).all()
    entries_by_key = {(entry.normalized_title, entry.year): entry for entry in entries}
    entries_by_title = defaultdict(list)
    for entry in entries:
        entries_by_title[entry.normalized_title].append(entry)

    for showing in showings:
        _enrich_film_from_components(showing, entries_by_key, entries_by_title)

    return showings


def merge_schedule_payload_with_db(payload: dict, db: Session, user: Optional[MovieUser] = None) -> dict:
    films = payload.get("films", [])
    if user is None:
        entries = []
    else:
        entries = db.query(MovieLetterboxdData).options(selectinload(MovieLetterboxdData.friend_ratings)).filter(
            MovieLetterboxdData.user_id == user.id
        ).all()
    entries_by_key = {(entry.normalized_title, entry.year): entry for entry in entries}
    entries_by_title = defaultdict(list)
    for entry in entries:
        entries_by_title[entry.normalized_title].append(entry)

    for film in films:
        _enrich_film_from_components(film, entries_by_key, entries_by_title)

    return payload


def group_by_film(showings: list[dict]) -> list[dict]:
    films: dict[str, dict] = {}
    for showing in showings:
        key = showing["title"]
        if key not in films:
            films[key] = {
                "title": showing["title"],
                "film_id": showing["film_id"],
                "film_url": showing["film_url"],
                "director": showing["director"],
                "year": showing["year"],
                "runtime": showing["runtime"],
                "format": showing["format"],
                "on_watchlist": showing.get("on_watchlist", False),
                "letterboxd_rating": showing.get("letterboxd_rating"),
                "watched": showing.get("watched", False),
                "personal_rating": showing.get("personal_rating"),
                "friend_watch_count": showing.get("friend_watch_count", 0),
                "friend_avg_rating": showing.get("friend_avg_rating"),
                "friend_watchers": showing.get("friend_watchers", []),
                "rating_components": showing.get("rating_components", []),
                "special_event": showing.get("special_event", False),
                "special_event_reason": showing.get("special_event_reason"),
                "showings": [],
            }
        films[key]["showings"].append(
            {
                "date": showing["date"],
                "time": showing["time"],
                "ticket_url": showing["ticket_url"],
                "sold_out": showing.get("sold_out", False),
            }
        )

    film_list = list(films.values())

    def sort_key(film: dict):
        watchlist_order = 0 if film["on_watchlist"] else 1
        friend_watch_count = film["friend_watch_count"] if film["friend_watch_count"] is not None else -1
        friend_avg_rating = film["friend_avg_rating"] if film["friend_avg_rating"] is not None else -1
        public_rating = film["letterboxd_rating"] if film["letterboxd_rating"] is not None else -1
        return (watchlist_order, -friend_watch_count, -friend_avg_rating, -public_rating, film["title"])

    film_list.sort(key=sort_key)
    return film_list


def build_schedule_payload(db: Session) -> dict:
    _log("🎬 Scraping Metrograph schedule...")
    showings = scrape_schedule()
    _log(f"  Found {len(showings)} total showings.")

    _log("🗃️ Preparing base schedule payload...")
    showings = enrich_showings_from_db(showings, db, user=None)

    _log("📋 Grouping by film...")
    films = group_by_film(showings)
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "films": films,
    }


def get_stored_schedule_payload(db: Session, snapshot_key: str = SCHEDULE_SNAPSHOT_KEY) -> Optional[dict]:
    snapshot = db.query(MovieScheduleSnapshot).filter(MovieScheduleSnapshot.snapshot_key == snapshot_key).first()
    if snapshot is None:
        return None
    return snapshot.payload


def get_schedule_payload_for_user(db: Session, user: Optional[MovieUser]) -> dict:
    payload = get_stored_schedule_payload(db)
    if payload is None:
        payload = store_schedule_payload(db, build_schedule_payload(db))

    return merge_schedule_payload_with_db(json.loads(json.dumps(payload)), db, user=user)


def store_schedule_payload(db: Session, payload: dict, snapshot_key: str = SCHEDULE_SNAPSHOT_KEY) -> dict:
    snapshot = db.query(MovieScheduleSnapshot).filter(MovieScheduleSnapshot.snapshot_key == snapshot_key).first()
    stored_payload = json.loads(json.dumps(payload))
    stored_payload["updated_at"] = datetime.now(timezone.utc).isoformat()

    if snapshot is None:
        snapshot = MovieScheduleSnapshot(
            snapshot_key=snapshot_key,
            payload=stored_payload,
            updated_at=datetime.now(timezone.utc),
        )
        db.add(snapshot)
    else:
        snapshot.payload = stored_payload
        snapshot.updated_at = datetime.now(timezone.utc)

    db.commit()
    return stored_payload


def _collect_new_watchlist_films(schedule_payload: dict, new_watchlist_entries: list[dict]) -> list[dict]:
    if not new_watchlist_entries:
        return []

    new_entry_keys = {
        (entry["normalized_title"], entry.get("year"))
        for entry in new_watchlist_entries
    }

    matches = []
    seen_titles = set()
    for film in schedule_payload.get("films", []):
        components = film.get("rating_components") or [
            {
                "search_title": film.get("title"),
                "year": film.get("year"),
                "on_watchlist": film.get("on_watchlist", False),
            }
        ]

        if not any(
            component.get("on_watchlist")
            and (_norm(component.get("search_title") or ""), component.get("year", film.get("year"))) in new_entry_keys
            for component in components
        ):
            continue

        dedupe_key = (film.get("title"), film.get("director"))
        if dedupe_key in seen_titles:
            continue
        seen_titles.add(dedupe_key)

        matches.append(
            {
                "title": film.get("title"),
                "director": film.get("director") or "Unknown Director",
                "special_event": film.get("special_event", False),
            }
        )

    return matches


def _send_movie_sync_sms(user: MovieUser, sync_result: dict) -> None:
    if not user.phone_number:
        return

    new_watchlist_films = sync_result.get("new_watchlist_films", [])
    highlighted_titles = ", ".join(film["title"] for film in new_watchlist_films[:3]) if new_watchlist_films else "None"
    if len(new_watchlist_films) > 3:
        highlighted_titles = f"{highlighted_titles}, +{len(new_watchlist_films) - 3} more"

    message_body = (
        f"Movie sync finished for {user.username}.\n"
        f"Letterboxd: {user.letterboxd_username}\n"
        f"Updated titles: {sync_result.get('updated_movies', 0)}\n"
        f"Skipped recent titles: {sync_result.get('skipped_movies', 0)}\n"
        f"New watchlist matches: {len(new_watchlist_films)}\n"
        f"Highlights: {highlighted_titles}"
    )

    try:
        send_text_message(user.phone_number, message_body)
    except Exception as error:
        _log(f"  ⚠️  SMS notification failed for {user.username}: {error}")


def run_movie_refresh_pipeline(
    db: Session,
    user: Optional[MovieUser] = None,
    *,
    send_sms: bool = False,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    active_user = user or get_or_create_default_movie_user(db)
    if progress_callback is not None:
        progress_callback(f"Starting Letterboxd refresh for @{active_user.letterboxd_username}.")

    active_user = set_movie_user_sync_state(db, active_user, sync_in_progress=True)

    try:
        sync_result = update_letterboxd_table(db, active_user, progress_callback=progress_callback)
        schedule_payload = build_schedule_payload(db)
        stored_schedule = store_schedule_payload(db, schedule_payload)
        user_schedule = merge_schedule_payload_with_db(json.loads(json.dumps(stored_schedule)), db, user=active_user)
        new_watchlist_films = _collect_new_watchlist_films(
            user_schedule,
            sync_result.get("new_watchlist_entries", []),
        )

        sync_result["schedule_updated_at"] = stored_schedule.get("updated_at")
        sync_result["new_watchlist_films"] = new_watchlist_films
        active_user = set_movie_user_sync_state(db, active_user, sync_in_progress=False, friend_sync_pending=False)
        sync_result["user"] = _serialize_movie_user(active_user)
        if send_sms:
            _send_movie_sync_sms(active_user, sync_result)
        if progress_callback is not None:
            progress_callback(f"Stored friend data and refreshed Metrograph data for {active_user.username}.")
        return sync_result
    except Exception:
        active_user = get_movie_user(db, active_user.username) or active_user
        set_movie_user_sync_state(db, active_user, sync_in_progress=False)
        raise


def update_letterboxd_table(
    db: Session,
    user: Optional[MovieUser] = None,
    *,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    if not ENABLE_LETTERBOXD:
        return {
            "enabled": False,
            "message": "Letterboxd syncing is disabled by ENABLE_LETTERBOXD.",
            "updated_movies": 0,
            "new_watchlist_entries": [],
        }

    active_user = user or get_or_create_default_movie_user(db)

    _log("  Fetching Letterboxd watchlist...")
    watchlist = _fetch_letterboxd_watchlist(active_user.letterboxd_username)
    friend_profiles = _refresh_movie_user_friends(db, active_user, progress_callback=progress_callback)
    _log(f"  Loaded {len(friend_profiles)} friend profiles.")
    if progress_callback is not None:
        progress_callback("Scraping Metrograph titles for this account.")

    _log("🎬 Scraping Metrograph titles for Letterboxd sync...")
    showings = scrape_schedule()
    unique_films = sorted({(showing["title"], showing.get("year")) for showing in showings}, key=lambda item: (item[0], item[1] or 0))
    _log(f"  Found {len(unique_films)} unique Metrograph films to scan.")
    if progress_callback is not None:
        progress_callback("Syncing friend list with Metrograph showings...")

    processed_components = set()
    updated_movies = 0
    skipped_movies = 0
    new_watchlist_entries = []
    for index, (title, year) in enumerate(unique_films, start=1):
        _log(f"  [{index}/{len(unique_films)}] Syncing {title} ({year or 'unknown'})")

        film_director = next((showing.get("director") for showing in showings if showing["title"] == title and showing.get("year") == year), None)

        title_context = _parse_title_context(title)
        for component in title_context["components"]:
            normalized_title = _norm(component["search_title"])
            component_key = (normalized_title, year)
            if component_key in processed_components:
                continue

            processed_components.add(component_key)
            _log(f"    Component sync: {component['title']}")

            entry = _find_movie_entry(db, active_user.id, normalized_title, year)
            if _was_scanned_recently(entry):
                skipped_movies += 1
                _log(
                    f"    Skipping {component['title']} - scanned within the last {LETTERBOXD_REFRESH_SKIP_DAYS} days"
                )
                continue

            watchlist_path = watchlist.get(normalized_title)
            public_rating = _fetch_letterboxd_rating(component["search_title"], year, watchlist_path, director=film_director)
            personal = _fetch_member_film_data(db, active_user.letterboxd_username, component["search_title"], year, director=film_director)
            is_on_watchlist = normalized_title in watchlist

            is_new_entry = entry is None
            if entry is None:
                entry = MovieLetterboxdData(
                    user_id=active_user.id,
                    title=component["title"],
                    normalized_title=normalized_title,
                    year=year,
                )
                db.add(entry)
                db.flush()

            entry.title = component["title"]
            entry.normalized_title = normalized_title
            entry.year = year
            entry.letterboxd_rating = public_rating
            entry.on_watchlist = is_on_watchlist
            entry.watched = personal["watched"]
            entry.personal_rating = personal["personal_rating"]
            entry.last_scanned_at = datetime.now(timezone.utc)

            if is_new_entry and is_on_watchlist:
                new_watchlist_entries.append(
                    {
                        "title": component["title"],
                        "normalized_title": normalized_title,
                        "year": year,
                    }
                )

            entry.friend_ratings.clear()
            db.flush()

            for friend_index, profile in enumerate(friend_profiles, start=1):
                info = _fetch_member_film_data(db, profile["username"], component["search_title"], year, director=film_director)
                _log(
                    f"    Friend {friend_index}/{len(friend_profiles)} {profile['username']}: watched={info['watched']} rating={info['personal_rating']}"
                )
                if not info["watched"]:
                    time.sleep(0.15)
                    continue

                entry.friend_ratings.append(
                    MovieFriendRating(
                        friend_username=profile["username"],
                        friend_display_name=profile.get("display_name"),
                        rating=info["personal_rating"],
                    )
                )
                time.sleep(0.15)

            updated_movies += 1
            db.commit()
            time.sleep(0.25)

    return {
        "enabled": True,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_movies": updated_movies,
        "skipped_movies": skipped_movies,
        "friend_profiles": len(friend_profiles),
        "new_watchlist_entries": new_watchlist_entries,
        "user": _serialize_movie_user(active_user),
        "message": f"Updated stored Letterboxd data for {active_user.username}: {updated_movies} Metrograph films and skipped {skipped_movies} recently scanned films.",
    }


def run_movie_refresh_pipeline_for_username(
    username: str,
    *,
    send_sms: bool = False,
    progress_logs: bool = False,
    redirect_path: Optional[str] = None,
) -> None:
    init_message = f"Preparing sync for {username}."
    if progress_logs:
        append_sync_job_log(username, init_message)

    db = SessionLocal()
    try:
        user = get_movie_user(db, username)
        if user is None:
            raise LookupError("User not found.")

        callback = (lambda message: append_sync_job_log(username, message)) if progress_logs else None
        run_movie_refresh_pipeline(db, user, send_sms=send_sms, progress_callback=callback)
        if progress_logs:
            finish_sync_job(username, redirect_path=redirect_path)
    except Exception as error:
        if progress_logs:
            finish_sync_job(username, error=str(error))
        raise
    finally:
        db.close()


def run_nightly_movie_refreshes() -> list[dict]:
    init_db_needed = False
    results = []
    db = SessionLocal()
    try:
        users = list_movie_users(db)
        if not users:
            users = [get_or_create_default_movie_user(db)]

        for user in users:
            stored_friend_count = len(user.friends)
            if stored_friend_count < 1:
                _log(f"🔎 Rechecking following list for {user.username} because only {stored_friend_count} friends are stored.")
            result = run_movie_refresh_pipeline(db, user, send_sms=False)
            results.append(
                {
                    "username": user.username,
                    "updated_movies": result.get("updated_movies", 0),
                    "friend_profiles": result.get("friend_profiles", 0),
                    "schedule_updated_at": result.get("schedule_updated_at"),
                }
            )
        return results
    finally:
        db.close()