from __future__ import annotations

import calendar
from datetime import date

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS_RU = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


class CalendarNavCD(CallbackData, prefix="cal"):
    action: str  # "prev" | "next" | "ignore"
    year: int
    month: int


class DateCD(CallbackData, prefix="dt"):
    date: str  # YYYY-MM-DD


def calendar_keyboard(
    year: int,
    month: int,
    available_dates: set[date],
    min_date: date,
    max_date: date,
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []

    # Header: prev / Month YEAR / next
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    prev_date = date(prev_year, prev_month, 1)
    next_date = date(next_year, next_month, 1)

    prev_btn = (
        InlineKeyboardButton(
            text="◀️",
            callback_data=CalendarNavCD(action="prev", year=prev_year, month=prev_month).pack(),
        )
        if prev_date >= date(min_date.year, min_date.month, 1)
        else InlineKeyboardButton(text=" ", callback_data=CalendarNavCD(action="ignore", year=year, month=month).pack())
    )
    next_btn = (
        InlineKeyboardButton(
            text="▶️",
            callback_data=CalendarNavCD(action="next", year=next_year, month=next_month).pack(),
        )
        if date(next_year, next_month, 1) <= date(max_date.year, max_date.month, 1)
        else InlineKeyboardButton(text=" ", callback_data=CalendarNavCD(action="ignore", year=year, month=month).pack())
    )

    buttons.append([
        prev_btn,
        InlineKeyboardButton(
            text=f"{MONTHS_RU[month]} {year}",
            callback_data=CalendarNavCD(action="ignore", year=year, month=month).pack(),
        ),
        next_btn,
    ])

    # Weekday header
    buttons.append([
        InlineKeyboardButton(
            text=d,
            callback_data=CalendarNavCD(action="ignore", year=year, month=month).pack(),
        )
        for d in WEEKDAYS
    ])

    # Days grid
    cal = calendar.monthcalendar(year, month)
    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(
                    text=" ",
                    callback_data=CalendarNavCD(action="ignore", year=year, month=month).pack(),
                ))
                continue
            d = date(year, month, day)
            if d < min_date or d > max_date:
                row.append(InlineKeyboardButton(
                    text="·",
                    callback_data=CalendarNavCD(action="ignore", year=year, month=month).pack(),
                ))
            elif d in available_dates:
                row.append(InlineKeyboardButton(
                    text=str(day),
                    callback_data=DateCD(date=d.isoformat()).pack(),
                ))
            else:
                row.append(InlineKeyboardButton(
                    text=f"·{day}·",
                    callback_data=CalendarNavCD(action="ignore", year=year, month=month).pack(),
                ))
        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_master")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
