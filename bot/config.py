from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Required secrets have no defaults: the bot refuses to start without them.
    # min_length=1 also rejects an empty "BOT_TOKEN=" line in .env.
    bot_token: SecretStr = Field(min_length=1)
    groq_api_key: SecretStr = Field(min_length=1)

    # Answers questions.
    groq_model: str = Field(default="openai/gpt-oss-120b", min_length=1)
    # Picks documents and classifies uploads; a smaller model with its own rate limit.
    groq_router_model: str = Field(default="openai/gpt-oss-20b", min_length=1)
    groq_reasoning_effort: Literal["low", "medium", "high"] = "low"

    database_url: str = "postgresql+asyncpg://bot:bot@db:5432/bot"
    archive_dir: Path = Path("data/archive")
    timezone: str = "Europe/Kyiv"
    # How many previous question/answer pairs per chat the bot remembers.
    history_turns: int = 3

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


settings = Settings()
