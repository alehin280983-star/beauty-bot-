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
from config import settings
from db.queries.bookings import get_bookings_for_date
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
from db.queries.slots import block_slot, generate_slots, get_slots_for_date

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
        "<b>Админ-панель</b>\n\n"
        "/admin_today — записи на сегодня\n"
        "/admin_tomorrow — записи на завтра\n"
        "/admin_slots — генерация слотов\n"
        "/admin_services — управление услугами\n"
        "/admin_masters — управление мастерами\n"
        "/admin_reviews — последние отзывы"
    )


# ── /admin_today / /admin_tomorrow ────────────────────────────────────────────

@router.message(Command("admin_today"))
async def cmd_admin_today(message: Message, session: AsyncSession) -> None:
    await _send_bookings_for_date(message, session, date.today())


@router.message(Command("admin_tomorrow"))
async def cmd_admin_tomorrow(message: Message, session: AsyncSession) -> None:
    await _send_bookings_for_date(message, session, date.today() + timedelta(days=1))


async def _send_bookings_for_date(
    message: Message, session: AsyncSession, target: date
) -> None:
    bookings = await get_bookings_for_date(session, target)
    label = "сегодня" if target == date.today() else "завтра"
    if not bookings:
        await message.answer(f"Записей на {label} нет.")
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
        await message.answer("Нет активных мастеров.")
        return
    buttons = [
        [InlineKeyboardButton(
            text=m.name,
            callback_data=AdminMasterActionCD(master_id=str(m.id), action="slots").pack(),
        )]
        for m in masters
    ]
    await state.set_state(AdminSlotsFSM.choosing_master)
    await message.answer("Выберите мастера:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(AdminSlotsFSM.choosing_master, AdminMasterActionCD.filter(F.action == "slots"))
async def admin_slots_master_chosen(
    callback: CallbackQuery, callback_data: AdminMasterActionCD, state: FSMContext
) -> None:
    await state.update_data(master_id=callback_data.master_id)
    await state.set_state(AdminSlotsFSM.choosing_date)
    await callback.answer()
    await callback.message.edit_text(
        "Введите дату в формате ДД.ММ.ГГГГ:"
    )


@router.message(AdminSlotsFSM.choosing_date)
async def admin_slots_date_entered(message: Message, state: FSMContext) -> None:
    try:
        d = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except ValueError:
        await message.answer("Неверный формат. Введите дату как ДД.ММ.ГГГГ:")
        return
    await state.update_data(slot_date=d.isoformat())
    await state.set_state(AdminSlotsFSM.choosing_hours)
    await message.answer("Введите часы начала и конца через пробел (например: <code>9 19</code>):")


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
        await message.answer("Неверный формат. Введите два числа через пробел (например: <code>9 19</code>):")
        return

    data = await state.get_data()
    master_id = uuid.UUID(data["master_id"])
    slot_date = date.fromisoformat(data["slot_date"])
    await state.clear()

    count = await generate_slots(session, master_id, slot_date, start_hour, end_hour, 30)
    await session.commit()
    await message.answer(
        f"✅ Создано {count} слотов на {slot_date.strftime('%d.%m.%Y')} "
        f"с {start_hour}:00 до {end_hour}:00."
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
            text=f"{status} {s.name} — {s.duration_min}мин — {int(s.price)}руб",
            callback_data=AdminServiceActionCD(service_id=str(s.id), action="menu").pack(),
        )])
    buttons.append([InlineKeyboardButton(text="➕ Добавить услугу", callback_data="admin_add_service")])
    await message.answer(
        "<b>Услуги:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "admin_add_service")
async def admin_add_service_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminServiceFSM.adding_name)
    await callback.answer()
    await callback.message.answer("Введите название услуги:")


@router.message(AdminServiceFSM.adding_name)
async def admin_service_name(message: Message, state: FSMContext) -> None:
    await state.update_data(service_name=message.text.strip())
    await state.set_state(AdminServiceFSM.adding_duration)
    await message.answer("Введите длительность в минутах:")


@router.message(AdminServiceFSM.adding_duration)
async def admin_service_duration(message: Message, state: FSMContext) -> None:
    try:
        duration = int(message.text.strip())
        if duration <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите целое число минут:")
        return
    await state.update_data(duration=duration)
    await state.set_state(AdminServiceFSM.adding_price)
    await message.answer("Введите цену в рублях:")


