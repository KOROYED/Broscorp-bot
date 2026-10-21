"""Telegram layer: receives messages, calls the services, replies.

No prompts or SQL here: handlers only translate between Telegram and the
service modules (qa, documents, parsing, llm).
"""

import asyncio
import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
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
    SaveResult,
    active_documents,
    file_hash,
    find_active_by_hash,
    find_active_version,
    format_sent_at,
    get_user,
    normalize_title,
    save_document,
    set_user_name,
    undo_replacement,
)
from bot.llm import LLMError, classify_document, find_previous_version
from bot.memory import ChatMemory
from bot.parsing import ParsingError, check_extension, extract_text
from bot.places import ALL_SCOPES, Scope

logger = logging.getLogger(__name__)

router = Router()
# Private chats only.
router.message.filter(F.chat.type == ChatType.PRIVATE)

# Telegram bots can download files up to 20 MB.
MAX_FILE_SIZE = 20 * 1024 * 1024
MAX_NAME_LENGTH = 50
TELEGRAM_MESSAGE_LIMIT = 4096

# Every message speaks as Лісовий Порадник, a forest character: no "bot", "document" or
# "file" talk. «Рукавичка» is fine: the client confirmed it's a real place in the forest.
# The citation under answers keeps the client's confirmed format.
GREETING = """\
Привіт! Я — Лісовий Порадник 🌲 Бережу поради звірів нашого лісу.

Питай мене про лісове життя, наприклад: «Скільки внесок під дубом?»

Знаєш щось корисне? Принеси мені свою пораду в PDF, Word або простому тексті, \
і я збережу її для всіх."""

LLM_UNAVAILABLE = "Ох, щось я задумався й загубив думку 🍂 Спитай мене ще раз за хвилинку."


class Upload(StatesGroup):
    waiting_name = State()
    waiting_scope = State()
    # Advice was saved as a new version; the sender may still say it's different advice.
    can_undo = State()


class ScopeChoice(CallbackData, prefix="scope"):
    index: int  # position in ALL_SCOPES; callback data is limited to 64 bytes


class UndoReplace(CallbackData, prefix="undo"):
    """«Ні, це інша порада — поверни стару». Carries no ids: the new document's id is
    kept in FSM state, so a forged callback can't touch someone else's document."""


# Telegram sends /start when an animal opens the bot. No other commands:
# plain text is always a question, a file is always advice (client's spec).
@router.message(CommandStart())
async def on_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(GREETING)


# --- uploading documents ---


