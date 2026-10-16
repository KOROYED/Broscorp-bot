import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from bot.archive import import_archive
from bot.config import settings
from bot.db import create_tables, make_engine, make_session_factory
from bot.handlers import router
from bot.memory import ChatMemory

logger = logging.getLogger(__name__)


async def on_error(event: ErrorEvent) -> None:
    """Last resort: log the bug and tell the user something went wrong."""
    logger.exception("Unhandled error", exc_info=event.exception)
    message = event.update.message or (
        event.update.callback_query.message if event.update.callback_query else None
    )
    if message is not None:
        await message.answer("Ой, щось пішло не так. Спробуй ще раз трохи пізніше.")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    engine = make_engine(settings.database_url)
    await create_tables(engine)
    session_factory = make_session_factory(engine)

    imported = await import_archive(session_factory, settings.archive_dir)
    logger.info("Archive import done: %d new document(s)", imported)

    bot = Bot(token=settings.bot_token.get_secret_value())
    # Objects passed here are injected into handlers by parameter name.
    dp = Dispatcher(
        storage=MemoryStorage(),
        session_factory=session_factory,
        memory=ChatMemory(settings.history_turns),
    )
    dp.include_router(router)
    dp.errors.register(on_error)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
