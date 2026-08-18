from datetime import date, timedelta
import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.core import clock
from app.core.clock import epoch_seconds, utcnow
from app.core.config import settings
from app.api.deps import get_current_user
from app.db.deps import get_db
from app.models.inventory import Disposition, Expiration, InventoryItem
from app.models.user import User
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
    ItemSort,
    ReminderEntry,
    RemindersResponse,
    ScanCandidate,
    SortDirection,
)
from app.services.category import Category
from app.services.inventory import (
    assign_user_category,
    ensure_expiration,
    infer_category,
)
from app.services.inventory import create_item as create_inventory_item
from app.services.nutrition import lookup_nutrition
from app.services.packaged_label import read_packaged_label
from app.services.disposition import (
    AlreadyResolvedError,
    DispositionError,
    ExcessQuantityError,
    apply_disposition,
    revert_disposition,
)
from app.services.classifier import (
    UNKNOWN_LABEL,
    Detection,
    classify_image,
    collapse_duplicate_labels,
    detect_items,
)
from app.services.urgency import (
    URGENCY_ORDER,
    Urgency,
    bucket_bounds,
    classify,
    days_until,
    is_actionable,
)

router = APIRouter()


def _owned_item(db: Session, item_id: str, user: User) -> InventoryItem:
    """The item, or 404. Another user's item is 404, not 403.

    A 403 would confirm the id exists. Guessing ids should not be a way to map
    someone else's fridge.
    """
    item = db.get(InventoryItem, item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


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
def create_item(
    payload: InventoryItemCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return create_inventory_item(
        db,
        name=payload.name,
        quantity=payload.quantity,
        unit=payload.unit,
        expiration_date=payload.expiration_date,
        category=payload.category,
        user_id=user.id,
    )


def _urgency_predicate(urgency: Urgency, today: date):
    """One urgency bucket expressed as a condition on the expiration row.

    The bounds come from `bucket_bounds` rather than being written again here, so
    the filter cannot disagree with the label the same item is shown with.
    """
    bounds = bucket_bounds(urgency, today)
    if bounds is None:
        # UNKNOWN covers both "no expiration row" and "a row with no date".
        return or_(
            Expiration.item_id.is_(None), Expiration.expiration_date.is_(None)
        )
    low, high = bounds
    conditions = [Expiration.expiration_date.isnot(None)]
    if low is not None:
        conditions.append(Expiration.expiration_date >= low)
    if high is not None:
        conditions.append(Expiration.expiration_date <= high)
    return and_(*conditions)


def _apply_sort(query, sort: ItemSort, direction: SortDirection):
    """Order the list, keeping undated and uncategorised items at the end.

    Direction applies to the value being sorted, not to whether a value exists.
    An item with no expiry date is not the most urgent or the least urgent, it is
    unknown, so it belongs last either way; the same holds for a missing
    category. Sorting a gap as though it were a value is how "unknown" ends up
    presented as "fine".
    """
    if sort is ItemSort.NAME:
        primary = func.lower(InventoryItem.name)
        nulls_last = None
    elif sort is ItemSort.CREATED:
        primary = InventoryItem.created_at
        nulls_last = None
    elif sort is ItemSort.QUANTITY:
        primary = InventoryItem.quantity
        nulls_last = None
    elif sort is ItemSort.CATEGORY:
        primary = InventoryItem.category
        nulls_last = InventoryItem.category.is_(None)
    else:
        primary = Expiration.expiration_date
        nulls_last = Expiration.expiration_date.is_(None)

    ordered = primary.desc() if direction is SortDirection.DESC else primary.asc()
    keys = [ordered] if nulls_last is None else [nulls_last, ordered]
    # Final tiebreaker so equal keys produce a stable order across requests.
    return query.order_by(*keys, InventoryItem.id)


@router.get("/", response_model=list[InventoryItemOut])
def list_items(
    search: str | None = None,
    category: list[Category] | None = Query(default=None),
    urgency: list[Urgency] | None = Query(default=None),
    sort: ItemSort = ItemSort.URGENCY,
    direction: SortDirection = SortDirection.ASC,
    include_resolved: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The current shelf, filtered and ordered.

    Filtering and ordering happen in the database rather than in the client, so
    every client agrees and the rules stay testable without a browser. The
    outer join is what lets an item with no expiration row still be returned,
    matched by the `unknown` urgency filter, and sorted to the end.
    """
    query = db.query(InventoryItem).outerjoin(
        Expiration, InventoryItem.id == Expiration.item_id
    ).filter(InventoryItem.user_id == user.id)

    if not include_resolved:
        query = query.filter(InventoryItem.resolved_at.is_(None))

    if search:
        # Case-insensitive substring. Escaped so a name containing % or _ is
        # matched literally instead of behaving as a wildcard.
        pattern = (
            search.strip().lower().replace("!", "!!").replace("%", "!%").replace("_", "!_")
        )
        query = query.filter(
            func.lower(InventoryItem.name).like(f"%{pattern}%", escape="!")
        )

    if category:
        requested = set(category)
        conditions = []
        if Category.UNKNOWN in requested:
            conditions.append(InventoryItem.category.is_(None))
            requested.discard(Category.UNKNOWN)
        if requested:
            conditions.append(
                InventoryItem.category.in_([item.value for item in requested])
            )
        query = query.filter(or_(*conditions))

    if urgency:
        today = clock.today(user.timezone)
        query = query.filter(
            or_(*[_urgency_predicate(bucket, today) for bucket in set(urgency)])
        )

    return _apply_sort(query, sort, direction).all()


@router.get("/reminders", response_model=RemindersResponse)
def reminders(
    days: int = 7,
    include_expired: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Items needing attention within the window.

    Previously this returned everything at or before the cutoff with no lower
    bound and no labelling, so an item that expired six months ago was presented
    identically to one expiring tomorrow. Each entry now carries its urgency and
    day count, and already-expired items can be excluded outright.
    """
    today = clock.today(user.timezone)
    cutoff = today + timedelta(days=days)

    query = (
        db.query(InventoryItem, Expiration)
        .join(Expiration, InventoryItem.id == Expiration.item_id)
        .filter(InventoryItem.user_id == user.id)
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
            category=item.category,
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
        .filter(InventoryItem.user_id == user.id)
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
def get_item(
    item_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _owned_item(db, item_id, user)


@router.patch("/{item_id}", response_model=InventoryItemOut)
def update_item(
    item_id: str,
    payload: InventoryItemUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = _owned_item(db, item_id, user)
    if item.resolved_at is not None:
        raise HTTPException(
            status_code=409, detail="Resolved items cannot be edited"
        )

    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if field != "category":
            setattr(item, field, value)

    if "category" in changes:
        assign_user_category(item, changes["category"])
    elif "name" in changes and item.category_source != "user":
        # A rename changes what the item *is*, so an inferred category is now
        # about the old name. A user-stated one is left alone: renaming is not
        # a licence to overwrite an answer the user gave.
        infer_category(item)

    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{item_id}")
def delete_item(
    item_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = _owned_item(db, item_id, user)
    db.delete(item)
    db.commit()
    return {"status": "deleted"}


@router.post("/{item_id}/dispositions", response_model=DispositionResult)
def record_disposition(
    item_id: str,
    payload: DispositionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = _owned_item(db, item_id, user)
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
def list_dispositions(
    item_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owned_item(db, item_id, user)
    return (
        db.query(Disposition)
        .filter(Disposition.item_id == item_id)
        .order_by(Disposition.occurred_at.asc())
        .all()
    )


@router.delete(
    "/{item_id}/dispositions/{disposition_id}", response_model=InventoryItemOut
)
def undo_disposition(
    item_id: str,
    disposition_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Undo a recorded outcome, returning the item as it now stands.

    The assistant can record outcomes on its own reading of a sentence, so this is
    what makes those writes safe: anything it does, a person can put back.
    """
    _owned_item(db, item_id, user)
    event = db.get(Disposition, disposition_id)
    if event is None or event.item_id != item_id:
        raise HTTPException(status_code=404, detail="Disposition not found")

    try:
        item = revert_disposition(db, event)
    except DispositionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    db.commit()
    db.refresh(item)
    return item


@router.post("/scan", response_model=InventoryScanResponse)
def scan_item(
    expiration_date: date | None = Form(None),
    quantity: float = Form(1.0),
    unit: str = Form("count"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    file_path = _persist_upload(file)
    detections = collapse_duplicate_labels(detect_items(file_path))

    # Split on the confidence gate. Anything the model is sure about is added
    # directly; anything else is handed back for the user to confirm, so low
    # confidence can never quietly corrupt the inventory. Identical names from
    # one photo are already collapsed to a single row.
    confident: list[Detection] = []
    uncertain: list[Detection] = []
    for detection in detections:
        is_usable = (
            detection.confidence >= settings.model_confidence_threshold
            and detection.label != UNKNOWN_LABEL
        )
        (confident if is_usable else uncertain).append(detection)

    created: list[InventoryItem] = []
    packaged = read_packaged_label(file_path) if confident else None
    nutrition = None
    if packaged is not None and len(confident) == 1:
        nutrition = lookup_nutrition(
            brand=packaged.brand, product_name=packaged.product_name
        )
    printed_use_by = (
        packaged.use_by if packaged is not None and len(confident) == 1 else None
    )

    for detection in confident:
        item = InventoryItem(
            name=detection.label,
            quantity=quantity,
            unit=unit,
            image_uri=str(file_path),
            confidence=detection.confidence,
            user_id=user.id,
            nutrition_source="none",
        )
        if packaged is not None and len(confident) == 1:
            item.brand = (nutrition.brand if nutrition else None) or packaged.brand
            item.product_name = (
                (nutrition.product_name if nutrition else None) or packaged.product_name
            )
            if nutrition is not None:
                item.calories_kcal = nutrition.calories_kcal
                item.protein_g = nutrition.protein_g
                item.carbs_g = nutrition.carbs_g
                item.fat_g = nutrition.fat_g
                item.nutrition_source = nutrition.source
        infer_category(item)
        db.add(item)
        db.commit()
        db.refresh(item)
        ensure_expiration(
            db,
            item,
            expiration_date or printed_use_by,
            date_source="user" if expiration_date else ("label" if printed_use_by else None),
        )
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
    item_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = _owned_item(db, item_id, user)

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
def label_item(
    payload: InventoryLabelRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    upload_dir = Path(settings.upload_dir)
    image_id = Path(payload.image_id).name
    file_path = upload_dir / image_id
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")

    item = InventoryItem(
        name=payload.label,
        quantity=payload.quantity,
        unit=payload.unit,
        image_uri=str(file_path),
        confidence=None,
        user_id=user.id,
    )
    infer_category(item)
    db.add(item)
    db.commit()
    db.refresh(item)

    ensure_expiration(db, item, payload.expiration_date)
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
    item_id: str,
    payload: ExpirationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = _owned_item(db, item_id, user)
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


