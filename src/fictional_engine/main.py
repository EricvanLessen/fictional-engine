from __future__ import annotations

import logging

from fictional_engine.config import get_settings
from fictional_engine.observability.json_logging import configure_json_logging


def main() -> int:
    settings = get_settings()
    configure_json_logging(settings.log_level)

    logger = logging.getLogger("fictional_engine")
    logger.info("engine_startup", extra={"settings": settings.redacted_dict()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
