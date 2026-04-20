"""Movie services for Metrograph schedule scraping and stored Letterboxd data."""

import json
import os
import re
import time
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from typing import Optional

import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session, selectinload

from models import MovieFriendRating, MovieLetterboxdData

SCHEDULE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "metrograph_schedule.json")
LETTERBOXD_FRIENDS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "letterboxd_friends.json")
LETTERBOXD_USERNAME = "gbonez100"
ENABLE_LETTERBOXD = os.getenv("ENABLE_LETTERBOXD", "true").lower() == "true"
METROGRAPH_CALENDAR_URL = "https://metrograph.com/nyc/"
LETTERBOXD_BASE_URL = "https://letterboxd.com"
LETTERBOXD_FRIEND_USERNAMES_ENV = [
    username.strip()
    for username in os.getenv("LETTERBOXD_FRIEND_USERNAMES", "").split(",")
    if username.strip()
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def _log(message: str) -> None:
    print(message, flush=True)


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", value.lower()).strip()


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
                    "ticket_url": ticket_links[index] if index < len(ticket_links) else None,
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


def _validate_film_match(soup: BeautifulSoup, title: str, year: Optional[int]) -> bool:
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
        if year_match and int(year_match.group(1)) != year:
            return False

    return True


def _validate_member_film_match(soup: BeautifulSoup, title: str, year: Optional[int]) -> bool:
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

    if year is not None and extracted_year is not None and extracted_year != year:
        return False

    return True


def _fetch_letterboxd_rating(title: str, year: Optional[int], film_path: Optional[str] = None) -> Optional[float]:
    candidate_paths = []
    if film_path:
        candidate_paths.append(film_path)
    candidate_paths.extend(_generate_slug_candidates(title, year))

    seen_paths = set()
    for path in candidate_paths:
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
        if not _validate_film_match(soup, title, year):
            continue

        rating = _extract_rating_from_film_page(soup)
        if rating is not None:
            return rating

    return None


def _fetch_member_film_data(username: str, title: str, year: Optional[int]) -> dict:
    for path in _generate_slug_candidates(title, year):
        try:
            resp = requests.get(f"{LETTERBOXD_BASE_URL}/{username}{path}", headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
        except requests.RequestException:
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        if not _validate_member_film_match(soup, title, year):
            continue

        rating_meta = soup.select_one('meta[name="twitter:data2"]')
        personal_rating = _extract_star_rating(rating_meta.get("content", "") if rating_meta else "")
        return {"watched": True, "personal_rating": personal_rating}

    return {"watched": False, "personal_rating": None}


def _find_movie_entry(db: Session, normalized_title: str, year: Optional[int]) -> Optional[MovieLetterboxdData]:
    query = db.query(MovieLetterboxdData).options(selectinload(MovieLetterboxdData.friend_ratings)).filter(
        MovieLetterboxdData.normalized_title == normalized_title
    )
    if year is None:
        return query.filter(MovieLetterboxdData.year.is_(None)).first()

    exact_match = query.filter(MovieLetterboxdData.year == year).first()
    if exact_match:
        return exact_match

    return query.filter(MovieLetterboxdData.year.is_(None)).first()


def _serialize_friend_rows(friend_rows: list[MovieFriendRating]) -> list[dict]:
    return [
        {
            "username": row.friend_username,
            "display_name": row.friend_display_name,
            "rating": row.rating,
        }
        for row in sorted(friend_rows, key=lambda row: (row.friend_display_name or row.friend_username or "").lower())
    ]


def enrich_showings_from_db(showings: list[dict], db: Session) -> list[dict]:
    entries = db.query(MovieLetterboxdData).options(selectinload(MovieLetterboxdData.friend_ratings)).all()
    entries_by_key = {(entry.normalized_title, entry.year): entry for entry in entries}
    entries_by_title = defaultdict(list)
    for entry in entries:
        entries_by_title[entry.normalized_title].append(entry)

    for showing in showings:
        normalized_title = _norm(showing["title"])
        year = showing.get("year")

        entry = entries_by_key.get((normalized_title, year))
        if entry is None:
            matching_entries = entries_by_title.get(normalized_title, [])
            if len(matching_entries) == 1:
                entry = matching_entries[0]
            else:
                entry = next((candidate for candidate in matching_entries if candidate.year is None), None)

        if entry is None:
            showing["on_watchlist"] = False
            showing["letterboxd_rating"] = None
            showing["watched"] = False
            showing["personal_rating"] = None
            showing["friend_watch_count"] = 0
            showing["friend_avg_rating"] = None
            showing["friend_watchers"] = []
            continue

        rated_friend_values = [friend.rating for friend in entry.friend_ratings if friend.rating is not None]
        showing["on_watchlist"] = entry.on_watchlist
        showing["letterboxd_rating"] = entry.letterboxd_rating
        showing["watched"] = entry.watched
        showing["personal_rating"] = entry.personal_rating
        showing["friend_watch_count"] = len(entry.friend_ratings)
        showing["friend_avg_rating"] = round(sum(rated_friend_values) / len(rated_friend_values), 2) if rated_friend_values else None
        showing["friend_watchers"] = _serialize_friend_rows(entry.friend_ratings)

    return showings


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
                "showings": [],
            }
        films[key]["showings"].append(
            {
                "date": showing["date"],
                "time": showing["time"],
                "ticket_url": showing["ticket_url"],
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

    _log("🗃️ Merging stored Letterboxd data...")
    showings = enrich_showings_from_db(showings, db)

    _log("📋 Grouping by film...")
    films = group_by_film(showings)
    return {
        "updated_at": date.today().isoformat(),
        "films": films,
    }


def write_schedule_payload(payload: dict) -> str:
    out_path = os.path.abspath(SCHEDULE_PATH)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return out_path


def update_letterboxd_table(db: Session) -> dict:
    if not ENABLE_LETTERBOXD:
        return {
            "enabled": False,
            "message": "Letterboxd syncing is disabled by ENABLE_LETTERBOXD.",
            "updated_movies": 0,
        }

    _log("🎬 Scraping Metrograph titles for Letterboxd sync...")
    showings = scrape_schedule()
    unique_films = sorted({(showing["title"], showing.get("year")) for showing in showings}, key=lambda item: (item[0], item[1] or 0))
    _log(f"  Found {len(unique_films)} unique Metrograph films to scan.")

    _log("  Fetching Letterboxd watchlist...")
    watchlist = _fetch_letterboxd_watchlist(LETTERBOXD_USERNAME)
    friend_profiles = _load_friend_profiles()
    _log(f"  Loaded {len(friend_profiles)} friend profiles.")

    updated_movies = 0
    for index, (title, year) in enumerate(unique_films, start=1):
        normalized_title = _norm(title)
        _log(f"  [{index}/{len(unique_films)}] Syncing {title} ({year or 'unknown'})")

        watchlist_path = watchlist.get(normalized_title)
        public_rating = _fetch_letterboxd_rating(title, year, watchlist_path)
        personal = _fetch_member_film_data(LETTERBOXD_USERNAME, title, year)

        entry = _find_movie_entry(db, normalized_title, year)
        if entry is None:
            entry = MovieLetterboxdData(
                title=title,
                normalized_title=normalized_title,
                year=year,
            )
            db.add(entry)
            db.flush()

        entry.title = title
        entry.normalized_title = normalized_title
        entry.year = year
        entry.letterboxd_rating = public_rating
        entry.on_watchlist = normalized_title in watchlist
        entry.watched = personal["watched"]
        entry.personal_rating = personal["personal_rating"]
        entry.last_scanned_at = datetime.now(timezone.utc)

        entry.friend_ratings.clear()
        db.flush()

        for friend_index, profile in enumerate(friend_profiles, start=1):
            info = _fetch_member_film_data(profile["username"], title, year)
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
        "friend_profiles": len(friend_profiles),
        "message": f"Updated stored Letterboxd data for {updated_movies} Metrograph films.",
    }