@router.message(F.document)
async def on_document(
    message: Message,
    bot: Bot,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    document = message.document
    filename = document.file_name or "порада"
    try:
        check_extension(filename)
    except ParsingError as e:
        await message.answer(str(e))
        return
    if document.file_size and document.file_size > MAX_FILE_SIZE:
        await message.answer("Ця порада заважка для мене: я можу взяти не більше ніж 20 МБ.")
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
        await message.answer("Дякую за пораду! Бачу тебе вперше. Як тебе звати?")
        return
    await _process_upload(message, bot, state, session_factory, pending)


@router.message(Upload.waiting_name, F.text)
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
    await message.answer("Скажи своє ім’я словами, будь ласка.")


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
        await callback.message.edit_text("Цю пораду я вже прибрав на місце.")
        return
    scope = ALL_SCOPES[callback_data.index]
    await callback.message.edit_text(f"Дякую! Запам’ятав: {scope.label}.")
    await _save_checking_version(
        callback.message, callback.from_user.id, state, session_factory, upload, scope
    )


@router.callback_query(ScopeChoice.filter())
async def on_stale_scope(callback: CallbackQuery) -> None:
    await callback.answer("Це питання вже минуло.")


@router.callback_query(Upload.can_undo, UndoReplace.filter())
async def on_undo(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    data = await state.get_data()
    if data.get("undo_message_id") != callback.message.message_id:
        # A button under an earlier upload: only the latest one may undo. Keep the state,
        # so the latest message's button still works.
        await callback.answer("Це питання вже минуло.")
        return
    new_id = data["new_id"]
    await state.clear()
    await callback.answer()
    async with session_factory() as session:
        result = await undo_replacement(session, new_id)
    if result.restored is not None:
        await callback.message.edit_text(
            f"Добре, повернув пораду «{result.restored.title}». "
            "Тепер обидві поради живуть окремо."
        )
    elif result.newer_exists:
        await callback.message.edit_text(
            "Стару пораду вже не повернути: відтоді хтось приніс ще новішу."
        )
    else:
        await callback.message.edit_text("Стару пораду вже не повернути.")


@router.callback_query(UndoReplace.filter())
async def on_stale_undo(callback: CallbackQuery) -> None:
    await callback.answer("Це питання вже минуло.")


async def _process_upload(
    message: Message,
    bot: Bot,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    pending: dict,
) -> None:
    filename = pending["filename"]
    await message.answer(f"Гортаю «{filename}»…")
    file = await bot.download(pending["file_id"])
    data = file.read()
    digest = file_hash(data)

    async with session_factory() as session:
        duplicate = await find_active_by_hash(session, digest)
    if duplicate is not None:
        await message.answer(f"Цю пораду я вже знаю: «{duplicate.title}» ({duplicate.scope.label}).")
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
        await message.answer("Щось я не зміг розібрати цю пораду. Принеси її ще раз за хвилинку.")
        return

    if classification.has_instructions:
        # Defence in depth against prompt injection: such text would try to steer answers.
        logger.warning("Rejected upload %s: contains instructions to the bot", filename)
        await message.answer(
            "Тут є вказівки, як мені відповідати, а не поради звірам, тож я цього не збережу. "
            "Прибери їх і принеси пораду ще раз."
        )
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
            f"Порада «{classification.title}». "
            "Не можу зрозуміти, для якої рукавички чи частини лісу вона. "
            "Підкажи, будь ласка:",
            reply_markup=_scope_keyboard(),
        )
        return
    await _save_checking_version(
        message, message.from_user.id, state, session_factory, upload, classification.scope
    )


async def _save_checking_version(
    message: Message,
    author_id: int,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    upload: dict,
    scope: Scope,
) -> None:
    """The scope is final: save. If the advice looks like a new version of one already
    stored for that scope under another title, replace it right away (client: a newer
    version always replaces the old one) and let the sender undo it."""
    async with session_factory() as session:
        previous = await _previous_version(session, upload, scope)
    result = await _store(
        author_id, session_factory, upload, scope, previous.id if previous else None
    )
    saved = f"Зберіг пораду «{result.document.title}» ({scope.label}). Дякую!"
    old = result.replaced
    if previous is None or old is None:
        # Same title (replaced automatically) or nothing to replace: as before.
        if old is not None:
            saved += "\nЦе свіжіша порада: стару я прибрав, тепер звірі чутимуть лише нову."
        await message.answer(saved)
        return

    if old.author is not None:
        origin = f", яку надіслав {old.author.name}, {format_sent_at(old, settings.tz)}"
    else:
        origin = " з «Лісового архіву»"
    builder = InlineKeyboardBuilder()
    builder.button(text="Ні, це інша порада — поверни стару", callback_data=UndoReplace())
    sent = await message.answer(
        f"{saved}\nВона замінила пораду «{old.title}»{origin}: "
        "тепер звірі чутимуть лише нову.",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(Upload.can_undo)
    # Which message's button may undo: earlier uploads' buttons are stale.
    await state.update_data(new_id=result.document.id, undo_message_id=sent.message_id)


async def _previous_version(session: AsyncSession, upload: dict, scope: Scope) -> Document | None:
    """Advice in exactly this scope that the upload replaces; the sender can undo it."""
    if await find_active_version(session, scope, normalize_title(upload["title"])) is not None:
        # Same title: save_document replaces it automatically, as before. No undo button.
        return None
    existing = await active_documents(session, [scope])
    if not existing:
        return None
    try:
        number = await find_previous_version(
            upload["title"], upload["text"], [d.title for d in existing]
        )
    except LLMError:
        # If the check fails, save as separate advice instead of losing the upload.
        logger.exception("Version check failed for %r", upload["title"])
        return None
    return existing[number - 1] if number is not None else None


async def _store(
    author_id: int,
    session_factory: async_sessionmaker[AsyncSession],
    upload: dict,
    scope: Scope,
    replace_id: int | None = None,
) -> SaveResult:
    async with session_factory() as session:
        return await save_document(
            session,
            title=upload["title"],
            scope=scope,
            text=upload["text"],
            source=SOURCE_USER,
            content_hash=upload["hash"],
            author_id=author_id,
            sent_at=datetime.fromisoformat(upload["sent_at"]),
            replace_id=replace_id,
        )


async def _save_name(
    message: Message, raw_name: str, session_factory: async_sessionmaker[AsyncSession]
) -> bool:
    name = " ".join(raw_name.split())
    if not name or len(name) > MAX_NAME_LENGTH:
        await message.answer(f"Ім’я має бути від 1 до {MAX_NAME_LENGTH} літер. Спробуй ще раз.")
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
    await message.answer("Малюнків я не розгляну. Принеси пораду в PDF, Word (.docx) або простому тексті (.txt).")


@router.message(F.voice | F.audio | F.video_note)
async def on_voice(message: Message) -> None:
    await message.answer("Голосу я не чую. Напиши питання словами, будь ласка.")


# --- questions ---


# Any state except waiting_name (handled above): while the place buttons are
# shown, the animal can still ask questions.
@router.message(F.text)
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
    await message.answer("Я розумію питання, написані словами, і поради в PDF, Word (.docx) або .txt.")


# --- formatting helpers ---


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
