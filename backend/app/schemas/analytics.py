from pydantic import BaseModel, Field


class OutcomeTotalsOut(BaseModel):
    events: int
    items: int


class NameBreakdownOut(BaseModel):
    name: str
    events: int
    # Null when the same name was wasted in more than one unit, because those
    # quantities cannot be added without inventing a conversion.
    quantity: float | None = None
    unit: str | None = None


class WasteReportOut(BaseModel):
    """Waste over a trailing window, counted in events rather than money.

    There are no prices in the data, so there is no rupee total. `waste_rate`
    is wasted events over (wasted + consumed) events; quantities of different
    units are never summed.
    """

    window_days: int
    consumed: OutcomeTotalsOut
    wasted: OutcomeTotalsOut
    waste_rate: float
    wasted_after_expiry: int
    wasted_before_expiry: int
    wasted_undated: int
    by_name: list[NameBreakdownOut] = Field(default_factory=list)
