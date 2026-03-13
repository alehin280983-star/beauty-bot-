from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards.booking import (
    MasterCD,
    ServiceCD,
    TimeCD,
    masters_keyboard,
    services_keyboard,
    time_slots_keyboard,
)
from bot.keyboards.calendar import CalendarNavCD, DateCD, calendar_keyboard
from bot.keyboards.main_menu import MAIN_MENU
from config import settings
from db.models import Client
from db.queries.bookings import NotEnoughSlots, SlotAlreadyTaken, cancel_booking, create_booking
from db.queries.clients import get_or_create_client, save_client_phone
from db.queries.masters import get_active_masters
from db.queries.services import get_services_for_master
from db.queries.slots import get_available_slots, get_dates_with_available_slots

router = Router()


class BookingFSM(StatesGroup):
    choosing_master = State()
    choosing_service = State()
    choosing_date = State()
    choosing_time = State()
    confirming = State()
    waiting_phone_post_booking = State()


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await get_or_create_client(
        session,
        telegram_id=message.from_user.id,
        first_name=message.from_user.first_name,
    )
    await session.commit()
    if message.from_user.id in settings.admin_ids:
        from bot.keyboards.admin_menu import ADMIN_MENU as _ADMIN_MENU
        await message.answer(
            f"Ласкаво просимо, адміне! <b>{settings.studio_name}</b>",
            reply_markup=_ADMIN_MENU,
        )
    else:
        await message.answer(
            f"Ласкаво просимо до <b>{settings.studio_name}</b>! 👋\n\nОберіть дію:",
            reply_markup=MAIN_MENU,
        )


# ── Прайс ─────────────────────────────────────────────────────────────────────

@router.message(F.text == "💰 Прайс")
async def cmd_price(message: Message, session: AsyncSession) -> None:
    services = await get_visible_services(session)
    if not services:
        await message.answer("Послуги ще не додано.", reply_markup=MAIN_MENU)
        return
    lines = [f"<b>{s.name}</b> — {s.duration_min} хв — {int(s.price)} грн" for s in services]
    await message.answer("\n".join(lines), reply_markup=MAIN_MENU)


# ── О нас ─────────────────────────────────────────────────────────────────────

@router.message(F.text == "ℹ️ Про нас")
async def cmd_about(message: Message) -> None:
    await message.answer(
        f"<b>{settings.studio_name}</b>\n\n"
        f"📍 {settings.studio_address}\n"
        f"📞 {settings.studio_phone}",
        reply_markup=MAIN_MENU,
    )


# ── Записатись: вибір майстра ─────────────────────────────────────────────────

@router.message(F.text == "📋 Записатись")
async def cmd_book(message: Message, session: AsyncSession, state: FSMContext) -> None:
    masters = await get_active_masters(session)
    if not masters:
        await message.answer("Майстри ще не додані.", reply_markup=MAIN_MENU)
        return
    await state.set_state(BookingFSM.choosing_master)
    await message.answer("Оберіть майстра:", reply_markup=masters_keyboard(masters))


