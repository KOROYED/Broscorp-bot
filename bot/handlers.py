"""Telegram layer: receives messages, calls the services, replies.

No prompts or SQL here: handlers only translate between Telegram and the
service modules (qa, documents, parsing, llm).
"""

import asyncio
import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot import qa
from bot.config import settings
from bot.db import Document
from bot.documents import (
    SOURCE_USER,
    active_documents,
    delete_document,
    file_hash,
    find_active_by_hash,
    get_user,
    save_document,
    set_user_name,
    user_documents,
)
from bot.llm import LLMError, classify_document
from bot.memory import ChatMemory
from bot.parsing import ParsingError, check_extension, extract_text
from bot.places import ALL_SCOPES, Scope

logger = logging.getLogger(__name__)

router = Router()
# Private chats only (decision to confirm with the client).
router.message.filter(F.chat.type == ChatType.PRIVATE)

# Telegram bots can download files up to 20 MB.
MAX_FILE_SIZE = 20 * 1024 * 1024
MAX_NAME_LENGTH = 50
TELEGRAM_MESSAGE_LIMIT = 4096

HELP = """\
Привіт! Я бот-порадник лісу 🌲

❓ Питай звичайним повідомленням і назви рукавичку, наприклад:
«Скільки внесок під дубом і до якого числа?»
Я відповідаю лише з документів лісу й завжди кажу, з якого документа взяв відповідь.

📄 Щоб поділитися порадою, надішли документ: PDF, Word (.docx) або текстовий файл.
Новіша версія того самого документа для того самого місця замінює стару.

Команди:
/docs — усі документи лісу
/name — змінити своє ім’я
/delete — видалити документ, який ти надіслав
/cancel — скасувати поточну дію
/help — ця підказка"""

LLM_UNAVAILABLE = "Зараз не можу відповісти: щось із моїм розумом 🧠. Спробуй, будь ласка, за хвилину."


class Upload(StatesGroup):
    waiting_name = State()
    waiting_scope = State()


class ScopeChoice(CallbackData, prefix="scope"):
    index: int  # position in ALL_SCOPES; callback data is limited to 64 bytes


class DeleteChoice(CallbackData, prefix="del"):
    doc_id: int


# --- commands ---


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(HELP)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Скасовано. Можеш питати або надсилати документи.")


