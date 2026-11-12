# Vidflix

A movie catalog featuring a **Similar Movies** recommendation system powered by **content-based filtering** (TF-IDF + cosine similarity).

> Status: Stage 0 — Repository foundation. The backend, frontend, and features have not been implemented yet.

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

## Repository Structure (Planned)

```text
Vidflix/
├── backend/       # FastAPI, models, Alembic migrations
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