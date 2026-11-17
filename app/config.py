from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: str = ""
    answer_model: str = "gemini-3.5-flash"
    fast_model: str = "gemini-3.5-flash-lite"
    fallback_model: str = "gemini-3.5-flash-lite"  # when the main model is busy (503)
    llm_timeout_ms: int = 60_000


settings = Settings()
