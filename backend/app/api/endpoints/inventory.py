from datetime import date, datetime, timedelta
import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.deps import get_db
from app.models.inventory import Expiration, InventoryItem
from app.schemas.inventory import (
    ExpirationCreate,
    ExpirationOut,
    InventoryLabelRequest,
    InventoryItemCreate,
    InventoryItemOut,
    InventoryScanResponse,
    InventoryItemUpdate,
)
from app.services.classifier import classify_image
from app.services.shelf_life import lookup_shelf_life_days

router = APIRouter()


@router.post("/", response_model=InventoryItemOut)
def create_item(payload: InventoryItemCreate, db: Session = Depends(get_db)):
    item = InventoryItem(
        name=payload.name,
        category=payload.category,
        quantity=payload.quantity,
        unit=payload.unit,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    _ensure_expiration(item, payload.expiration_date, db)
    db.refresh(item)
    return item


@router.get("/", response_model=list[InventoryItemOut])
def list_items(db: Session = Depends(get_db)):
    return db.query(InventoryItem).all()


@router.get("/reminders")
def reminders(days: int = 7, db: Session = Depends(get_db)):
    cutoff = date.today() + timedelta(days=days)
    results = (
        db.query(InventoryItem, Expiration)
        .join(Expiration, InventoryItem.id == Expiration.item_id)
        .filter(Expiration.expiration_date <= cutoff)
        .all()
    )
    items = []
    for item, exp in results:
        items.append(
            {
                "id": item.id,
                "name": item.name,
                "quantity": item.quantity,
                "expiration_date": exp.expiration_date,
                "source": exp.source,
            }
        )
    return {"items": items}


@router.get("/{item_id}", response_model=InventoryItemOut)
def get_item(item_id: str, db: Session = Depends(get_db)):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.patch("/{item_id}", response_model=InventoryItemOut)
def update_item(item_id: str, payload: InventoryItemUpdate, db: Session = Depends(get_db)):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{item_id}")
def delete_item(item_id: str, db: Session = Depends(get_db)):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(item)
    db.commit()
    return {"status": "deleted"}


@router.post("/scan", response_model=InventoryScanResponse)
def scan_item(
    expiration_date: date | None = Form(None),
    quantity: float = Form(1.0),
    unit: str = Form("count"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / f"{datetime.utcnow().timestamp()}_{file.filename}"
    with file_path.open("wb") as handle:
        handle.write(file.file.read())

    label, confidence = classify_image(file_path)

    if confidence < settings.model_confidence_threshold or label == "unknown":
        return InventoryScanResponse(
            status="needs_label",
            image_id=file_path.name,
            suggested_label=label,
            confidence=confidence,
        )

    item = InventoryItem(
        name=label,
        category=None,
        quantity=quantity,
        unit=unit,
        image_uri=str(file_path),
        confidence=confidence,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    _ensure_expiration(item, expiration_date, db)
    db.refresh(item)
    return InventoryScanResponse(status="created", item=item)


@router.post("/{item_id}/image", response_model=InventoryItemOut)
def upload_image(
    item_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / f"{datetime.utcnow().timestamp()}_{file.filename}"
    with file_path.open("wb") as handle:
        handle.write(file.file.read())

    label, confidence = classify_image(file_path)
    item.image_uri = str(file_path)
    item.confidence = confidence
    if item.name == "unknown":
        item.name = label
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.post("/label", response_model=InventoryItemOut)
def label_item(payload: InventoryLabelRequest, db: Session = Depends(get_db)):
    upload_dir = Path(settings.upload_dir)
    image_id = Path(payload.image_id).name
    file_path = upload_dir / image_id
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")

    item = InventoryItem(
        name=payload.label,
        category=None,
        quantity=payload.quantity,
        unit=payload.unit,
        image_uri=str(file_path),
        confidence=None,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    _ensure_expiration(item, payload.expiration_date, db)
    db.refresh(item)

    safe_label = "".join(
        char if char.isalnum() or char in ("-", "_") else "_" for char in payload.label
    ).lower()
    training_root = Path(settings.upload_dir).resolve().parent / "training" / "labels"
    training_dir = training_root / safe_label
    training_dir.mkdir(parents=True, exist_ok=True)
    target_path = training_dir / image_id
    shutil.copyfile(file_path, target_path)

    manifest_path = training_root.parent / "manifest.jsonl"
    record = {
        "image_id": image_id,
        "label": payload.label,
        "source_path": str(file_path),
        "training_path": str(target_path),
        "created_at": datetime.utcnow().isoformat(),
    }
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")

    return item


@router.post("/{item_id}/expiration", response_model=ExpirationOut)
def set_expiration(
    item_id: str, payload: ExpirationCreate, db: Session = Depends(get_db)
):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    expiration = Expiration(
        item_id=item_id, expiration_date=payload.expiration_date, source="user"
    )
    db.merge(expiration)
    db.commit()
    db.refresh(expiration)
    return expiration


def _ensure_expiration(
    item: InventoryItem, expiration_date: date | None, db: Session
) -> None:
    if expiration_date:
        expiration = Expiration(
            item_id=item.id,
            expiration_date=expiration_date,
            source="user",
            shelf_life_days=None,
        )
        db.add(expiration)
        db.commit()
        return

    shelf_life_days, source = lookup_shelf_life_days(item.name)
    expiration_date = None
    if shelf_life_days:
        expiration_date = date.today() + timedelta(days=shelf_life_days)
    expiration = Expiration(
        item_id=item.id,
        expiration_date=expiration_date,
        source=source,
        shelf_life_days=shelf_life_days,
    )
    db.add(expiration)
    db.commit()
