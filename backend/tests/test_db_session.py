"""Tests for engine construction.

SQLite needs a driver-specific flag that other drivers must not receive, and
Postgres is the intended production target, so both branches are pinned. The
decision is a pure function precisely so it can be tested without reloading
modules and disturbing the shared settings singleton.
"""

import pytest

from app.db.session import SessionLocal, build_connect_args, connect_args, engine


class TestConnectArgs:
    @pytest.mark.parametrize(
        "url",
        [
            "sqlite:///./shelfit.db",
            "sqlite:////absolute/path/shelfit.db",
            "sqlite+pysqlite:///:memory:",
        ],
    )
    def test_sqlite_disables_same_thread_check(self, url):
        """FastAPI serves requests on a threadpool, so the default would break."""
        assert build_connect_args(url) == {"check_same_thread": False}

    @pytest.mark.parametrize(
        "url",
        [
            "postgresql+psycopg://user:pass@localhost:5432/shelfit",
            "postgresql://user:pass@localhost:5432/shelfit",
            "mysql+pymysql://user:pass@localhost/shelfit",
        ],
    )
    def test_other_drivers_get_no_sqlite_flags(self, url):
        """check_same_thread is not a valid argument for psycopg."""
        assert build_connect_args(url) == {}


class TestConfiguredEngine:
    def test_test_run_is_wired_to_sqlite(self):
        assert engine.url.get_backend_name() == "sqlite"

    def test_module_level_connect_args_match_the_configured_url(self):
        assert connect_args == {"check_same_thread": False}


class TestSessionFactory:
    def test_sessions_do_not_autocommit_or_autoflush(self):
        """Handlers commit explicitly; implicit writes would mask ordering bugs."""
        assert SessionLocal.kw["autocommit"] is False
        assert SessionLocal.kw["autoflush"] is False

    def test_factory_is_bound_to_the_configured_engine(self):
        assert SessionLocal.kw["bind"] is engine
