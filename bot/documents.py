"""Storing documents and users: versioning, listing, deleting, citations."""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import Document, User, utcnow
from bot.places import PLACES, Area, Scope, ScopeKind

SOURCE_ARCHIVE = "archive"
SOURCE_USER = "user"

_PLACE_WORDS = re.compile(
    "|".join(re.escape(p.lower()) for p in sorted(PLACES, key=len, reverse=True))
)
_RUKAVYCHKA = re.compile(r"\bрукавич\w*")
_NON_WORD = re.compile(r"[^\w\s]")


def normalize_title(title: str) -> str:
    """Reduce a title to its topic, so versions of one document compare equal.

    «Членські внески рукавички «Під дубом»» and «Членські внески» both become
    "членські внески". The place itself is compared separately (via the scope).
    """
    t = title.lower().replace("’", "'").replace("ʼ", "'")
    t = _PLACE_WORDS.sub(" ", t)
    t = _RUKAVYCHKA.sub(" ", t)
    t = _NON_WORD.sub(" ", t)
    return " ".join(t.split())


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- users ---


async def get_user(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.get(User, telegram_id)


async def set_user_name(session: AsyncSession, telegram_id: int, name: str) -> User:
    user = await session.get(User, telegram_id)
    if user is None:
        user = User(telegram_id=telegram_id, name=name)
        session.add(user)
    else:
        user.name = name
    await session.commit()
    return user


# --- documents ---


@dataclass
class SaveResult:
    document: Document
    replaced: Document | None


async def find_active_version(
    session: AsyncSession, scope: Scope, norm_title: str
) -> Document | None:
    return await session.scalar(
        select(Document).where(
            Document.is_active,
            Document.scope_kind == scope.kind.value,
            Document.scope_value == scope.value,
            Document.norm_title == norm_title,
        )
    )


async def save_document(
    session: AsyncSession,
    *,
    title: str,
    scope: Scope,
    text: str,
    source: str,
    content_hash: str,
    author_id: int | None = None,
    archive_key: str | None = None,
    sent_at: datetime | None = None,
) -> SaveResult:
    """Store a document. An active one with the same topic and scope is replaced.

    "Same document" = same normalized title + same scope (client: newer version
    of the same advice for the same place replaces the old one, anyone may replace).
    """
    norm = normalize_title(title)
    old = await find_active_version(session, scope, norm)

    doc = Document(
        title=title,
        norm_title=norm,
        text=text,
        source=source,
        author_id=author_id,
        archive_key=archive_key,
        content_hash=content_hash,
        sent_at=sent_at or utcnow(),
    )
    doc.scope = scope
    session.add(doc)
    await session.flush()  # assigns doc.id

    if old is not None:
        old.is_active = False
        old.replaced_by_id = doc.id
    await session.commit()
    await session.refresh(doc, ["author"])
    return SaveResult(document=doc, replaced=old)


async def find_active_by_hash(session: AsyncSession, content_hash: str) -> Document | None:
    """The exact same file is already stored (e.g. sent twice)."""
    return await session.scalar(
        select(Document).where(Document.is_active, Document.content_hash == content_hash)
    )


async def archive_hashes(session: AsyncSession) -> dict[str, set[str]]:
    """Archive file name -> hashes ever imported (active or replaced)."""
    rows = await session.execute(
        select(Document.archive_key, Document.content_hash).where(
            Document.archive_key.is_not(None)
        )
    )
    result: dict[str, set[str]] = {}
    for key, digest in rows:
        result.setdefault(key, set()).add(digest)
    return result


async def active_documents(
    session: AsyncSession, scopes: list[Scope] | None = None
) -> list[Document]:
    """Active documents, optionally only those in the given scopes."""
    query = select(Document).where(Document.is_active).order_by(Document.id)
    docs = list(await session.scalars(query))
    if scopes is not None:
        wanted = set(scopes)
        docs = [d for d in docs if d.scope in wanted]
    return docs


# --- citations ---


def format_citation(doc: Document, tz: ZoneInfo) -> str:
    """Citation in the format agreed with the client.

    — з документа «Членські внески» рукавички «Під дубом», надіслав Їжак, 12.10 о 14:30
    Archive documents have no author or time, so only the title (and place).
    """
    parts = f"— з документа «{doc.title}»"
    if doc.scope_kind == ScopeKind.PLACE:
        parts += f" рукавички «{doc.scope_value}»"
    elif doc.scope_kind == ScopeKind.AREA and not _mentions_area(doc.title, doc.scope_value):
        parts += f" ({doc.scope.label})"
    if doc.source == SOURCE_USER and doc.author is not None:
        sent_at = doc.sent_at
        if sent_at.tzinfo is None:  # SQLite drops tzinfo; we always store UTC
            sent_at = sent_at.replace(tzinfo=UTC)
        sent = sent_at.astimezone(tz)
        parts += f", надіслав {doc.author.name}, {sent:%d.%m} о {sent:%H:%M}"
    return parts


_AREA_STEMS = {Area.CONIFEROUS: "хвойн", Area.DECIDUOUS: "листян", Area.RIVER: "річ"}


def _mentions_area(title: str, area: str) -> bool:
    """«Небезпеки хвойного лісу» already says which area; don't repeat it."""
    return _AREA_STEMS[Area(area)] in title.lower()