@router.message(AdminServiceFSM.adding_price)
async def admin_service_price(message: Message, session: AsyncSession, state: FSMContext) -> None:
    from decimal import Decimal, InvalidOperation
    try:
        price = Decimal(message.text.strip().replace(",", "."))
        if price < 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer("Введите корректную цену:")
        return

    data = await state.get_data()
    await state.clear()
    service = await create_service(session, data["service_name"], data["duration"], price)
    await session.commit()
    await message.answer(f"✅ Услуга «{service.name}» добавлена.")


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
        await callback.answer("Услуга не найдена.")
        return

    status = "видна" if service.is_visible else "скрыта"
    buttons = [
        [InlineKeyboardButton(
            text="🔁 Скрыть/показать",
            callback_data=AdminServiceActionCD(service_id=callback_data.service_id, action="toggle").pack(),
        )],
        [InlineKeyboardButton(
            text="💰 Изменить цену",
            callback_data=AdminServiceActionCD(service_id=callback_data.service_id, action="price").pack(),
        )],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_services_back")],
    ]
    await callback.answer()
    await callback.message.edit_text(
        f"<b>{service.name}</b>\n"
        f"Длительность: {service.duration_min} мин\n"
        f"Цена: {int(service.price)} руб\n"
        f"Статус: {status}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(AdminServiceActionCD.filter(F.action == "toggle"))
async def admin_service_toggle(
    callback: CallbackQuery, callback_data: AdminServiceActionCD, session: AsyncSession
) -> None:
    service = await toggle_service_visible(session, uuid.UUID(callback_data.service_id))
    await session.commit()
    status = "видна ✅" if service.is_visible else "скрыта 🚫"
    await callback.answer(f"Услуга теперь {status}")
    await callback.message.delete()


@router.callback_query(AdminServiceActionCD.filter(F.action == "price"))
async def admin_service_price_start(
    callback: CallbackQuery, callback_data: AdminServiceActionCD, state: FSMContext
) -> None:
    await state.set_state(AdminServiceFSM.updating_price)
    await state.update_data(service_id=callback_data.service_id)
    await callback.answer()
    await callback.message.answer("Введите новую цену:")


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
        await message.answer("Введите корректную цену:")
        return

    data = await state.get_data()
    await state.clear()
    service = await update_service(session, uuid.UUID(data["service_id"]), price=price)
    await session.commit()
    await message.answer(f"✅ Цена «{service.name}» обновлена: {int(service.price)} руб.")


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
    buttons.append([InlineKeyboardButton(text="➕ Добавить мастера", callback_data="admin_add_master")])
    await message.answer(
        "<b>Мастера:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "admin_add_master")
async def admin_add_master_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminMasterFSM.adding_name)
    await callback.answer()
    await callback.message.answer("Введите имя мастера:")


@router.message(AdminMasterFSM.adding_name)
async def admin_master_name(message: Message, session: AsyncSession, state: FSMContext) -> None:
    name = message.text.strip()
    await state.clear()
    master = await create_master(session, name)
    await session.commit()
    await message.answer(f"✅ Мастер «{master.name}» добавлен.")


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
        await callback.answer("Мастер не найден.")
        return

    svc_result = await session.execute(
        select(Service)
        .join(MasterService, MasterService.service_id == Service.id)
        .where(MasterService.master_id == master.id)
    )
    master_services = list(svc_result.scalars().all())
    svc_names = ", ".join(s.name for s in master_services) or "нет"

    status = "активен" if master.is_active else "неактивен"
    buttons = [
        [InlineKeyboardButton(
            text="🔁 Активировать/деактивировать",
            callback_data=AdminMasterActionCD(master_id=callback_data.master_id, action="toggle").pack(),
        )],
        [InlineKeyboardButton(
            text="🛠 Управление услугами",
            callback_data=AdminMasterActionCD(master_id=callback_data.master_id, action="services").pack(),
        )],
        [InlineKeyboardButton(
            text="🖼 Установить фото",
            callback_data=AdminMasterActionCD(master_id=callback_data.master_id, action="set_photo").pack(),
        )],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_masters_back")],
    ]
    await callback.answer()
    await callback.message.edit_text(
        f"<b>{master.name}</b>\nСтатус: {status}\nУслуги: {svc_names}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(AdminMasterActionCD.filter(F.action == "toggle"))
async def admin_master_toggle(
    callback: CallbackQuery, callback_data: AdminMasterActionCD, session: AsyncSession
) -> None:
    master = await toggle_master_active(session, uuid.UUID(callback_data.master_id))
    await session.commit()
    status = "активен ✅" if master.is_active else "деактивирован 🚫"
    await callback.answer(f"Мастер теперь {status}")
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
        f"Услуги мастера <b>{master.name}</b>:",
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
        await callback.answer("Услуга добавлена ✅")
    else:
        await session.execute(
            delete(MasterService)
            .where(MasterService.master_id == master_id)
            .where(MasterService.service_id == service_id)
        )
        await session.commit()
        await callback.answer("Услуга удалена")

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
    await callback.message.answer("Отправьте фото мастера:")


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
    await message.answer(f"✅ Фото мастера «{master.name}» обновлено.")


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
        await message.answer("Отзывов пока нет.")
        return

    lines = ["<b>Последние отзывы:</b>\n"]
    for r in reviews:
        stars = "⭐" * r.rating
        comment = r.comment or "—"
        lines.append(f"{stars}\n{comment}\n")
    await message.answer("\n".join(lines))
