"""Railway worker entrypoint for queued movie sync jobs."""

import os

from models import DATABASE_URL, engine, init_db
from services.movie_service import run_movie_sync_worker


if __name__ == "__main__":
    app_role = os.getenv("APP_ROLE", "web")
    print(f"🎬 Starting movie worker with APP_ROLE={app_role}", flush=True)
    print(f"🎬 Worker database dialect: {engine.dialect.name}", flush=True)
    if DATABASE_URL.startswith("sqlite"):
        print(
            "⚠️  Worker is using SQLite. If your web service is on Railway Postgres, this worker will never see queued jobs. "
            "Attach the same Postgres database to the worker service.",
            flush=True,
        )

    init_db()
    run_movie_sync_worker()