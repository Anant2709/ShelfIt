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
    shelf_life_api_url: str = "https://api.spoonacular.com/food/ingredients/search"
    shelf_life_api_key: str | None = None
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
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


settings = Settings()
DATA_DIR.mkdir(parents=True, exist_ok=True)
