from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    app_env: str = "development"
    secret_key: str = "change-me-in-production"
    database_url: str = "postgresql+psycopg://adcreative_app:adcreative_app@localhost:5433/adcreative"  # runtime (RLS applies)
    migration_database_url: str = "postgresql+psycopg://adcreative:adcreative@localhost:5433/adcreative"  # owner
    redis_url: str = "redis://localhost:6380/0"
    cors_origins: str = "http://localhost:3000"
    cookie_secure: bool = False
    session_ttl_hours: int = 24 * 7
    storage_backend: str = "local"
    public_base_url: str = "http://localhost:8000"   # origin used in signed file URLs (local storage)
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "adcreative"
    celery_eager: bool = False            # run tasks inline (tests / no-broker dev)
    llm_provider: str = "mock"
    llm_model: str = ""
    anthropic_api_key: str = ""
    llm_input_cost_per_mtok: float = 0.0   # USD per million tokens; 0 => usage is recorded but flagged unpriced
    llm_output_cost_per_mtok: float = 0.0
    image_provider: str = "mock"
    image_model: str = ""
    image_cost_per_image: float = 0.0     # USD per generated image; 0 => unpriced
    openai_api_key: str = ""
    editor_version_interval_s: int = 600   # automatic version checkpoint at most this often while editing
    editor_max_batch: int = 50             # commands per save request


@lru_cache
def get_settings() -> Settings:
    return Settings()
