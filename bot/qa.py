"""Answer a question from the stored documents, with citations.

Flow: scope filter → router + lexical search → answering model → the code
appends citations for the documents the model says it used. Citations are
formatted by code, not by the LLM, so they always match the agreed format.
"""

import logging
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import Document
from bot.documents import active_documents, format_citation
from bot.llm import ContextDocument, answer_question, select_documents
from bot.memory import ChatMemory
from bot.retrieval import candidates, detect_scope, lexical_search, level_label

logger = logging.getLogger(__name__)

MAX_CONTEXT_DOCUMENTS = 10
# Groq's free tier allows 8000 tokens/minute; one huge upload must not eat it all.
MAX_DOCUMENT_CHARS = 6000

NOT_FOUND = (
    "На жаль, у документах лісу я не знайшов відповіді на це питання. "
    "Якщо хтось має таку пораду, надішліть її мені документом."
)


async def answer(
    session: AsyncSession, chat_id: int, question: str, memory: ChatMemory, tz: ZoneInfo
) -> str:
    previous = memory.last_question(chat_id)
    pool = candidates(await active_documents(session), detect_scope(question, previous))
    docs = await _select(question, previous, pool)
    if not docs:
        memory.add(chat_id, question, NOT_FOUND)
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
    result = await answer_question(question, context, memory.turns(chat_id))
    logger.info("Question %r: context %s, used %s", question, [d.id for d in docs], result.used)

    text = result.text or NOT_FOUND
    if result.used:
        citations = [format_citation(docs[n - 1], tz) for n in result.used]
        text = text + "\n\n" + "\n".join(citations)
    memory.add(chat_id, question, text)
    return text


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
