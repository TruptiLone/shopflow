from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Twelve-factor config: everything comes from env vars (or a local .env)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "shopflow-api"
    environment: str = "local"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://shopflow:shopflow@localhost:5432/shopflow"
    db_pool_size: int = 10
    db_max_overflow: int = 10

    # Pagination guard rails
    default_page_size: int = 20
    max_page_size: int = 100

    # Synthetic inventory: Olist has no stock levels, so every product starts with this.
    default_stock: int = 100

    @property
    def sync_database_url(self) -> str:
        """psycopg (sync) URL for ingestion and Alembic."""
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()
