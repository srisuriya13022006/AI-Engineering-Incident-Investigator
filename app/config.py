from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    # FalkorDB Connection
    falkordb_host: str = "127.0.0.1"
    falkordb_port: int = 6379
    falkordb_graph: str = "mai_agent_memory"
    falkordb_password: Optional[str] = None
    falkordb_timeout_sec: float = 2.0

    # Application Configuration
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    debug: bool = False

    # LLM Provider Configuration (Optional, for Mode B conversation extraction)
    llm_provider: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model: str = "gpt-4o-mini"
    llm_base_url: Optional[str] = None
    llm_timeout_sec: float = 15.0

    # Gemini API Configuration (Milestone 5)
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-3.8-flash"
    gemini_timeout_sec: float = 30.0

    # GitHub Integration Configuration (Milestone 9)
    github_token: Optional[str] = None
    github_api_base_url: str = "https://api.github.com"
    github_timeout_sec: float = 15.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
