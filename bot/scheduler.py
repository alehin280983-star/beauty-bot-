from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config import settings
from db.queries.bookings import (
    get_pending_reminders_24h,
    get_pending_reminders_2h,
    get_pending_review_requests,
    mark_24h_reminder_sent,
    mark_2h_reminder_sent,
    mark_review_requested,
)

logger = logging.getLogger(__name__)

_OVERDUE_THRESHOLD = timedelta(minutes=30)


def _to_utc_naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return dt


def _fmt(dt: datetime) -> str:
    tz = ZoneInfo(settings.studio_timezone)
    local = (
        dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    )
    return local.strftime("%d.%m.%Y о %H:%M")


async def send_24h_reminders(bot, session_factory: async_sessionmaker[AsyncSession]) -> None:
    from bot.handlers.reviews import reminder_24h_keyboard

    async with session_factory() as session:
        reminders = await get_pending_reminders_24h(session)

    for r in reminders:
        if r["client_telegram_id"] is None:
            continue

        start_utc = _to_utc_naive(r["start_time"])
        now = datetime.utcnow()

        if start_utc < now - _OVERDUE_THRESHOLD:
            logger.info("24h reminder overdue, skipped: booking %s", r["id"])
            async with session_factory() as session:
                await mark_24h_reminder_sent(session, r["id"])
                await session.commit()
            continue

        text = (
            f"⏰ <b>Нагадування про запис</b>\n\n"
            f"💇 {r['service_name']}\n"
            f"👤 {r['master_name']}\n"
            f"📅 {_fmt(r['start_time'])}\n"
            f"💰 {int(r['price_at_booking'])} грн\n\n"
            "Чекаємо на вас завтра!"
        )
        try:
            await bot.send_message(
                r["client_telegram_id"],
                text,
                reply_markup=reminder_24h_keyboard(str(r["id"])),
            )
            async with session_factory() as session:
                await mark_24h_reminder_sent(session, r["id"])
                await session.commit()
        except Exception as e:
            logger.warning("Failed to send 24h reminder to %s: %s", r["client_telegram_id"], e)


async def send_2h_reminders(bot, session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        reminders = await get_pending_reminders_2h(session)

    for r in reminders:
        if r["client_telegram_id"] is None:
            continue

        start_utc = _to_utc_naive(r["start_time"])
        now = datetime.utcnow()

        if start_utc < now - _OVERDUE_THRESHOLD:
            logger.info("2h reminder overdue, skipped: booking %s", r["id"])
            async with session_factory() as session:
                await mark_2h_reminder_sent(session, r["id"])
                await session.commit()
            continue

        text = (
            f"⏰ <b>Незабаром ваш запис!</b>\n\n"
            f"💇 {r['service_name']}\n"
            f"👤 {r['master_name']}\n"
            f"📅 {_fmt(r['start_time'])}\n\n"
            "Чекаємо на вас приблизно через 2 години!"
        )
        try:
            await bot.send_message(r["client_telegram_id"], text)
            async with session_factory() as session:
                await mark_2h_reminder_sent(session, r["id"])
                await session.commit()
        except Exception as e:
            logger.warning("Failed to send 2h reminder to %s: %s", r["client_telegram_id"], e)


async def send_review_requests(bot, session_factory: async_sessionmaker[AsyncSession]) -> None:
    from bot.handlers.reviews import review_request_keyboard

    async with session_factory() as session:
        requests = await get_pending_review_requests(
            session, review_delay_hours=settings.review_delay_hours
        )

    for r in requests:
        if r["client_telegram_id"] is None:
            continue

        text = (
            f"Як пройшов візит?\n\n"
            f"💇 {r['service_name']}\n\n"
            "Оцініть, будь ласка:"
        )
        try:
            await bot.send_message(
                r["client_telegram_id"],
                text,
                reply_markup=review_request_keyboard(str(r["id"])),
            )
            async with session_factory() as session:
                await mark_review_requested(session, r["id"])
                await session.commit()
        except Exception as e:
            logger.warning("Failed to send review request to %s: %s", r["client_telegram_id"], e)


def setup_scheduler(bot, session_factory: async_sessionmaker[AsyncSession]) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        send_24h_reminders,
        trigger="interval",
        minutes=15,
        kwargs={"bot": bot, "session_factory": session_factory},
        id="reminders_24h",
        replace_existing=True,
    )
    scheduler.add_job(
        send_2h_reminders,
        trigger="interval",
        minutes=15,
        kwargs={"bot": bot, "session_factory": session_factory},
        id="reminders_2h",
        replace_existing=True,
    )
    scheduler.add_job(
        send_review_requests,
        trigger="interval",
        minutes=15,
        kwargs={"bot": bot, "session_factory": session_factory},
        id="review_requests",
        replace_existing=True,
    )

    return scheduler
