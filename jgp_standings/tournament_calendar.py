"""Derive calendar status and rating-list dates from tournament metadata.

Callers supply tournament data and `today`. These functions support run-input
resolution and checks for stale, mixed-year and future configurations.
"""
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, FrozenSet, List, Optional, Tuple
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class TournamentDates:
    tournament_id: int
    start: date
    end: date


@dataclass(frozen=True)
class CalendarAssessment:
    """
    `status` is one of:
      "current" - every tournament starts in `today`'s year.
      "stale"   - every tournament starts in one year, earlier than `today`'s year.
      "future"  - every tournament starts in one year, later than `today`'s year.
      "mixed"   - the configured tournaments do not agree on a start year.

    `years` names every distinct start year seen, sorted, so a refusal message can name the
    offending years without a second pass over the data.
    """
    status: str
    years: Tuple[int, ...]


def _parse_date(value: object, tournament_id: int, field: str) -> date:
    if not isinstance(value, str) or not value:
        raise ValueError(f"tournament {tournament_id} is missing a valid '{field}' date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"tournament {tournament_id} has a malformed '{field}' date: {value!r}")


def tournament_dates(tournament_id: int, data: dict) -> TournamentDates:
    """Extract start/end dates from one tournament's upstream metadata."""
    start = _parse_date(data.get('start'), tournament_id, 'start')
    end = _parse_date(data.get('end'), tournament_id, 'end')
    return TournamentDates(tournament_id, start, end)


def collect_tournament_dates(
    tournament_ids: List[int],
    fetch_tournament_data: Callable[[int], dict],
) -> List[TournamentDates]:
    """Resolve tournament dates through the supplied metadata fetcher."""
    if not tournament_ids:
        raise ValueError("no tournaments configured")
    return [tournament_dates(tid, fetch_tournament_data(tid)) for tid in tournament_ids]


def latest_ended_end_date(dates: List[TournamentDates], today: date) -> Optional[date]:
    """
    The rating-list request date: the latest end date among tournaments that have already ended
    as of `today`. `None` if none have ended yet - callers decide whether that is itself an error.
    """
    ended_dates = [d.end for d in dates if d.end <= today]
    if not ended_dates:
        return None
    return max(ended_dates)


def start_years(dates: List[TournamentDates]) -> FrozenSet[int]:
    return frozenset(d.start.year for d in dates)


def assess_calendar(dates: List[TournamentDates], today: date) -> CalendarAssessment:
    """Classify the configured tournaments' start years against `today`."""
    years = tuple(sorted(start_years(dates)))
    if len(years) > 1:
        return CalendarAssessment("mixed", years)
    (year,) = years
    if year == today.year:
        status = "current"
    elif year < today.year:
        status = "stale"
    else:
        status = "future"
    return CalendarAssessment(status, years)


STOCKHOLM_TIME_ZONE = ZoneInfo("Europe/Stockholm")


def stockholm_today(clock: Callable[[], datetime]) -> date:
    """Return the Stockholm date from a supplied timezone-aware clock."""
    instant = clock()
    if not isinstance(instant, datetime):
        raise TypeError("clock must return a datetime")
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return instant.astimezone(STOCKHOLM_TIME_ZONE).date()
