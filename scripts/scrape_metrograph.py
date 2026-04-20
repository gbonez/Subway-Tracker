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
from datetime import date
from typing import Optional
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCHEDULE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "metrograph_schedule.json")
LETTERBOXD_USERNAME = "gbonez100"
ENABLE_LETTERBOXD = os.getenv("ENABLE_LETTERBOXD", "false").lower() == "true"
METROGRAPH_CALENDAR_URL = "https://metrograph.com/nyc/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


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
    """Return a set of normalised film titles on the user's Letterboxd watchlist."""
    watchlist_titles: set[str] = set()
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
        # Each film entry: <li class="poster-container"> with data-film-slug or <span class="frame-title">
        films = soup.select("li.poster-container")
        if not films:
            break
        for li in films:
            img = li.find("img", alt=True)
            if img:
                watchlist_titles.add(_norm(img["alt"]))
        # Check if there's a next page
        if not soup.select_one("a.next"):
            break
        page += 1
        time.sleep(0.4)

    return watchlist_titles


def _fetch_letterboxd_ratings(title: str, year: Optional[int]) -> Optional[float]:
    """Fetch the public average rating for a film from Letterboxd."""
    # Letterboxd film pages are at /film/<slug>/ — slug is derived from title
    # Search is more reliable
    query = f"{title} {year}" if year else title
    search_url = f"https://letterboxd.com/search/films/{quote(query)}/"
    try:
        resp = requests.get(search_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    # The first result's rating is in <span class="average-rating"><a ...>X.XX</a></span>
    first_result = soup.select_one("li.search-result")
    if not first_result:
        return None

    # Validate title match roughly
    result_title_el = first_result.select_one("span.film-title-wrapper a") or first_result.select_one("h2.name a")
    if result_title_el:
        result_title = _norm(result_title_el.get_text(strip=True))
        if result_title and _norm(title) not in result_title and result_title not in _norm(title):
            return None

    rating_el = first_result.select_one("span.average-rating a") or first_result.select_one(".average-rating")
    if rating_el:
        try:
            return float(rating_el.get_text(strip=True))
        except ValueError:
            pass
    return None


def _norm(s: str) -> str:
    """Normalise a title string for comparison."""
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def enrich_with_letterboxd(showings: list[dict]) -> list[dict]:
    """Add on_watchlist and letterboxd_rating fields to each showing."""
    if not ENABLE_LETTERBOXD:
        for s in showings:
            s["on_watchlist"] = False
            s["letterboxd_rating"] = None
        return showings

    print("  Fetching Letterboxd watchlist...")
    watchlist = _fetch_letterboxd_watchlist(LETTERBOXD_USERNAME)
    print(f"  Found {len(watchlist)} titles on watchlist.")

    # Deduplicate film lookups (title+year)
    film_ratings: dict[tuple, Optional[float]] = {}
    unique_films = {(s["title"], s["year"]) for s in showings}

    print(f"  Fetching Letterboxd ratings for {len(unique_films)} unique films...")
    for title, year in unique_films:
        key = (title, year)
        if key not in film_ratings:
            film_ratings[key] = _fetch_letterboxd_ratings(title, year)
            time.sleep(0.3)

    for s in showings:
        s["on_watchlist"] = _norm(s["title"]) in watchlist
        s["letterboxd_rating"] = film_ratings.get((s["title"], s["year"]))

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
        rating = f["letterboxd_rating"] if f["letterboxd_rating"] is not None else -1
        return (watchlist_order, -rating, f["title"])

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
