"""Settings + client factory for integrating statement extraction into a service.

Reuses the existing `OPENAI_` env vars (TrueFoundry gateway), so the service's
current `.env` works unchanged. The transaction date format for BUSY is a separate
knob (`STATEMENT_DATE_FMT`, read by excel_writer) since it isn't gateway config.

    from app.config import StatementLLMSettings, make_client, convert

    settings = StatementLLMSettings()                 # reads OPENAI_* from env/.env
    xlsx = convert(pdf_bytes, "statement.pdf", settings=settings)   # -> .xlsx bytes
"""
from __future__ import annotations

from pydantic import HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

from .service import statement_to_xlsx


class StatementLLMSettings(BaseSettings):
    """Gateway/model config, mirroring the service's existing OpenAISettings."""
    API_KEY: str = ""
    API_BASE_ENDPOINT: HttpUrl | None = None
    API_VERSION: str = ""
    USE_GATEWAY: bool = True
    # Model the extraction uses. Benchmarked on a real scanned statement:
    # openai/gpt-5.1 and internal-bedrock/sonnet-46 extracted all rows with 0 digit
    # errors; openai/gpt-4o missed a row. openai/gpt-5 is NOT authorized for the
    # transcription-helper-va account (403) — use gpt-5.1.
    CHAT_MODEL: str = "openai/gpt-5.1"
    # 32k gives "thinking" models (e.g. gemini-2.5-flash) room to emit a full
    # large table — 16k truncated page 2 to empty. Override via OPENAI_MAX_TOKENS.
    MAX_TOKENS: int = 32000

    model_config = SettingsConfigDict(
        env_prefix="OPENAI_", env_file=".env", env_file_encoding="utf-8", extra="ignore",
    )


def make_client(settings: StatementLLMSettings):
    """Build an OpenAI-compatible client pointed at the gateway (or vanilla OpenAI)."""
    from openai import OpenAI

    kwargs: dict = {"api_key": settings.API_KEY}
    if settings.USE_GATEWAY and settings.API_BASE_ENDPOINT:
        kwargs["base_url"] = str(settings.API_BASE_ENDPOINT)
    return OpenAI(**kwargs)


def convert(content: bytes, filename: str, *, settings: StatementLLMSettings | None = None,
            password: str | None = None) -> bytes:
    """High-level: statement bytes -> BUSY-ready .xlsx bytes, using settings/env."""
    settings = settings or StatementLLMSettings()
    client = make_client(settings)
    return statement_to_xlsx(
        content, filename, client=client, model=settings.CHAT_MODEL,
        password=password, max_tokens=settings.MAX_TOKENS,
    )
