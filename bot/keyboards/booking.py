from __future__ import annotations

from zoneinfo import ZoneInfo

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.models import Master, Service, Slot


class ServiceCD(CallbackData, prefix="svc"):
    service_id: str


class MasterCD(CallbackData, prefix="mst"):
    master_id: str


class TimeCD(CallbackData, prefix="tm"):
    starts_at: str  # ISO datetime


def services_keyboard(services: list[Service]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=f"{s.name} — {s.duration_min} мин — {int(s.price)} руб",
                callback_data=ServiceCD(service_id=str(s.id)).pack(),
            )
        ]
        for s in services
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def masters_keyboard(masters: list[Master]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=m.name,
                callback_data=MasterCD(master_id=str(m.id)).pack(),
            )
        ]
        for m in masters
    ]
    buttons.append(
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_service")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def time_slots_keyboard(slots: list[Slot], tz: ZoneInfo) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for slot in slots:
        dt = slot.starts_at
        if dt.tzinfo is not None:
            local = dt.astimezone(tz)
        else:
            local = dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
        row.append(
            InlineKeyboardButton(
                text=local.strftime("%H:%M"),
                callback_data=TimeCD(starts_at=dt.isoformat()).pack(),
            )
        )
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append(
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_date")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)
