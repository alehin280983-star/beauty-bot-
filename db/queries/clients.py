import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Client


async def get_or_create_client(
    session: AsyncSession,
    telegram_id: int,
    first_name: Optional[str] = None,
) -> Client:
    result = await session.execute(
        select(Client).where(Client.telegram_id == telegram_id)
    )
    client = result.scalar_one_or_none()
    if client is None:
        client = Client(telegram_id=telegram_id, first_name=first_name)
        session.add(client)
        await session.flush()
    return client


async def create_phone_client(
    session: AsyncSession,
    phone: str,
    first_name: Optional[str] = None,
) -> Client:
    client = Client(phone=phone, first_name=first_name)
    session.add(client)
    await session.flush()
    return client


async def find_client_by_phone(
    session: AsyncSession,
    phone: str,
) -> Optional[Client]:
    result = await session.execute(
        select(Client).where(Client.phone == phone)
    )
    return result.scalar_one_or_none()


async def save_client_phone(
    session: AsyncSession,
    telegram_id: int,
    phone: str,
) -> None:
    """Save phone for telegram client. If a phone-only client already exists, merge into it."""
    from sqlalchemy import update as sa_update
    from db.models import Booking

    tg_client_result = await session.execute(
        select(Client).where(Client.telegram_id == telegram_id)
    )
    tg_client = tg_client_result.scalar_one_or_none()
    if tg_client is None:
        return

    phone_client_result = await session.execute(
        select(Client).where(Client.phone == phone).where(Client.telegram_id.is_(None))
    )
    phone_client = phone_client_result.scalar_one_or_none()

    if phone_client is not None:
        # Move all bookings from phone_client to tg_client
        await session.execute(
            sa_update(Booking)
            .where(Booking.client_id == phone_client.id)
            .values(client_id=tg_client.id)
        )
        await session.delete(phone_client)

    tg_client.phone = phone
    await session.flush()


async def set_client_blocked(
    session: AsyncSession,
    client_id: uuid.UUID,
    blocked: bool = True,
) -> None:
    result = await session.execute(
        select(Client).where(Client.id == client_id)
    )
    client = result.scalar_one_or_none()
    if client is not None:
        client.is_blocked = blocked
        await session.flush()
