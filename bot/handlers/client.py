from __future__ import annotations

import uuid
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
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
from db.queries.clients import get_or_create_client
from db.queries.masters import get_masters_for_service
from db.queries.services import get_visible_services
from db.queries.slots import get_available_slots, get_dates_with_available_slots

router = Router()


class BookingFSM(StatesGroup):
    choosing_service = State()
    choosing_master = State()
    choosing_date = State()
    choosing_time = State()
    confirming = State()


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
    await message.answer(
        f"Добро пожаловать в <b>{settings.studio_name}</b>! 👋\n\nВыберите действие:",
        reply_markup=MAIN_MENU,
    )


# ── Прайс ─────────────────────────────────────────────────────────────────────

@router.message(F.text == "💰 Прайс")
async def cmd_price(message: Message, session: AsyncSession) -> None:
    services = await get_visible_services(session)
    if not services:
        await message.answer("Услуги пока не добавлены.", reply_markup=MAIN_MENU)
        return
    lines = [f"<b>{s.name}</b> — {s.duration_min} мин — {int(s.price)} руб" for s in services]
    await message.answer("\n".join(lines), reply_markup=MAIN_MENU)


# ── О нас ─────────────────────────────────────────────────────────────────────

@router.message(F.text == "ℹ️ О нас")
async def cmd_about(message: Message) -> None:
    await message.answer(
        f"<b>{settings.studio_name}</b>\n\n"
        f"📍 {settings.studio_address}\n"
        f"📞 {settings.studio_phone}",
        reply_markup=MAIN_MENU,
    )


# ── Записаться: выбор услуги ──────────────────────────────────────────────────

@router.message(F.text == "📋 Записаться")
async def cmd_book(message: Message, session: AsyncSession, state: FSMContext) -> None:
    services = await get_visible_services(session)
    if not services:
        await message.answer("Услуги пока не добавлены.", reply_markup=MAIN_MENU)
        return
    await state.set_state(BookingFSM.choosing_service)
    await message.answer("Выберите услугу:", reply_markup=services_keyboard(services))


