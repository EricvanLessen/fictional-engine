from __future__ import annotations

from functools import lru_cache
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
    )

    execution_mode: ExecutionMode = "shadow"
    allow_live_trading: bool = False

    telegram_channel_id: int
    telegram_api_id: int
    telegram_api_hash: SecretStr
    telegram_session_string: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_SESSION_STRING", "TELEGRAM_SESSION"),
    )

    tradelocker_base_url: AnyUrl
    tradelocker_username: SecretStr
    tradelocker_password: SecretStr
    tradelocker_account_id: str

    database_url: str
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
            "telegram_session_string": "***" if self.telegram_session_string else None,
            "tradelocker_base_url": str(self.tradelocker_base_url),
            "tradelocker_username": "***",
            "tradelocker_password": "***",
            "tradelocker_account_id": self.tradelocker_account_id,
            "database_url": "***",
            "log_level": self.log_level,
        }


@lru_cache(maxsize=1)
def get_settings() -> EngineSettings:
    return EngineSettings()
