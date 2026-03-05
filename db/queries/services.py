from __future__ import annotations

import uuid
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Service


async def get_visible_services(session: AsyncSession) -> List[Service]:
    result = await session.execute(
        select(Service).where(Service.is_visible == True).order_by(Service.name)
    )
    return list(result.scalars().all())


async def create_service(
    session: AsyncSession,
    name: str,
    duration_min: int,
    price: Decimal,
) -> Service:
    service = Service(name=name, duration_min=duration_min, price=price)
    session.add(service)
    await session.flush()
    return service


async def update_service(
    session: AsyncSession,
    service_id: uuid.UUID,
    name: Optional[str] = None,
    duration_min: Optional[int] = None,
    price: Optional[Decimal] = None,
) -> Service:
    result = await session.execute(
        select(Service).where(Service.id == service_id)
    )
    service = result.scalar_one()
    if name is not None:
        service.name = name
    if duration_min is not None:
        service.duration_min = duration_min
    if price is not None:
        service.price = price
    await session.flush()
    return service


async def toggle_service_visible(
    session: AsyncSession,
    service_id: uuid.UUID,
) -> Service:
    result = await session.execute(
        select(Service).where(Service.id == service_id)
    )
    service = result.scalar_one()
    service.is_visible = not service.is_visible
    await session.flush()
    return service
