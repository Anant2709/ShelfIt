"""Migrations, and whether they agree with the models.

These tests exist because of a specific, documented failure. Adding
`dispositions.source` to the models did not add it to the developer's database:
`Base.metadata.create_all()` creates missing *tables* and never looks inside one it
decides already exists. The app started, the new chat endpoints worked, and every
disposition query failed on `no such column`.

The suite could not have caught that, and the reason is structural rather than an
oversight in coverage. Tests built their schema from the models on every run, so they
only ever exercised the one case `create_all` handles. Proving a schema change
*applies* means starting from the old schema and evolving it, and nothing recorded
what the old schema was.

`test_models_and_migrations_agree` closes that hole. It builds a database from the
migrations alone, then asks Alembic to diff the result against the models. A model
change made without a migration shows up here as a pending operation, so the failure
that shipped cannot ship silently again.
"""

import logging
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError

from alembic import command
from app.db import schema
from app.models import Base

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_DIR = BACKEND_ROOT / "alembic"

# Tables the models define. Kept explicit so that adding a model without a
# migration fails loudly rather than quietly agreeing with itself.
EXPECTED_TABLES = {
    "cache_entries",
    "chat_messages",
    "conversations",
    "diet_logs",
    "diet_plan_meals",
    "diet_plans",
    "diet_profiles",
    "diet_weigh_ins",
    "dispositions",
    "expirations",
    "inventory_items",
    "learned_categories",
    "learned_shelf_life",
    "sessions",
    "users",
}


@pytest.fixture
def migrated(tmp_path):
    """A throwaway database built by running every migration.

    The URL is passed through Alembic's config, which `env.py` prefers over the
    application settings, so this never touches the developer's database.
    """
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    yield config, engine, url
    engine.dispose()


class TestMigrationsBuildTheSchema:
    def test_every_expected_table_is_created(self, migrated):
        _, engine, _ = migrated
        tables = set(inspect(engine).get_table_names())
        assert EXPECTED_TABLES <= tables

    def test_the_version_is_recorded(self, migrated):
        """Without this row there is no way to know what has been applied."""
        _, engine, _ = migrated
        assert "alembic_version" in set(inspect(engine).get_table_names())

    def test_the_database_reports_the_head_revision(self, migrated):
        _, engine, _ = migrated
        head = ScriptDirectory(str(ALEMBIC_DIR)).get_current_head()
        with engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == (
                head
            )

    def test_the_column_that_caused_this_exists(self, migrated):
        """`dispositions.source` is the column `create_all` silently skipped."""
        _, engine, _ = migrated
        columns = {
            column["name"]
            for column in inspect(engine).get_columns("dispositions")
        }
        assert "source" in columns

    def test_indexes_are_created_too(self, migrated):
        """A migration that forgets an index leaves a slow query, not an error."""
        _, engine, _ = migrated
        inspector = inspect(engine)
        names = {
            index["name"] for index in inspector.get_indexes("inventory_items")
        }
        assert "ix_inventory_items_category" in names
        assert "ix_inventory_items_user_id" in names


class TestAuthMigrationPreservesExistingRows:
    """Option A: the fridge that was already there becomes the demo user's.

    The live database has rows and is stamped at the baseline. Upgrading must
    attach those rows to the known demo account rather than delete them or
    leave `user_id` null.
    """

    def test_existing_items_are_assigned_to_the_demo_user(self, tmp_path):
        from app.core.config import settings
        from app.services.auth import DEMO_USER_ID

        url = f"sqlite:///{tmp_path / 'owned.db'}"
        config = Config(str(BACKEND_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "e94e1828fb01")

        engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO inventory_items "
                    "(id, name, quantity, unit, created_at) "
                    "VALUES ('keep-me', 'Paneer', 1.0, 'pack', '2026-01-01')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO conversations (id, created_at, updated_at) "
                    "VALUES ('talk-1', '2026-01-01', '2026-01-01')"
                )
            )

        command.upgrade(config, "head")

        with engine.connect() as connection:
            item_owner = connection.execute(
                text("SELECT user_id FROM inventory_items WHERE id = 'keep-me'")
            ).scalar()
            conversation_owner = connection.execute(
                text("SELECT user_id FROM conversations WHERE id = 'talk-1'")
            ).scalar()
            email, username = connection.execute(
                text("SELECT email, username FROM users WHERE id = :uid"),
                {"uid": DEMO_USER_ID},
            ).one()
            name = connection.execute(
                text("SELECT name FROM inventory_items WHERE id = 'keep-me'")
            ).scalar()

        assert item_owner == DEMO_USER_ID
        assert conversation_owner == DEMO_USER_ID
        assert email == settings.demo_email.strip().lower()
        assert username == "juhi"
        assert name == "Paneer", "the upgrade must not drop existing rows"
        engine.dispose()


