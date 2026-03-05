from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards.main_menu import MAIN_MENU
from config import settings
from db.queries.clients import get_or_create_client
from db.queries.services import get_visible_services

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession) -> None:
    await get_or_create_client(
        session,
        telegram_id=message.from_user.id,
        first_name=message.from_user.first_name,
    )
    await session.commit()
    await message.answer(
        f"Добро пожаловать в <b>{settings.studio_name}</b>! 👋\n\n"
        "Выберите действие:",
        reply_markup=MAIN_MENU,
        parse_mode="HTML",
    )


@router.message(F.text == "💰 Прайс")
async def cmd_price(message: Message, session: AsyncSession) -> None:
    services = await get_visible_services(session)
    if not services:
        await message.answer("Услуги пока не добавлены.", reply_markup=MAIN_MENU)
        return

    lines = [f"<b>{s.name}</b> — {s.duration_min} мин — {int(s.price)} руб" for s in services]
    await message.answer("\n".join(lines), reply_markup=MAIN_MENU, parse_mode="HTML")


@router.message(F.text == "ℹ️ О нас")
async def cmd_about(message: Message) -> None:
    text = (
        f"<b>{settings.studio_name}</b>\n\n"
        f"📍 {settings.studio_address}\n"
        f"📞 {settings.studio_phone}"
    )
    await message.answer(text, reply_markup=MAIN_MENU, parse_mode="HTML")


@router.message()
async def fallback(message: Message) -> None:
    await message.answer("Выберите действие:", reply_markup=MAIN_MENU)
