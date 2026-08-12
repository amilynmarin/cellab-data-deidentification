from __future__ import annotations

import calendar as calendar_module
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Iterable
from zoneinfo import ZoneInfo

from .errors import DataValidationError
from .models import HolidayRule, parse_boundary


def behavioral_date(value: datetime, boundary: str | time) -> date:
    cutoff = parse_boundary(boundary) if isinstance(boundary, str) else boundary
    result = value.date()
    if value.time().replace(tzinfo=None) < cutoff:
        result -= timedelta(days=1)
    return result


def season_for_date(value: date) -> str:
    if value.month in {12, 1, 2}:
        return "winter"
    if value.month in {3, 4, 5}:
        return "spring"
    if value.month in {6, 7, 8}:
        return "summer"
    return "autumn"


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last_day = calendar_module.monthrange(year, month)[1]
    last = date(year, month, last_day)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(value: date) -> date:
    if value.weekday() == calendar_module.SATURDAY:
        return value - timedelta(days=1)
    if value.weekday() == calendar_module.SUNDAY:
        return value + timedelta(days=1)
    return value


@lru_cache(maxsize=256)
def us_federal_observed_holidays(year: int) -> tuple[tuple[date, str], ...]:
    holidays: list[tuple[date, str]] = [
        (_observed(date(year, 1, 1)), "New Year's Day"),
        (_nth_weekday(year, 1, calendar_module.MONDAY, 3), "Birthday of Martin Luther King, Jr."),
        (_nth_weekday(year, 2, calendar_module.MONDAY, 3), "Washington's Birthday"),
        (_last_weekday(year, 5, calendar_module.MONDAY), "Memorial Day"),
        (_observed(date(year, 7, 4)), "Independence Day"),
        (_nth_weekday(year, 9, calendar_module.MONDAY, 1), "Labor Day"),
        (_nth_weekday(year, 10, calendar_module.MONDAY, 2), "Columbus Day"),
        (_observed(date(year, 11, 11)), "Veterans Day"),
        (_nth_weekday(year, 11, calendar_module.THURSDAY, 4), "Thanksgiving Day"),
        (_observed(date(year, 12, 25)), "Christmas Day"),
    ]
    if year >= 2021:
        holidays.append((_observed(date(year, 6, 19)), "Juneteenth National Independence Day"))
    # Include an observed New Year's Day that falls in the preceding calendar year.
    next_new_year = _observed(date(year + 1, 1, 1))
    if next_new_year.year == year:
        holidays.append((next_new_year, "New Year's Day"))
    return tuple(sorted(set(holidays)))


def holiday_dates(rule: HolidayRule, years: Iterable[int]) -> tuple[date, ...]:
    selected: set[date] = set()
    year_set = set(years)
    if rule.mode == "us_federal":
        for year in range(min(year_set) - 1, max(year_set) + 2) if year_set else []:
            selected.update(day for day, _ in us_federal_observed_holidays(year))
    else:
        try:
            selected.update(date.fromisoformat(value) for value in rule.dates)
        except ValueError as exc:
            raise DataValidationError("The explicit holiday list contains an invalid ISO date.") from exc
    try:
        selected.update(date.fromisoformat(value) for value in rule.additional_dates)
        selected.difference_update(date.fromisoformat(value) for value in rule.excluded_dates)
    except ValueError as exc:
        raise DataValidationError("A holiday inclusion or exclusion contains an invalid ISO date.") from exc
    return tuple(sorted(selected))


def nearest_holiday_distance(value: date, holidays: Iterable[date], window_days: int = 7) -> int | None:
    candidates = [(abs((value - holiday).days), holiday, (value - holiday).days) for holiday in holidays]
    if not candidates:
        return None
    absolute, _, signed = min(candidates, key=lambda item: (item[0], item[1]))
    return signed if absolute <= window_days else None


@lru_cache(maxsize=1024)
def _dst_transition_dates(timezone_name: str, year: int) -> tuple[date, ...]:
    zone = ZoneInfo(timezone_name)
    start = date(year - 1, 12, 31)
    end = date(year + 1, 1, 2)
    transitions: list[date] = []
    previous_day = start
    previous = datetime.combine(previous_day, time(12, 0), tzinfo=zone).dst() or timedelta(0)
    current_day = start + timedelta(days=1)
    while current_day <= end:
        current = datetime.combine(current_day, time(12, 0), tzinfo=zone).dst() or timedelta(0)
        if current != previous:
            transitions.append(current_day)
        previous = current
        previous_day = current_day
        current_day += timedelta(days=1)
    return tuple(transitions)


@lru_cache(maxsize=1024)
def timezone_uses_dst(timezone_name: str, year: int) -> bool:
    zone = ZoneInfo(timezone_name)
    for month in range(1, 13):
        sample = datetime(year, month, 15, 12, 0, tzinfo=zone)
        if (sample.dst() or timedelta(0)) != timedelta(0):
            return True
    return bool(_dst_transition_dates(timezone_name, year))


def dst_status(local_aware: datetime | None, timezone_name: str | None) -> str | None:
    if local_aware is None or timezone_name is None:
        return None
    if not timezone_uses_dst(timezone_name, local_aware.year):
        return "not_applicable"
    return "daylight" if (local_aware.dst() or timedelta(0)) != timedelta(0) else "standard"


def nearest_dst_transition_distance(
    local_aware: datetime | None,
    timezone_name: str | None,
    window_days: int = 14,
) -> int | None:
    if local_aware is None or timezone_name is None:
        return None
    transitions: set[date] = set()
    for year in (local_aware.year - 1, local_aware.year, local_aware.year + 1):
        transitions.update(_dst_transition_dates(timezone_name, year))
    if not transitions:
        return None
    candidates = [
        (abs((local_aware.date() - transition).days), transition, (local_aware.date() - transition).days)
        for transition in transitions
    ]
    absolute, _, signed = min(candidates, key=lambda item: (item[0], item[1]))
    return signed if absolute <= window_days else None
