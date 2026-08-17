from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
BASE_DIR = BACKEND_DIR.parent
DATA_DIR = BASE_DIR / "data"


class Settings(BaseSettings):
    # Every path below is absolute and anchored to the repo, so the app behaves
    # identically no matter which directory the server is launched from.
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        extra="ignore",
        protected_namespaces=(),
    )

    api_prefix: str = "/api"
    database_url: str = f"sqlite:///{DATA_DIR / 'shelfit.db'}"
    upload_dir: str = str(DATA_DIR / "uploads")
    shelf_life_path: str = str(DATA_DIR / "shelf_life.json")
    categories_path: str = str(DATA_DIR / "categories.json")
    recipes_path: str = str(DATA_DIR / "recipes.json")
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    # Prompt cost grows with every stored turn, so old history is dropped rather
    # than letting a long-running conversation get steadily more expensive.
    chat_history_messages: int = 20
    # Comma-separated. Cookies cannot be sent to `*` origins, so this has to be
    # an explicit list the moment login exists.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    cookie_secure: bool = False
    session_days: int = 14
    demo_email: str = "juhi@local"
    demo_username: str = "juhi"
    demo_password: str = "shelfit"
    demo_timezone: str = "America/New_York"
    frontend_url: str = "http://localhost:5173"
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str = "http://localhost:8000/api/auth/google/callback"
    model_path: str = str(DATA_DIR / "model.pt")
    model_confidence_threshold: float = 0.7
    # "vision_llm" recognises arbitrary groceries; "yolo" uses local weights and
    # needs requirements-ml.txt; "null" detects nothing.
    classifier_backend: str = "vision_llm"
    vision_model: str = "gpt-4o-mini"
    max_detections_per_image: int = 10
    # "sql" persists across restarts, which matters because `uvicorn --reload`
    # would otherwise discard paid lookup results on every code change.
    cache_backend: str = "sql"
    cache_ttl_days: int = 30
    roboflow_api_key: str | None = None
    roboflow_workspace: str | None = None
    roboflow_project: str | None = None
    roboflow_version: int | None = None
    # Optional Exa key for packaged-product nutrition when Open Food Facts misses.
    exa_api_key: str | None = None


settings = Settings()
DATA_DIR.mkdir(parents=True, exist_ok=True)
