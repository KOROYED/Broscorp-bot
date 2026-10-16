"""Everything that talks to Groq. Handlers never build prompts themselves."""

import json
import logging
from dataclasses import dataclass

import groq
from groq import AsyncGroq
from pydantic import BaseModel, ValidationError

from bot import prompts
from bot.config import settings
from bot.memory import Turn
from bot.places import FOREST, PLACES, Area, Scope, area_scope, place_scope

logger = logging.getLogger(__name__)

# One client for the whole app: it keeps a pool of HTTPS connections.
# AsyncGroq, because a sync call would block the event loop for every user.
client = AsyncGroq(api_key=settings.groq_api_key.get_secret_value())

CLASSIFY_MAX_CHARS = 4000


class LLMError(Exception):
    """The LLM is unavailable or returned something unusable."""


JSON_RETRIES = 1


async def _complete_json(model: str, system: str, user: str) -> dict:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in range(JSON_RETRIES + 1):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                reasoning_effort=settings.groq_reasoning_effort,
                response_format={"type": "json_object"},
                temperature=0,
            )
            break
        except groq.BadRequestError as e:
            # Groq rejects output that isn't valid JSON; a second try usually works.
            if _is_json_failure(e) and attempt < JSON_RETRIES:
                logger.warning("Model %s produced invalid JSON, retrying", model)
                continue
            raise LLMError(str(e)) from e
        except groq.APIError as e:
            # Base class of RateLimitError, APIConnectionError, APIStatusError...
            raise LLMError(str(e)) from e

    content = response.choices[0].message.content or ""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMError(f"Model returned invalid JSON: {content[:200]!r}") from e
    if not isinstance(data, dict):
        raise LLMError(f"Model returned non-object JSON: {content[:200]!r}")
    return data


def _is_json_failure(error: groq.BadRequestError) -> bool:
    body = error.body if isinstance(error.body, dict) else {}
    inner = body.get("error", body)
    return isinstance(inner, dict) and inner.get("code") == "json_validate_failed"


# --- answering questions ---


@dataclass
class ContextDocument:
    """A document as shown to the model: numbered, with its level and text."""

    number: int
    level: str
    title: str
    text: str


@dataclass
class Answer:
    text: str
    used: list[int]  # ContextDocument.number values the answer is based on


class _AnswerJSON(BaseModel):
    answer: str
    used: list[int] = []


async def answer_question(
    question: str, documents: list[ContextDocument], history: list[Turn] | None = None
) -> Answer:
    blocks = "\n\n".join(
        f"<документ номер={d.number}>\nНазва: «{d.title}»\nДля: {d.level}\n{d.text}\n</документ>"
        for d in documents
    )
    history_text = ""
    if history:
        turns = "\n".join(f"Звір: {t.question}\nТи: {t.answer}" for t in history)
        history_text = prompts.HISTORY.format(turns=turns)
    data = await _complete_json(
        settings.groq_model,
        prompts.ANSWER_SYSTEM,
        prompts.ANSWER_USER.format(documents=blocks, history=history_text, question=question),
    )
    try:
        parsed = _AnswerJSON.model_validate(data)
    except ValidationError as e:
        raise LLMError(f"Unexpected answer format: {data!r}") from e
    valid = {d.number for d in documents}
    # Keep only real document numbers: never cite something we didn't provide.
    used = list(dict.fromkeys(n for n in parsed.used if n in valid))
    return Answer(text=parsed.answer.strip(), used=used)


# --- choosing documents ---


class _RouterJSON(BaseModel):
    ids: list[int] = []


async def select_documents(
    question: str, catalog: list[tuple[int, str]], previous_question: str | None = None
) -> list[int]:
    """Ask the small model which catalog entries (id, description) may answer."""
    if not catalog:
        return []
    lines = "\n".join(f"{doc_id}. {description}" for doc_id, description in catalog)
    history = f"Попереднє питання звіра: {previous_question}\n" if previous_question else ""
    data = await _complete_json(
        settings.groq_router_model,
        prompts.ROUTER_SYSTEM,
        prompts.ROUTER_USER.format(catalog=lines, history=history, question=question),
    )
    try:
        ids = _RouterJSON.model_validate(data).ids
    except ValidationError as e:
        raise LLMError(f"Unexpected router format: {data!r}") from e
    valid = {doc_id for doc_id, _ in catalog}
    return list(dict.fromkeys(i for i in ids if i in valid))


# --- classifying uploads ---


@dataclass
class Classification:
    title: str
    scope: Scope | None  # None = unclear; the bot must ask the sender, never guess
    # The text tries to instruct the bot (prompt injection): such uploads are rejected.
    has_instructions: bool = False


class _ClassifyJSON(BaseModel):
    title: str
    scope: str
    has_instructions: bool = False


def parse_scope(value: str) -> Scope | None:
    value = value.strip()
    if value in PLACES:
        return place_scope(value)
    if value in {a.value for a in Area}:
        return area_scope(Area(value))
    if value == "forest":
        return FOREST
    return None


async def classify_document(filename: str, text: str) -> Classification:
    system = prompts.CLASSIFY_SYSTEM.format(places=", ".join(f"«{p}»" for p in PLACES))
    user = prompts.CLASSIFY_USER.format(filename=filename, text=text[:CLASSIFY_MAX_CHARS])
    data = await _complete_json(settings.groq_router_model, system, user)
    try:
        parsed = _ClassifyJSON.model_validate(data)
    except ValidationError as e:
        raise LLMError(f"Unexpected classification format: {data!r}") from e
    title = parsed.title.strip().strip("«»\"'") or filename.rsplit(".", 1)[0]
    return Classification(
        title=title, scope=parse_scope(parsed.scope), has_instructions=parsed.has_instructions
    )
