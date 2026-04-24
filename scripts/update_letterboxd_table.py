"""Scan Letterboxd data for the current Metrograph slate and refresh the DB-backed schedule snapshot."""

from models import init_db
from services.movie_service import run_nightly_movie_refreshes


def main():
    init_db()
    results = run_nightly_movie_refreshes()
    print(f"Refreshed {len(results)} movie users")
    for result in results:
        print(
            f"- {result['username']}: {result['updated_movies']} titles updated, "
            f"{result['friend_profiles']} friends stored"
        )


if __name__ == "__main__":
    main()