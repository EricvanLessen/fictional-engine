"""Telegram adapter package."""

from .mtproto_adapter import (
	MtprotoSessionClient,
	ReadOnlyTelegramMtprotoAdapter,
	ReconnectPolicy,
	TelegramReconnectableError,
)

__all__ = [
	"MtprotoSessionClient",
	"ReadOnlyTelegramMtprotoAdapter",
	"ReconnectPolicy",
	"TelegramReconnectableError",
]
