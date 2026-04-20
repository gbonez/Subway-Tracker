"""
Metrograph Schedule Scraper
----------------------------
Fetches the full upcoming schedule from metrograph.com/nyc/?date=YYYY-MM-DD,
enriches each film with its Letterboxd watchlist status and public rating,
then writes the result to data/metrograph_schedule.json.

Run on a cron job (e.g. daily at midnight EST).
"""

import json
import os
import re
import time
import unicodedata
from difflib import SequenceMatcher
from datetime import date
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
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
    """Print a consistent scraper log line."""
    print(message, flush=True)


# ---------------------------------------------------------------------------
# Metrograph scraping
# ---------------------------------------------------------------------------

def fetch_calendar_page() -> BeautifulSoup:
    """Fetch Metrograph's public calendar page once and return a parsed soup."""
    try:
        resp = requests.get(METROGRAPH_CALENDAR_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Failed to fetch Metrograph calendar: {e}") from e

    return BeautifulSoup(resp.text, "html.parser")


def parse_day_block(day_block) -> list[dict]:
    """Parse one Metrograph calendar day block into showing dictionaries."""
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

        # Extract vista_film_id from href
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
                for a in time_links:
                    t = a.get_text(strip=True)
                    href = a["href"]
                    if t:
                        times.append(t)
                        ticket_links.append(href)
            else:
                raw_text = showtimes_el.get_text(" ", strip=True)
                for t in re.findall(r"\d{1,2}:\d{2}(?:am|pm)", raw_text, re.IGNORECASE):
                    times.append(t)
                    ticket_links.append(None)

        # Parse director, year, runtime, format from meta_text
        # e.g. "Andrei Tarkovsky / 1983 / 125min / 4K DCP"
        director = year = runtime = fmt = None
        parts = [p.strip() for p in meta_text.split("/")]
        if len(parts) >= 1:
            director = parts[0]
        if len(parts) >= 2:
            year_match = re.search(r"\d{4}", parts[1])
            year = int(year_match.group()) if year_match else None
        if len(parts) >= 3:
            runtime = parts[2]
        if len(parts) >= 4:
            fmt = "/".join(parts[3:]).strip()

        for i, t in enumerate(times):
            showings.append({
                "date": target_date,
                "title": title,
                "film_id": film_id,
                "film_url": f"https://metrograph.com{film_url}" if film_url.startswith("/") else film_url,
                "director": director,
                "year": year,
                "runtime": runtime,
                "format": fmt,
                "description": description,
                "time": t,
                "ticket_url": ticket_links[i] if i < len(ticket_links) else None,
            })

    return showings


def scrape_schedule() -> list[dict]:
    """Scrape the public Metrograph calendar page and return deduplicated showings."""
    soup = fetch_calendar_page()
    all_showings = []
    seen = set()

    day_blocks = soup.select("div.calendar-list-day")
    print(f"  Found {len(day_blocks)} calendar day blocks.")
    for day_block in day_blocks:
        day_showings = parse_day_block(day_block)
        for s in day_showings:
            key = (s["date"], s["title"], s["time"])
            if key not in seen:
                seen.add(key)
                all_showings.append(s)

    return all_showings


# ---------------------------------------------------------------------------
# Letterboxd scraping
# ---------------------------------------------------------------------------

def _fetch_letterboxd_watchlist(username: str) -> set[str]:
    """Return a map of normalised watchlist film titles to their public Letterboxd links."""
    watchlist_titles: dict[str, str] = {}
    page = 1
    while True:
        url = f"https://letterboxd.com/{username}/watchlist/page/{page}/"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 404:
                break
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  ⚠️  Letterboxd watchlist fetch failed (page {page}): {e}")
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
        # Check if there's a next page
        if not soup.select_one("a.next"):
            break
        page += 1
        time.sleep(0.4)

    return watchlist_titles


def _load_friend_usernames() -> list[str]:
    """Load Letterboxd friend usernames from the saved JSON, with env var fallback."""
    if os.path.exists(LETTERBOXD_FRIENDS_PATH):
        try:
            with open(LETTERBOXD_FRIENDS_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)

            profiles = data.get("profiles", [])
            usernames = []
            for profile in profiles:
                username = profile.get("username", "").strip()
                if username:
                    usernames.append(username)

            if usernames:
                return usernames
        except (OSError, json.JSONDecodeError) as error:
            print(f"  ⚠️  Failed to load {LETTERBOXD_FRIENDS_PATH}: {error}")

    return LETTERBOXD_FRIEND_USERNAMES_ENV


def _slugify_title(title: str) -> str:
    """Convert a movie title into a likely Letterboxd slug."""
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"\[[^\]]*\]|\([^\)]*\)", " ", normalized)
    normalized = normalized.replace("&", " and ")
    normalized = normalized.replace("'", "")
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return re.sub(r"-+", "-", normalized)


