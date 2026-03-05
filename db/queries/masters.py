from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Master, MasterService


async def get_active_masters(session: AsyncSession) -> List[Master]:
    result = await session.execute(
        select(Master).where(Master.is_active == True).order_by(Master.name)
    )
    return list(result.scalars().all())


async def get_masters_for_service(
    session: AsyncSession,
    service_id: uuid.UUID,
) -> List[Master]:
    result = await session.execute(
        select(Master)
        .join(MasterService, MasterService.master_id == Master.id)
        .where(MasterService.service_id == service_id)
        .where(Master.is_active == True)
        .order_by(Master.name)
    )
    return list(result.scalars().all())


async def create_master(
    session: AsyncSession,
    name: str,
    photo_url: Optional[str] = None,
) -> Master:
    master = Master(name=name, photo_url=photo_url)
    session.add(master)
    await session.flush()
    return master


async def toggle_master_active(
    session: AsyncSession,
    master_id: uuid.UUID,
) -> Master:
    result = await session.execute(
        select(Master).where(Master.id == master_id)
    )
    master = result.scalar_one()
    master.is_active = not master.is_active
    await session.flush()
    return master
