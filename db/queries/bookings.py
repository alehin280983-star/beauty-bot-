from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.exceptions import NotEnoughSlots, SlotAlreadyTaken  # noqa: F401 — re-exported
from db.models import Booking, BookingStatus, Client, Master, Service, Slot
from db.queries.slots import lock_slots_for_booking, release_slots


async def create_booking(
    session: AsyncSession,
    client_id: uuid.UUID,
    master_id: uuid.UUID,
    service_id: uuid.UUID,
    slot_start: datetime,
    service_duration: int,
    service_price: Decimal,
    step_min: int,
) -> Booking:
    """Atomic multi-slot booking. Raises SlotAlreadyTaken or NotEnoughSlots on conflict.
    Caller is responsible for commit/rollback."""
    slots = await lock_slots_for_booking(
        session, master_id, slot_start, service_duration, step_min
    )
    booking = Booking(
        client_id=client_id,
        master_id=master_id,
        service_id=service_id,
        status=BookingStatus.confirmed,
        price_at_booking=service_price,
    )
    session.add(booking)
    await session.flush()
    for slot in slots:
        slot.booking_id = booking.id
    return booking


async def cancel_booking(
    session: AsyncSession,
    booking_id: uuid.UUID,
    cancelled_by: str,
) -> Booking:
    """Cancel booking and release all slots. No deadline check — that's the handler's job.
    Caller is responsible for commit."""
    status = (
        BookingStatus.cancelled_client
        if cancelled_by == "client"
        else BookingStatus.cancelled_admin
    )
    result = await session.execute(
        select(Booking).where(Booking.id == booking_id)
    )
    booking = result.scalar_one()
    booking.status = status
    await release_slots(session, booking_id)
    return booking


async def get_client_active_bookings(
    session: AsyncSession,
    client_id: uuid.UUID,
) -> List[dict]:
    """Return confirmed bookings with start time, service, and master info."""
    start_subq = (
        select(func.min(Slot.starts_at))
        .where(Slot.booking_id == Booking.id)
        .correlate(Booking)
        .scalar_subquery()
    )
    result = await session.execute(
        select(
            Booking.id,
            Booking.price_at_booking,
            Service.name.label("service_name"),
            Service.duration_min,
            Master.name.label("master_name"),
            start_subq.label("start_time"),
        )
        .join(Service, Booking.service_id == Service.id)
        .join(Master, Booking.master_id == Master.id)
        .where(Booking.client_id == client_id)
        .where(Booking.status == BookingStatus.confirmed)
        .order_by(start_subq)
    )
    return [row._asdict() for row in result.all()]


async def get_bookings_for_date(
    session: AsyncSession,
    target_date,
) -> List[dict]:
    """Return all confirmed bookings for a date. For admin use."""
    from datetime import date as date_type
    if isinstance(target_date, date_type):
        start = datetime(target_date.year, target_date.month, target_date.day, 0, 0)
    else:
        start = target_date
    end = start + timedelta(days=1)

    start_subq = (
        select(func.min(Slot.starts_at))
        .where(Slot.booking_id == Booking.id)
        .correlate(Booking)
        .scalar_subquery()
    )
    result = await session.execute(
        select(
            Booking.id,
            Booking.price_at_booking,
            Client.first_name.label("client_name"),
            Client.phone.label("client_phone"),
            Client.telegram_id.label("client_telegram_id"),
            Service.name.label("service_name"),
            Service.duration_min,
            Master.name.label("master_name"),
            start_subq.label("start_time"),
        )
        .join(Client, Booking.client_id == Client.id)
        .join(Service, Booking.service_id == Service.id)
        .join(Master, Booking.master_id == Master.id)
        .where(Booking.status == BookingStatus.confirmed)
        .where(start_subq >= start)
        .where(start_subq < end)
        .order_by(start_subq)
    )
    return [row._asdict() for row in result.all()]


async def get_booking_start_time(
    session: AsyncSession,
    booking_id: uuid.UUID,
) -> datetime:
    result = await session.execute(
        select(func.min(Slot.starts_at)).where(Slot.booking_id == booking_id)
    )
    return result.scalar_one()


