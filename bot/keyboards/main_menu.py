from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📋 Записатись"), KeyboardButton(text="📅 Мої записи")],
        [KeyboardButton(text="💰 Прайс"), KeyboardButton(text="ℹ️ Про нас")],
    ],
    resize_keyboard=True,
)
