"""
Extract Letterboxd following profile URLs with a headless browser.

Usage:
  python scripts/extract_letterboxd_following.py

Prereqs:
  pip install playwright
  playwright install chromium
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright


FOLLOWING_URLS = [
    "https://letterboxd.com/gbonez100/following/",
    "https://letterboxd.com/gbonez100/following/page/2/",
]

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "letterboxd_friends.json"


def extract_username(profile_url: str) -> str:
    path = urlparse(profile_url).path.strip("/")
    return path.split("/")[0] if path else ""


def collect_profiles(page) -> list[dict]:
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
        except PlaywrightTimeoutError:
            continue

    if not table_found:
        if page.locator("a.name").count() > 0:
            table_found = True

    if not table_found:
        raise RuntimeError("Could not find the Letterboxd following table on the page.")

    links = []
    summaries = page.locator(".person-summary")
    for index in range(summaries.count()):
        summary = summaries.nth(index)
        name_link = summary.locator("a.name").first
        if name_link.count() == 0:
            continue

        href = name_link.get_attribute("href")
        text = name_link.inner_text().strip()
        if not href:
            continue
        if href.startswith("/sign-in") or href.startswith("/search"):
            continue
        if any(segment in href for segment in ["/film/", "/list/", "/reviews/", "/likes/", "/diary/", "/following/", "/followers/"]):
            continue

        full_url = f"https://letterboxd.com{href}" if href.startswith("/") else href
        username = extract_username(full_url)
        if not username:
            continue

        links.append(
            {
                "username": username,
                "display_name": text or username,
                "profile_url": full_url,
            }
        )

    deduped = {}
    for item in links:
        deduped[item["username"]] = item

    return list(deduped.values())


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        all_profiles = {}
        visited_pages = []
        for page_index, url in enumerate(FOLLOWING_URLS, start=1):
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
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
            print(f"Visiting {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=120000)
            profiles = collect_profiles(page)
            print(f"  Found {len(profiles)} profiles")
            visited_pages.append(page.url)
            for profile in profiles:
                all_profiles[profile["username"]] = profile
            page.close()

        browser.close()

    sorted_profiles = sorted(all_profiles.values(), key=lambda item: item["username"].lower())

    with OUTPUT_PATH.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "source_pages": visited_pages,
                "count": len(sorted_profiles),
                "profiles": sorted_profiles,
            },
            handle,
            indent=2,
        )

    print(f"Wrote {len(sorted_profiles)} profiles to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()