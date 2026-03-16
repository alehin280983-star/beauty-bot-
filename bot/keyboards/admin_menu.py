from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

ADMIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📋 Записи на 2 тижні")],
        [KeyboardButton(text="📅 Забронювати час")],
        [KeyboardButton(text="✏️ Редагувати запис")],
        [KeyboardButton(text="🗑 Видалити послугу")],
    ],
    resize_keyboard=True,
)