@router.callback_query(BookingFSM.choosing_service, ServiceCD.filter())
async def on_service_chosen(
    callback: CallbackQuery,
    callback_data: ServiceCD,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    service_id = uuid.UUID(callback_data.service_id)
    services = await get_visible_services(session)
    service = next((s for s in services if s.id == service_id), None)
    if service is None:
        await callback.answer("Услуга недоступна. Начните заново.")
        await state.clear()
        return

    await state.update_data(
        service_id=str(service.id),
        service_name=service.name,
        duration_min=service.duration_min,
        price=str(service.price),
    )

    masters = await get_masters_for_service(session, service_id)
    if not masters:
        await callback.answer("Нет доступных мастеров для этой услуги.")
        return

    await callback.answer()

    if len(masters) == 1:
        # Auto-select single master
        master = masters[0]
        await state.update_data(master_id=str(master.id), master_name=master.name)
        await _show_calendar(callback.message, session, state, service.duration_min, service.id, master.id, edit=True)
    else:
        await state.set_state(BookingFSM.choosing_master)
        await callback.message.edit_text(
            f"Услуга: <b>{service.name}</b>\n\nВыберите мастера:",
            reply_markup=masters_keyboard(masters),
        )


# ── Выбор мастера ─────────────────────────────────────────────────────────────

@router.callback_query(BookingFSM.choosing_master, MasterCD.filter())
async def on_master_chosen(
    callback: CallbackQuery,
    callback_data: MasterCD,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    master_id = uuid.UUID(callback_data.master_id)
    data = await state.get_data()
    service_id = uuid.UUID(data["service_id"])

    masters = await get_masters_for_service(session, service_id)
    master = next((m for m in masters if m.id == master_id), None)
    if master is None:
        await callback.answer("Мастер недоступен. Начните заново.")
        await state.clear()
        return

    await state.update_data(master_id=str(master.id), master_name=master.name)
    await callback.answer()
    await _show_calendar(
        callback.message, session, state,
        data["duration_min"], service_id, master_id, edit=True,
    )


@router.callback_query(BookingFSM.choosing_master, F.data == "back_to_service")
async def back_to_service(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    services = await get_visible_services(session)
    await state.set_state(BookingFSM.choosing_service)
    await callback.answer()
    await callback.message.edit_text(
        "Выберите услугу:", reply_markup=services_keyboard(services)
    )


# ── Выбор даты (календарь) ────────────────────────────────────────────────────

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
    max_date = today + timedelta(days=29)

    available = await get_dates_with_available_slots(session, master_id, duration_min, 30)
    available_set = set(available)

    await state.update_data(
        available_dates=[d.isoformat() for d in available],
        cal_year=today.year,
        cal_month=today.month,
    )
    await state.set_state(BookingFSM.choosing_date)

    data = await state.get_data()
    text = (
        f"Услуга: <b>{data['service_name']}</b>\n"
        f"Мастер: <b>{data['master_name']}</b>\n\n"
        "Выберите дату:"
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
    max_date = today + timedelta(days=29)

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
        await callback.answer("На эту дату нет свободного времени.")
        return

    master_id = uuid.UUID(data["master_id"])
    duration_min = data["duration_min"]

    slots = await get_available_slots(session, master_id, chosen_date, duration_min, 30)
    if not slots:
        await callback.answer("Слоты заняты. Выберите другую дату.")
        return

    await state.update_data(chosen_date=chosen_date.isoformat())
    await state.set_state(BookingFSM.choosing_time)
    await callback.answer()

    tz = ZoneInfo(settings.studio_timezone)
    await callback.message.edit_text(
        f"Услуга: <b>{data['service_name']}</b>\n"
        f"Мастер: <b>{data['master_name']}</b>\n"
        f"Дата: <b>{chosen_date.strftime('%d.%m.%Y')}</b>\n\n"
        "Выберите время:",
        reply_markup=time_slots_keyboard(slots, tz),
    )


@router.callback_query(BookingFSM.choosing_date, F.data == "back_to_master")
async def back_to_master_from_date(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    data = await state.get_data()
    service_id = uuid.UUID(data["service_id"])
    masters = await get_masters_for_service(session, service_id)

    if len(masters) == 1:
        # Skip master step, go back to service
        services = await get_visible_services(session)
        await state.set_state(BookingFSM.choosing_service)
        await callback.answer()
        await callback.message.edit_text(
            "Выберите услугу:", reply_markup=services_keyboard(services)
        )
    else:
        await state.set_state(BookingFSM.choosing_master)
        await callback.answer()
        await callback.message.edit_text(
            f"Услуга: <b>{data['service_name']}</b>\n\nВыберите мастера:",
            reply_markup=masters_keyboard(masters),
        )


# ── Выбор времени ─────────────────────────────────────────────────────────────

@router.callback_query(BookingFSM.choosing_time, TimeCD.filter())
async def on_time_chosen(
    callback: CallbackQuery,
    callback_data: TimeCD,
    state: FSMContext,
) -> None:
    await state.update_data(slot_start=callback_data.starts_at)
    await state.set_state(BookingFSM.confirming)
    await callback.answer()

    data = await state.get_data()
    from datetime import datetime
    tz = ZoneInfo(settings.studio_timezone)
    dt = datetime.fromisoformat(callback_data.starts_at)
    if dt.tzinfo is not None:
        local = dt.astimezone(tz)
    else:
        local = dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)

    await callback.message.edit_text(
        f"<b>Подтвердите запись:</b>\n\n"
        f"💇 Услуга: {data['service_name']}\n"
        f"👤 Мастер: {data['master_name']}\n"
        f"📅 Дата: {local.strftime('%d.%m.%Y')}\n"
        f"🕐 Время: {local.strftime('%H:%M')}\n"
        f"💰 Цена: {data['price']} руб\n\n"
        "Всё верно?",
        reply_markup=_confirm_keyboard(),
    )


def _confirm_keyboard():
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_booking"),
            InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_booking_fsm"),
        ]
    ])


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
        await message.answer("Выберите действие:", reply_markup=MAIN_MENU)
