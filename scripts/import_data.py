"""Import MovieLens CSV data into PostgreSQL and enrich it with TMDB metadata.

Run from the repository root:

    python scripts/import_data.py --skip-tmdb   # CSV data only
    python scripts/import_data.py --limit 20    # quick TMDB test
    python scripts/import_data.py               # full import

The script is idempotent: running it twice does not duplicate rows.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests
from sqlalchemy import func, inspect, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.models.movie import Movie  # noqa: E402
from app.models.movielens_rating import MovielensRating  # noqa: E402

DATA_DIR = ROOT_DIR / "data"
MOVIES_CSV = DATA_DIR / "movies.csv"
LINKS_CSV = DATA_DIR / "links.csv"
RATINGS_CSV = DATA_DIR / "ratings.csv"

MOVIE_BATCH_SIZE = 1_000
RATING_BATCH_SIZE = 5_000
TMDB_COMMIT_EVERY = 100
TMDB_DELAY_SECONDS = 0.05
TMDB_MAX_ATTEMPTS = 4
TMDB_BEARER_PREFIX = "eyJ"
NO_GENRES = "(no genres listed)"

# MovieLens keeps the release year inside the title: "Toy Story (1995)".
# Some titles have trailing spaces, so the string is stripped before matching.
TITLE_PATTERN = re.compile(r"^(?P<title>.*)\s+\((?P<year>\d{4})\)$")


@dataclass
class EnrichmentStats:
    requested: int = 0
    updated: int = 0
    without_data: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Import MovieLens movies and ratings into PostgreSQL, then enrich "
            "movie metadata from TMDB."
        )
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Enrich at most N movies with TMDB data (useful for a quick test).",
    )
    parser.add_argument(
        "--skip-tmdb",
        action="store_true",
        help="Import movies and ratings only; never call TMDB.",
    )
    parser.add_argument(
        "--refresh-tmdb",
        action="store_true",
        help="Re-fetch TMDB metadata even when overview/poster already exist.",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be greater than zero")
    return args


def check_prerequisites(use_tmdb: bool) -> None:
    """Stop early with a clear message when files, tables, or the API key are missing."""
    missing_files = [
        path.name for path in (MOVIES_CSV, LINKS_CSV, RATINGS_CSV) if not path.exists()
    ]
    if missing_files:
        sys.exit(f"Missing CSV file(s) in {DATA_DIR}: {', '.join(missing_files)}")

    tables = set(inspect(engine).get_table_names())
    required = {"movies", "movielens_ratings"}
    if not required.issubset(tables):
        sys.exit(
            "Required tables are missing. Run `alembic upgrade head` inside backend/ first."
        )

    if use_tmdb and not settings.tmdb_api_key:
        sys.exit("TMDB_API_KEY is not set in .env. Set it, or run with --skip-tmdb.")


def split_title_year(raw_title: str) -> tuple[str, int | None]:
    """Split 'Toy Story (1995)' into ('Toy Story', 1995)."""
    normalized = raw_title.strip()
    match = TITLE_PATTERN.match(normalized)
    if match is None:
        return normalized, None
    return match.group("title").strip(), int(match.group("year"))


def split_genres(raw_genres: str) -> list[str]:
    """Turn 'Animation|Children|Comedy' into a list; '(no genres listed)' becomes []."""
    if raw_genres == NO_GENRES:
        return []
    return raw_genres.split("|")


def build_movie_rows() -> list[dict[str, object]]:
    """Read both CSV files and prepare one row per movie for the database."""
    movies = pd.read_csv(MOVIES_CSV)
    links = pd.read_csv(LINKS_CSV, dtype={"tmdbId": "Int64"})
    tmdb_by_movielens = {int(row.movieId): row.tmdbId for row in links.itertuples(index=False)}

    rows: list[dict[str, object]] = []
    for record in movies.itertuples(index=False):
        title, year = split_title_year(record.title)
        raw_tmdb_id = tmdb_by_movielens.get(int(record.movieId))
        rows.append(
            {
                "movielens_id": int(record.movieId),
                "tmdb_id": None if raw_tmdb_id is None or pd.isna(raw_tmdb_id) else int(raw_tmdb_id),
                "title": title,
                "year": year,
                "genres": split_genres(record.genres),
            }
        )
    return rows


def upsert_movies(session: Session, rows: list[dict[str, object]]) -> None:
    """Insert new movies and update existing ones, matched on movielens_id.

    overview and poster_path are deliberately not updated here, so re-running the
    import never erases TMDB enrichment that already happened.
    """
    table = Movie.__table__
    for start in range(0, len(rows), MOVIE_BATCH_SIZE):
        batch = rows[start : start + MOVIE_BATCH_SIZE]
        statement = pg_insert(table).values(batch)
        statement = statement.on_conflict_do_update(
            index_elements=[table.c.movielens_id],
            set_={
                "tmdb_id": statement.excluded.tmdb_id,
                "title": statement.excluded.title,
                "year": statement.excluded.year,
                "genres": statement.excluded.genres,
            },
        )
        session.execute(statement)
        session.commit()
        print(f"  movies: {start + len(batch)}/{len(rows)}")


def load_movie_id_map(session: Session) -> dict[int, int]:
    """Map MovieLens movieId -> internal movies.id (needed by the ratings table)."""
    rows = session.execute(select(Movie.movielens_id, Movie.id)).all()
    return {int(movielens_id): int(movie_id) for movielens_id, movie_id in rows}


def import_ratings(session: Session, movie_id_map: dict[int, int]) -> tuple[int, int]:
    """Upsert MovieLens ratings in chunks. Returns (imported, skipped)."""
    table = MovielensRating.__table__
    imported = 0
    skipped = 0

    reader = pd.read_csv(RATINGS_CSV, chunksize=RATING_BATCH_SIZE)
    for chunk in reader:
        rows: list[dict[str, object]] = []
        for record in chunk.itertuples(index=False):
            movie_id = movie_id_map.get(int(record.movieId))
            if movie_id is None:
                skipped += 1
                continue
            rows.append(
                {
                    "ml_user_id": int(record.userId),
                    "movie_id": movie_id,
                    "rating": float(record.rating),
                }
            )

        if not rows:
            continue

        statement = pg_insert(table).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=["ml_user_id", "movie_id"],
            set_={"rating": statement.excluded.rating},
        )
        session.execute(statement)
        session.commit()
        imported += len(rows)
        print(f"  ratings: {imported}")

    return imported, skipped


class TmdbClient:
    """Small TMDB client with retries, rate-limit handling, and auth auto-detection.

    Supports both auth styles:
    - TMDB v3 API key, sent as the `api_key` query parameter;
    - TMDB v4 read access token, sent as an `Authorization: Bearer` header.
    The style is guessed from the value and corrected automatically after a 401.
    """

    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, api_key: str, language: str) -> None:
        self._api_key = api_key
        self._language = language
        self._session = requests.Session()
        self._use_bearer = api_key.startswith(TMDB_BEARER_PREFIX) and api_key.count(".") == 2
        self._auth_switched = False

    def check_credentials(self) -> None:
        """Make one cheap request so a bad key fails before enrichment starts."""
        self._get("/configuration")

    def get_movie(self, tmdb_id: int) -> dict[str, object] | None:
        """Return the TMDB payload for a movie, or None when TMDB returns 404."""
        return self._get(f"/movie/{tmdb_id}")

    def _send(self, path: str) -> requests.Response:
        params: dict[str, str] = {"language": self._language}
        headers: dict[str, str] = {}
        if self._use_bearer:
            headers["Authorization"] = f"Bearer {self._api_key}"
        else:
            params["api_key"] = self._api_key

        return self._session.get(
            f"{self.BASE_URL}{path}",
            params=params,
            headers=headers,
            timeout=(5, 20),
        )

    def _get(self, path: str) -> dict[str, object] | None:
        for attempt in range(1, TMDB_MAX_ATTEMPTS + 1):
            try:
                response = self._send(path)
            except requests.RequestException as exc:
                if attempt == TMDB_MAX_ATTEMPTS:
                    raise RuntimeError(f"TMDB request failed: {exc}") from exc
                wait = 2**attempt
                print(f"  network error, retrying in {wait}s")
                time.sleep(wait)
                continue

            # Stay well below TMDB's rate limit.
            time.sleep(TMDB_DELAY_SECONDS)

            if response.status_code == 401 and not self._auth_switched:
                self._use_bearer = not self._use_bearer
                self._auth_switched = True
                print("  TMDB rejected the credentials, retrying with the other auth style")
                continue
            if response.status_code == 401:
                raise RuntimeError("TMDB rejected the credentials. Check TMDB_API_KEY in .env.")
            if response.status_code == 404:
                return None
            if response.status_code == 429:
                wait = _retry_after_seconds(response)
                print(f"  rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            if response.status_code >= 500:
                if attempt == TMDB_MAX_ATTEMPTS:
                    response.raise_for_status()
                wait = 2**attempt
                print(f"  TMDB server error {response.status_code}, retrying in {wait}s")
                time.sleep(wait)
                continue

            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError(f"Unexpected TMDB response for {path}")
            return payload

        raise RuntimeError(f"TMDB request failed after {TMDB_MAX_ATTEMPTS} attempts: {path}")


def _retry_after_seconds(response: requests.Response) -> int:
    raw_value = response.headers.get("Retry-After", "2")
    try:
        return max(1, int(float(raw_value)))
    except (TypeError, ValueError):
        return 2


def enrich_with_tmdb(
    session: Session,
    client: TmdbClient,
    *,
    limit: int | None,
    refresh: bool,
) -> EnrichmentStats:
    """Fetch overview/poster_path for movies that do not have them yet."""
    query = select(Movie.id, Movie.tmdb_id).where(Movie.tmdb_id.is_not(None))
    if not refresh:
        # Both empty means "never enriched". A partially filled row counts as done.
        query = query.where(Movie.overview.is_(None), Movie.poster_path.is_(None))
    query = query.order_by(Movie.id)
    if limit is not None:
        query = query.limit(limit)

    targets = session.execute(query).all()
    total = len(targets)
    stats = EnrichmentStats()
    if total == 0:
        print("TMDB: nothing to enrich.")
        return stats

    print(f"TMDB: enriching {total} movie(s)...")
    for index, (movie_id, tmdb_id) in enumerate(targets, start=1):
        stats.requested += 1
        data = client.get_movie(int(tmdb_id))

        overview = (data.get("overview") or None) if data else None
        poster_path = (data.get("poster_path") or None) if data else None

        if overview is None and poster_path is None:
            stats.without_data += 1
        else:
            session.execute(
                update(Movie)
                .where(Movie.id == movie_id)
                .values(overview=overview, poster_path=poster_path)
            )
            stats.updated += 1

        if index % TMDB_COMMIT_EVERY == 0:
            session.commit()
            print(f"  {index}/{total} processed ({stats.updated} updated)")

    session.commit()
    print(f"  {total}/{total} processed ({stats.updated} updated)")
    return stats


def print_summary(session: Session, stats: EnrichmentStats | None, elapsed: float) -> None:
    movies_total = session.scalar(select(func.count()).select_from(Movie)) or 0
    without_tmdb = (
        session.scalar(
            select(func.count()).select_from(Movie).where(Movie.tmdb_id.is_(None))
        )
        or 0
    )
    missing_overview = (
        session.scalar(
            select(func.count()).select_from(Movie).where(Movie.overview.is_(None))
        )
        or 0
    )
    missing_poster = (
        session.scalar(
            select(func.count()).select_from(Movie).where(Movie.poster_path.is_(None))
        )
        or 0
    )
    ratings_total = session.scalar(select(func.count()).select_from(MovielensRating)) or 0

    minutes, seconds = divmod(int(elapsed), 60)
    print("\n=== Import summary ===")
    print(f"Movies in database      : {movies_total}")
    print(f"  without tmdb_id       : {without_tmdb}")
    print(f"  missing overview      : {missing_overview}")
    print(f"  missing poster_path   : {missing_poster}")
    print(f"MovieLens ratings       : {ratings_total}")
    if stats is not None:
        print(f"TMDB requests           : {stats.requested}")
        print(f"  updated               : {stats.updated}")
        print(f"  without overview/poster: {stats.without_data}")
    print(f"Elapsed                 : {minutes}m {seconds}s")


def main() -> int:
    args = parse_args()
    started = time.perf_counter()

    check_prerequisites(use_tmdb=not args.skip_tmdb)

    session = SessionLocal()
    try:
        print(f"Importing movies from {MOVIES_CSV.name}...")
        movie_rows = build_movie_rows()
        upsert_movies(session, movie_rows)
        print(f"  {len(movie_rows)} movie(s) upserted")

        print(f"Importing ratings from {RATINGS_CSV.name}...")
        movie_id_map = load_movie_id_map(session)
        imported, skipped = import_ratings(session, movie_id_map)
        print(f"  {imported} rating(s) upserted, {skipped} skipped (unknown movie)")

        stats: EnrichmentStats | None = None
        if args.skip_tmdb:
            print("TMDB enrichment skipped (--skip-tmdb).")
        else:
            client = TmdbClient(settings.tmdb_api_key or "", settings.tmdb_language)
            print("Checking TMDB credentials...")
            client.check_credentials()
            stats = enrich_with_tmdb(
                session,
                client,
                limit=args.limit,
                refresh=args.refresh_tmdb,
            )

        print_summary(session, stats, time.perf_counter() - started)
    finally:
        session.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
