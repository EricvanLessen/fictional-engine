from __future__ import annotations

import logging
import sys

from pydantic import ValidationError

from fictional_engine.config import get_settings
from fictional_engine.observability.json_logging import configure_json_logging


def main() -> int:
    try:
        settings = get_settings()
    except ValidationError as e:
        # Prevent Pydantic from printing validation error details with input values to stderr
        sys.stderr.write("[CONFIGURATION ERROR] Settings validation failed. Check required environment variables.\n")
        sys.stderr.flush()
        return 1

    configure_json_logging(settings.log_level)

    logger = logging.getLogger("fictional_engine")
    logger.info("engine_startup", extra={"settings": settings.redacted_dict()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
