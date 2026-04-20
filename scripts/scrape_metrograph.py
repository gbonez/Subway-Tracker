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
from datetime import date, timedelta
from typing import Optional
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCHEDULE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "metrograph_schedule.json")
LETTERBOXD_USERNAME = "gbonez100"
# How many days ahead to scrape (Metrograph typically shows ~2 weeks)
DAYS_AHEAD = 30

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

def scrape_day(target_date: date) -> list[dict]:
    """Scrape all showings for a single date. Returns list of showing dicts."""
    url = f"https://metrograph.com/nyc/?date={target_date.isoformat()}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ⚠️  Failed to fetch {url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    showings = []

    # Each film block is an <article> or a section anchored by an <h4> with a link
    # The page structure groups by film within the day:
    #   <h4><a href="/film/?vista_film_id=...">TITLE</a></h4>
    #   <p> Director / Year / Runtime / Format  [optional special note] </p>
    #   show times are <a> tags linking to t.metrograph.com ticketing
    #   or bare text nodes for sold-out / no-ticket times

    # Find all film headings
    film_headings = soup.find_all("h4")
    for h4 in film_headings:
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

        # The metadata and times are siblings after the h4
        meta_text = ""
        times = []
        ticket_links = []

        # Walk siblings until next h4 or end
        for sibling in h4.next_siblings:
            if sibling.name == "h4":
                break
            if sibling.name in ("p", "div", None):
                text = sibling.get_text(" ", strip=True) if hasattr(sibling, "get_text") else str(sibling).strip()
                if not meta_text and ("/" in text or "min" in text.lower()):
                    meta_text = text
            # Ticket time links
            if hasattr(sibling, "find_all"):
                for a in sibling.find_all("a", href=True):
                    href = a["href"]
                    t = a.get_text(strip=True)
                    if "t.metrograph.com" in href and t:
                        times.append(t)
                        ticket_links.append(href)
                # Also catch bare time text nodes not wrapped in <a>
                for txt_node in sibling.find_all(string=True, recursive=False):
                    t = txt_node.strip()
                    if re.match(r"\d{1,2}:\d{2}(am|pm)", t, re.IGNORECASE):
                        if t not in times:
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
                "date": target_date.isoformat(),
                "title": title,
                "film_id": film_id,
                "film_url": f"https://metrograph.com{film_url}" if film_url.startswith("/") else film_url,
                "director": director,
                "year": year,
                "runtime": runtime,
                "format": fmt,
                "time": t,
                "ticket_url": ticket_links[i] if i < len(ticket_links) else None,
            })

    return showings


def scrape_schedule() -> list[dict]:
    """Scrape all upcoming days. Returns deduplicated list of showings."""
    today = date.today()
    all_showings = []
    seen = set()

    for delta in range(DAYS_AHEAD):
        d = today + timedelta(days=delta)
        print(f"  Scraping {d.isoformat()}...")
        day_showings = scrape_day(d)
        for s in day_showings:
            key = (s["date"], s["title"], s["time"])
            if key not in seen:
                seen.add(key)
                all_showings.append(s)
        # Be polite
        time.sleep(0.5)

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
    print(f"  Found {len(showings)} total showings across {DAYS_AHEAD} days.")

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
