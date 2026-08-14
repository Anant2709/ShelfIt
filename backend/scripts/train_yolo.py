from __future__ import annotations

from pathlib import Path
import shutil

from roboflow import Roboflow
from ultralytics import YOLO

from app.core.config import settings


def main() -> None:
    if not settings.roboflow_api_key:
        raise RuntimeError("ROBOFLOW_API_KEY is not set in backend/.env")
    if not settings.roboflow_project or not settings.roboflow_version:
        raise RuntimeError("ROBOFLOW_PROJECT/ROBOFLOW_VERSION are not set")

    rf = Roboflow(api_key=settings.roboflow_api_key)
    if settings.roboflow_workspace:
        workspace = rf.workspace(settings.roboflow_workspace)
        project = workspace.project(settings.roboflow_project)
    else:
        project = rf.workspace().project(settings.roboflow_project)

    dataset = project.version(settings.roboflow_version).download("yolov8")

    model = YOLO("yolov8n.pt")
    model.train(data=str(Path(dataset.location) / "data.yaml"), epochs=30, imgsz=640)

    runs_dir = Path("runs/detect/train/weights/best.pt")
    if not runs_dir.exists():
        raise RuntimeError("Training output not found at runs/detect/train/weights/best.pt")

    output_path = Path(settings.model_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(runs_dir, output_path)
    print(f"Saved model weights to {output_path}")


if __name__ == "__main__":
    main()
