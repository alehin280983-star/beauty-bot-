from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    bot_token: str
    database_url: str
    redis_url: str
    admin_ids: list[int]

    @field_validator("database_url", mode="before")
    @classmethod
    def fix_asyncpg_scheme(cls, v: str) -> str:
        # Railway provides postgresql:// or postgres://, asyncpg needs postgresql+asyncpg://
        if isinstance(v, str):
            v = v.replace("postgres://", "postgresql://", 1)
            if v.startswith("postgresql://"):
                v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    studio_name: str = "Beauty Studio"
    studio_address: str = ""
    studio_phone: str = ""
    cancel_deadline_hours: int = 2
    review_delay_hours: int = 3
    studio_timezone: str = "Europe/Kyiv"


settings = Settings()