class TestModelsAndMigrationsAgree:
    def test_models_and_migrations_agree(self, migrated):
        """The guard against a model change shipping without a migration.

        If this fails, the diff printed below names exactly what is missing. The
        fix is `alembic revision --autogenerate -m "..."`, then reviewing it.
        """
        _, engine, _ = migrated
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "compare_type": True,
                    "compare_server_default": True,
                    "render_as_batch": True,
                },
            )
            diff = compare_metadata(context, Base.metadata)

        assert diff == [], (
            "The models and the migrations have drifted. Missing migration for:\n"
            + "\n".join(f"  {entry}" for entry in diff)
        )

    def test_create_all_and_the_migrations_produce_the_same_tables(
        self, migrated, tmp_path
    ):
        """Belt and braces on the table set, independent of the diff above."""
        _, migrated_engine, _ = migrated
        from_migrations = set(inspect(migrated_engine).get_table_names()) - {
            "alembic_version"
        }

        direct = create_engine(f"sqlite:///{tmp_path / 'direct.db'}")
        Base.metadata.create_all(bind=direct)
        from_models = set(inspect(direct).get_table_names())
        direct.dispose()

        assert from_migrations == from_models


class TestMigrationsAreReversible:
    def test_downgrading_to_base_removes_the_schema(self, migrated):
        """A migration that cannot be undone is a one-way door in production."""
        config, engine, _ = migrated
        command.downgrade(config, "base")
        remaining = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert remaining == set()

    def test_the_full_cycle_returns_to_the_same_schema(self, migrated):
        config, engine, _ = migrated
        before = set(inspect(engine).get_table_names())
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        assert set(inspect(engine).get_table_names()) == before


class TestRevisionGraph:
    def test_there_is_exactly_one_head(self):
        """Two heads mean two branches and an ambiguous "latest"."""
        assert len(ScriptDirectory(str(ALEMBIC_DIR)).get_heads()) == 1

    def test_there_is_exactly_one_starting_point(self):
        script = ScriptDirectory(str(ALEMBIC_DIR))
        roots = [
            revision.revision
            for revision in script.walk_revisions()
            if revision.down_revision is None
        ]
        assert len(roots) == 1

    def test_every_revision_is_on_the_path_to_the_head(self):
        script = ScriptDirectory(str(ALEMBIC_DIR))
        head = script.get_current_head()
        all_revisions = {rev.revision for rev in script.walk_revisions()}
        on_path = {
            rev.revision for rev in script.iterate_revisions(head, "base")
        }
        assert all_revisions == on_path, "a revision is orphaned from the chain"

    def test_every_migration_defines_both_directions(self):
        """An empty downgrade is how a migration becomes irreversible by accident."""
        for path in sorted((ALEMBIC_DIR / "versions").glob("*.py")):
            body = path.read_text(encoding="utf-8")
            assert "def upgrade()" in body, f"{path.name} has no upgrade"
            assert "def downgrade()" in body, f"{path.name} has no downgrade"
            downgrade_body = body.split("def downgrade()", 1)[1]
            assert "pass" not in downgrade_body.split("\n\n")[0], (
                f"{path.name} has an empty downgrade"
            )


@pytest.fixture
def pre_migration_db(tmp_path):
    """A database built the old way, holding a row worth not losing."""
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, email, username, password_hash, timezone, created_at) "
                "VALUES ('u1', 'a@b.c', 'alice', 'x', 'UTC', '2026-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO inventory_items "
                "(id, name, quantity, unit, created_at, user_id) "
                "VALUES ('keep-me', 'Paneer', 1.0, 'pack', '2026-01-01', 'u1')"
            )
        )
    yield engine, url
    engine.dispose()


