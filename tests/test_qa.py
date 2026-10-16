from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from bot import qa
from bot.documents import SOURCE_ARCHIVE, save_document
from bot.llm import Answer
from bot.memory import ChatMemory
from bot.places import FOREST, place_scope

pytestmark = pytest.mark.anyio
KYIV = ZoneInfo("Europe/Kyiv")


@pytest.fixture
async def docs(session):
    fees = await save_document(
        session, title="Членські внески", scope=place_scope("Під дубом"),
        text="4 жолуді до 10 числа", source=SOURCE_ARCHIVE, content_hash="1",
    )
    fire = await save_document(
        session, title="Що робити при пожежі", scope=FOREST,
        text="Біжіть до річки", source=SOURCE_ARCHIVE, content_hash="2",
    )
    return fees.document, fire.document


# Patch where the functions are used (bot.qa), not where they're defined.
async def test_answer_gets_citations_of_used_documents(session, docs):
    fees, _ = docs
    with (
        patch("bot.qa.select_documents", AsyncMock(return_value=[fees.id])),
        patch("bot.qa.answer_question", AsyncMock(return_value=Answer("4 жолуді.", used=[1]))),
    ):
        reply = await qa.answer(session, 1, "Скільки внесок під дубом?", ChatMemory(3), KYIV)
    assert reply == "4 жолуді.\n\n— з документа «Членські внески» рукавички «Під дубом»"


async def test_no_documents_selected_skips_the_answer_model(session, docs):
    answer_question = AsyncMock()
    with (
        patch("bot.qa.select_documents", AsyncMock(return_value=[])),
        patch("bot.qa.answer_question", answer_question),
    ):
        reply = await qa.answer(session, 1, "Яка погода завтра?", ChatMemory(3), KYIV)
    assert reply == qa.NOT_FOUND
    answer_question.assert_not_called()


async def test_answer_without_used_documents_has_no_citation(session, docs):
    fees, _ = docs
    with (
        patch("bot.qa.select_documents", AsyncMock(return_value=[fees.id])),
        patch("bot.qa.answer_question", AsyncMock(return_value=Answer("Не знаю.", used=[]))),
    ):
        reply = await qa.answer(session, 1, "Скільки внесок під дубом?", ChatMemory(3), KYIV)
    assert reply == "Не знаю."


async def test_follow_up_question_keeps_the_place(session, docs):
    fees, fire = docs
    memory = ChatMemory(3)
    memory.add(1, "Скільки внесок під дубом?", "4 жолуді.")
    select = AsyncMock(return_value=[fees.id])
    with (
        patch("bot.qa.select_documents", select),
        patch("bot.qa.answer_question", AsyncMock(return_value=Answer("До 10 числа.", used=[1]))),
    ):
        await qa.answer(session, 1, "А до якого числа?", memory, KYIV)
    catalog = select.call_args.args[1]
    # Oak-specific and forest-wide documents only, oak first.
    assert [doc_id for doc_id, _ in catalog] == [fees.id, fire.id]