def _generate_slug_candidates(title: str, year: Optional[int]) -> list[str]:
    """Generate best-effort Letterboxd film-path candidates for a title."""
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


def _extract_rating_from_film_page(soup: BeautifulSoup) -> Optional[float]:
    """Extract Letterboxd public average rating from a film page soup."""
    meta_rating = soup.select_one('meta[name="twitter:data2"]')
    if meta_rating:
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)", meta_rating.get("content", ""))
        if match:
            return float(match.group(1))

    scripts = soup.select('script[type="application/ld+json"]')
    for script in scripts:
        text = script.get_text(strip=True)
        match = re.search(r'"ratingValue"\s*:\s*([0-9]+(?:\.[0-9]+)?)', text)
        if match:
            return float(match.group(1))

    return None


def _extract_star_rating(star_text: str) -> Optional[float]:
    """Convert Letterboxd star glyphs into a 0.5-step numeric rating."""
    if not star_text:
        return None

    star_text = star_text.strip()
    full_stars = star_text.count("★")
    half_star = 0.5 if "½" in star_text else 0.0
    if full_stars == 0 and half_star == 0:
        return None
    return full_stars + half_star


def _validate_film_match(soup: BeautifulSoup, title: str, year: Optional[int]) -> bool:
    """Check that a fetched Letterboxd film page matches the intended movie."""
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
    """Check that a user-specific film page matches the intended movie."""
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
    """Fetch the public average rating for a film from its Letterboxd page."""
    candidate_paths = []

    if film_path:
        candidate_paths.append(film_path)

    candidate_paths.extend(_generate_slug_candidates(title, year))

    seen_paths = set()
    for path in candidate_paths:
        if path in seen_paths:
            continue
        seen_paths.add(path)

        url = f"{LETTERBOXD_BASE_URL}{path}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
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
    """Fetch watched status and personal rating from a public user-specific film page."""
    for path in _generate_slug_candidates(title, year):
        url = f"{LETTERBOXD_BASE_URL}/{username}{path}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
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


