from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
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
from zoneinfo import ZoneInfo

from bot.filters import AdminFilter
from bot.keyboards.calendar import CalendarNavCD, DateCD, calendar_keyboard
from bot.keyboards.booking import TimeCD, time_slots_keyboard
from config import settings
from db.queries.bookings import (
    cancel_booking,
    change_booking_service,
    create_booking,
    get_bookings_for_date,
    get_upcoming_bookings,
    reschedule_booking,
)
from db.exceptions import NotEnoughSlots, SlotAlreadyTaken
from db.queries.clients import create_phone_client, find_client_by_phone
from db.queries.masters import (
    create_master,
    get_active_masters,
    get_masters_for_service,
    toggle_master_active,
)
from db.queries.reviews import get_recent_reviews
from db.queries.services import (
    create_service,
    get_visible_services,
    toggle_service_visible,
    update_service,
)
from db.queries.slots import block_slot, block_slots_range, generate_slots, get_available_slots, get_dates_with_available_slots, get_day_schedule, get_slots_for_date, unblock_slots_range

router = Router()
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


# ── CallbackData ───────────────────────────────────────────────────────────────

class AdminMasterActionCD(CallbackData, prefix="adm_m"):
    master_id: str
    action: str  # toggle | services | set_photo


class AdminServiceActionCD(CallbackData, prefix="adm_s"):
    service_id: str
    action: str  # toggle | price


class AdminMasterServiceCD(CallbackData, prefix="adm_ms"):
    master_id: str
    service_id: str
    action: str  # add | remove


class AdminSlotActionCD(CallbackData, prefix="adm_sl"):
    slot_id: str
    action: str  # block | unblock


# ── FSM states ────────────────────────────────────────────────────────────────

class AdminSlotsFSM(StatesGroup):
    choosing_master = State()
    choosing_date = State()
    choosing_hours = State()


class AdminServiceFSM(StatesGroup):
    adding_name = State()
    adding_duration = State()
    adding_price = State()
    updating_price = State()


class AdminMasterFSM(StatesGroup):
    adding_name = State()
    awaiting_photo = State()


class AdminMasterServiceFSM(StatesGroup):
    choosing_service = State()


class AdminBookFSM(StatesGroup):
    choosing_master = State()
    choosing_service = State()
    choosing_date = State()
    choosing_time = State()
    entering_name = State()
    entering_phone = State()


class AdminBlockFSM(StatesGroup):
    choosing_master = State()
    choosing_date = State()
    entering_range = State()


class AdminUnblockFSM(StatesGroup):
    choosing_master = State()
    choosing_date = State()
    entering_range = State()


class AdminEditFSM(StatesGroup):
    choosing_booking = State()
    choosing_action = State()
    reschedule_date = State()
    reschedule_time = State()
    change_service = State()


class AdminScheduleFSM(StatesGroup):
    choosing_date = State()


# ── CallbackData for admin_book ───────────────────────────────────────────────

class AdminBookMasterCD(CallbackData, prefix="ab_m"):
    master_id: str


class AdminBookServiceCD(CallbackData, prefix="ab_s"):
    service_id: str


class AdminBlockMasterCD(CallbackData, prefix="abl_m"):
    master_id: str
    action: str  # block | unblock


class AdminEditBookingCD(CallbackData, prefix="aeb"):
    booking_id: str


class AdminEditActionCD(CallbackData, prefix="aea"):
    booking_id: str
    action: str  # reschedule | service | delete | confirm_delete


class AdminEditServiceCD(CallbackData, prefix="aes"):
    booking_id: str
    service_id: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tz() -> ZoneInfo:
    return ZoneInfo(settings.studio_timezone)


def _fmt_time(dt: datetime) -> str:
    tz = _tz()
    if dt.tzinfo:
        local = dt.astimezone(tz)
    else:
        local = dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    return local.strftime("%H:%M")


def _fmt_date(dt: datetime) -> str:
    tz = _tz()
    if dt.tzinfo:
        local = dt.astimezone(tz)
    else:
        local = dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    return local.strftime("%d.%m.%Y")


# ── /admin ─────────────────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    await message.answer(
        "<b>Адмін-панель</b>\n\n"
        "/admin_today — записи на сьогодні\n"
        "/admin_tomorrow — записи на завтра\n"
        "/admin_week — всі записи на 2 тижні\n"
        "/admin_slots — генерація слотів\n"
        "/admin_block — заблокувати години майстра\n"
        "/admin_unblock — розблокувати години майстра\n"
        "/admin_services — управління послугами\n"
        "/admin_masters — управління майстрами\n"
        "/admin_reviews — останні відгуки\n"
        "/admin_book — ручний запис клієнта"
    )


# ── /admin_today / /admin_tomorrow ────────────────────────────────────────────

@router.message(Command("admin_today"))
async def cmd_admin_today(message: Message, session: AsyncSession) -> None:
    await _send_bookings_for_date(message, session, date.today())


@router.message(Command("admin_tomorrow"))
async def cmd_admin_tomorrow(message: Message, session: AsyncSession) -> None:
    await _send_bookings_for_date(message, session, date.today() + timedelta(days=1))


@router.message(Command("admin_week"))
async def cmd_admin_week(message: Message, session: AsyncSession) -> None:
    bookings = await get_upcoming_bookings(session, days=14)
    if not bookings:
        await message.answer("Немає записів на найближчі 2 тижні.")
        return

    tz = _tz()
    from collections import defaultdict
    by_date: dict = defaultdict(list)
    for b in bookings:
        st = b["start_time"]
        local = st.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz) if not st.tzinfo else st.astimezone(tz)
        by_date[local.date()].append((local, b))

    lines = [f"<b>Записи на 2 тижні:</b>\n"]
    for d in sorted(by_date.keys()):
        lines.append(f"\n📅 <b>{d.strftime('%d.%m.%Y')}</b>")
        for local, b in by_date[d]:
            client = b["client_name"] or b["client_phone"] or "—"
            lines.append(f"  {local.strftime('%H:%M')} — {client} — {b['service_name']} — {b['master_name']}")

    await message.answer("\n".join(lines))


