from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from bot import qa
from bot.documents import SOURCE_ARCHIVE, save_document
from bot.llm import (
    ContextDocument,
    Pick,
    PickStatus,
    answer_from_document,
    pick_document,
)
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


def found(source, question="Скільки внесок під дубом?"):
    return AsyncMock(return_value=Pick(PickStatus.FOUND, source, question))


# Patch where the functions are used (bot.qa), not where they're defined.
async def test_answer_cites_its_source_document(session, docs):
    fees, _ = docs
    with (
        patch("bot.qa.select_documents", AsyncMock(return_value=[fees.id])),
        patch("bot.qa.pick_document", found(1)),
        patch("bot.qa.answer_from_document", AsyncMock(return_value="4 жолуді.")),
    ):
        reply = await qa.answer(session, 1, "Скільки внесок під дубом?", ChatMemory(3), KYIV)
    assert reply == "4 жолуді.\n\n— з документа «Членські внески» рукавички «Під дубом»"


async def test_answer_model_sees_only_the_picked_document(session, docs):
    fees, fire = docs
    answer = AsyncMock(return_value="Біжіть до річки.")
    with (
        patch("bot.qa.select_documents", AsyncMock(return_value=[fees.id, fire.id])),
        patch("bot.qa.pick_document", found(2, "Що робити при пожежі під дубом?")),
        patch("bot.qa.answer_from_document", answer),
    ):
        reply = await qa.answer(session, 1, "Що робити при пожежі?", ChatMemory(3), KYIV)
    question, document = answer.call_args.args
    # The standalone question from the pick step, and one document: nothing else to mix in.
    assert question == "Що робити при пожежі під дубом?"
    assert document.title == "Що робити при пожежі"
    assert reply == "Біжіть до річки.\n\n— з документа «Що робити при пожежі»"


async def test_no_documents_selected_skips_the_models(session, docs):
    pick = AsyncMock()
    with (
        patch("bot.qa.select_documents", AsyncMock(return_value=[])),
        patch("bot.qa.pick_document", pick),
    ):
        reply = await qa.answer(session, 1, "Яка погода завтра?", ChatMemory(3), KYIV)
    assert reply == qa.NOT_FOUND
    pick.assert_not_called()


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (PickStatus.NOT_FOUND, qa.NOT_FOUND),
        (PickStatus.NEED_PLACE, qa.NEED_PLACE),
        (PickStatus.TOO_BROAD, qa.TOO_BROAD),
    ],
)
async def test_no_pick_gets_a_fixed_reply_without_citation(session, docs, status, expected):
    fees, fire = docs
    answer = AsyncMock()
    with (
        patch("bot.qa.select_documents", AsyncMock(return_value=[fees.id, fire.id])),
        patch("bot.qa.pick_document", AsyncMock(return_value=Pick(status, None, "?"))),
        patch("bot.qa.answer_from_document", answer),
    ):
        reply = await qa.answer(session, 1, "Покажи всі поради", ChatMemory(3), KYIV)
    assert reply == expected
    answer.assert_not_called()


async def test_document_without_the_answer_means_not_found(session, docs):
    fees, _ = docs
    with (
        patch("bot.qa.select_documents", AsyncMock(return_value=[fees.id])),
        patch("bot.qa.pick_document", found(1)),
        patch("bot.qa.answer_from_document", AsyncMock(return_value=None)),
    ):
        reply = await qa.answer(session, 1, "Скільки внесок під дубом?", ChatMemory(3), KYIV)
    assert reply == qa.NOT_FOUND


async def test_follow_up_question_keeps_the_place(session, docs):
    fees, fire = docs
    memory = ChatMemory(3)
    memory.add(1, "Скільки внесок під дубом?", "4 жолуді.")
    select = AsyncMock(return_value=[fees.id])
    pick = found(1)
    with (
        patch("bot.qa.select_documents", select),
        patch("bot.qa.pick_document", pick),
        patch("bot.qa.answer_from_document", AsyncMock(return_value="До 10 числа.")),
    ):
        await qa.answer(session, 1, "А до якого числа?", memory, KYIV)
    catalog = select.call_args.args[1]
    # Oak-specific and forest-wide documents only, oak first.
    assert [doc_id for doc_id, _ in catalog] == [fees.id, fire.id]
    # The pick step sees earlier questions only, never earlier answers.
    assert pick.call_args.args[2] == ["Скільки внесок під дубом?"]


DOCUMENTS = [
    ContextDocument(number=1, level="увесь ліс", title="А", text="а"),
    ContextDocument(number=2, level="увесь ліс", title="Б", text="б"),
]


@pytest.mark.parametrize(
    ("model_reply", "status", "source"),
    [
        ({"status": "found", "source": 2}, PickStatus.FOUND, 2),
        # A list is cut to its first item: one answer, one source.
        ({"status": "found", "source": [2, 1]}, PickStatus.FOUND, 2),
        # A number we never sent is not cited.
        ({"status": "found", "source": 7}, PickStatus.NOT_FOUND, None),
        ({"status": "found", "source": None}, PickStatus.NOT_FOUND, None),
        # Not found: any source the model adds is dropped.
        ({"status": "too_broad", "source": 1}, PickStatus.TOO_BROAD, None),
    ],
)
async def test_pick_keeps_at_most_one_real_source(model_reply, status, source):
    with patch("bot.llm._complete_json", AsyncMock(return_value=model_reply)):
        pick = await pick_document("Питання?", DOCUMENTS, [])
    assert (pick.status, pick.source) == (status, source)
    # No rewritten question from the model: the original one is used.
    assert pick.question == "Питання?"


@pytest.mark.parametrize(
    ("model_reply", "expected"),
    [
        ({"found": True, "answer": " Так, 6 більше за 5. "}, "Так, 6 більше за 5."),
        ({"found": False, "answer": "Не знаю"}, None),
        ({"found": True, "answer": ""}, None),
    ],
)
async def test_answer_from_document(model_reply, expected):
    complete = AsyncMock(return_value=model_reply)
    with patch("bot.llm._complete_json", complete):
        assert await answer_from_document("Питання?", DOCUMENTS[1]) == expected
    prompt = complete.call_args.args[2]
    assert "<документ номер=2>" in prompt and "<документ номер=1>" not in prompt


async def test_pick_shows_previous_questions_in_order_with_latest_marked():
    # The real bug: two fee questions, then the festival one; «до якої дати?» is about the festival.
    previous = [
        "скільки внесок під дубом?",
        "скільки внесок під дубом?",
        "що приносити на свято врожаю під дубом?",
    ]
    reply = {"status": "found", "source": 1, "question": "До якої дати свято врожаю під дубом?"}
    complete = AsyncMock(return_value=reply)
    with patch("bot.llm._complete_json", complete):
        await pick_document("до якої дати?", DOCUMENTS, previous)

    prompt = complete.call_args.args[2]
    assert (
        "1. скільки внесок під дубом?\n"
        "2. скільки внесок під дубом?\n"
        "3. що приносити на свято врожаю під дубом? ← останнє\n"
    ) in prompt
    assert prompt.count("← останнє") == 1
    # Earlier answers never reach the prompt; the new question comes after the history.
    assert prompt.index("← останнє") < prompt.index("Питання звіра: до якої дати?")
