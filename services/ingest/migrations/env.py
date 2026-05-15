"""Alembic migration environment.

SQLite-backed; migrations run synchronously (Alembic's async support is in
preview — keeping it simple for Phase 1).
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Resolve the DB URL from env if not set in alembic.ini.
_db_path = os.environ.get("INGEST_DB_PATH", "data/ingest.db")
_db_url = f"sqlite:///{_db_path}"


def run_migrations_offline() -> None:
    """Run migrations without a live connection (generates SQL script)."""
    context.configure(
        url=_db_url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the live database."""
    engine = create_engine(_db_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
