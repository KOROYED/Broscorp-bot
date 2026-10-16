"""Answer a question from the stored documents, with a citation.

Flow: scope filter → router + lexical search (candidates) → the model picks the
ONE document that answers → a second call answers from that document alone →
the code appends its citation.

One answer, one document: the answering model never sees a second document or
earlier answers, so it can't mix them. Citations are formatted by code, not by
the LLM, so they always match the agreed format.
"""

import logging
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import Document
from bot.documents import active_documents, format_citation
from bot.llm import (
    ContextDocument,
    PickStatus,
    answer_from_document,
    pick_document,
    select_documents,
)
from bot.memory import ChatMemory
from bot.retrieval import candidates, detect_scope, lexical_search, level_label

logger = logging.getLogger(__name__)

MAX_CONTEXT_DOCUMENTS = 10
# Groq's free tier allows 8000 tokens/minute; one huge upload must not eat it all.
MAX_DOCUMENT_CHARS = 6000

NOT_FOUND = (
    "На жаль, про це я нічого не знаю. "
    "Якщо хтось у лісі знає відповідь, нехай принесе мені пораду."
)
NEED_PLACE = (
    "У різних рукавичках це по-різному. Скажи, про яку рукавичку питаєш, "
    "наприклад: «Скільки внесок під дубом?»"
)
TOO_BROAD = (
    "Усього одразу не перекажеш 🌿 Спитай мене про щось одне, "
    "наприклад: «Скільки внесок під дубом?» або «Коли тиша у соснах?»"
)
# Fixed texts, written by code: no LLM wording, so nothing from any document leaks in.
# Spoken by Лісовий Порадник: no talk of documents, files or a bot. «Рукавичка» is fine:
# the client confirmed it's a real place in the forest, not a technical term.
_NO_ANSWER = {
    PickStatus.NOT_FOUND: NOT_FOUND,
    PickStatus.NEED_PLACE: NEED_PLACE,
    PickStatus.TOO_BROAD: TOO_BROAD,
}


async def answer(
    session: AsyncSession, chat_id: int, question: str, memory: ChatMemory, tz: ZoneInfo
) -> str:
    reply = await _answer(session, chat_id, question, memory, tz)
    memory.add(chat_id, question, reply)
    return reply


async def _answer(
    session: AsyncSession, chat_id: int, question: str, memory: ChatMemory, tz: ZoneInfo
) -> str:
    previous = memory.last_question(chat_id)
    pool = candidates(await active_documents(session), detect_scope(question, previous))
    docs = await _select(question, previous, pool)
    if not docs:
        return NOT_FOUND

    context = [
        ContextDocument(
            number=i,
            level=level_label(d.scope),
            title=d.title,
            text=d.text[:MAX_DOCUMENT_CHARS],
        )
        for i, d in enumerate(docs, start=1)
    ]
    previous_questions = [t.question for t in memory.turns(chat_id)]
    pick = await pick_document(question, context, previous_questions)
    logger.info(
        "Question %r: candidates %s, pick %s %s",
        question, [d.id for d in docs], pick.status, pick.source,
    )
    if pick.source is None:
        return _NO_ANSWER[pick.status]

    text = await answer_from_document(pick.question, context[pick.source - 1])
    if text is None:
        return NOT_FOUND
    return text + "\n\n" + format_citation(docs[pick.source - 1], tz)


async def _select(question: str, previous: str | None, pool: list[Document]) -> list[Document]:
    by_id = {d.id: d for d in pool}
    catalog = [(d.id, f"«{d.title}» — для: {level_label(d.scope)}") for d in pool]
    routed = await select_documents(question, catalog, previous)
    lexical = [d.id for d in lexical_search(question, pool)]

    chosen_ids = list(dict.fromkeys(routed + lexical))[:MAX_CONTEXT_DOCUMENTS]
    chosen = [by_id[i] for i in chosen_ids]
    # Keep the pool's order: place-specific advice first, as the client asked.
    order = {d.id: i for i, d in enumerate(pool)}
    return sorted(chosen, key=lambda d: order[d.id])