class TestAdoptingAnExistingDatabase:
    """The awkward case: a database that already has tables but no revision.

    This is the developer's own database, and it is the one situation Alembic cannot
    work out for itself. `upgrade` would fail on the first CREATE TABLE; `stamp`
    asserts "this is already at that revision" without running any DDL. Getting it
    wrong either destroys the data or leaves the version table lying about it.
    """

    def test_it_reports_no_revision(self, pre_migration_db):
        engine, _ = pre_migration_db
        assert schema.current_revision(engine) is None
        assert schema.is_current(engine) is False

    def test_upgrading_it_fails_because_the_tables_exist(self, pre_migration_db):
        """Why the documented recovery is `stamp` and not `upgrade`."""
        _, url = pre_migration_db
        with pytest.raises(OperationalError, match="already exists"):
            schema.upgrade_to_head(url)

    def test_stamping_it_records_the_baseline_without_touching_data(
        self, pre_migration_db
    ):
        engine, url = pre_migration_db
        command.stamp(schema.alembic_config(url), "head")

        assert schema.is_current(engine) is True
        with engine.connect() as connection:
            surviving = connection.execute(
                text("SELECT name FROM inventory_items WHERE id = 'keep-me'")
            ).scalar()
        assert surviving == "Paneer", "stamping must not touch rows"

    def test_a_stamped_database_agrees_with_the_models(self, pre_migration_db):
        """Proves the baseline really describes what create_all used to build."""
        engine, url = pre_migration_db
        command.stamp(schema.alembic_config(url), "head")
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True, "render_as_batch": True},
            )
            assert compare_metadata(context, Base.metadata) == []


class TestBatchModeOnPopulatedTables:
    """The mechanism that makes migrations possible on SQLite at all.

    SQLite's ALTER TABLE cannot drop a column, change a type, or add a constrained
    column. Batch mode rewrites the table instead: create a copy with the new shape,
    move the rows, swap it in. Without it, the first migration that changes an
    existing column fails on the development database.
    """

    def test_a_column_can_be_added_to_a_table_with_rows_in_it(self, tmp_path):
        engine = create_engine(f"sqlite:///{tmp_path / 'batch.db'}")
        Base.metadata.create_all(bind=engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, email, username, password_hash, timezone, created_at) "
                    "VALUES ('u1', 'a@b.c', 'alice', 'x', 'UTC', '2026-01-01')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO inventory_items "
                    "(id, name, quantity, unit, created_at, user_id) "
                    "VALUES ('a', 'Milk', 2.0, 'litre', '2026-01-01', 'u1')"
                )
            )

        with engine.begin() as connection:
            operations = Operations(
                MigrationContext.configure(
                    connection, opts={"render_as_batch": True}
                )
            )
            with operations.batch_alter_table("inventory_items") as batch_op:
                batch_op.add_column(sa.Column("note", sa.String(), nullable=True))

        inspector = inspect(engine)
        columns = {c["name"] for c in inspector.get_columns("inventory_items")}
        assert "note" in columns

        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT name, quantity FROM inventory_items WHERE id = 'a'")
            ).one()
        assert row == ("Milk", 2.0), "the table rewrite must preserve rows"
        engine.dispose()

    def test_a_column_can_be_dropped_which_plain_sqlite_refuses(self, tmp_path):
        engine = create_engine(f"sqlite:///{tmp_path / 'drop.db'}")
        Base.metadata.create_all(bind=engine)
        with engine.begin() as connection:
            operations = Operations(
                MigrationContext.configure(
                    connection, opts={"render_as_batch": True}
                )
            )
            with operations.batch_alter_table("inventory_items") as batch_op:
                batch_op.drop_column("confidence")

        columns = {
            c["name"] for c in inspect(engine).get_columns("inventory_items")
        }
        assert "confidence" not in columns
        engine.dispose()


