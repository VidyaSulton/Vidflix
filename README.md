# Vidflix

A movie catalog featuring a **Similar Movies** recommendation system powered by **content-based filtering** (TF-IDF + cosine similarity).

> Status: Stage 2 — Data import tooling. API endpoints, recommendation training, and the frontend are not implemented yet.

## Tech Stack

| Layer | Technologies |
|---|---|
| Frontend | Vite, React, TypeScript, Tailwind CSS, React Router, TanStack Query, Embla Carousel |
| Backend | Python 3.11+, FastAPI, Pydantic, SQLAlchemy, Alembic, PyJWT, passlib (bcrypt) |
| ML | pandas, numpy, scikit-learn (TF-IDF + cosine similarity) |
| Database | PostgreSQL |
| DevOps | Docker, Docker Compose |
| Frontend Package Manager | pnpm |

## Prerequisites

- Docker Desktop (Docker Compose)
- Python 3.11+ (this project is developed using Python 3.13)
- Node.js LTS + pnpm
- The `ml-latest-small` dataset from GroupLens in the `data/` directory
- TMDB API key

## Initial Setup

1. Copy `.env.example` to `.env`, then configure the database credentials, `JWT_SECRET`, and `TMDB_API_KEY`.

   ```powershell
   Copy-Item .env.example .env
   ```

2. Start PostgreSQL:

   ```powershell
   docker compose up -d db
   ```

3. Verify that the database is healthy:

   ```powershell
   docker compose ps
   ```

   The `db` service status should be `healthy`.

## Backend

Create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
```

Review the migration in `backend/alembic/versions/`, then apply it:

```powershell
cd backend
..\.venv\Scripts\alembic.exe upgrade head
```

Run the API in development mode:

```powershell
cd backend
..\.venv\Scripts\uvicorn.exe app.main:app --reload
```

- API docs: <http://localhost:8000/docs>
- OpenAPI JSON: <http://localhost:8000/openapi.json>

The backend reads `DATABASE_URL`, `JWT_SECRET`, and other settings from `.env`
at the repository root.

## Data Import

`scripts/import_data.py` loads the MovieLens CSV files into PostgreSQL and then
fetches movie overviews and posters from TMDB.

What it does, in order:

1. Upserts `movies` from `movies.csv`, parsing `Toy Story (1995)` into
   `title = "Toy Story"` and `year = 1995`.
2. Attaches `tmdb_id` from `links.csv`.
3. Upserts `movielens_ratings` from `ratings.csv` (the `timestamp` column is not stored).
4. Fetches `overview` and `poster_path` from TMDB for movies that have a `tmdb_id`.

The script is idempotent: running it again updates existing rows instead of
creating duplicates. Rows are committed in batches, so an interrupted run can be
resumed by running the same command again.

```powershell
# 1. CSV only, no network calls (fast)
.\.venv\Scripts\python.exe scripts\import_data.py --skip-tmdb

# 2. Quick TMDB test on the first 20 movies that still need metadata
.\.venv\Scripts\python.exe scripts\import_data.py --limit 20

# 3. Full import (CSV data + TMDB enrichment)
.\.venv\Scripts\python.exe scripts\import_data.py
```

Useful flags:

| Flag | Meaning |
|---|---|
| `--limit N` | Enrich at most `N` movies with TMDB data. |
| `--skip-tmdb` | Import movies and ratings only; never call TMDB. |
| `--refresh-tmdb` | Re-fetch TMDB metadata even when overview/poster already exist. |

Notes:

- `TMDB_API_KEY` may be a v3 API key or a v4 read access token; the script detects which one to use.
- Requests are spaced out and retried on rate limits, network errors, and 5xx responses.
- Enriching roughly 9,700 movies usually takes 10–30 minutes depending on the network.
- Movies whose TMDB entry has neither an overview nor a poster stay empty and are
  retried on the next run. Movies with at least one of the two are treated as done.
- Re-running the CSV import never erases TMDB metadata that was already fetched.

Verify the result:

```sql
SELECT COUNT(*) FROM movies;
SELECT COUNT(*) FROM movielens_ratings;
SELECT COUNT(*) FROM movies WHERE tmdb_id IS NULL;
SELECT COUNT(*) FROM movies WHERE overview IS NULL;
SELECT COUNT(*) FROM movies WHERE poster_path IS NULL;
```

`notebooks/01_data_exploration.ipynb` records the CSV checks that shaped the
import (missing values, year parsing, genre coverage, `tmdb_id` coverage, and
rating statistics). Notebook tooling (`ipykernel`, `nbconvert`) is
development-only and is not part of `backend/requirements.txt`.

## Repository Structure (Planned)

```text
Vidflix/
├── backend/       # FastAPI app, SQLAlchemy models, Alembic migrations
├── frontend/      # React + Vite
├── scripts/       # import_data.py, train_content_based.py
├── notebooks/     # Content-based exploration and experiments
├── docs/          # Recommendation evaluation
├── ml/            # Training configuration
└── data/          # MovieLens CSV files (not committed to the repository)
```

## Data & Attribution

- Dataset: [MovieLens ml-latest-small](https://grouplens.org/datasets/movielens/latest/), provided by GroupLens Research.
- Movie metadata (posters, overviews): [TMDB](https://www.themoviedb.org/). This product uses the TMDB API but is not endorsed or certified by TMDB.