from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, AnyUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ExecutionMode = Literal["shadow", "demo", "live"]


class EngineSettings(BaseSettings):
    """Application settings with safety-first defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    execution_mode: ExecutionMode = "shadow"
    allow_live_trading: bool = False

    telegram_channel_id: int | None = None
    telegram_api_id: int
    telegram_api_hash: SecretStr
    telegram_phone_number: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_PHONE_NUMBER", "TELEGRAM_PHONE"),
    )
    telegram_session_path: Path = Field(
        default=Path(".telegram/session"),
        validation_alias=AliasChoices("TELEGRAM_SESSION_PATH", "TELEGRAM_SESSION"),
    )
    telegram_catchup_limit: int = Field(
        default=200,
        ge=1,
        le=5000,
        validation_alias=AliasChoices("TELEGRAM_CATCHUP_LIMIT", "TELEGRAM_HISTORY_LIMIT"),
    )
    telegram_session_string: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_SESSION_STRING"),
    )

    tradelocker_base_url: AnyUrl | None = None
    tradelocker_username: SecretStr | None = None
    tradelocker_password: SecretStr | None = None
    tradelocker_account_id: str | None = None

    database_url: str = "sqlite:///./data/fictional-engine.db"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @model_validator(mode="after")
    def validate_safety_constraints(self) -> EngineSettings:
        if self.allow_live_trading:
            raise ValueError(
                "ALLOW_LIVE_TRADING=true is rejected in MVP; production gate is not implemented"
            )

        if self.execution_mode == "live":
            raise ValueError("EXECUTION_MODE=live is not supported in MVP")

        return self

    def redacted_dict(self) -> dict[str, object]:
        return {
            "execution_mode": self.execution_mode,
            "allow_live_trading": self.allow_live_trading,
            "telegram_channel_id": self.telegram_channel_id,
            "telegram_api_id": self.telegram_api_id,
            "telegram_api_hash": "***",
            "telegram_phone_number": "***" if self.telegram_phone_number else None,
            "telegram_session_path": str(self.telegram_session_path),
            "telegram_catchup_limit": self.telegram_catchup_limit,
            "telegram_session_string": "***" if self.telegram_session_string else None,
            "tradelocker_base_url": (
                None if self.tradelocker_base_url is None else str(self.tradelocker_base_url)
            ),
            "tradelocker_username": "***" if self.tradelocker_username else None,
            "tradelocker_password": "***" if self.tradelocker_password else None,
            "tradelocker_account_id": self.tradelocker_account_id,
            "database_url": "***",
            "log_level": self.log_level,
        }


@lru_cache(maxsize=1)
def get_settings() -> EngineSettings:
    return EngineSettings()
