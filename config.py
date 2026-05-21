"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Database ──
    database_url: str = "farm_data.db"

    # ── CORS (which origins are allowed to call the API) ──
    cors_origins: list[str] = [
        "http://localhost:3000",          # Next.js dev server
        "http://127.0.0.1:3000",
    ]

    # ── Server ──
    api_host: str = "0.0.0.0"             # Listen on all interfaces
    api_port: int = 8000

    class Config:
        env_file = ".env"


settings = Settings()
