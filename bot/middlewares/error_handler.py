from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import TelegramObject, Update

logger = logging.getLogger(__name__)

_STALE_PHRASES = (
    "query is too old",
    "message is not modified",
    "message to edit not found",
    "bot was blocked by the user",
)


class ErrorHandlerMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)

        except TelegramForbiddenError:
            # User blocked the bot — mark client as blocked
            telegram_id = _extract_user_id(event)
            if telegram_id is not None:
                session = data.get("session")
                if session is not None:
                    await _block_client(session, telegram_id)
            logger.info("Client %s blocked the bot — marked as blocked.", telegram_id)

        except TelegramBadRequest as e:
            if any(phrase in str(e).lower() for phrase in _STALE_PHRASES):
                # Stale callback or message already modified — silently ignore
                cq = _extract_callback_query(event)
                if cq is not None:
                    try:
                        await cq.answer("Начните заново", show_alert=False)
                    except Exception:
                        pass
                logger.debug("Stale callback/message: %s", e)
            else:
                logger.error("TelegramBadRequest: %s", e, exc_info=True)
                await _notify_user(event, "Произошла ошибка. Попробуйте ещё раз.")

        except Exception as e:
            logger.error("Unhandled exception in handler: %s", e, exc_info=True)
            await _notify_user(event, "Произошла ошибка. Попробуйте ещё раз.")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_user_id(event: TelegramObject) -> int | None:
    if not isinstance(event, Update):
        return None
    if event.message and event.message.from_user:
        return event.message.from_user.id
    if event.callback_query and event.callback_query.from_user:
        return event.callback_query.from_user.id
    return None


def _extract_callback_query(event: TelegramObject):
    if isinstance(event, Update) and event.callback_query:
        return event.callback_query
    return None


async def _block_client(session, telegram_id: int) -> None:
    try:
        from sqlalchemy import select
        from db.models import Client
        result = await session.execute(
            select(Client).where(Client.telegram_id == telegram_id)
        )
        client = result.scalar_one_or_none()
        if client is not None:
            client.is_blocked = True
            await session.commit()
    except Exception as e:
        logger.error("Failed to block client %s: %s", telegram_id, e)


async def _notify_user(event: TelegramObject, text: str) -> None:
    try:
        if not isinstance(event, Update):
            return
        if event.message:
            await event.message.answer(text)
        elif event.callback_query:
            await event.callback_query.answer(text, show_alert=True)
    except Exception:
        pass