async def _send_bookings_for_date(
    message: Message, session: AsyncSession, target: date
) -> None:
    bookings = await get_bookings_for_date(session, target)
    label = "сьогодні" if target == date.today() else "завтра"
    if not bookings:
        await message.answer(f"Записів на {label} немає.")
        return

    lines = [f"<b>Записи на {label} ({target.strftime('%d.%m.%Y')}):</b>\n"]
    for b in bookings:
        start: datetime = b["start_time"]
        client = b["client_name"] or b["client_phone"] or "—"
        lines.append(
            f"{_fmt_time(start)} — {client} — {b['service_name']} — {b['master_name']}"
        )
    await message.answer("\n".join(lines))


# ── /admin_slots ───────────────────────────────────────────────────────────────

@router.message(Command("admin_slots"))
async def cmd_admin_slots(message: Message, session: AsyncSession, state: FSMContext) -> None:
    masters = await get_active_masters(session)
    if not masters:
        await message.answer("Немає активних майстрів.")
        return
    buttons = [
        [InlineKeyboardButton(
            text=m.name,
            callback_data=AdminMasterActionCD(master_id=str(m.id), action="slots").pack(),
        )]
        for m in masters
    ]
    await state.set_state(AdminSlotsFSM.choosing_master)
    await message.answer("Оберіть майстра:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(AdminSlotsFSM.choosing_master, AdminMasterActionCD.filter(F.action == "slots"))
async def admin_slots_master_chosen(
    callback: CallbackQuery, callback_data: AdminMasterActionCD, state: FSMContext
) -> None:
    await state.update_data(master_id=callback_data.master_id)
    await state.set_state(AdminSlotsFSM.choosing_date)
    await callback.answer()
    await callback.message.edit_text(
        "Введіть дату у форматі ДД.ММ.РРРР:"
    )


@router.message(AdminSlotsFSM.choosing_date)
async def admin_slots_date_entered(message: Message, state: FSMContext) -> None:
    try:
        d = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except ValueError:
        await message.answer("Невірний формат. Введіть дату як ДД.ММ.РРРР:")
        return
    await state.update_data(slot_date=d.isoformat())
    await state.set_state(AdminSlotsFSM.choosing_hours)
    await message.answer("Введіть години початку та кінця через пробіл (наприклад: <code>9 19</code>):")


@router.message(AdminSlotsFSM.choosing_hours)
async def admin_slots_hours_entered(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    try:
        parts = message.text.strip().split()
        start_hour, end_hour = int(parts[0]), int(parts[1])
        if not (0 <= start_hour < end_hour <= 24):
            raise ValueError
    except (ValueError, IndexError):
        await message.answer("Невірний формат. Введіть два числа через пробіл (наприклад: <code>9 19</code>):")
        return

    data = await state.get_data()
    master_id = uuid.UUID(data["master_id"])
    slot_date = date.fromisoformat(data["slot_date"])
    await state.clear()

    count = await generate_slots(session, master_id, slot_date, start_hour, end_hour, 30)
    await session.commit()
    await message.answer(
        f"✅ Створено {count} слотів на {slot_date.strftime('%d.%m.%Y')} "
        f"з {start_hour}:00 до {end_hour}:00."
    )


# ── /admin_services ────────────────────────────────────────────────────────────

@router.message(Command("admin_services"))
async def cmd_admin_services(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await _show_services_menu(message, session)


async def _show_services_menu(message: Message, session: AsyncSession) -> None:
    from db.models import Service
    from sqlalchemy import select
    result = await session.execute(select(Service).order_by(Service.name))
    services = list(result.scalars().all())

    buttons = []
    for s in services:
        status = "✅" if s.is_visible else "🚫"
        buttons.append([InlineKeyboardButton(
            text=f"{status} {s.name} — {s.duration_min}хв — {int(s.price)}грн",
            callback_data=AdminServiceActionCD(service_id=str(s.id), action="menu").pack(),
        )])
    buttons.append([InlineKeyboardButton(text="➕ Додати послугу", callback_data="admin_add_service")])
    await message.answer(
        "<b>Послуги:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "admin_add_service")
async def admin_add_service_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminServiceFSM.adding_name)
    await callback.answer()
    await callback.message.answer("Введіть назву послуги:")


@router.message(AdminServiceFSM.adding_name)
async def admin_service_name(message: Message, state: FSMContext) -> None:
    await state.update_data(service_name=message.text.strip())
    await state.set_state(AdminServiceFSM.adding_duration)
    await message.answer("Введіть тривалість у хвилинах:")


@router.message(AdminServiceFSM.adding_duration)
async def admin_service_duration(message: Message, state: FSMContext) -> None:
    try:
        duration = int(message.text.strip())
        if duration <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Введіть ціле число хвилин:")
        return
    await state.update_data(duration=duration)
    await state.set_state(AdminServiceFSM.adding_price)
    await message.answer("Введіть ціну в гривнях:")


@router.message(AdminServiceFSM.adding_price)
async def admin_service_price(message: Message, session: AsyncSession, state: FSMContext) -> None:
    from decimal import Decimal, InvalidOperation
    try:
        price = Decimal(message.text.strip().replace(",", "."))
        if price < 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer("Введіть коректну ціну:")
        return

    data = await state.get_data()
    await state.clear()
    service = await create_service(session, data["service_name"], data["duration"], price)
    await session.commit()
    await message.answer(f"✅ Послугу «{service.name}» додано.")


@router.callback_query(AdminServiceActionCD.filter(F.action == "menu"))
async def admin_service_menu(
    callback: CallbackQuery, callback_data: AdminServiceActionCD, session: AsyncSession
) -> None:
    from db.models import Service
    from sqlalchemy import select
    result = await session.execute(
        select(Service).where(Service.id == uuid.UUID(callback_data.service_id))
    )
    service = result.scalar_one_or_none()
    if service is None:
        await callback.answer("Послугу не знайдено.")
        return

    status = "видима" if service.is_visible else "прихована"
    buttons = [
        [InlineKeyboardButton(
            text="🔁 Приховати/показати",
            callback_data=AdminServiceActionCD(service_id=callback_data.service_id, action="toggle").pack(),
        )],
        [InlineKeyboardButton(
            text="💰 Змінити ціну",
            callback_data=AdminServiceActionCD(service_id=callback_data.service_id, action="price").pack(),
        )],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_services_back")],
    ]
    await callback.answer()
    await callback.message.edit_text(
        f"<b>{service.name}</b>\n"
        f"Тривалість: {service.duration_min} хв\n"
        f"Ціна: {int(service.price)} грн\n"
        f"Статус: {status}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(AdminServiceActionCD.filter(F.action == "toggle"))
async def admin_service_toggle(
    callback: CallbackQuery, callback_data: AdminServiceActionCD, session: AsyncSession
) -> None:
    service = await toggle_service_visible(session, uuid.UUID(callback_data.service_id))
    await session.commit()
    status = "видима ✅" if service.is_visible else "прихована 🚫"
    await callback.answer(f"Послуга тепер {status}")
    await callback.message.delete()


@router.callback_query(AdminServiceActionCD.filter(F.action == "price"))
async def admin_service_price_start(
    callback: CallbackQuery, callback_data: AdminServiceActionCD, state: FSMContext
) -> None:
    await state.set_state(AdminServiceFSM.updating_price)
    await state.update_data(service_id=callback_data.service_id)
    await callback.answer()
    await callback.message.answer("Введіть нову ціну:")


@router.message(AdminServiceFSM.updating_price)
async def admin_service_price_update(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    from decimal import Decimal, InvalidOperation
    try:
        price = Decimal(message.text.strip().replace(",", "."))
        if price < 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer("Введіть коректну ціну:")
        return

    data = await state.get_data()
    await state.clear()
    service = await update_service(session, uuid.UUID(data["service_id"]), price=price)
    await session.commit()
    await message.answer(f"✅ Ціну «{service.name}» оновлено: {int(service.price)} грн.")


@router.callback_query(F.data == "admin_services_back")
async def admin_services_back(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    await callback.message.delete()
    await _show_services_menu(callback.message, session)


# ── /admin_masters ─────────────────────────────────────────────────────────────

@router.message(Command("admin_masters"))
async def cmd_admin_masters(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await _show_masters_menu(message, session)


async def _show_masters_menu(message: Message, session: AsyncSession) -> None:
    from db.models import Master
    from sqlalchemy import select
    result = await session.execute(select(Master).order_by(Master.name))
    masters = list(result.scalars().all())

    buttons = []
    for m in masters:
        status = "✅" if m.is_active else "🚫"
        buttons.append([InlineKeyboardButton(
            text=f"{status} {m.name}",
            callback_data=AdminMasterActionCD(master_id=str(m.id), action="menu").pack(),
        )])
    buttons.append([InlineKeyboardButton(text="➕ Додати майстра", callback_data="admin_add_master")])
    await message.answer(
        "<b>Майстри:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "admin_add_master")
async def admin_add_master_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminMasterFSM.adding_name)
    await callback.answer()
    await callback.message.answer("Введіть ім'я майстра:")


@router.message(AdminMasterFSM.adding_name)
async def admin_master_name(message: Message, session: AsyncSession, state: FSMContext) -> None:
    name = message.text.strip()
    await state.clear()
    master = await create_master(session, name)
    await session.commit()
    await message.answer(f"✅ Майстра «{master.name}» додано.")


@router.callback_query(AdminMasterActionCD.filter(F.action == "menu"))
async def admin_master_menu(
    callback: CallbackQuery, callback_data: AdminMasterActionCD, session: AsyncSession
) -> None:
    from db.models import Master, MasterService, Service
    from sqlalchemy import select
    result = await session.execute(
        select(Master).where(Master.id == uuid.UUID(callback_data.master_id))
    )
    master = result.scalar_one_or_none()
    if master is None:
        await callback.answer("Майстра не знайдено.")
        return

    svc_result = await session.execute(
        select(Service)
        .join(MasterService, MasterService.service_id == Service.id)
        .where(MasterService.master_id == master.id)
    )
    master_services = list(svc_result.scalars().all())
    svc_names = ", ".join(s.name for s in master_services) or "немає"

    status = "активний" if master.is_active else "неактивний"
    buttons = [
        [InlineKeyboardButton(
            text="🔁 Активувати/деактивувати",
            callback_data=AdminMasterActionCD(master_id=callback_data.master_id, action="toggle").pack(),
        )],
        [InlineKeyboardButton(
            text="🛠 Управління послугами",
            callback_data=AdminMasterActionCD(master_id=callback_data.master_id, action="services").pack(),
        )],
        [InlineKeyboardButton(
            text="🖼 Встановити фото",
            callback_data=AdminMasterActionCD(master_id=callback_data.master_id, action="set_photo").pack(),
        )],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_masters_back")],
    ]
    await callback.answer()
    await callback.message.edit_text(
        f"<b>{master.name}</b>\nСтатус: {status}\nПослуги: {svc_names}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(AdminMasterActionCD.filter(F.action == "toggle"))
async def admin_master_toggle(
    callback: CallbackQuery, callback_data: AdminMasterActionCD, session: AsyncSession
) -> None:
    master = await toggle_master_active(session, uuid.UUID(callback_data.master_id))
    await session.commit()
    status = "активний ✅" if master.is_active else "деактивований 🚫"
    await callback.answer(f"Майстер тепер {status}")
    await callback.message.delete()


@router.callback_query(AdminMasterActionCD.filter(F.action == "services"))
async def admin_master_services(
    callback: CallbackQuery, callback_data: AdminMasterActionCD, session: AsyncSession
) -> None:
    from db.models import Master, MasterService, Service
    from sqlalchemy import select

    master_id = uuid.UUID(callback_data.master_id)
    result = await session.execute(select(Master).where(Master.id == master_id))
    master = result.scalar_one()

    all_svc = await get_visible_services(session)
    linked_result = await session.execute(
        select(MasterService.service_id).where(MasterService.master_id == master_id)
    )
    linked_ids = {row[0] for row in linked_result.all()}

    buttons = []
    for s in all_svc:
        linked = s.id in linked_ids
        label = f"✅ {s.name}" if linked else f"➕ {s.name}"
        action = "remove" if linked else "add"
        buttons.append([InlineKeyboardButton(
            text=label,
            callback_data=AdminMasterServiceCD(
                master_id=str(master_id), service_id=str(s.id), action=action
            ).pack(),
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_masters_back")])

    await callback.answer()
    await callback.message.edit_text(
        f"Послуги майстра <b>{master.name}</b>:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(AdminMasterServiceCD.filter())
async def admin_master_service_toggle(
    callback: CallbackQuery,
    callback_data: AdminMasterServiceCD,
    session: AsyncSession,
) -> None:
    from db.models import MasterService
    from sqlalchemy import delete, select

    master_id = uuid.UUID(callback_data.master_id)
    service_id = uuid.UUID(callback_data.service_id)

    if callback_data.action == "add":
        existing = await session.execute(
            select(MasterService)
            .where(MasterService.master_id == master_id)
            .where(MasterService.service_id == service_id)
        )
        if existing.scalar_one_or_none() is None:
            session.add(MasterService(master_id=master_id, service_id=service_id))
            await session.commit()
        await callback.answer("Послугу додано ✅")
    else:
        await session.execute(
            delete(MasterService)
            .where(MasterService.master_id == master_id)
            .where(MasterService.service_id == service_id)
        )
        await session.commit()
        await callback.answer("Послугу видалено")

    # Refresh the services list
    fake_cd = AdminMasterActionCD(master_id=callback_data.master_id, action="services")
    await admin_master_services(callback, fake_cd, session)


@router.callback_query(AdminMasterActionCD.filter(F.action == "set_photo"))
async def admin_master_set_photo_start(
    callback: CallbackQuery, callback_data: AdminMasterActionCD, state: FSMContext
) -> None:
    await state.set_state(AdminMasterFSM.awaiting_photo)
    await state.update_data(master_id=callback_data.master_id)
    await callback.answer()
    await callback.message.answer("Надішліть фото майстра:")


@router.message(AdminMasterFSM.awaiting_photo, F.photo)
async def admin_master_photo_received(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    from db.models import Master
    from sqlalchemy import select

    data = await state.get_data()
    await state.clear()
    file_id = message.photo[-1].file_id
    result = await session.execute(
        select(Master).where(Master.id == uuid.UUID(data["master_id"]))
    )
    master = result.scalar_one()
    master.photo_url = file_id
    await session.commit()
    await message.answer(f"✅ Фото майстра «{master.name}» оновлено.")


@router.callback_query(F.data == "admin_masters_back")
async def admin_masters_back(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    await callback.message.delete()
    await _show_masters_menu(callback.message, session)


# ── /admin_reviews ─────────────────────────────────────────────────────────────

@router.message(Command("admin_reviews"))
async def cmd_admin_reviews(message: Message, session: AsyncSession) -> None:
    reviews = await get_recent_reviews(session, limit=20)
    if not reviews:
        await message.answer("Відгуків поки немає.")
        return

    lines = ["<b>Останні відгуки:</b>\n"]
    for r in reviews:
        stars = "⭐" * r.rating
        comment = r.comment or "—"
        lines.append(f"{stars}\n{comment}\n")
    await message.answer("\n".join(lines))


# ── /admin_book ────────────────────────────────────────────────────────────────

@router.message(Command("admin_book"))
async def cmd_admin_book(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    masters = await get_active_masters(session)
    if not masters:
        await message.answer("Немає активних майстрів.")
        return
    buttons = [
        [InlineKeyboardButton(
            text=m.name,
            callback_data=AdminBookMasterCD(master_id=str(m.id)).pack(),
        )]
        for m in masters
    ]
    await state.set_state(AdminBookFSM.choosing_master)
    await message.answer("Оберіть майстра:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(AdminBookFSM.choosing_master, AdminBookMasterCD.filter())
async def admin_book_master_chosen(
    callback: CallbackQuery,
    callback_data: AdminBookMasterCD,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    master_id = uuid.UUID(callback_data.master_id)
    from db.models import Master
    from sqlalchemy import select
    result = await session.execute(select(Master).where(Master.id == master_id))
    master = result.scalar_one()

    from db.models import MasterService, Service
    svc_result = await session.execute(
        select(Service)
        .join(MasterService, MasterService.service_id == Service.id)
        .where(MasterService.master_id == master_id)
        .where(Service.is_visible == True)  # noqa: E712
        .order_by(Service.name)
    )
    services = list(svc_result.scalars().all())
    if not services:
        await callback.answer("У майстра немає послуг.", show_alert=True)
        return

    await state.update_data(master_id=str(master_id), master_name=master.name)
    await state.set_state(AdminBookFSM.choosing_service)

    buttons = [
        [InlineKeyboardButton(
            text=f"{s.name} — {s.duration_min} хв — {int(s.price)} грн",
            callback_data=AdminBookServiceCD(service_id=str(s.id)).pack(),
        )]
        for s in services
    ]
    await callback.answer()
    await callback.message.edit_text(
        f"Майстер: <b>{master.name}</b>\n\nОберіть послугу:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(AdminBookFSM.choosing_service, AdminBookServiceCD.filter())
async def admin_book_service_chosen(
    callback: CallbackQuery,
    callback_data: AdminBookServiceCD,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    service_id = uuid.UUID(callback_data.service_id)
    from db.models import Service
    from sqlalchemy import select
    result = await session.execute(select(Service).where(Service.id == service_id))
    service = result.scalar_one()

    await state.update_data(
        service_id=str(service_id),
        service_name=service.name,
        duration_min=service.duration_min,
        price=str(service.price),
    )

    data = await state.get_data()
    master_id = uuid.UUID(data["master_id"])
    today = date.today()
    max_date = today + timedelta(days=13)

    available = await get_dates_with_available_slots(session, master_id, service.duration_min, 30)
    await state.update_data(available_dates=[d.isoformat() for d in available])
    await state.set_state(AdminBookFSM.choosing_date)

    await callback.answer()
    await callback.message.edit_text(
        f"Майстер: <b>{data['master_name']}</b>\n"
        f"Послуга: <b>{service.name}</b>\n\n"
        "Оберіть дату:",
        reply_markup=calendar_keyboard(today.year, today.month, set(available), today, max_date),
    )


@router.callback_query(AdminBookFSM.choosing_date, CalendarNavCD.filter())
async def admin_book_cal_nav(
    callback: CallbackQuery, callback_data: CalendarNavCD, state: FSMContext
) -> None:
    if callback_data.action == "ignore":
        await callback.answer()
        return
    data = await state.get_data()
    available = {date.fromisoformat(d) for d in data.get("available_dates", [])}
    today = date.today()
    max_date = today + timedelta(days=13)
    await callback.answer()
    await callback.message.edit_reply_markup(
        reply_markup=calendar_keyboard(
            callback_data.year, callback_data.month, available, today, max_date
        )
    )


@router.callback_query(AdminBookFSM.choosing_date, DateCD.filter())
async def admin_book_date_chosen(
    callback: CallbackQuery,
    callback_data: DateCD,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    chosen_date = date.fromisoformat(callback_data.date)
    data = await state.get_data()
    available = {date.fromisoformat(d) for d in data.get("available_dates", [])}

    if chosen_date not in available:
        await callback.answer("На цю дату немає вільного часу.")
        return

    master_id = uuid.UUID(data["master_id"])
    slots = await get_available_slots(session, master_id, chosen_date, data["duration_min"], 30)
    if not slots:
        await callback.answer("Слоти зайняті, оберіть іншу дату.")
        return

    await state.update_data(chosen_date=chosen_date.isoformat())
    await state.set_state(AdminBookFSM.choosing_time)
    await callback.answer()
    await callback.message.edit_text(
        f"Майстер: <b>{data['master_name']}</b>\n"
        f"Послуга: <b>{data['service_name']}</b>\n"
        f"Дата: <b>{chosen_date.strftime('%d.%m.%Y')}</b>\n\n"
        "Оберіть час:",
        reply_markup=time_slots_keyboard(slots, _tz()),
    )


@router.callback_query(AdminBookFSM.choosing_time, TimeCD.filter())
async def admin_book_time_chosen(
    callback: CallbackQuery, callback_data: TimeCD, state: FSMContext
) -> None:
    from datetime import timezone as _tz_utc
    dt_utc = datetime.fromtimestamp(callback_data.ts, tz=_tz_utc.utc)
    await state.update_data(slot_start=dt_utc.isoformat())
    await state.set_state(AdminBookFSM.entering_name)
    await callback.answer()
    await callback.message.edit_text("Введіть ім'я клієнта:")


@router.message(AdminBookFSM.entering_name)
async def admin_book_name_entered(message: Message, state: FSMContext) -> None:
    await state.update_data(client_name=message.text.strip())
    await state.set_state(AdminBookFSM.entering_phone)
    await message.answer("Введіть номер телефону клієнта:")


@router.message(AdminBookFSM.entering_phone)
async def admin_book_phone_entered(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    from decimal import Decimal
    phone = message.text.strip()
    data = await state.get_data()
    await state.clear()

    # Find or create client by phone
    client = await find_client_by_phone(session, phone)
    if client is None:
        client = await create_phone_client(session, phone, first_name=data["client_name"])

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
        await message.answer("❌ Час вже зайнятий. Почніть спочатку з /admin_book.")
        return

    tz = _tz()
    if slot_start.tzinfo:
        local = slot_start.astimezone(tz)
    else:
        local = slot_start.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)

    await message.answer(
        f"✅ <b>Запис створено</b>\n\n"
        f"Клієнт: {data['client_name']} ({phone})\n"
        f"Послуга: {data['service_name']}\n"
        f"Майстер: {data['master_name']}\n"
        f"Дата: {local.strftime('%d.%m.%Y')} о {local.strftime('%H:%M')}\n"
        f"Ціна: {data['price']} грн"
    )


# ── /admin_block ───────────────────────────────────────────────────────────────

def _block_masters_keyboard(masters, action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=m.name,
            callback_data=AdminBlockMasterCD(master_id=str(m.id), action=action).pack(),
        )]
        for m in masters
    ])


@router.message(Command("admin_block"))
async def cmd_admin_block(message: Message, session: AsyncSession, state: FSMContext) -> None:
    masters = await get_active_masters(session)
    if not masters:
        await message.answer("Немає активних майстрів.")
        return
    await state.set_state(AdminBlockFSM.choosing_master)
    await message.answer("Оберіть майстра для блокування:", reply_markup=_block_masters_keyboard(masters, "block"))


@router.callback_query(AdminBlockFSM.choosing_master, AdminBlockMasterCD.filter(F.action == "block"))
async def admin_block_master_chosen(
    callback: CallbackQuery, callback_data: AdminBlockMasterCD, state: FSMContext
) -> None:
    await state.update_data(master_id=callback_data.master_id)
    await state.set_state(AdminBlockFSM.choosing_date)
    await callback.answer()
    await callback.message.edit_text("Введіть дату (ДД.ММ.РРРР):")


@router.message(AdminBlockFSM.choosing_date)
async def admin_block_date_entered(message: Message, state: FSMContext) -> None:
    try:
        d = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except ValueError:
        await message.answer("Невірний формат. Введіть дату як ДД.ММ.РРРР:")
        return
    await state.update_data(slot_date=d.isoformat())
    await state.set_state(AdminBlockFSM.entering_range)
    await message.answer(
        "Введіть діапазон годин через пробіл.\n"
        "Наприклад: <code>10 14</code> — заблокує з 10:00 до 14:00"
    )


@router.message(AdminBlockFSM.entering_range)
async def admin_block_range_entered(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    try:
        parts = message.text.strip().split()
        from_h, to_h = int(parts[0]), int(parts[1])
        if not (0 <= from_h < to_h <= 24):
            raise ValueError
    except (ValueError, IndexError):
        await message.answer("Невірний формат. Введіть два числа, наприклад: <code>10 14</code>")
        return

    data = await state.get_data()
    await state.clear()
    master_id = uuid.UUID(data["master_id"])
    slot_date = date.fromisoformat(data["slot_date"])

    count = await block_slots_range(session, master_id, slot_date, from_h, to_h)
    await session.commit()
    await message.answer(
        f"🔒 Заблоковано {count} слотів на {slot_date.strftime('%d.%m.%Y')} "
        f"з {from_h}:00 до {to_h}:00."
    )


# ── /admin_unblock ─────────────────────────────────────────────────────────────

@router.message(Command("admin_unblock"))
async def cmd_admin_unblock(message: Message, session: AsyncSession, state: FSMContext) -> None:
    masters = await get_active_masters(session)
    if not masters:
        await message.answer("Немає активних майстрів.")
        return
    await state.set_state(AdminUnblockFSM.choosing_master)
    await message.answer("Оберіть майстра для розблокування:", reply_markup=_block_masters_keyboard(masters, "unblock"))


@router.callback_query(AdminUnblockFSM.choosing_master, AdminBlockMasterCD.filter(F.action == "unblock"))
async def admin_unblock_master_chosen(
    callback: CallbackQuery, callback_data: AdminBlockMasterCD, state: FSMContext
) -> None:
    await state.update_data(master_id=callback_data.master_id)
    await state.set_state(AdminUnblockFSM.choosing_date)
    await callback.answer()
    await callback.message.edit_text("Введіть дату (ДД.ММ.РРРР):")


@router.message(AdminUnblockFSM.choosing_date)
async def admin_unblock_date_entered(message: Message, state: FSMContext) -> None:
    try:
        d = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except ValueError:
        await message.answer("Невірний формат. Введіть дату як ДД.ММ.РРРР:")
        return
    await state.update_data(slot_date=d.isoformat())
    await state.set_state(AdminUnblockFSM.entering_range)
    await message.answer(
        "Введіть діапазон годин через пробіл.\n"
        "Наприклад: <code>10 14</code> — розблокує з 10:00 до 14:00"
    )


@router.message(AdminUnblockFSM.entering_range)
async def admin_unblock_range_entered(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    try:
        parts = message.text.strip().split()
        from_h, to_h = int(parts[0]), int(parts[1])
        if not (0 <= from_h < to_h <= 24):
            raise ValueError
    except (ValueError, IndexError):
        await message.answer("Невірний формат. Введіть два числа, наприклад: <code>10 14</code>")
        return

    data = await state.get_data()
    await state.clear()
    master_id = uuid.UUID(data["master_id"])
    slot_date = date.fromisoformat(data["slot_date"])

    count = await unblock_slots_range(session, master_id, slot_date, from_h, to_h)
    await session.commit()
    await message.answer(
        f"🔓 Розблоковано {count} слотів на {slot_date.strftime('%d.%m.%Y')} "
        f"з {from_h}:00 до {to_h}:00."
    )


# ── Адмін-меню (кнопки) ───────────────────────────────────────────────────────

@router.message(F.text == "📋 Записи на 2 тижні")
async def admin_menu_week(message: Message, session: AsyncSession) -> None:
    await cmd_admin_week(message, session)


# ── Розклад дня ────────────────────────────────────────────────────────────────

@router.message(F.text == "🗓 Розклад дня")
async def admin_menu_schedule(message: Message, state: FSMContext) -> None:
    today = date.today()
    max_date = today + timedelta(days=13)
    await state.set_state(AdminScheduleFSM.choosing_date)
    await message.answer(
        "Оберіть дату:",
        reply_markup=calendar_keyboard(today.year, today.month, set(), today, max_date),
    )


@router.callback_query(AdminScheduleFSM.choosing_date, CalendarNavCD.filter())
async def admin_schedule_nav(
    callback: CallbackQuery, callback_data: CalendarNavCD, state: FSMContext,
) -> None:
    if callback_data.action == "ignore":
        await callback.answer()
        return
    today = date.today()
    max_date = today + timedelta(days=13)
    await callback.answer()
    await callback.message.edit_reply_markup(
        reply_markup=calendar_keyboard(
            callback_data.year, callback_data.month, set(), today, max_date
        )
    )


@router.callback_query(AdminScheduleFSM.choosing_date, DateCD.filter())
async def admin_schedule_date_chosen(
    callback: CallbackQuery, callback_data: DateCD,
    session: AsyncSession, state: FSMContext,
) -> None:
    chosen_date = date.fromisoformat(callback_data.date)
    await state.clear()
    await callback.answer()

    slots = await get_day_schedule(session, chosen_date)
    if not slots:
        await callback.message.edit_text(
            f"На {chosen_date.strftime('%d.%m.%Y')} слотів немає."
        )
        return

    tz = _tz()
    from collections import defaultdict
    by_master: dict = defaultdict(list)
    for s in slots:
        by_master[s["master_name"]].append(s)

    lines = [f"📅 <b>{chosen_date.strftime('%d.%m.%Y')}</b>\n"]
    prev_booking_id = {}

    for master_name, master_slots in sorted(by_master.items()):
        lines.append(f"\n<b>{master_name}</b>")
        prev_bid = None
        for s in master_slots:
            st = s["starts_at"]
            local = st.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz) if not st.tzinfo else st.astimezone(tz)
            time_str = local.strftime("%H:%M")

            if s["is_blocked"]:
                lines.append(f"{time_str} 🔒")
            elif s["booking_id"] is not None:
                if s["booking_id"] == prev_bid:
                    lines.append(f"{time_str}    ↕")
                else:
                    client = s["client_name"] or s["client_phone"] or "—"
                    lines.append(f"{time_str} ✅ {s['service_name']} — {client}")
                prev_bid = s["booking_id"]
            else:
                lines.append(f"{time_str} · вільно")
                prev_bid = None

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await callback.message.edit_text(text)


@router.message(F.text == "📅 Забронювати час")
async def admin_menu_book(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await cmd_admin_book(message, session, state)


@router.message(F.text == "🗑 Видалити послугу")
async def admin_menu_services(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await _show_services_menu(message, session)


# ── Редагувати запис ──────────────────────────────────────────────────────────

def _booking_label(b: dict) -> str:
    tz = _tz()
    st = b["start_time"]
    local = st.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz) if not st.tzinfo else st.astimezone(tz)
    client = b["client_name"] or b["client_phone"] or "—"
    return f"{local.strftime('%d.%m %H:%M')} — {client} — {b['service_name']}"


@router.message(F.text == "✏️ Редагувати запис")
async def admin_menu_edit(message: Message, session: AsyncSession, state: FSMContext) -> None:
    bookings = await get_upcoming_bookings(session, days=14)
    if not bookings:
        await message.answer("Немає майбутніх записів на найближчі 14 днів.")
        return
    buttons = [
        [InlineKeyboardButton(
            text=_booking_label(b),
            callback_data=AdminEditBookingCD(booking_id=str(b["id"])).pack(),
        )]
        for b in bookings
    ]
    await state.set_state(AdminEditFSM.choosing_booking)
    await message.answer("Оберіть запис:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(AdminEditFSM.choosing_booking, AdminEditBookingCD.filter())
async def admin_edit_booking_chosen(
    callback: CallbackQuery, callback_data: AdminEditBookingCD,
    session: AsyncSession, state: FSMContext,
) -> None:
    booking_id = uuid.UUID(callback_data.booking_id)
    bookings = await get_upcoming_bookings(session, days=14)
    b = next((x for x in bookings if x["id"] == booking_id), None)
    if b is None:
        await callback.answer("Запис не знайдено.")
        await state.clear()
        return

    await state.update_data(
        edit_booking_id=str(booking_id),
        edit_master_id=str(b["master_id"]),
        edit_service_id=str(b["service_id"]),
        edit_duration=b["duration_min"],
    )
    await state.set_state(AdminEditFSM.choosing_action)

    tz = _tz()
    st = b["start_time"]
    local = st.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz) if not st.tzinfo else st.astimezone(tz)
    client = b["client_name"] or b["client_phone"] or "—"

    buttons = [
        [
            InlineKeyboardButton(text="📅 Перенести", callback_data=AdminEditActionCD(booking_id=str(booking_id), action="reschedule").pack()),
            InlineKeyboardButton(text="🔄 Послуга", callback_data=AdminEditActionCD(booking_id=str(booking_id), action="service").pack()),
            InlineKeyboardButton(text="❌ Видалити", callback_data=AdminEditActionCD(booking_id=str(booking_id), action="delete").pack()),
        ]
    ]
    await callback.answer()
    await callback.message.edit_text(
        f"<b>{_booking_label(b)}</b>\n"
        f"Майстер: {b['master_name']}\n"
        f"Дата: {local.strftime('%d.%m.%Y')} о {local.strftime('%H:%M')}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


# ── Видалити бронь ─────────────────────────────────────────────────────────────

@router.callback_query(AdminEditFSM.choosing_action, AdminEditActionCD.filter(F.action == "delete"))
async def admin_edit_delete(
    callback: CallbackQuery, callback_data: AdminEditActionCD,
    session: AsyncSession, state: FSMContext,
) -> None:
    booking_id = uuid.UUID(callback_data.booking_id)
    await cancel_booking(session, booking_id, cancelled_by="admin")
    await session.commit()
    await state.clear()
    await callback.answer("Бронь видалено ✅")
    await callback.message.edit_text("❌ Бронювання скасовано.")


# ── Перенести ─────────────────────────────────────────────────────────────────

@router.callback_query(AdminEditFSM.choosing_action, AdminEditActionCD.filter(F.action == "reschedule"))
async def admin_edit_reschedule_start(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession,
) -> None:
    data = await state.get_data()
    master_id = uuid.UUID(data["edit_master_id"])
    duration = data["edit_duration"]
    today = date.today()
    max_date = today + timedelta(days=13)
    available = await get_dates_with_available_slots(session, master_id, duration, 30, days=14)
    await state.update_data(available_dates=[d.isoformat() for d in available])
    await state.set_state(AdminEditFSM.reschedule_date)
    await callback.answer()
    await callback.message.edit_text(
        "Оберіть нову дату:",
        reply_markup=calendar_keyboard(today.year, today.month, set(available), today, max_date),
    )


@router.callback_query(AdminEditFSM.reschedule_date, CalendarNavCD.filter())
async def admin_edit_reschedule_nav(
    callback: CallbackQuery, callback_data: CalendarNavCD, state: FSMContext,
) -> None:
    if callback_data.action == "ignore":
        await callback.answer()
        return
    data = await state.get_data()
    available = {date.fromisoformat(d) for d in data.get("available_dates", [])}
    today = date.today()
    max_date = today + timedelta(days=13)
    await callback.answer()
    await callback.message.edit_reply_markup(
        reply_markup=calendar_keyboard(callback_data.year, callback_data.month, available, today, max_date)
    )


@router.callback_query(AdminEditFSM.reschedule_date, DateCD.filter())
async def admin_edit_reschedule_date_chosen(
    callback: CallbackQuery, callback_data: DateCD,
    session: AsyncSession, state: FSMContext,
) -> None:
    chosen_date = date.fromisoformat(callback_data.date)
    data = await state.get_data()
    available = {date.fromisoformat(d) for d in data.get("available_dates", [])}
    if chosen_date not in available:
        await callback.answer("На цю дату немає вільного часу.", show_alert=True)
        return
    master_id = uuid.UUID(data["edit_master_id"])
    slots = await get_available_slots(session, master_id, chosen_date, data["edit_duration"], 30)
    if not slots:
        await callback.answer("Слоти зайняті, оберіть іншу дату.", show_alert=True)
        return
    await state.update_data(reschedule_date=chosen_date.isoformat())
    await state.set_state(AdminEditFSM.reschedule_time)
    await callback.answer()
    await callback.message.edit_text(
        f"Нова дата: <b>{chosen_date.strftime('%d.%m.%Y')}</b>\n\nОберіть час:",
        reply_markup=time_slots_keyboard(slots, _tz()),
    )


@router.callback_query(AdminEditFSM.reschedule_time, TimeCD.filter())
async def admin_edit_reschedule_time_chosen(
    callback: CallbackQuery, callback_data: TimeCD,
    session: AsyncSession, state: FSMContext,
) -> None:
    from datetime import timezone as _tz_utc
    data = await state.get_data()
    booking_id = uuid.UUID(data["edit_booking_id"])
    new_start = datetime.fromtimestamp(callback_data.ts, tz=_tz_utc.utc)
    try:
        await reschedule_booking(session, booking_id, new_start, step_min=30)
        await session.commit()
    except Exception as e:
        await session.rollback()
        await callback.answer(f"Помилка: слот зайнятий.", show_alert=True)
        return
    await state.clear()
    local = new_start.astimezone(_tz())
    await callback.answer("Перенесено ✅")
    await callback.message.edit_text(
        f"✅ Запис перенесено на {local.strftime('%d.%m.%Y о %H:%M')}"
    )


# ── Змінити послугу ────────────────────────────────────────────────────────────

@router.callback_query(AdminEditFSM.choosing_action, AdminEditActionCD.filter(F.action == "service"))
async def admin_edit_service_start(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession,
) -> None:
    data = await state.get_data()
    master_id = uuid.UUID(data["edit_master_id"])
    booking_id = data["edit_booking_id"]
    from db.queries.services import get_services_for_master
    services = await get_services_for_master(session, master_id)
    buttons = [
        [InlineKeyboardButton(
            text=f"{s.name} — {s.duration_min} хв",
            callback_data=AdminEditServiceCD(booking_id=booking_id, service_id=str(s.id)).pack(),
        )]
        for s in services
    ]
    await state.set_state(AdminEditFSM.change_service)
    await callback.answer()
    await callback.message.edit_text("Оберіть нову послугу:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(AdminEditFSM.change_service, AdminEditServiceCD.filter())
async def admin_edit_service_chosen(
    callback: CallbackQuery, callback_data: AdminEditServiceCD,
    session: AsyncSession, state: FSMContext,
) -> None:
    booking_id = uuid.UUID(callback_data.booking_id)
    service_id = uuid.UUID(callback_data.service_id)
    try:
        await change_booking_service(session, booking_id, service_id, step_min=30)
        await session.commit()
    except Exception:
        await session.rollback()
        await callback.answer("Помилка: слоти зайняті для нової тривалості.", show_alert=True)
        return
    await state.clear()
    await callback.answer("Послугу змінено ✅")
    await callback.message.edit_text("✅ Послугу оновлено.")
