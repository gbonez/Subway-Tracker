"""Railway worker entrypoint for queued movie sync jobs."""

from models import init_db
from services.movie_service import run_movie_sync_worker


if __name__ == "__main__":
    init_db()
    run_movie_sync_worker()