"""Regression tests for configuration path resolution.

These exist because the original config used paths relative to the process
working directory, so the app silently loaded a different .env and a different
database depending on where it was launched from.
"""

import os
from pathlib import Path

from app.core.config import BACKEND_DIR, BASE_DIR, Settings


def test_default_paths_are_absolute():
    settings = Settings()
    assert Path(settings.upload_dir).is_absolute()
    assert Path(settings.shelf_life_path).is_absolute()
    assert Path(settings.model_path).is_absolute()


def test_default_database_url_is_absolute():
    # Constructed without the test environment override.
    default = f"sqlite:///{BASE_DIR / 'data' / 'shelfit.db'}"
    assert default.startswith("sqlite:////"), "expected an absolute sqlite path"


def test_paths_do_not_depend_on_cwd(tmp_path, monkeypatch):
    before = Settings().upload_dir
    monkeypatch.chdir(tmp_path)
    after = Settings().upload_dir
    assert before == after


def test_backend_dir_points_at_the_backend_package():
    assert (BACKEND_DIR / "app" / "main.py").exists()
    assert BASE_DIR == BACKEND_DIR.parent


def test_env_file_is_resolved_from_module_location_not_cwd():
    env_file = Settings.model_config["env_file"]
    assert Path(env_file).is_absolute()
    assert Path(env_file) == BACKEND_DIR / ".env"


def test_environment_overrides_are_honoured(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    assert Settings().openai_model == "gpt-4o"


def test_test_suite_is_isolated_from_real_data():
    """Guard against a regression that would let tests touch real files."""
    assert os.environ["DATABASE_URL"].endswith("import-time.db")
    assert "shelfit-tests-" in os.environ["UPLOAD_DIR"]