class TestSchemaHelpers:
    """The runtime answer to "is this database at the revision I expect?"."""

    def test_head_revision_matches_the_script_directory(self):
        assert schema.head_revision() == (
            ScriptDirectory(str(ALEMBIC_DIR)).get_current_head()
        )

    def test_paths_are_absolute_so_the_working_directory_does_not_matter(self):
        config = schema.alembic_config("sqlite://")
        assert Path(config.get_main_option("script_location")).is_absolute()

    def test_the_supplied_url_wins_over_settings(self):
        config = schema.alembic_config("sqlite:///supplied.db")
        assert config.get_main_option("sqlalchemy.url") == "sqlite:///supplied.db"

    def test_it_falls_back_to_the_configured_database(self):
        from app.core.config import settings

        config = schema.alembic_config()
        assert config.get_main_option("sqlalchemy.url") == settings.database_url

    def test_a_migrated_database_is_reported_as_current(self, migrated):
        _, engine, _ = migrated
        assert schema.is_current(engine) is True
        assert schema.log_schema_state(engine) is True

    def test_an_empty_database_is_reported_as_behind(self, tmp_path, caplog):
        engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
        with caplog.at_level(logging.WARNING):
            assert schema.log_schema_state(engine) is False
        assert "no schema revision" in caplog.text
        # The warning has to name the recovery, or it is just noise.
        assert "stamp" in caplog.text
        engine.dispose()

    def test_a_stale_revision_is_reported_with_both_versions(self, tmp_path, caplog):
        """The message that was missing when this failed the first time."""
        engine = create_engine(f"sqlite:///{tmp_path / 'stale.db'}")
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32))")
            )
            connection.execute(
                text("INSERT INTO alembic_version VALUES ('0000deadbeef')")
            )

        with caplog.at_level(logging.WARNING):
            assert schema.log_schema_state(engine) is False
        assert "0000deadbeef" in caplog.text
        assert schema.head_revision() in caplog.text
        assert "alembic upgrade head" in caplog.text
        engine.dispose()

class TestEnsureSchema:
    """The helper Docker and the seed script actually call.

    `upgrade` is correct for an empty or already-versioned database and wrong for
    the one case this project started from: tables, no revision. `ensure_schema`
    distinguishes those, and refuses the one it cannot tell apart safely.
    """

    def test_an_empty_database_is_migrated(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'fresh.db'}"
        assert schema.ensure_schema(url) == "upgraded"
        engine = create_engine(url)
        assert schema.is_current(engine) is True
        assert "inventory_items" in inspect(engine).get_table_names()
        engine.dispose()

    def test_a_current_database_is_left_alone(self, migrated):
        _, _, url = migrated
        assert schema.ensure_schema(url) == "current"

    def test_a_pre_migration_database_is_stamped_not_recreated(
        self, pre_migration_db
    ):
        engine, url = pre_migration_db
        assert schema.ensure_schema(url) == "stamped"
        assert schema.is_current(engine) is True
        with engine.connect() as connection:
            surviving = connection.execute(
                text("SELECT name FROM inventory_items WHERE id = 'keep-me'")
            ).scalar()
        assert surviving == "Paneer"

    def test_a_mismatched_legacy_database_is_refused(self, tmp_path):
        """Stamping a drifted schema would hide a missing column.

        That is the original `create_all` failure, written down. Refusing is the
        whole point of introducing migrations.
        """
        url = f"sqlite:///{tmp_path / 'drifted.db'}"
        engine = create_engine(url)
        # A real table, but not the current shape -- the situation `create_all`
        # used to hide by deciding the table already existed.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE inventory_items ("
                    "id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, "
                    "quantity FLOAT NOT NULL, unit VARCHAR NOT NULL, "
                    "created_at DATETIME NOT NULL)"
                )
            )
        engine.dispose()

        with pytest.raises(schema.SchemaError, match="do not match"):
            schema.ensure_schema(url)

        engine = create_engine(url)
        assert schema.current_revision(engine) is None, (
            "a refused adopt must not write a revision"
        )
        engine.dispose()

    def test_an_older_revision_is_upgraded(self, migrated):
        config, engine, url = migrated
        command.downgrade(config, "base")
        assert schema.current_revision(engine) is None
        assert schema.ensure_schema(url) == "upgraded"
        assert schema.is_current(engine) is True

    def test_a_versioned_database_behind_head_runs_upgrade(self, tmp_path):
        """A database at the baseline is upgraded, not stamped or refused."""
        url = f"sqlite:///{tmp_path / 'behind.db'}"
        config = schema.alembic_config(url)
        command.upgrade(config, "e94e1828fb01")
        engine = create_engine(url)
        assert schema.current_revision(engine) == "e94e1828fb01"
        assert schema.ensure_schema(url) == "upgraded"
        assert schema.is_current(engine) is True
        assert "users" in inspect(engine).get_table_names()
        engine.dispose()


