"""SQLAlchemy models."""

from app.models.movie import Movie
from app.models.movie_similarity import MovieSimilarity
from app.models.movielens_rating import MovielensRating
from app.models.rating import Rating
from app.models.user import User

__all__ = [
    "Movie",
    "MovieSimilarity",
    "MovielensRating",
    "Rating",
    "User",
]
