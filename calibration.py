"""Time-aware calibration status. Pure functions so the run bridge can reuse them."""
import calendar
import datetime as dt

VALID, EXPIRED, FAILED, NOT_REQUIRED, UNKNOWN = "Valid", "Expired", "Failed", "Not Required", "Unknown"


def add_months(d, months):
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


def _date(v):
    if v is None or isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])


def status_at(events, at, required=True, conditional_policy=UNKNOWN):
    """Calibration status of one instrument on date `at`.

    events: dicts with "date" (calibration date), "result" (Pass/Fail/Conditional) and "due".
    Only events on or before `at` count, so a calibration done after a run never makes that run
    look calibrated. The latest such event decides. Conditional results map to `conditional_policy`.
    """
    if not required:
        return NOT_REQUIRED
    at = _date(at)
    past = [e for e in events if e.get("date") and _date(e["date"]) <= at]
    if not past:
        return UNKNOWN
    last = max(past, key=lambda e: _date(e["date"]))
    result = (last.get("result") or "").lower()
    if result == "fail":
        return FAILED
    if result == "conditional" and conditional_policy != VALID:
        return conditional_policy
    if result not in ("pass", "conditional"):
        return UNKNOWN
    due = _date(last.get("due"))
    if due is None:
        return UNKNOWN
    return VALID if at <= due else EXPIRED


def latest_event(events):
    dated = [e for e in events if e.get("date")]
    return max(dated, key=lambda e: _date(e["date"])) if dated else None
