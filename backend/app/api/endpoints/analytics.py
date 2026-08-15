from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.schemas.analytics import (
    CategoryBreakdownOut,
    NameBreakdownOut,
    OutcomeTotalsOut,
    WasteReportOut,
)
from app.services.disposition import waste_report

router = APIRouter()


@router.get("/waste", response_model=WasteReportOut)
def get_waste_report(days: int = 30, db: Session = Depends(get_db)):
    """How much left the shelf as waste versus use, over a trailing window."""
    if days < 1:
        raise HTTPException(status_code=422, detail="days must be at least 1")
    report = waste_report(db, window_days=days)
    return WasteReportOut(
        window_days=report.window_days,
        consumed=OutcomeTotalsOut(
            events=report.consumed.events, items=report.consumed.items
        ),
        wasted=OutcomeTotalsOut(
            events=report.wasted.events, items=report.wasted.items
        ),
        waste_rate=report.waste_rate,
        wasted_after_expiry=report.wasted_after_expiry,
        wasted_before_expiry=report.wasted_before_expiry,
        wasted_undated=report.wasted_undated,
        by_name=[
            NameBreakdownOut(
                name=row.name,
                events=row.events,
                quantity=row.quantity,
                unit=row.unit,
            )
            for row in report.by_name
        ],
        by_category=[
            CategoryBreakdownOut(
                category=row.category, events=row.events, items=row.items
            )
            for row in report.by_category
        ],
    )
