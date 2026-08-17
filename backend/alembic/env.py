"""Alembic environment.

The database URL is read from the application's own settings rather than from
`alembic.ini`. Two copies of a connection string is two things to keep in step, and
the one that drifts is always the one you are not looking at -- so `alembic.ini`
holds no URL at all and this is the single source.

`render_as_batch` is on because the development database is SQLite, which cannot
`ALTER TABLE` in most of the ways a schema change needs: it will not drop a column
on older versions, will not change a column type, and will not add a constrained
column to a populated table. Batch mode expands those operations into create-copy-
swap against a temporary table. It is a no-op on Postgres, so leaving it on keeps
one migration working on both.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core.config import settings
from app.db.schema import escape_configparser

# Imports the model package rather than just Base, so every table is registered on
# the metadata before autogenerate compares it against the database. Importing only
# Base would silently produce migrations that drop the tables it had not seen.
from app.models import Base

config = context.config

# Logging setup belongs to whoever owns the process. From the `alembic` CLI that is
# this file, so the ini's config is applied. When migrations are run in-process --
# seeding, or the test suite -- the application already owns logging, and fileConfig
# would tear it down: it replaces the root handlers and, by default, disables every
# logger already created. The visible symptom is the app going quiet afterwards,
# including the warning that says the schema is out of date.
if config.config_file_name is not None and config.attributes.get(
    "configure_logging", True
):
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _database_url() -> str:
    """The URL to migrate, preferring one supplied by the caller.

    Normally nothing sets it and this is `settings.database_url`, so `alembic
    upgrade head` targets the same database the app does. A caller that passes one
    explicitly wins, which is what lets the tests run real migrations against a
    throwaway file instead of the developer's database.
    """
    return config.get_main_option("sqlalchemy.url", None) or settings.database_url


config.set_main_option("sqlalchemy.url", escape_configparser(_database_url()))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting, for review or manual application."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        # Off by default, which is how a widened column silently goes unmigrated.
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
