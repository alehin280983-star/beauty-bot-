import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Review


async def create_review(
    session: AsyncSession,
    booking_id: uuid.UUID,
    client_id: uuid.UUID,
    rating: int,
    comment: Optional[str] = None,
) -> Review:
    review = Review(
        booking_id=booking_id,
        client_id=client_id,
        rating=rating,
        comment=comment,
    )
    session.add(review)
    await session.flush()
    return review


async def get_recent_reviews(
    session: AsyncSession,
    limit: int = 20,
) -> List[Review]:
    result = await session.execute(
        select(Review).order_by(Review.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def has_review(
    session: AsyncSession,
    booking_id: uuid.UUID,
) -> bool:
    result = await session.execute(
        select(Review.id).where(Review.booking_id == booking_id)
    )
    return result.scalar_one_or_none() is not None
