from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from bot.places import Scope, ScopeKind

# Titles come from the LLM; llm.py cuts them to this length so Postgres accepts them.
MAX_TITLE_LENGTH = 300


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    # Telegram user id, not username: usernames can change or be missing.
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(MAX_TITLE_LENGTH))
    # Title normalized for "is this a new version of the same document?" checks.
    norm_title: Mapped[str] = mapped_column(String(MAX_TITLE_LENGTH), index=True)
    scope_kind: Mapped[str] = mapped_column(String(10))
    scope_value: Mapped[str] = mapped_column(String(50), default="")
    text: Mapped[str] = mapped_column(Text)

    # "archive" (from «Лісовий архів») or "user" (sent via Telegram).
    source: Mapped[str] = mapped_column(String(10))
    # Archive file name, so re-imports on restart are idempotent.
    archive_key: Mapped[str | None] = mapped_column(String(300), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))

    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.telegram_id"))
    author: Mapped[User | None] = relationship(lazy="joined")
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Replaced versions are kept (history) but never shown or used for answers.
    is_active: Mapped[bool] = mapped_column(default=True, index=True)
    replaced_by_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))

    @property
    def scope(self) -> Scope:
        return Scope(ScopeKind(self.scope_kind), self.scope_value)

    @scope.setter
    def scope(self, scope: Scope) -> None:
        self.scope_kind = scope.kind.value
        self.scope_value = scope.value


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: objects stay usable after commit (no lazy reloads in async code).
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
