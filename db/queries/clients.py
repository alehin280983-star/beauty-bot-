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
