"""New versions with a different title: replaced at once, the sender can undo it."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from bot.documents import (
    SOURCE_USER,
    active_documents,
    save_document,
    set_user_name,
    undo_replacement,
)
from bot.handlers import Upload, UndoReplace, _previous_version, _save_checking_version
from bot.llm import LLMError, find_previous_version
from bot.places import FOREST, place_scope

pytestmark = pytest.mark.anyio
OAK = place_scope("Під дубом")


@pytest.mark.parametrize(
    ("model_reply", "expected"),
    [
        ({"version_of": 2}, 2),
        # A list is cut to its first item: one upload replaces at most one document.
        ({"version_of": [2, 1]}, 2),
        ({"version_of": None}, None),
        ({}, None),
        # Numbers we never sent are dropped.
        ({"version_of": 0}, None),
        ({"version_of": 3}, None),
        ({"version_of": -1}, None),
    ],
)
async def test_find_previous_version_keeps_only_a_number_it_sent(model_reply, expected):
    with patch("bot.llm._complete_json", AsyncMock(return_value=model_reply)):
        result = await find_previous_version("Внески на зиму", "5 жолудів", ["А", "Б"])
    assert result == expected


async def test_find_previous_version_with_nothing_to_compare_skips_the_model():
    complete = AsyncMock()
    with patch("bot.llm._complete_json", complete):
        assert await find_previous_version("Внески", "текст", []) is None
    complete.assert_not_called()


async def test_find_previous_version_rejects_malformed_reply():
    with patch("bot.llm._complete_json", AsyncMock(return_value={"version_of": "дві"})):
        with pytest.raises(LLMError):
            await find_previous_version("Внески", "текст", ["А"])


async def save(session, title, scope=OAK, **kw):
    result = await save_document(
        session, title=title, scope=scope, text=title, source=SOURCE_USER,
        content_hash=f"{title}-{scope}", **kw,
    )
    return result.document


def upload(title):
    return {"title": title, "text": "текст", "hash": "new", "sent_at": "2026-10-18T10:00:00+00:00"}


# Patch where the function is used (bot.handlers), not where it's defined.
async def test_previous_version_offers_the_matched_document_of_that_scope(session):
    await save(session, "Пожежі", scope=FOREST)
    rules = await save(session, "Правила співжиття")
    fees = await save(session, "Членські внески")
    find = AsyncMock(return_value=2)
    with patch("bot.handlers.find_previous_version", find):
        previous = await _previous_version(session, upload("Внески на зиму"), OAK)

    assert previous.id == fees.id
    # Only this exact scope's titles are offered, numbered in this order.
    assert find.call_args.args[2] == [rules.title, fees.title]


async def test_same_normalized_title_skips_the_question(session):
    await save(session, "Членські внески")
    find = AsyncMock()
    with patch("bot.handlers.find_previous_version", find):
        previous = await _previous_version(session, upload("ЧЛЕНСЬКІ ВНЕСКИ!"), OAK)
    # save_document replaces it by title, as before.
    assert previous is None
    find.assert_not_called()


async def test_empty_scope_skips_the_question(session):
    await save(session, "Членські внески", scope=place_scope("Під ялиною"))
    find = AsyncMock()
    with patch("bot.handlers.find_previous_version", find):
        assert await _previous_version(session, upload("Внески на зиму"), OAK) is None
    find.assert_not_called()


@pytest.mark.parametrize("model", [AsyncMock(return_value=None), AsyncMock(side_effect=LLMError)])
async def test_no_match_or_failed_check_saves_as_before(session, model):
    await save(session, "Членські внески")
    with patch("bot.handlers.find_previous_version", model):
        assert await _previous_version(session, upload("Нічна варта"), OAK) is None


def fake_message():
    message = AsyncMock()
    message.answer = AsyncMock()
    return message


def fsm():
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


async def test_matched_version_is_replaced_at_once_and_can_be_undone(session_factory):
    async with session_factory() as session:
        await set_user_name(session, 1, "Їжак")
        fees = await save(
            session, "Членські внески", author_id=1,
            sent_at=datetime(2026, 10, 12, 11, 30, tzinfo=UTC),
        )
    message, state = fake_message(), fsm()
    with patch("bot.handlers.find_previous_version", AsyncMock(return_value=1)):
        await _save_checking_version(
            message, 7, state, session_factory, upload("Внески на зиму"), OAK
        )

    async with session_factory() as session:
        active = await active_documents(session)
    # Saved and replaced right away: no waiting before the client's rule applies.
    assert [d.title for d in active] == ["Внески на зиму"]
    new_id = active[0].id
    assert await state.get_state() == Upload.can_undo
    # Only the new document's id is kept; the button itself carries nothing.
    assert await state.get_data() == {"new_id": new_id}
    # Tells the sender what was replaced: title, author and time (Kyiv).
    assert message.answer.call_args.args[0] == (
        "Зберіг пораду «Внески на зиму» (Під дубом). Дякую!\n"
        "Вона замінила пораду «Членські внески», яку надіслав Їжак, 12.10 о 14:30: "
        "тепер звірі чутимуть лише нову."
    )
    button = message.answer.call_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Ні, це інша порада — поверни стару"
    assert button.callback_data == UndoReplace().pack()

    async with session_factory() as session:
        restored = (await undo_replacement(session, new_id)).restored
    assert restored.id == fees.id


async def test_no_match_saves_without_undo_button(session_factory):
    async with session_factory() as session:
        await save(session, "Членські внески")
    message, state = fake_message(), fsm()
    with patch("bot.handlers.find_previous_version", AsyncMock(return_value=None)):
        await _save_checking_version(message, 7, state, session_factory, upload("Нічна варта"), OAK)

    assert await state.get_state() is None
    assert "reply_markup" not in message.answer.call_args.kwargs
    async with session_factory() as session:
        assert len(await active_documents(session)) == 2
