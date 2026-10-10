import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import calibration as cal  # noqa: E402

EV = [{"date": "2025-09-15", "result": "Pass", "due": "2026-09-15"},
      {"date": "2026-10-20", "result": "Pass", "due": "2027-10-20"}]


def test_valid_before_due():
    assert cal.status_at(EV, "2026-01-01") == cal.VALID


def test_expired_gap_not_fixed_by_later_cal():
    # run on 2026-10-01: old cal expired, the 2026-10-20 cal came later and must not count
    assert cal.status_at(EV, "2026-10-01") == cal.EXPIRED


def test_later_cal_counts_after_its_date():
    assert cal.status_at(EV, "2026-10-21") == cal.VALID


def test_unknown_before_any_cal():
    assert cal.status_at(EV, "2025-01-01") == cal.UNKNOWN


def test_fail_and_conditional():
    ev = [{"date": "2026-01-01", "result": "Fail", "due": "2027-01-01"}]
    assert cal.status_at(ev, "2026-02-01") == cal.FAILED
    ev = [{"date": "2026-01-01", "result": "Conditional", "due": "2027-01-01"}]
    assert cal.status_at(ev, "2026-02-01") == cal.UNKNOWN
    assert cal.status_at(ev, "2026-02-01", conditional_policy=cal.VALID) == cal.VALID


def test_not_required():
    assert cal.status_at([], "2026-01-01", required=False) == cal.NOT_REQUIRED


def test_add_months_clamps():
    import datetime as dt
    assert cal.add_months(dt.date(2026, 1, 31), 1) == dt.date(2026, 2, 28)
