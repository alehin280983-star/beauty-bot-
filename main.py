import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage

from bot.handlers import admin, client, my_bookings, reviews
from bot.middlewares.db import DbSessionMiddleware
from bot.middlewares.error_handler import ErrorHandlerMiddleware
from bot.scheduler import auto_generate_slots, setup_scheduler
from config import settings
from db.session import AsyncSessionFactory

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    force=True,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    storage = RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(storage=storage)

    dp.update.middleware(ErrorHandlerMiddleware())
    dp.update.middleware(DbSessionMiddleware(AsyncSessionFactory))

    dp.include_router(admin.router)
    dp.include_router(reviews.router)
    dp.include_router(my_bookings.router)
    dp.include_router(client.router)

    scheduler = setup_scheduler(bot, AsyncSessionFactory)
    scheduler.start()
    logger.info("Scheduler started.")

    await auto_generate_slots(AsyncSessionFactory)
    logger.info("Initial slot generation complete.")

    logger.info("Starting bot...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
