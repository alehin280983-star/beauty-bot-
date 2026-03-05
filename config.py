from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    bot_token: str
    database_url: str
    redis_url: str
    admin_ids: list[int]

    studio_name: str = "Beauty Studio"
    studio_address: str = ""
    studio_phone: str = ""
    cancel_deadline_hours: int = 2
    review_delay_hours: int = 3
    studio_timezone: str = "Europe/Kyiv"


settings = Settings()
