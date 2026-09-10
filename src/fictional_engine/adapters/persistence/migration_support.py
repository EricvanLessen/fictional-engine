from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import as_file, files

from alembic.config import Config

MIGRATION_PACKAGE = "fictional_engine.adapters.persistence.migrations"


@contextmanager
def packaged_alembic_config(database_url: str) -> Iterator[Config]:
    migration_scripts = files(MIGRATION_PACKAGE)
    with as_file(migration_scripts) as script_location:
        config = Config()
        config.set_main_option("script_location", str(script_location))
        config.set_main_option("sqlalchemy.url", database_url)
        yield config
