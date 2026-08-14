from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    api_prefix: str = "/api"
    database_url: str = "sqlite:///./shelfit.db"
    upload_dir: str = str(BASE_DIR / "data" / "uploads")
    shelf_life_path: str = str(BASE_DIR / "data" / "shelf_life.json")
    shelf_life_api_url: str = "https://api.spoonacular.com/food/ingredients/search"
    shelf_life_api_key: str | None = None
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    model_path: str = str(BASE_DIR / "data" / "model.pt")
    model_confidence_threshold: float = 0.7
    roboflow_api_key: str | None = None
    roboflow_workspace: str | None = None
    roboflow_project: str | None = None
    roboflow_version: int | None = None

    class Config:
        env_file = ".env"


settings = Settings()
