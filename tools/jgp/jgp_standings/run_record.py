"""Resolve and replay the rating-list date, season year and tournament ids.

Matching tournament ids allow replay of a stored run input. Otherwise the input
is resolved from supplied tournament metadata and `today`, then persisted when
the run commits. The recorded year determines the klass bands.

Default resolution rejects stale, mixed-year and future configurations.
`override_year` allows another season only when every supplied tournament
starts in that year.
"""
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .tournament_calendar import assess_calendar, collect_tournament_dates, latest_ended_end_date


@dataclass(frozen=True)
class RunInput:
    rating_list_date: date
    effective_year: int
    tournament_ids: Tuple[int, ...]


def resolve_run_input(
    tournament_ids: List[int],
    fetch_tournament_data: Callable[[int], dict],
    today: date,
    override_year: Optional[int] = None,
) -> RunInput:
    """Derive a fresh run input. `today` is the only clock read anywhere on this path."""
    dates = collect_tournament_dates(tournament_ids, fetch_tournament_data)

    rating_list_date = latest_ended_end_date(dates, today)
    if rating_list_date is None:
        raise ValueError("no configured tournament has ended yet; there is no rating-list date to request")

    assessment = assess_calendar(dates, today)
    if override_year is None:
        if assessment.status != "current":
            raise ValueError(
                f"configured tournaments are {assessment.status} for year(s) {list(assessment.years)}; "
                "supply an explicit --year together with --tournaments to override"
            )
        effective_year = today.year
    else:
        if assessment.years != (override_year,):
            raise ValueError(
                f"override year {override_year} does not match the supplied tournaments' "
                f"start year(s) {list(assessment.years)}"
            )
        effective_year = override_year

    return RunInput(rating_list_date, effective_year, tuple(tournament_ids))


def write_run_record(run_input: RunInput, path: Path) -> None:
    payload = {
        "rating_list_date": run_input.rating_list_date.isoformat(),
        "effective_year": run_input.effective_year,
        "tournament_ids": list(run_input.tournament_ids),
    }
    path.write_text(json.dumps(payload, indent=2))


def read_run_record(path: Path) -> RunInput:
    payload = json.loads(path.read_text())
    return RunInput(
        rating_list_date=date.fromisoformat(payload["rating_list_date"]),
        effective_year=payload["effective_year"],
        tournament_ids=tuple(payload["tournament_ids"]),
    )


def load_or_resolve_run_input(
    tournament_ids: List[int],
    fetch_tournament_data: Callable[[int], dict],
    today: date,
    path: Path,
    override_year: Optional[int] = None,
) -> RunInput:
    """Replay a record with matching tournament ids, or resolve fresh inputs.

    The caller persists the result when publication is ready to commit.
    """
    if path.exists():
        recorded = read_run_record(path)
        if recorded.tournament_ids == tuple(tournament_ids):
            return recorded
    return resolve_run_input(tournament_ids, fetch_tournament_data, today, override_year)
