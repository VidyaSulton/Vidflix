from sqlalchemy import REAL, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class MovieSimilarity(Base):
    __tablename__ = "movie_similarities"
    __table_args__ = (
        Index("ix_movie_similarities_movie_id", "movie_id"),
    )

    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id"), primary_key=True)
    similar_movie_id: Mapped[int] = mapped_column(
        ForeignKey("movies.id"), primary_key=True
    )
    score: Mapped[float] = mapped_column(REAL, nullable=False)
