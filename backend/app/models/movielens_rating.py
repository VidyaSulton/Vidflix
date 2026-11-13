from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class MovielensRating(Base):
    __tablename__ = "movielens_ratings"
    __table_args__ = (
        CheckConstraint(
            "rating >= 0.5 AND rating <= 5",
            name="ck_movielens_ratings_rating_range",
        ),
    )

    ml_user_id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id"), primary_key=True)
    rating: Mapped[Decimal] = mapped_column(Numeric(2, 1), nullable=False)
