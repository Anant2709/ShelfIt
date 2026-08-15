from datetime import date, timedelta
import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.clock import epoch_seconds, utcnow
from app.core.config import settings
from app.db.deps import get_db
from app.models.inventory import Disposition, Expiration, InventoryItem
from app.schemas.inventory import (
    DispositionCreate,
    DispositionOut,
    DispositionResult,
    ExpirationCreate,
    ExpirationOut,
    InventoryLabelRequest,
    InventoryItemCreate,
    InventoryItemOut,
    InventoryScanResponse,
    InventoryItemUpdate,
    ReminderEntry,
    RemindersResponse,
    ScanCandidate,
)
from app.services.disposition import (
    AlreadyResolvedError,
    DispositionError,
    ExcessQuantityError,
    apply_disposition,
)
from app.services.classifier import (
    UNKNOWN_LABEL,
    Detection,
    classify_image,
    detect_items,
)
from app.services.shelf_life import lookup_shelf_life_days
from app.services.urgency import URGENCY_ORDER, classify, days_until, is_actionable

router = APIRouter()


def _persist_upload(file: UploadFile) -> Path:
    """Save an uploaded image and return where it landed.

    The stored name is prefixed with a timestamp so two uploads of the same
    filename cannot overwrite each other, and reduced to its basename because a
    client-supplied filename may contain path separators.
    """
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "upload").name
    file_path = upload_dir / f"{epoch_seconds()}_{safe_name}"
    with file_path.open("wb") as handle:
        handle.write(file.file.read())
    return file_path


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
def list_items(
    include_resolved: bool = False,
    db: Session = Depends(get_db),
):
    query = db.query(InventoryItem)
    if not include_resolved:
        query = query.filter(InventoryItem.resolved_at.is_(None))
    return query.all()


@router.get("/reminders", response_model=RemindersResponse)
def reminders(
    days: int = 7,
    include_expired: bool = True,
    db: Session = Depends(get_db),
):
    """Items needing attention within the window.

    Previously this returned everything at or before the cutoff with no lower
    bound and no labelling, so an item that expired six months ago was presented
    identically to one expiring tomorrow. Each entry now carries its urgency and
    day count, and already-expired items can be excluded outright.
    """
    today = date.today()
    cutoff = today + timedelta(days=days)

    query = (
        db.query(InventoryItem, Expiration)
        .join(Expiration, InventoryItem.id == Expiration.item_id)
        .filter(InventoryItem.resolved_at.is_(None))
        .filter(Expiration.expiration_date.isnot(None))
        .filter(Expiration.expiration_date <= cutoff)
    )
    if not include_expired:
        query = query.filter(Expiration.expiration_date >= today)

    entries = [
        ReminderEntry(
            id=item.id,
            name=item.name,
            quantity=item.quantity,
            unit=item.unit,
            expiration_date=expiration.expiration_date,
            source=expiration.source,
            days_remaining=days_until(expiration.expiration_date, today),
            urgency=classify(expiration.expiration_date, today),
        )
        for item, expiration in query.all()
    ]
    # Most urgent first, so a client can render the list without re-sorting.
    entries.sort(key=lambda entry: entry.days_remaining)

    counts = {bucket.value: 0 for bucket in URGENCY_ORDER}
    for entry in entries:
        counts[entry.urgency.value] += 1

    # Counted separately because these items have no date to rank or filter on.
    undated = (
        db.query(InventoryItem)
        .outerjoin(Expiration, InventoryItem.id == Expiration.item_id)
        .filter(InventoryItem.resolved_at.is_(None))
        .filter(
            (Expiration.item_id.is_(None)) | (Expiration.expiration_date.is_(None))
        )
        .count()
    )

    return RemindersResponse(
        items=entries,
        counts=counts,
        action_required=sum(
            1 for entry in entries if is_actionable(entry.urgency)
        ),
        needs_expiry_date=undated,
    )


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
    if item.resolved_at is not None:
        raise HTTPException(
            status_code=409, detail="Resolved items cannot be edited"
        )
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


@router.post("/{item_id}/dispositions", response_model=DispositionResult)
def record_disposition(
    item_id: str,
    payload: DispositionCreate,
    db: Session = Depends(get_db),
):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    try:
        event = apply_disposition(
            db,
            item,
            outcome=payload.outcome,
            quantity=payload.quantity,
            reason=payload.reason,
        )
    except AlreadyResolvedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ExcessQuantityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DispositionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    db.refresh(item)
    db.refresh(event)
    return DispositionResult(disposition=event, item=item)


@router.get("/{item_id}/dispositions", response_model=list[DispositionOut])
def list_dispositions(item_id: str, db: Session = Depends(get_db)):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return (
        db.query(Disposition)
        .filter(Disposition.item_id == item_id)
        .order_by(Disposition.occurred_at.asc())
        .all()
    )


@router.post("/scan", response_model=InventoryScanResponse)
def scan_item(
    expiration_date: date | None = Form(None),
    quantity: float = Form(1.0),
    unit: str = Form("count"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    file_path = _persist_upload(file)
    detections = detect_items(file_path)

    # Split on the confidence gate. Anything the model is sure about is added
    # directly; anything else is handed back for the user to confirm, so low
    # confidence can never quietly corrupt the inventory. Partitioned in a single
    # pass rather than by membership test, because two identical detections would
    # otherwise both be treated as the same entry.
    confident: list[Detection] = []
    uncertain: list[Detection] = []
    for detection in detections:
        is_usable = (
            detection.confidence >= settings.model_confidence_threshold
            and detection.label != UNKNOWN_LABEL
        )
        (confident if is_usable else uncertain).append(detection)

    created: list[InventoryItem] = []
    for detection in confident:
        item = InventoryItem(
            name=detection.label,
            category=None,
            quantity=quantity,
            unit=unit,
            image_uri=str(file_path),
            confidence=detection.confidence,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        _ensure_expiration(item, expiration_date, db)
        db.refresh(item)
        created.append(item)

    candidates = [
        ScanCandidate(
            label=detection.label,
            confidence=detection.confidence,
            box=list(detection.box) if detection.box else None,
        )
        for detection in uncertain
    ]

    if created:
        status = "created"
    elif candidates:
        status = "needs_label"
    else:
        status = "empty"

    return InventoryScanResponse(
        status=status,
        image_id=file_path.name,
        created_items=created,
        candidates=candidates,
        item=created[0] if created else None,
        suggested_label=candidates[0].label if candidates else UNKNOWN_LABEL,
        confidence=candidates[0].confidence if candidates else 0.0,
    )


@router.post("/{item_id}/image", response_model=InventoryItemOut)
def upload_image(
    item_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    file_path = _persist_upload(file)

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
        "created_at": utcnow().isoformat(),
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
    if item.resolved_at is not None:
        raise HTTPException(
            status_code=409, detail="Resolved items cannot be edited"
        )
    # merge() returns the session-managed instance; the transient one passed in
    # stays detached and cannot be refreshed.
    expiration = db.merge(
        Expiration(
            item_id=item_id,
            expiration_date=payload.expiration_date,
            source="user",
            # An explicit date supersedes any previously inferred shelf life, so
            # the row does not claim both a user source and an inferred duration.
            shelf_life_days=None,
        )
    )
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