class TestMigrateScript:
    def test_it_prints_what_it_did(self, tmp_path, monkeypatch, capsys):
        from scripts import migrate as migrate_script

        url = f"sqlite:///{tmp_path / 'cli.db'}"
        monkeypatch.setattr(migrate_script, "ensure_schema", lambda: "upgraded")
        monkeypatch.setattr(migrate_script, "head_revision", lambda: "abc123")
        assert migrate_script.main() == 0
        assert "abc123" in capsys.readouterr().out

    def test_a_refusal_is_a_nonzero_exit(self, monkeypatch, capsys):
        from scripts import migrate as migrate_script

        def _refuse():
            raise schema.SchemaError("do not match")

        monkeypatch.setattr(migrate_script, "ensure_schema", _refuse)
        assert migrate_script.main() == 1
        assert "Refused" in capsys.readouterr().out


class TestStartupDoesNotMutate:
    def test_startup_does_not_create_the_schema(self, tmp_path):
        """The regression guard on the whole change.

        If importing the app creates tables again, every protection here is moot.
        """
        engine = create_engine(f"sqlite:///{tmp_path / 'untouched.db'}")
        schema.log_schema_state(engine)
        assert inspect(engine).get_table_names() == []
        engine.dispose()


class TestEnvironmentConfiguration:
    def test_the_container_adopts_rather_than_blindly_upgrading(self):
        """`alembic upgrade head` fails on a pre-migration volume.

        Docker bind-mounts `./data`, so a developer who already has a database
        from before migrations would otherwise get a container that cannot start.
        """
        body = (BACKEND_ROOT / "Dockerfile").read_text(encoding="utf-8")
        assert "python -m scripts.migrate" in body
        assert "alembic upgrade head &&" not in body

    def test_the_ini_file_hardcodes_no_url(self):
        """Two copies of a connection string is one too many."""
        body = (BACKEND_ROOT / "alembic.ini").read_text(encoding="utf-8")
        active = [
            line
            for line in body.splitlines()
            if line.strip().startswith("sqlalchemy.url")
        ]
        assert active == [], f"alembic.ini sets a URL: {active}"

    def test_batch_mode_is_enabled(self):
        """SQLite cannot ALTER TABLE without it, so migrations would fail there."""
        body = (ALEMBIC_DIR / "env.py").read_text(encoding="utf-8")
        assert body.count("render_as_batch=True") == 2, (
            "batch mode must be set for both offline and online paths"
        )

    def test_type_changes_are_compared(self):
        """Off by default, which is how a widened column goes unmigrated."""
        body = (ALEMBIC_DIR / "env.py").read_text(encoding="utf-8")
        assert body.count("compare_type=True") == 2

    def test_running_a_migration_leaves_application_logging_working(
        self, tmp_path, caplog
    ):
        """Found because these tests failed for a reason unrelated to what they test.

        `env.py` calls `fileConfig`, which replaces the root logger's handlers and by
        default disables every logger already created. Under the CLI that is invisible
        and correct. In-process -- seeding, or a test run -- it tears down logging the
        application already configured, so the schema warning above goes nowhere. The
        programmatic path therefore opts out of configuring logging entirely.
        """
        schema.upgrade_to_head(f"sqlite:///{tmp_path / 'logging.db'}")

        empty = create_engine(f"sqlite:///{tmp_path / 'still-empty.db'}")
        with caplog.at_level(logging.WARNING):
            schema.log_schema_state(empty)
        assert caplog.text != "", "migrations tore down the application's logging"
        assert logging.getLogger("app.db.schema").disabled is False
        empty.dispose()
