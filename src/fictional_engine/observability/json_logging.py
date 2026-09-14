from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

_BASE_RECORD_KEYS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra_fields = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _BASE_RECORD_KEYS and not key.startswith("_")
        }
        if extra_fields:
            payload["fields"] = _redact(extra_fields)
        return json.dumps(payload, separators=(",", ":"))


def configure_json_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_key_value(key, item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    return value


def _redact_key_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    sensitive_tokens = (
        "hash",
        "phone",
        "password",
        "secret",
        "session",
        "token",
        "code",
    )
    if any(token in lowered for token in sensitive_tokens):
        return "***"
    return _redact(value)
