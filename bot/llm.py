"""Everything that talks to Groq. Handlers never build prompts themselves."""

import json
import logging
from dataclasses import dataclass
from enum import StrEnum

import groq
from groq import AsyncGroq
from pydantic import BaseModel, ValidationError, field_validator

from bot import prompts
from bot.config import settings
from bot.places import FOREST, PLACES, Area, Scope, area_scope, place_scope

logger = logging.getLogger(__name__)

# One client for the whole app: it keeps a pool of HTTPS connections.
# AsyncGroq, because a sync call would block the event loop for every user.
client = AsyncGroq(api_key=settings.groq_api_key.get_secret_value())

# Must be ≥ qa.MAX_DOCUMENT_CHARS: the answer step must never see text the injection check skipped.
CLASSIFY_MAX_CHARS = 6000


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


def _first_item(value: object) -> object:
    """The model may send a list where one number is asked for: keep only the first."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


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


def _document_block(d: ContextDocument) -> str:
    return f"<документ номер={d.number}>\nНазва: «{d.title}»\nДля: {d.level}\n{d.text}\n</документ>"


class PickStatus(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    NEED_PLACE = "need_place"  # the answer depends on a place nobody named
    TOO_BROAD = "too_broad"  # «покажи все», comparisons: no single document answers


@dataclass
class Pick:
    status: PickStatus
    source: int | None  # ContextDocument.number, set only when status is FOUND
    question: str  # standalone question: «а до якого числа?» with the place and topic filled in


class _PickJSON(BaseModel):
    status: PickStatus
    source: int | None = None
    question: str = ""

    @field_validator("source", mode="before")
    @classmethod
    def _one_source(cls, value: object) -> object:
        # One source per answer is our rule, not the model's choice.
        return _first_item(value)


async def pick_document(
    question: str, documents: list[ContextDocument], previous_questions: list[str]
) -> Pick:
    """Step 1: choose the ONE document that answers. Writes no answer text.

    Only earlier *questions* are shown, never earlier answers: those came from
    other documents, and their facts must not leak into this answer.
    """
    history = ""
    if previous_questions:
        # Oldest → newest, latest marked: a follow-up refers to the latest question,
        # even if earlier ones were mostly about another topic.
        lines = [f"{i}. {q}" for i, q in enumerate(previous_questions, start=1)]
        lines[-1] += " ← останнє"
        history = prompts.PREVIOUS_QUESTIONS.format(questions="\n".join(lines))
    data = await _complete_json(
        settings.groq_model,
        prompts.PICK_SYSTEM,
        prompts.PICK_USER.format(
            documents="\n\n".join(_document_block(d) for d in documents),
            history=history,
            question=question,
        ),
    )
    try:
        parsed = _PickJSON.model_validate(data)
    except ValidationError as e:
        raise LLMError(f"Unexpected pick format: {data!r}") from e

    standalone = parsed.question.strip() or question
    if parsed.status is not PickStatus.FOUND:
        return Pick(parsed.status, None, standalone)
    # Only a real document number: never cite something we didn't provide.
    if parsed.source not in {d.number for d in documents}:
        return Pick(PickStatus.NOT_FOUND, None, standalone)
    return Pick(PickStatus.FOUND, parsed.source, standalone)


class _AnswerJSON(BaseModel):
    found: bool
    answer: str = ""


async def answer_from_document(question: str, document: ContextDocument) -> str | None:
    """Step 2: answer from this one document only. None = it doesn't answer the question.

    The model sees no other document and no chat history, so it has nothing to mix in.
    """
    data = await _complete_json(
        settings.groq_model,
        prompts.ANSWER_SYSTEM,
        prompts.ANSWER_USER.format(document=_document_block(document), question=question),
    )
    try:
        parsed = _AnswerJSON.model_validate(data)
    except ValidationError as e:
        raise LLMError(f"Unexpected answer format: {data!r}") from e
    text = parsed.answer.strip()
    return text if parsed.found and text else None


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


# --- new versions of existing advice ---


class _VersionJSON(BaseModel):
    version_of: int | None = None

    @field_validator("version_of", mode="before")
    @classmethod
    def _one_document(cls, value: object) -> object:
        return _first_item(value)


async def find_previous_version(title: str, text: str, existing_titles: list[str]) -> int | None:
    """Which existing advice (1-based number in `existing_titles`) the upload is a new
    version of, or None. Only a suggestion: the sender confirms it with a button."""
    if not existing_titles:
        return None
    catalog = "\n".join(f"{i}. «{t}»" for i, t in enumerate(existing_titles, start=1))
    data = await _complete_json(
        settings.groq_router_model,
        prompts.VERSION_SYSTEM,
        prompts.VERSION_USER.format(
            existing=catalog, title=title, text=text[:CLASSIFY_MAX_CHARS]
        ),
    )
    try:
        number = _VersionJSON.model_validate(data).version_of
    except ValidationError as e:
        raise LLMError(f"Unexpected version format: {data!r}") from e
    # Only a number we sent: never point at a document that wasn't offered.
    return number if number in range(1, len(existing_titles) + 1) else None