@router.message(Command("name"))
async def cmd_name(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if command.args:
        await _save_name(message, command.args, session_factory)
        return
    await state.set_state(Upload.waiting_name)
    await message.answer("Як тебе звати?")


@router.message(Command("docs"))
async def cmd_docs(
    message: Message, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        docs = await active_documents(session)
    for chunk in _split(_format_catalog(docs)):
        await message.answer(chunk)


@router.message(Command("delete"))
async def cmd_delete(
    message: Message, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        docs = await user_documents(session, message.from_user.id)
    if not docs:
        await message.answer("Ти ще не надсилав документів, які можна видалити.")
        return
    builder = InlineKeyboardBuilder()
    for doc in docs:
        builder.button(
            text=f"«{doc.title}» — {doc.scope.label}", callback_data=DeleteChoice(doc_id=doc.id)
        )
    builder.adjust(1)
    await message.answer(
        "Який документ видалити? Можна видаляти лише ті, що надіслав ти.",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(DeleteChoice.filter())
async def on_delete(
    callback: CallbackQuery,
    callback_data: DeleteChoice,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        deleted = await delete_document(session, callback_data.doc_id, callback.from_user.id)
    text = "Документ видалено." if deleted else "Цей документ уже видалено або замінено."
    await callback.message.edit_text(text)
    await callback.answer()


# --- uploading documents ---


@router.message(F.document)
async def on_document(
    message: Message,
    bot: Bot,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    document = message.document
    filename = document.file_name or "документ"
    try:
        check_extension(filename)
    except ParsingError as e:
        await message.answer(str(e))
        return
    if document.file_size and document.file_size > MAX_FILE_SIZE:
        await message.answer("Файл завеликий: я можу прийняти документ до 20 МБ.")
        return

    pending = {
        "file_id": document.file_id,
        "filename": filename,
        # Time of sending, as the client asked, not the time we finish processing.
        "sent_at": message.date.isoformat(),
    }
    async with session_factory() as session:
        user = await get_user(session, message.from_user.id)
    if user is None:
        await state.set_state(Upload.waiting_name)
        await state.update_data(pending=pending)
        await message.answer("Дякую за документ! Це твій перший документ, тож скажи: як тебе звати?")
        return
    await _process_upload(message, bot, state, session_factory, pending)


@router.message(Upload.waiting_name, F.text, ~F.text.startswith("/"))
async def on_name(
    message: Message,
    bot: Bot,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if not await _save_name(message, message.text, session_factory):
        return
    pending = (await state.get_data()).get("pending")
    await state.clear()
    if pending:
        await _process_upload(message, bot, state, session_factory, pending)


@router.message(Upload.waiting_name)
async def on_name_other(message: Message) -> None:
    await message.answer("Напиши, будь ласка, своє ім’я текстом. Або /cancel, щоб скасувати.")


@router.callback_query(Upload.waiting_scope, ScopeChoice.filter())
async def on_scope(
    callback: CallbackQuery,
    callback_data: ScopeChoice,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    upload = (await state.get_data()).get("upload")
    await state.clear()
    await callback.answer()
    if not upload:
        await callback.message.edit_text("Цей документ уже оброблено.")
        return
    scope = ALL_SCOPES[callback_data.index]
    await callback.message.edit_text(f"Дякую! Місце: {scope.label}.")
    await _save_upload(callback.message, callback.from_user.id, session_factory, upload, scope)


@router.message(Upload.waiting_scope)
async def on_scope_other(message: Message) -> None:
    await message.answer("Спершу обери кнопкою вище, для якого місця ця порада. Або /cancel.")


@router.callback_query(ScopeChoice.filter())
async def on_stale_scope(callback: CallbackQuery) -> None:
    await callback.answer("Цей вибір уже неактуальний.")


async def _process_upload(
    message: Message,
    bot: Bot,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    pending: dict,
) -> None:
    filename = pending["filename"]
    await message.answer(f"Читаю «{filename}»…")
    file = await bot.download(pending["file_id"])
    data = file.read()
    digest = file_hash(data)

    async with session_factory() as session:
        duplicate = await find_active_by_hash(session, digest)
    if duplicate is not None:
        await message.answer(f"Такий документ уже є: «{duplicate.title}» ({duplicate.scope.label}).")
        return

    try:
        # Parsing/OCR is CPU-bound: a thread keeps the bot responsive for others.
        text = await asyncio.to_thread(extract_text, filename, data)
    except ParsingError as e:
        await message.answer(str(e))
        return

    try:
        classification = await classify_document(filename, text)
    except LLMError:
        logger.exception("Classification failed for %s", filename)
        await message.answer("Не вдалося обробити документ. Спробуй надіслати його ще раз за хвилину.")
        return

    upload = {
        "title": classification.title,
        "text": text,
        "hash": digest,
        "sent_at": pending["sent_at"],
    }
    if classification.scope is None:
        # The client's rule: if the place is unclear, ask the sender, never guess.
        await state.set_state(Upload.waiting_scope)
        await state.update_data(upload=upload)
        await message.answer(
            f"Документ «{classification.title}». З тексту не зрозуміло, для якого місця ця порада. "
            "Обери, будь ласка:",
            reply_markup=_scope_keyboard(),
        )
        return
    await _save_upload(message, message.from_user.id, session_factory, upload, classification.scope)


async def _save_upload(
    message: Message,
    author_id: int,
    session_factory: async_sessionmaker[AsyncSession],
    upload: dict,
    scope: Scope,
) -> None:
    async with session_factory() as session:
        result = await save_document(
            session,
            title=upload["title"],
            scope=scope,
            text=upload["text"],
            source=SOURCE_USER,
            content_hash=upload["hash"],
            author_id=author_id,
            sent_at=datetime.fromisoformat(upload["sent_at"]),
        )
    reply = f"Зберіг документ «{result.document.title}» для: {scope.label}. Дякую!"
    if result.replaced is not None:
        reply += "\nЦе новіша версія — попередню замінено, тепер звірі бачитимуть лише нову."
    await message.answer(reply)


async def _save_name(
    message: Message, raw_name: str, session_factory: async_sessionmaker[AsyncSession]
) -> bool:
    name = " ".join(raw_name.split())
    if not name or len(name) > MAX_NAME_LENGTH:
        await message.answer(f"Ім’я має бути від 1 до {MAX_NAME_LENGTH} символів. Спробуй ще раз.")
        return False
    async with session_factory() as session:
        await set_user_name(session, message.from_user.id, name)
    await message.answer(f"Приємно познайомитись, {name}!")
    return True


def _scope_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, scope in enumerate(ALL_SCOPES):
        builder.button(text=scope.label.capitalize(), callback_data=ScopeChoice(index=i))
    builder.adjust(2)
    return builder.as_markup()


# --- other message types ---


@router.message(F.photo)
async def on_photo(message: Message) -> None:
    await message.answer("Фото документів я не читаю. Надішли, будь ласка, PDF, Word (.docx) або .txt.")


@router.message(F.voice | F.audio | F.video_note)
async def on_voice(message: Message) -> None:
    await message.answer("Голосові повідомлення я не слухаю. Напиши питання текстом, будь ласка.")


# --- questions ---


@router.message(StateFilter(None), F.text)
async def on_question(
    message: Message,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    memory: ChatMemory,
) -> None:
    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        try:
            async with session_factory() as session:
                reply = await qa.answer(session, message.chat.id, message.text, memory, settings.tz)
        except LLMError:
            logger.exception("LLM failed for question %r", message.text)
            reply = LLM_UNAVAILABLE
    for chunk in _split(reply):
        await message.answer(chunk)


@router.message()
async def on_other(message: Message) -> None:
    await message.answer("Я розумію текстові питання й документи PDF, Word (.docx) або .txt. Деталі — /help")


# --- formatting helpers ---


def _format_catalog(docs: list[Document]) -> str:
    if not docs:
        return "Документів поки немає."
    lines = [f"Документи лісу ({len(docs)}):"]
    for scope in reversed(ALL_SCOPES):  # whole forest first, then areas, then places
        in_scope = [d for d in docs if d.scope == scope]
        if not in_scope:
            continue
        lines.append(f"\n{scope.label.capitalize()}:")
        for doc in in_scope:
            line = f"• {doc.title}"
            if doc.source == SOURCE_USER and doc.author is not None:
                line += f" (надіслав {doc.author.name}, {doc.sent_at.astimezone(settings.tz):%d.%m})"
            lines.append(line)
    return "\n".join(lines)


def _split(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split a long text by lines into Telegram-sized messages."""
    chunks, current = [], ""
    for line in text.split("\n"):
        if current and len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks
