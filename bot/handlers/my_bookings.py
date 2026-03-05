from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards.main_menu import MAIN_MENU
from config import settings
from db.models import Client
from db.queries.bookings import cancel_booking, get_booking_start_time, get_client_active_bookings

router = Router()


class CancelBookingCD(CallbackData, prefix="cxl"):
    booking_id: str


@router.message(F.text == "📅 Мої записи")
async def cmd_my_bookings(message: Message, session: AsyncSession) -> None:
    client_result = await session.execute(
        select(Client).where(Client.telegram_id == message.from_user.id)
    )
    client = client_result.scalar_one_or_none()
    if client is None:
        await message.answer("Ви ще не записувались.", reply_markup=MAIN_MENU)
        return

    bookings = await get_client_active_bookings(session, client.id)
    if not bookings:
        await message.answer("Немає активних записів.", reply_markup=MAIN_MENU)
        return

    tz = ZoneInfo(settings.studio_timezone)
    for b in bookings:
        start: datetime = b["start_time"]
        if start is None:
            continue
        if start.tzinfo:
            local = start.astimezone(tz)
        else:
            local = start.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)

        text = (
            f"💇 <b>{b['service_name']}</b>\n"
            f"👤 {b['master_name']}\n"
            f"📅 {local.strftime('%d.%m.%Y')} о {local.strftime('%H:%M')}\n"
            f"💰 {int(b['price_at_booking'])} грн"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="❌ Скасувати",
                callback_data=CancelBookingCD(booking_id=str(b["id"])).pack(),
            )
        ]])
        await message.answer(text, reply_markup=kb)


@router.callback_query(CancelBookingCD.filter())
async def on_cancel_booking(
    callback: CallbackQuery,
    callback_data: CancelBookingCD,
    session: AsyncSession,
    bot: Bot,
) -> None:
    booking_id = uuid.UUID(callback_data.booking_id)

    start_time = await get_booking_start_time(session, booking_id)
    if start_time is None:
        await callback.answer("Запис не знайдено.", show_alert=True)
        return

    now = datetime.utcnow()
    if start_time.tzinfo:
        start_utc = start_time.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    else:
        start_utc = start_time

    deadline = start_utc - timedelta(hours=settings.cancel_deadline_hours)
    if now > deadline:
        await callback.answer(
            f"Скасування недоступне менш ніж за {settings.cancel_deadline_hours} год до запису.",
            show_alert=True,
        )
        return

    booking = await cancel_booking(session, booking_id, cancelled_by="client")
    await session.commit()

    await callback.answer("Запис скасовано.")
    await callback.message.edit_text(
        callback.message.text + "\n\n<i>❌ Скасовано</i>",
        reply_markup=None,
    )

    # Notify admins
    tz = ZoneInfo(settings.studio_timezone)
    if start_time.tzinfo:
        local = start_time.astimezone(tz)
    else:
        local = start_time.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)

    admin_text = (
        f"❌ <b>Скасування запису</b>\n\n"
        f"Клієнт: {callback.from_user.full_name} (@{callback.from_user.username or '—'})\n"
        f"Дата: {local.strftime('%d.%m.%Y')} о {local.strftime('%H:%M')}"
    )
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, admin_text)
        except Exception:
            pass
