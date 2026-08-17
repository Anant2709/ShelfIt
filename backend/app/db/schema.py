"""Where the schema comes from, and how the app knows it is current.

The schema used to be created by `Base.metadata.create_all()` at import time. That
is convenient and wrong in a specific way: it creates tables that are missing and
never inspects a table it decides already exists. So new tables appeared and a new
column did not, the app booted happily, and every query touching that column failed
at runtime. Recovering meant moving the database file aside and reseeding, which on
a laptop is annoying and on a deployment is data loss.

Migrations replace it. The trade is that the schema no longer appears by magic, so
the app has to be able to answer "is this database at the revision I expect?" --
hence `is_current`. Startup reports the answer rather than fixing it, because
silently mutating a production schema on boot is the same class of mistake as
`create_all`, just with better intentions.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect

from alembic import command
from app.core.config import settings
from app.db.session import build_connect_args
from app.models import Base

logger = logging.getLogger(__name__)

# backend/, resolved from this file so it holds regardless of the working directory
# the app is launched from.
BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
ALEMBIC_DIR = BACKEND_ROOT / "alembic"
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"


def escape_configparser(value: str) -> str:
    """ConfigParser treats `%` as interpolation. Encode it so URLs survive."""
    return value.replace("%", "%%")


def alembic_config(database_url: str | None = None) -> Config:
    """Alembic config with absolute paths.

    `alembic.ini` gives `script_location` relative to the backend directory, which
    breaks the moment the app is started from anywhere else. Both paths are set
    explicitly here so programmatic use does not depend on the caller's cwd.
    """
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    # ConfigParser treats `%` as interpolation. URL-encoded passwords (`%40`
    # for `@`) are otherwise rejected before Alembic ever opens a connection.
    url = database_url or settings.database_url
    config.set_main_option("sqlalchemy.url", escape_configparser(url))
    # Everything reached through here is in-process, where the caller already owns
    # logging. env.py reads this and leaves the root logger alone.
    config.attributes["configure_logging"] = False
    return config


def head_revision() -> str | None:
    """The newest revision the code knows about."""
    return ScriptDirectory(str(ALEMBIC_DIR)).get_current_head()


def current_revision(engine: Engine) -> str | None:
    """The revision a database believes it is at.

    None means either an empty database or one created before migrations existed.
    The two are indistinguishable from here, which is why adopting an existing
    database needs an explicit `alembic stamp`.
    """
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def is_current(engine: Engine) -> bool:
    return current_revision(engine) == head_revision()


def upgrade_to_head(database_url: str | None = None) -> None:
    """Apply outstanding migrations. Used by tooling, never on request paths."""
    command.upgrade(alembic_config(database_url), "head")


class SchemaError(RuntimeError):
    """The database cannot be brought to the current revision safely."""


def _application_tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


def _models_agree(engine: Engine) -> bool:
    """Whether the live schema matches the models, ignoring the version table."""
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
                "compare_server_default": True,
                "render_as_batch": True,
            },
        )
        return compare_metadata(context, Base.metadata) == []


def ensure_schema(database_url: str | None = None) -> str:
    """Bring a database to head, adopting a pre-migration one if it already matches.

    Three starting points, and only one of them is safe to guess about:

    - Empty, or already versioned: run the migrations. That is what `upgrade`
      is for.
    - Tables but no revision, and they match the models: this is the developer's
      database from before Alembic existed. `upgrade` would fail on the first
      CREATE TABLE, so it is *stamped* at head instead. Stamping records a
      revision without running any DDL, and is only safe because the match was
      checked first.
    - Tables but no revision, and they do *not* match: refuse. Stamping that
      would claim the database is current when a column is missing -- the same
      lie `create_all` used to tell, just written down.

    Returns `current`, `upgraded`, or `stamped` so callers can say what happened
    rather than only that it did not crash.
    """
    url = database_url or settings.database_url
    engine = create_engine(url, connect_args=build_connect_args(url))
    try:
        current = current_revision(engine)
        if current == head_revision():
            return "current"

        if current is not None:
            upgrade_to_head(url)
            return "upgraded"

        if not _application_tables(engine):
            upgrade_to_head(url)
            return "upgraded"

        if _models_agree(engine):
            command.stamp(alembic_config(url), "head")
            return "stamped"

        raise SchemaError(
            "This database has tables but no revision, and they do not match "
            "the current models. Stamping it would claim it is current when it "
            "is not. Inspect the drift, or restore a backup and run "
            "'python -m scripts.migrate' again."
        )
    finally:
        engine.dispose()


def log_schema_state(engine: Engine) -> bool:
    """Warn when the database is behind the code. Returns whether it is current.

    Deliberately does not raise. The failure this replaced was a confusing runtime
    error with no explanation; an explicit warning naming both revisions and the
    command to fix it is the actual improvement. Refusing to boot would also make
    the app unrunnable in the one case where you most want a shell: a half-migrated
    database you are trying to inspect.
    """
    try:
        current = current_revision(engine)
    except Exception as exc:  # pragma: no cover - driver-level failure
        logger.warning("Could not determine schema revision: %s", exc)
        return False

    head = head_revision()
    if current == head:
        return True

    if current is None:
        logger.warning(
            "Database has no schema revision. If it is empty, run "
            "'alembic upgrade head'. If it predates migrations, run "
            "'alembic stamp %s' first so its existing tables are not recreated.",
            head,
        )
    else:
        logger.warning(
            "Database is at revision %s but the code expects %s. "
            "Run 'alembic upgrade head'.",
            current,
            head,
        )
    return False
