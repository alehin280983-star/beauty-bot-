from __future__ import annotations

import calendar as _cal
from datetime import timezone
from zoneinfo import ZoneInfo

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.models import Master, Service, Slot


class ServiceCD(CallbackData, prefix="svc"):
    service_id: str


class MasterCD(CallbackData, prefix="mst"):
    master_id: str


class TimeCD(CallbackData, prefix="tm"):
    ts: int  # Unix timestamp (UTC, naive)


def services_keyboard(services: list[Service]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=s.name,
                callback_data=ServiceCD(service_id=str(s.id)).pack(),
            )
        ]
        for s in services
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_master")])
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
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def time_slots_keyboard(slots: list[Slot], tz: ZoneInfo) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for slot in slots:
        dt = slot.starts_at
        if dt.tzinfo is not None:
            local = dt.astimezone(tz)
            ts = int(dt.astimezone(timezone.utc).timestamp())
        else:
            dt_utc = dt.replace(tzinfo=timezone.utc)
            local = dt_utc.astimezone(tz)
            ts = int(dt_utc.timestamp())
        row.append(
            InlineKeyboardButton(
                text=local.strftime("%H:%M"),
                callback_data=TimeCD(ts=ts).pack(),
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