def _norm(s: str) -> str:
    """Normalise a title string for comparison."""
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def enrich_with_letterboxd(showings: list[dict]) -> list[dict]:
    """Add on_watchlist and letterboxd_rating fields to each showing."""
    if not ENABLE_LETTERBOXD:
        for s in showings:
            s["on_watchlist"] = False
            s["letterboxd_rating"] = None
            s["watched"] = False
            s["personal_rating"] = None
            s["friend_watch_count"] = 0
            s["friend_avg_rating"] = None
        return showings

    _log("  Fetching Letterboxd watchlist...")
    watchlist = _fetch_letterboxd_watchlist(LETTERBOXD_USERNAME)
    _log(f"  Found {len(watchlist)} titles on watchlist.")

    friend_usernames = _load_friend_usernames()
    _log(f"  Using {len(friend_usernames)} friend usernames for network metrics.")

    # Deduplicate film lookups (title+year)
    film_ratings: dict[tuple, Optional[float]] = {}
    member_data: dict[tuple, dict] = {}
    friend_data: dict[tuple, dict] = {}
    unique_films = sorted({(s["title"], s["year"]) for s in showings}, key=lambda item: (item[0], item[1] or 0))

    _log(f"  Fetching Letterboxd ratings for {len(unique_films)} unique films...")
    for film_index, (title, year) in enumerate(unique_films, start=1):
        key = (title, year)
        _log(f"  [{film_index}/{len(unique_films)}] {title} ({year or 'unknown'})")

        if key not in film_ratings:
            watchlist_path = watchlist.get(_norm(title))
            _log("    Fetching public rating...")
            film_ratings[key] = _fetch_letterboxd_rating(title, year, watchlist_path)
            _log(f"    Public rating fetched: {film_ratings[key]}")
            time.sleep(0.3)

    watchlist_keys = {film_key for film_key in film_ratings if _norm(film_key[0]) in watchlist}
    top_public_rated_keys = {
        film_key
        for film_key, _ in sorted(
            (
                (film_key, rating)
                for film_key, rating in film_ratings.items()
                if rating is not None and film_key not in watchlist_keys
            ),
            key=lambda item: item[1],
            reverse=True,
        )[:10]
    }
    targeted_keys = watchlist_keys | top_public_rated_keys

    _log(
        "  Targeted films for personal/friend checks: "
        f"{len(targeted_keys)} ({len(watchlist_keys)} watchlist, {len(top_public_rated_keys)} top public non-watchlist)"
    )

    for film_index, (title, year) in enumerate(unique_films, start=1):
        key = (title, year)
        if key not in targeted_keys:
            _log(f"  [{film_index}/{len(unique_films)}] {title} ({year or 'unknown'}) -> skipping personal/friend checks")
            member_data[key] = {"watched": False, "personal_rating": None}
            friend_data[key] = {"friend_watch_count": 0, "friend_avg_rating": None}
            continue

        _log(f"  [{film_index}/{len(unique_films)}] {title} ({year or 'unknown'}) -> targeted")

        _log("    Fetching personal watch/rating...")
        member_data[key] = _fetch_member_film_data(LETTERBOXD_USERNAME, title, year)
        _log(f"    Personal fetched: watched={member_data[key]['watched']} rating={member_data[key]['personal_rating']}")
        time.sleep(0.2)

        watched_count = 0
        ratings = []
        for friend_index, friend_username in enumerate(friend_usernames, start=1):
            _log(f"    Friend {friend_index}/{len(friend_usernames)} {friend_username}: fetching...")
            info = _fetch_member_film_data(friend_username, title, year)
            _log(f"    Friend {friend_index}/{len(friend_usernames)} {friend_username}: watched={info['watched']} rating={info['personal_rating']}")

            if info["watched"]:
                watched_count += 1
                if info["personal_rating"] is not None:
                    ratings.append(info["personal_rating"])
            time.sleep(0.15)

        friend_data[key] = {
            "friend_watch_count": watched_count,
            "friend_avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        }
        _log(
            f"    Friend summary: watched_count={friend_data[key]['friend_watch_count']} friend_avg_rating={friend_data[key]['friend_avg_rating']}"
        )

    for s in showings:
        s["on_watchlist"] = _norm(s["title"]) in watchlist
        s["letterboxd_rating"] = film_ratings.get((s["title"], s["year"]))
        s["watched"] = member_data.get((s["title"], s["year"]), {}).get("watched", False)
        s["personal_rating"] = member_data.get((s["title"], s["year"]), {}).get("personal_rating")
        s["friend_watch_count"] = friend_data.get((s["title"], s["year"]), {}).get("friend_watch_count", 0)
        s["friend_avg_rating"] = friend_data.get((s["title"], s["year"]), {}).get("friend_avg_rating")

    return showings


# ---------------------------------------------------------------------------
# Group by film
# ---------------------------------------------------------------------------

def group_by_film(showings: list[dict]) -> list[dict]:
    """
    Collapse the flat showings list into one entry per film,
    with a nested list of {date, time, ticket_url} showings.
    """
    films: dict[str, dict] = {}
    for s in showings:
        key = s["title"]
        if key not in films:
            films[key] = {
                "title": s["title"],
                "film_id": s["film_id"],
                "film_url": s["film_url"],
                "director": s["director"],
                "year": s["year"],
                "runtime": s["runtime"],
                "format": s["format"],
                "on_watchlist": s.get("on_watchlist", False),
                "letterboxd_rating": s.get("letterboxd_rating"),
                "watched": s.get("watched", False),
                "personal_rating": s.get("personal_rating"),
                "friend_watch_count": s.get("friend_watch_count", 0),
                "friend_avg_rating": s.get("friend_avg_rating"),
                "showings": [],
            }
        films[key]["showings"].append({
            "date": s["date"],
            "time": s["time"],
            "ticket_url": s["ticket_url"],
        })

    film_list = list(films.values())

    # Sort: watchlist first, then by letterboxd_rating desc (None last), then alpha
    def sort_key(f):
        watchlist_order = 0 if f["on_watchlist"] else 1
        friend_watch_count = f["friend_watch_count"] if f["friend_watch_count"] is not None else -1
        friend_avg_rating = f["friend_avg_rating"] if f["friend_avg_rating"] is not None else -1
        public_rating = f["letterboxd_rating"] if f["letterboxd_rating"] is not None else -1
        return (watchlist_order, -friend_watch_count, -friend_avg_rating, -public_rating, f["title"])

    film_list.sort(key=sort_key)
    return film_list


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("🎬 Scraping Metrograph schedule...")
    showings = scrape_schedule()
    print(f"  Found {len(showings)} total showings.")

    print("🔤 Enriching with Letterboxd data...")
    showings = enrich_with_letterboxd(showings)

    print("📋 Grouping by film...")
    films = group_by_film(showings)

    out_path = os.path.abspath(SCHEDULE_PATH)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "updated_at": date.today().isoformat(),
            "films": films,
        }, f, indent=2)

    print(f"✅ Wrote {len(films)} films to {out_path}")


if __name__ == "__main__":
    main()