@router.callback_query(BookingFSM.choosing_master, MasterCD.filter())
async def on_master_chosen(
    callback: CallbackQuery,
    callback_data: MasterCD,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    master_id = uuid.UUID(callback_data.master_id)
    masters = await get_active_masters(session)
    master = next((m for m in masters if m.id == master_id), None)
    if master is None:
        await callback.answer("Майстер недоступний. Почніть спочатку.")
        await state.clear()
        return

    await state.update_data(master_id=str(master.id), master_name=master.name)

    services = await get_services_for_master(session, master_id)
    if not services:
        await callback.answer("Немає послуг для цього майстра.")
        return

    await callback.answer()
    await state.set_state(BookingFSM.choosing_service)
    await callback.message.edit_text(
        f"Майстер: <b>{master.name}</b>\n\nОберіть послугу:",
        reply_markup=services_keyboard(services),
    )


# ── Вибір послуги ─────────────────────────────────────────────────────────────

@router.callback_query(BookingFSM.choosing_service, ServiceCD.filter())
async def on_service_chosen(
    callback: CallbackQuery,
    callback_data: ServiceCD,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    master_id = uuid.UUID(data["master_id"])
    service_id = uuid.UUID(callback_data.service_id)

    services = await get_services_for_master(session, master_id)
    service = next((s for s in services if s.id == service_id), None)
    if service is None:
        await callback.answer("Послуга недоступна. Почніть спочатку.")
        await state.clear()
        return

    await state.update_data(
        service_id=str(service.id),
        service_name=service.name,
        duration_min=service.duration_min,
        price=str(service.price),
    )
    await callback.answer()
    await _show_calendar(
        callback.message, session, state,
        service.duration_min, service_id, master_id, edit=True,
    )


@router.callback_query(BookingFSM.choosing_service, F.data == "back_to_master")
async def back_to_master_from_service(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    masters = await get_active_masters(session)
    await state.set_state(BookingFSM.choosing_master)
    await callback.answer()
    await callback.message.edit_text(
        "Оберіть майстра:", reply_markup=masters_keyboard(masters)
    )


# ── Вибір дати (календар) ─────────────────────────────────────────────────────

async def _show_calendar(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    duration_min: int,
    service_id: uuid.UUID,
    master_id: uuid.UUID,
    edit: bool = False,
) -> None:
    today = date.today()
    max_date = today + timedelta(days=13)

    available = await get_dates_with_available_slots(session, master_id, duration_min, 30, days=14)
    available_set = set(available)

    await state.update_data(
        available_dates=[d.isoformat() for d in available],
        cal_year=today.year,
        cal_month=today.month,
    )
    await state.set_state(BookingFSM.choosing_date)

    data = await state.get_data()
    text = (
        f"Послуга: <b>{data['service_name']}</b>\n"
        f"Майстер: <b>{data['master_name']}</b>\n\n"
        "Оберіть дату:"
    )
    kb = calendar_keyboard(today.year, today.month, available_set, today, max_date)

    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.callback_query(BookingFSM.choosing_date, CalendarNavCD.filter())
async def on_calendar_nav(
    callback: CallbackQuery,
    callback_data: CalendarNavCD,
    state: FSMContext,
) -> None:
    if callback_data.action == "ignore":
        await callback.answer()
        return

    data = await state.get_data()
    available_set = {date.fromisoformat(d) for d in data.get("available_dates", [])}
    today = date.today()
    max_date = today + timedelta(days=13)

    kb = calendar_keyboard(
        callback_data.year, callback_data.month, available_set, today, max_date
    )
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=kb)


@router.callback_query(BookingFSM.choosing_date, DateCD.filter())
async def on_date_chosen(
    callback: CallbackQuery,
    callback_data: DateCD,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    chosen_date = date.fromisoformat(callback_data.date)
    data = await state.get_data()
    available_set = {date.fromisoformat(d) for d in data.get("available_dates", [])}

    if chosen_date not in available_set:
        await callback.answer("На жаль, на цю дату всі майстри зайняті. Виберіть, будь ласка, іншу дату.", show_alert=True)
        return

    master_id = uuid.UUID(data["master_id"])
    duration_min = data["duration_min"]

    slots = await get_available_slots(session, master_id, chosen_date, duration_min, 30)
    if not slots:
        await callback.answer("На жаль, на цю дату всі майстри зайняті. Виберіть, будь ласка, іншу дату.", show_alert=True)
        return

    await state.update_data(chosen_date=chosen_date.isoformat())
    await state.set_state(BookingFSM.choosing_time)
    await callback.answer()

    tz = ZoneInfo(settings.studio_timezone)
    await callback.message.edit_text(
        f"Послуга: <b>{data['service_name']}</b>\n"
        f"Майстер: <b>{data['master_name']}</b>\n"
        f"Дата: <b>{chosen_date.strftime('%d.%m.%Y')}</b>\n\n"
        "Оберіть час:",
        reply_markup=time_slots_keyboard(slots, tz),
    )


@router.callback_query(BookingFSM.choosing_date, F.data == "back_to_service")
async def back_to_service_from_date(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    data = await state.get_data()
    master_id = uuid.UUID(data["master_id"])
    services = await get_services_for_master(session, master_id)
    await state.set_state(BookingFSM.choosing_service)
    await callback.answer()
    await callback.message.edit_text(
        f"Майстер: <b>{data['master_name']}</b>\n\nОберіть послугу:",
        reply_markup=services_keyboard(services),
    )


# ── Вибір часу ────────────────────────────────────────────────────────────────

@router.callback_query(BookingFSM.choosing_time, TimeCD.filter())
async def on_time_chosen(
    callback: CallbackQuery,
    callback_data: TimeCD,
    state: FSMContext,
) -> None:
    from datetime import datetime, timezone as _tz
    dt_utc = datetime.fromtimestamp(callback_data.ts, tz=_tz.utc)
    await state.update_data(slot_start=dt_utc.isoformat())
    await state.set_state(BookingFSM.confirming)
    await callback.answer()

    data = await state.get_data()
    tz = ZoneInfo(settings.studio_timezone)
    local = dt_utc.astimezone(tz)

    await callback.message.edit_text(
        f"<b>Підтвердіть запис:</b>\n\n"
        f"💇 Послуга: {data['service_name']}\n"
        f"👤 Майстер: {data['master_name']}\n"
        f"📅 Дата: {local.strftime('%d.%m.%Y')}\n"
        f"🕐 Час: {local.strftime('%H:%M')}\n"
        f"💰 Ціна: {data['price']} грн\n\n"
        "Все вірно?",
        reply_markup=_confirm_keyboard(),
    )


def _confirm_keyboard():
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Підтвердити", callback_data="confirm_booking"),
            InlineKeyboardButton(text="❌ Скасувати", callback_data="cancel_booking_fsm"),
        ]
    ])


# ── Підтвердження / скасування FSM ───────────────────────────────────────────

@router.callback_query(BookingFSM.confirming, F.data == "confirm_booking")
async def on_confirm_booking(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    data = await state.get_data()
    from sqlalchemy import select as sa_select
    client_result = await session.execute(
        sa_select(Client).where(Client.telegram_id == callback.from_user.id)
    )
    client = client_result.scalar_one()

    slot_start = datetime.fromisoformat(data["slot_start"])
    try:
        booking = await create_booking(
            session,
            client_id=client.id,
            master_id=uuid.UUID(data["master_id"]),
            service_id=uuid.UUID(data["service_id"]),
            slot_start=slot_start,
            service_duration=data["duration_min"],
            service_price=Decimal(data["price"]),
            step_min=30,
        )
        await session.commit()
    except (SlotAlreadyTaken, NotEnoughSlots):
        await session.rollback()
        await callback.answer("Цей час вже зайнятий. Оберіть інший.", show_alert=True)
        # Return to time selection keeping service/master/date
        chosen_date = date.fromisoformat(data["chosen_date"])
        master_id = uuid.UUID(data["master_id"])
        slots = await get_available_slots(
            session, master_id, chosen_date, data["duration_min"], 30
        )
        await state.set_state(BookingFSM.choosing_time)
        tz = ZoneInfo(settings.studio_timezone)
        await callback.message.edit_text(
            f"Послуга: <b>{data['service_name']}</b>\n"
            f"Майстер: <b>{data['master_name']}</b>\n"
            f"Дата: <b>{chosen_date.strftime('%d.%m.%Y')}</b>\n\n"
            "Оберіть час:",
            reply_markup=time_slots_keyboard(slots, tz),
        )
        return

    # Check if phone is needed
    if not client.phone:
        from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
        await state.set_state(BookingFSM.waiting_phone_post_booking)
        await state.update_data(booking_id=str(booking.id))
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📱 Поділитись номером", request_contact=True)],
                [KeyboardButton(text="❌ Скасувати запис")],
            ],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await callback.answer()
        await callback.message.edit_text(
            "✅ Майже готово!\n\n"
            "Для підтвердження запису поділіться номером телефону.\n"
            "Якщо відмовитесь — запис буде скасовано."
        )
        await callback.message.answer("Натисніть кнопку нижче:", reply_markup=kb)
        return

    await callback.answer("Запис підтверджено! ✅")
    await state.clear()
    await _finish_booking(callback.message, callback.from_user, data, slot_start, bot, phone=client.phone or "")


async def _finish_booking(message, from_user, data: dict, slot_start: datetime, bot: Bot, phone: str = "") -> None:
    tz = ZoneInfo(settings.studio_timezone)
    if slot_start.tzinfo:
        local = slot_start.astimezone(tz)
    else:
        local = slot_start.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)

    await message.answer(
        f"✅ <b>Запис створено!</b>\n\n"
        f"💇 {data['service_name']}\n"
        f"👤 {data['master_name']}\n"
        f"📅 {local.strftime('%d.%m.%Y')} о {local.strftime('%H:%M')}\n"
        f"💰 {data['price']} грн",
        reply_markup=MAIN_MENU,
    )

    admin_text = (
        f"📌 <b>Новий запис</b>\n\n"
        f"Клієнт: {from_user.full_name} (@{from_user.username or '—'})\n"
        f"Телефон: {phone or '—'}\n"
        f"Послуга: {data['service_name']}\n"
        f"Майстер: {data['master_name']}\n"
        f"Дата: {local.strftime('%d.%m.%Y')} о {local.strftime('%H:%M')}\n"
        f"Ціна: {data['price']} грн"
    )
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, admin_text)
        except Exception:
            pass


