from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Find Fortune Route"
    database_url: str = "postgresql+psycopg://fortune:fortune@localhost:5432/find_fortune_route"
    redis_url: str = "redis://localhost:6379/0"
    akshare_enabled: bool = True
    default_stock_pool: str = "000001,600519,000858,601318,000333,300750,600036,600276"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def stock_pool(self) -> list[str]:
        return [item.strip() for item in self.default_stock_pool.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
