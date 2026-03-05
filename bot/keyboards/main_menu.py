from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📋 Записаться"), KeyboardButton(text="📅 Мои записи")],
        [KeyboardButton(text="💰 Прайс"), KeyboardButton(text="ℹ️ О нас")],
    ],
    resize_keyboard=True,
)