@router.message(BookingFSM.waiting_phone_post_booking, F.contact)
async def on_phone_post_booking(
    message: Message, session: AsyncSession, state: FSMContext, bot: Bot
) -> None:
    from aiogram.types import ReplyKeyboardRemove
    phone = message.contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone
    await save_client_phone(session, message.from_user.id, phone)
    await session.commit()

    data = await state.get_data()
    await state.clear()
    await message.answer("✅ Номер збережено!", reply_markup=ReplyKeyboardRemove())

    slot_start = datetime.fromisoformat(data["slot_start"])
    await _finish_booking(message, message.from_user, data, slot_start, bot, phone=phone)


@router.message(BookingFSM.waiting_phone_post_booking, F.text == "❌ Скасувати запис")
async def on_cancel_post_booking(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    from aiogram.types import ReplyKeyboardRemove
    data = await state.get_data()
    booking_id = uuid.UUID(data["booking_id"])
    await cancel_booking(session, booking_id)
    await session.commit()
    await state.clear()
    await message.answer(
        "Запис скасовано. Номер телефону не збережено.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer("Оберіть дію:", reply_markup=MAIN_MENU)


@router.callback_query(BookingFSM.confirming, F.data == "cancel_booking_fsm")
async def on_cancel_booking_fsm(
    callback: CallbackQuery, state: FSMContext
) -> None:
    await state.clear()
    await callback.answer()
    await callback.message.edit_text("Запис скасовано.")
    await callback.message.answer("Оберіть дію:", reply_markup=MAIN_MENU)


@router.callback_query(BookingFSM.choosing_time, F.data == "back_to_date")
async def back_to_date(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    data = await state.get_data()
    master_id = uuid.UUID(data["master_id"])
    duration_min = data["duration_min"]
    service_id = uuid.UUID(data["service_id"])

    await _show_calendar(
        callback.message, session, state,
        duration_min, service_id, master_id, edit=True,
    )
    await callback.answer()


# ── Fallback ──────────────────────────────────────────────────────────────────

@router.message()
async def fallback(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is None:
        await message.answer("Оберіть дію:", reply_markup=MAIN_MENU)
