from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.web import mount_frontend


def test_render_url_rewrites_localhost_defaults():
    settings = Settings(
        render_external_url="https://shelfit.onrender.com",
        frontend_url="http://localhost:5173",
        cors_origins="http://localhost:5173,http://127.0.0.1:5173",
        google_redirect_uri="http://localhost:8000/api/auth/google/callback",
        cookie_secure=False,
    )
    assert settings.frontend_url == "https://shelfit.onrender.com"
    assert settings.cors_origins == "https://shelfit.onrender.com"
    assert (
        settings.google_redirect_uri
        == "https://shelfit.onrender.com/api/auth/google/callback"
    )
    assert settings.cookie_secure is True


def test_spa_serves_index_and_real_files(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>shelf</html>")
    (tmp_path / "icon-192.png").write_bytes(b"png")
    from app.core import config

    monkeypatch.setattr(config.settings, "static_dir", str(tmp_path))
    app = FastAPI()
    mount_frontend(app)
    client = TestClient(app)
    assert "shelf" in client.get("/").text
    assert "shelf" in client.get("/inventory").text
    assert client.get("/icon-192.png").content == b"png"
    assert client.get("/api/inventory/reminders/").status_code == 404


def test_missing_dist_does_not_register_a_catch_all(monkeypatch, tmp_path):
    from app.core import config

    monkeypatch.setattr(config.settings, "static_dir", str(tmp_path))
    app = FastAPI()
    mount_frontend(app)
    assert TestClient(app).get("/nope").status_code == 404
