from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards.main_menu import MAIN_MENU
from config import settings
from db.queries.bookings import cancel_booking, get_booking_start_time
from db.queries.reviews import create_review

router = Router()


# ── CallbackData ───────────────────────────────────────────────────────────────

class ReminderAckCD(CallbackData, prefix="rack"):
    booking_id: str


class ReminderCancelCD(CallbackData, prefix="rcxl"):
    booking_id: str


class ReviewRatingCD(CallbackData, prefix="rev_r"):
    booking_id: str
    rating: int


# ── FSM ───────────────────────────────────────────────────────────────────────

class ReviewFSM(StatesGroup):
    entering_comment = State()


# ── Reminder ack ──────────────────────────────────────────────────────────────

@router.callback_query(ReminderAckCD.filter())
async def on_reminder_ack(callback: CallbackQuery) -> None:
    await callback.answer("Ждём вас! 💇")
    await callback.message.edit_reply_markup(reply_markup=None)


# ── Reminder cancel (from reminder message) ───────────────────────────────────

@router.callback_query(ReminderCancelCD.filter())
async def on_reminder_cancel(
    callback: CallbackQuery,
    callback_data: ReminderCancelCD,
    session: AsyncSession,
    bot: Bot,
) -> None:
    booking_id = uuid.UUID(callback_data.booking_id)

    start_time = await get_booking_start_time(session, booking_id)
    if start_time is None:
        await callback.answer("Запись не найдена.", show_alert=True)
        return

    now = datetime.utcnow()
    start_utc = (
        start_time.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        if start_time.tzinfo
        else start_time
    )
    deadline = start_utc - timedelta(hours=settings.cancel_deadline_hours)
    if now > deadline:
        await callback.answer(
            f"Отмена недоступна менее чем за {settings.cancel_deadline_hours} ч до записи.",
            show_alert=True,
        )
        return

    await cancel_booking(session, booking_id, cancelled_by="client")
    await session.commit()

    await callback.answer("Запись отменена.")
    await callback.message.edit_text(
        callback.message.text + "\n\n<i>❌ Отменено</i>",
        reply_markup=None,
    )

    tz = ZoneInfo(settings.studio_timezone)
    local = (
        start_time.astimezone(tz)
        if start_time.tzinfo
        else start_time.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    )
    admin_text = (
        f"❌ <b>Отмена записи (из напоминания)</b>\n\n"
        f"Клиент: {callback.from_user.full_name}\n"
        f"Дата: {local.strftime('%d.%m.%Y')} в {local.strftime('%H:%M')}"
    )
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, admin_text)
        except Exception:
            pass


# ── Review flow ───────────────────────────────────────────────────────────────

@router.callback_query(ReviewRatingCD.filter())
async def on_review_rating(
    callback: CallbackQuery,
    callback_data: ReviewRatingCD,
    state: FSMContext,
) -> None:
    await state.set_state(ReviewFSM.entering_comment)
    await state.update_data(
        booking_id=callback_data.booking_id,
        rating=callback_data.rating,
    )
    stars = "⭐" * callback_data.rating
    await callback.answer()
    await callback.message.edit_text(
        f"Оценка: {stars}\n\nНапишите комментарий или нажмите «Пропустить»:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Пропустить", callback_data="review_skip")
        ]]),
    )


@router.callback_query(ReviewFSM.entering_comment, F.data == "review_skip")
async def on_review_skip(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    await _save_review(callback.message, session, state, comment=None)
    await callback.answer()


@router.message(ReviewFSM.entering_comment)
async def on_review_comment(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    await _save_review(message, session, state, comment=message.text.strip())


async def _save_review(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    comment: str | None,
) -> None:
    from sqlalchemy import select
    from db.models import Client

    data = await state.get_data()
    await state.clear()

    client_result = await session.execute(
        select(Client).where(Client.telegram_id == message.chat.id)
    )
    client = client_result.scalar_one_or_none()
    if client is None:
        await message.answer("Не удалось сохранить отзыв.", reply_markup=MAIN_MENU)
        return

    await create_review(
        session,
        booking_id=uuid.UUID(data["booking_id"]),
        client_id=client.id,
        rating=data["rating"],
        comment=comment,
    )
    await session.commit()
    await message.answer("Спасибо за отзыв! 🙏", reply_markup=MAIN_MENU)


# ── Public keyboard builders (used by scheduler) ──────────────────────────────

def reminder_24h_keyboard(booking_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Приду",
            callback_data=ReminderAckCD(booking_id=booking_id).pack(),
        ),
        InlineKeyboardButton(
            text="❌ Отменить",
            callback_data=ReminderCancelCD(booking_id=booking_id).pack(),
        ),
    ]])


def review_request_keyboard(booking_id: str) -> InlineKeyboardMarkup:
    buttons = [[
        InlineKeyboardButton(
            text=str(i),
            callback_data=ReviewRatingCD(booking_id=booking_id, rating=i).pack(),
        )
        for i in range(1, 6)
    ]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
