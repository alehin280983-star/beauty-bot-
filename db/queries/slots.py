from __future__ import annotations

import math
import uuid
from datetime import date, datetime, timedelta
from typing import List

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.exceptions import NotEnoughSlots, SlotAlreadyTaken
from db.models import Slot


async def generate_slots(
    session: AsyncSession,
    master_id: uuid.UUID,
    slot_date: date,
    start_hour: int,
    end_hour: int,
    step_minutes: int,
) -> int:
    """Generate slots for a master on a given date. Skips existing ones silently."""
    slots = []
    current = datetime(slot_date.year, slot_date.month, slot_date.day, start_hour, 0)
    end = datetime(slot_date.year, slot_date.month, slot_date.day, end_hour, 0)
    while current < end:
        slots.append({"id": uuid.uuid4(), "master_id": master_id, "starts_at": current})
        current += timedelta(minutes=step_minutes)

    if not slots:
        return 0

    stmt = insert(Slot).values(slots).on_conflict_do_nothing(
        index_elements=["master_id", "starts_at"]
    )
    result = await session.execute(stmt)
    return result.rowcount


async def get_slots_for_date(
    session: AsyncSession,
    master_id: uuid.UUID,
    slot_date: date,
) -> List[Slot]:
    start = datetime(slot_date.year, slot_date.month, slot_date.day, 0, 0)
    end = start + timedelta(days=1)
    result = await session.execute(
        select(Slot)
        .where(Slot.master_id == master_id)
        .where(Slot.starts_at >= start)
        .where(Slot.starts_at < end)
        .order_by(Slot.starts_at)
    )
    return list(result.scalars().all())


async def get_slots_for_range(
    session: AsyncSession,
    master_id: uuid.UUID,
    date_from: date,
    date_to: date,
) -> List[Slot]:
    start = datetime(date_from.year, date_from.month, date_from.day, 0, 0)
    end = datetime(date_to.year, date_to.month, date_to.day, 0, 0) + timedelta(days=1)
    result = await session.execute(
        select(Slot)
        .where(Slot.master_id == master_id)
        .where(Slot.starts_at >= start)
        .where(Slot.starts_at < end)
        .order_by(Slot.starts_at)
    )
    return list(result.scalars().all())


async def get_available_slots(
    session: AsyncSession,
    master_id: uuid.UUID,
    slot_date: date,
    service_duration_min: int,
    step_min: int,
) -> List[Slot]:
    all_slots = await get_slots_for_date(session, master_id, slot_date)
    slots_needed = max(1, math.ceil(service_duration_min / step_min))

    available = []
    for i, slot in enumerate(all_slots):
        if slot.booking_id is not None or slot.is_blocked:
            continue
        window = all_slots[i : i + slots_needed]
        if len(window) < slots_needed:
            continue
        if all(not s.booking_id and not s.is_blocked for s in window):
            available.append(slot)
    return available


async def get_dates_with_available_slots(
    session: AsyncSession,
    master_id: uuid.UUID,
    service_duration_min: int,
    step_min: int,
    days: int = 30,
) -> List[date]:
    today = date.today()
    date_to = today + timedelta(days=days - 1)
    all_slots = await get_slots_for_range(session, master_id, today, date_to)

    slots_needed = max(1, math.ceil(service_duration_min / step_min))

    # Group by date
    from collections import defaultdict
    slots_by_date: dict[date, List[Slot]] = defaultdict(list)
    for slot in all_slots:
        slots_by_date[slot.starts_at.date()].append(slot)

    available_dates = []
    for d in sorted(slots_by_date.keys()):
        day_slots = slots_by_date[d]  # already ordered from DB
        for i, slot in enumerate(day_slots):
            if slot.booking_id is not None or slot.is_blocked:
                continue
            window = day_slots[i : i + slots_needed]
            if len(window) < slots_needed:
                continue
            if all(not s.booking_id and not s.is_blocked for s in window):
                available_dates.append(d)
                break
    return available_dates


async def lock_slots_for_booking(
    session: AsyncSession,
    master_id: uuid.UUID,
    start_time: datetime,
    duration_min: int,
    step_min: int,
) -> List[Slot]:
    """Lock slots with FOR UPDATE NOWAIT. Raises NotEnoughSlots or SlotAlreadyTaken."""
    # Normalise to naive UTC to match DB storage
    if start_time.tzinfo is not None:
        from zoneinfo import ZoneInfo
        start_time = start_time.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    end_time = start_time + timedelta(minutes=duration_min)
    expected_count = max(1, math.ceil(duration_min / step_min))

    result = await session.execute(
        select(Slot)
        .where(Slot.master_id == master_id)
        .where(Slot.starts_at >= start_time)
        .where(Slot.starts_at < end_time)
        .order_by(Slot.starts_at)
        .with_for_update(nowait=True)
    )
    slots = list(result.scalars().all())

    if len(slots) < expected_count:
        raise NotEnoughSlots(f"Expected {expected_count} slots, got {len(slots)}")

    if any(s.booking_id is not None or s.is_blocked for s in slots):
        raise SlotAlreadyTaken("One or more slots are already taken or blocked")

    return slots


async def release_slots(session: AsyncSession, booking_id: uuid.UUID) -> None:
    await session.execute(
        update(Slot)
        .where(Slot.booking_id == booking_id)
        .values(booking_id=None)
    )


async def block_slot(session: AsyncSession, slot_id: uuid.UUID) -> None:
    result = await session.execute(select(Slot).where(Slot.id == slot_id))
    slot = result.scalar_one()
    slot.is_blocked = True
    await session.flush()


async def unblock_slot(session: AsyncSession, slot_id: uuid.UUID) -> None:
    result = await session.execute(select(Slot).where(Slot.id == slot_id))
    slot = result.scalar_one()
    slot.is_blocked = False
    await session.flush()