async def get_pending_reminders_24h(session: AsyncSession) -> List[dict]:
    """Bookings confirmed, not reminded yet, starting within the next 24 hours."""
    now = datetime.utcnow()
    cutoff = now + timedelta(hours=24)
    return await _reminder_query(
        session,
        sent_flag=Booking.reminder_24h_sent,
        now=now,
        cutoff=cutoff,
    )


async def get_pending_reminders_2h(session: AsyncSession) -> List[dict]:
    """Bookings confirmed, not reminded yet, starting within the next 2 hours."""
    now = datetime.utcnow()
    cutoff = now + timedelta(hours=2)
    return await _reminder_query(
        session,
        sent_flag=Booking.reminder_2h_sent,
        now=now,
        cutoff=cutoff,
    )


async def _reminder_query(
    session: AsyncSession,
    sent_flag,
    now: datetime,
    cutoff: datetime,
) -> List[dict]:
    start_subq = (
        select(func.min(Slot.starts_at))
        .where(Slot.booking_id == Booking.id)
        .correlate(Booking)
        .scalar_subquery()
    )
    result = await session.execute(
        select(
            Booking.id,
            Booking.price_at_booking,
            Client.telegram_id.label("client_telegram_id"),
            Client.first_name.label("client_name"),
            Service.name.label("service_name"),
            Service.duration_min,
            Master.name.label("master_name"),
            start_subq.label("start_time"),
        )
        .join(Client, Booking.client_id == Client.id)
        .join(Service, Booking.service_id == Service.id)
        .join(Master, Booking.master_id == Master.id)
        .where(Booking.status == BookingStatus.confirmed)
        .where(sent_flag == False)  # noqa: E712
        .where(Client.telegram_id != None)  # noqa: E711
        .where(start_subq > now)
        .where(start_subq <= cutoff)
        .order_by(start_subq)
    )
    return [row._asdict() for row in result.all()]


async def get_pending_review_requests(
    session: AsyncSession,
    review_delay_hours: int = 3,
) -> List[dict]:
    """Bookings whose appointment ended >= review_delay_hours ago, not yet reviewed."""
    now = datetime.utcnow()
    deadline = now - timedelta(hours=review_delay_hours)

    # end_time = MIN(starts_at) + duration_min
    start_subq = (
        select(func.min(Slot.starts_at))
        .where(Slot.booking_id == Booking.id)
        .correlate(Booking)
        .scalar_subquery()
    )
    end_time_expr = start_subq + func.make_interval(mins=Service.duration_min)

    result = await session.execute(
        select(
            Booking.id,
            Client.telegram_id.label("client_telegram_id"),
            Client.first_name.label("client_name"),
            Service.name.label("service_name"),
        )
        .join(Client, Booking.client_id == Client.id)
        .join(Service, Booking.service_id == Service.id)
        .where(Booking.status == BookingStatus.confirmed)
        .where(Booking.review_requested == False)  # noqa: E712
        .where(Client.telegram_id != None)  # noqa: E711
        .where(end_time_expr <= deadline)
        .order_by(start_subq)
    )
    return [row._asdict() for row in result.all()]


async def mark_24h_reminder_sent(
    session: AsyncSession,
    booking_id: uuid.UUID,
) -> None:
    result = await session.execute(
        select(Booking).where(Booking.id == booking_id)
    )
    booking = result.scalar_one()
    booking.reminder_24h_sent = True
    await session.flush()


async def mark_2h_reminder_sent(
    session: AsyncSession,
    booking_id: uuid.UUID,
) -> None:
    result = await session.execute(
        select(Booking).where(Booking.id == booking_id)
    )
    booking = result.scalar_one()
    booking.reminder_2h_sent = True
    await session.flush()


async def mark_review_requested(
    session: AsyncSession,
    booking_id: uuid.UUID,
) -> None:
    """Mark review as requested and set booking status to completed."""
    result = await session.execute(
        select(Booking).where(Booking.id == booking_id)
    )
    booking = result.scalar_one()
    booking.review_requested = True
    booking.status = BookingStatus.completed
    await session.flush()
