"""Asking «is this a new version?» when an upload's title differs from existing advice."""

from unittest.mock import AsyncMock, patch

import pytest

from bot.documents import SOURCE_USER, save_document
from bot.handlers import _previous_version
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


async def save(session, title, scope=OAK):
    result = await save_document(
        session, title=title, scope=scope, text=title, source=SOURCE_USER,
        content_hash=f"{title}-{scope}",
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
