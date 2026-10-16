from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8"
    )

    bot_token: SecretStr
    groq_api_key: SecretStr
    groq_model: str = "llama-3.3-70b-versatile"

settings = Settings()