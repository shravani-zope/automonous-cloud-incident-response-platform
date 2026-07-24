from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./acirp.db"
    kafka_bootstrap: str = "localhost:19092"
    kafka_enabled: bool = False
    prometheus_url: str = "http://localhost:9090"
    chaos_target_url: str = "http://localhost:8081"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    auto_remediate: bool = True
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    poll_interval_seconds: float = 8.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def use_llm(self) -> bool:
        return bool(self.openai_api_key.strip())


settings = Settings()
