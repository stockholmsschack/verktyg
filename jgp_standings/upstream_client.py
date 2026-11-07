"""Fetch and validate member.schack.se responses.

A `Fetch` callable takes `(url, stage)` and returns parsed JSON or raises
`UpstreamError`. Live requests use connect and response timeouts. Network
errors, timeouts, 429 and 5xx responses receive bounded retries with linear
backoff. Other non-2xx responses fail immediately.

`FixtureReplay` serves captured responses. `standalone_fetch` selects fixtures
through JGP_FIXTURES_DIR for the CLI and standalone app. `MemberSystemClient`
validates club lists, tournament searches, tournaments and player searches.
"""
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Optional
from urllib.parse import quote

import requests

from .tournament_exceptions import policy_for
from .klass_naming import KlassNamingProblem, is_excluded_jgp_klass, is_jdm_tournament_name, read_group_klasses

FIXTURES_ENV_VAR = "JGP_FIXTURES_DIR"

Fetch = Callable[[str, str], Any]


class UpstreamError(RuntimeError):
    """Base class for member-system failures, naming the stage that failed."""

    def __init__(self, stage: str, detail: str) -> None:
        self.stage = stage
        super().__init__(f"upstream failure while {stage}: {detail}")


class UpstreamRetryExhausted(UpstreamError):
    """Raised after every permitted attempt failed transiently."""


class UpstreamResponseError(UpstreamError):
    """Raised for a non-retryable HTTP response."""

    def __init__(self, stage: str, detail: str, *, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(stage, detail)


class UpstreamDataError(UpstreamError):
    """Raised when a successful response is not valid JSON."""


class UpstreamClient:
    """HTTP GET with a single timeout and retry policy; the session and sleep are injectable."""

    def __init__(
        self,
        *,
        connect_timeout: float = 3.0,
        response_timeout: float = 15.0,
        max_attempts: int = 3,
        backoff_seconds: float = 0.25,
        session: Optional[requests.Session] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if connect_timeout <= 0 or response_timeout <= 0:
            raise ValueError("timeouts must be greater than zero")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds cannot be negative")

        self.timeout = (connect_timeout, response_timeout)
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.session = session if session is not None else requests.Session()
        self.sleep = sleep

    def get_json(self, url: str, stage: str) -> Any:
        last_failure = "unknown transient error"

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as error:
                last_failure = f"{type(error).__name__}: {error}"
            else:
                status = response.status_code
                if 200 <= status <= 299:
                    return _parse_json(response, stage)
                if status != 429 and not 500 <= status <= 599:
                    raise UpstreamResponseError(stage, f"HTTP {status} (not retried)", status_code=status)
                last_failure = f"HTTP {status}"

            if attempt < self.max_attempts and self.backoff_seconds:
                self.sleep(self.backoff_seconds * attempt)

        raise UpstreamRetryExhausted(stage, f"{last_failure}; exhausted {self.max_attempts} attempts")


def _parse_json(response: requests.Response, stage: str) -> Any:
    try:
        return response.json()
    except ValueError as error:
        raise UpstreamDataError(stage, "response was not valid JSON") from error


class FixtureReplay:
    """A `Fetch` that answers from a captured fixture set's `responses.json`, read on first use."""

    def __init__(self, directory: Path) -> None:
        self.responses_file = Path(directory) / "responses.json"
        self._responses: Optional[dict[str, Any]] = None

    def __call__(self, url: str, stage: str) -> Any:
        if self._responses is None:
            self._responses = json.loads(self.responses_file.read_text())
        if url not in self._responses:
            raise KeyError(f"no fixture captured for {url}; see {self.responses_file}")
        return deepcopy(self._responses[url])


def standalone_fetch() -> Fetch:
    """The fixture set named by JGP_FIXTURES_DIR when that is set, and the live API otherwise."""
    directory = os.environ.get(FIXTURES_ENV_VAR)
    return FixtureReplay(Path(directory)) if directory else UpstreamClient().get_json


# Web client responses are validated after fetching from the live API or fixtures.

BASE_URL = "https://member.schack.se/public/api/v1"
DISTRICT_ID = 5821


@dataclass(frozen=True)
class PlayerInfo:
    """Validated player identity returned by name search."""

    player_id: int
    first_name: str
    last_name: str
    birth_year: int
    current_club_id: int | None = None
    current_club_name: str | None = None
    country: str | None = None
    fide_id: int | None = None


@dataclass(frozen=True)
class CanonicalClub:
    """One verified club from the district's canonical list."""

    club_id: int
    name: str


@dataclass(frozen=True)
class TournamentGroupSearchHit:
    """One matching group returned by tournament-group search."""

    matching_group_id: int
    tournament_id: int
    matching_group_name: str
    tournament_name: str
    latest_updated_game: str | None = None


@dataclass(frozen=True)
class ProcessableTournamentGroup:
    """A group of a tournament's first root class that competes in the JGP standings."""

    group_id: int
    name: str
    klass: str


@dataclass(frozen=True)
class CanonicalTournament:
    """An authoritatively resolved tournament suitable for selection."""

    tournament_id: int
    name: str
    start_date: date
    end_date: date
    processable_groups: tuple[ProcessableTournamentGroup, ...]


@dataclass(frozen=True)
class TournamentResolutionProblem:
    """One tournament identifier that could not be resolved."""

    tournament_id: int
    detail: str
    klass_problems: tuple[KlassNamingProblem, ...] = ()


@dataclass(frozen=True)
class TournamentResolutionResult:
    """Tournaments in start-date order, and any identifier-specific problems."""

    tournaments: tuple[CanonicalTournament, ...]
    problems: tuple[TournamentResolutionProblem, ...] = ()

    def require_all(self) -> tuple[CanonicalTournament, ...]:
        """Return every tournament, or raise naming failures before other refusals."""
        if self.problems:
            klass_problems = tuple(
                klass_problem for problem in self.problems for klass_problem in problem.klass_problems
            )
            if klass_problems:
                raise TournamentKlassNamingError(klass_problems[0].tournament_id, klass_problems)
            problem = self.problems[0]
            raise TournamentResolutionError(problem.tournament_id, problem.detail)
        return self.tournaments


class TournamentResolutionError(RuntimeError):
    """Raised when an id cannot become a verified tournament selection."""

    def __init__(self, tournament_id: int, detail: str) -> None:
        self.tournament_id = tournament_id
        self.detail = detail
        super().__init__(f"tournament {tournament_id} cannot be resolved: {detail}")


class TournamentKlassNamingError(TournamentResolutionError):
    """Every group of a tournament's first root class whose name `KLASS_NAMING.md` refuses."""

    def __init__(self, tournament_id: int, klass_problems: Sequence[KlassNamingProblem]) -> None:
        self.klass_problems = tuple(klass_problems)
        super().__init__(tournament_id, "\n".join(map(str, self.klass_problems)))


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    value: Any


class MemberSystemClient:
    """Validate and cache member-system responses.

    Searches and tournaments are cached for five minutes; club lists for an hour.
    """

    DEFAULT_SHORT_CACHE_TTL = 300.0
    DEFAULT_REFERENCE_CACHE_TTL = 3600.0

    def __init__(
        self,
        *,
        fetch: Fetch,
        clock: Callable[[], float] = time.monotonic,
        short_cache_ttl: float = DEFAULT_SHORT_CACHE_TTL,
        reference_cache_ttl: float = DEFAULT_REFERENCE_CACHE_TTL,
    ) -> None:
        if short_cache_ttl < 0 or reference_cache_ttl < 0:
            raise ValueError("cache TTLs cannot be negative")
        self.fetch = fetch
        self.clock = clock
        self.short_cache_ttl = short_cache_ttl
        self.reference_cache_ttl = reference_cache_ttl
        self._cache: dict[tuple[str, str], _CacheEntry] = {}
        self._cache_lock = threading.RLock()

    def district_clubs(self, district_id: int = DISTRICT_ID) -> list[CanonicalClub]:
        stage = "fetching district club list"

        def validate(data: Any) -> list[CanonicalClub]:
            return [
                CanonicalClub(
                    club_id=_require_positive_int(club["id"], stage, "club id"),
                    name=_require_nonempty_string(club["name"], stage, "club name"),
                )
                for club in _list_of_objects(data, {"id", "name"}, stage, "club")
            ]

        return self._validated_request(
            "district-clubs", f"/organisation/district/clubs/{district_id}", stage,
            self.reference_cache_ttl, validate,
        )

    def tournament_group_search(self, search_word: str) -> list[TournamentGroupSearchHit]:
        stage = "searching tournament groups"

        def validate(data: Any) -> list[TournamentGroupSearchHit]:
            subject = "tournament search result"
            hits = []
            for row in _list_of_objects(data, {"id", "name", "tournamentid", "tournamentname"}, stage, subject):
                latest_updated_game = row.get("latestUpdatedGame")
                if latest_updated_game is not None:
                    latest_updated_game = _require_nonempty_string(
                        latest_updated_game, stage, f"{subject} latestUpdatedGame"
                    )
                hits.append(TournamentGroupSearchHit(
                    matching_group_id=_require_positive_int(row["id"], stage, f"{subject} group id"),
                    tournament_id=_require_positive_int(row["tournamentid"], stage, f"{subject} tournamentid"),
                    matching_group_name=_require_nonempty_string(row["name"], stage, f"{subject} group name"),
                    tournament_name=_require_nonempty_string(
                        row["tournamentname"], stage, f"{subject} tournament name"
                    ),
                    latest_updated_game=latest_updated_game,
                ))
            return hits

        return self._validated_request(
            "tournament-group-search", f"/tournament/group/search/{quote(search_word, safe='')}", stage,
            self.short_cache_ttl, validate,
        )

    def canonical_tournament(self, tournament_id: int) -> CanonicalTournament:
        """
        Resolve and validate one tournament. Its groups are read as the generation reads them: the
        first root class only, every name through `read_group_klasses`, and klass E and above dropped
        unless the tournament is a JDM event. A refused group name refuses the tournament with every
        refused group; a tournament with no competing group is unresolvable.
        """
        if not _is_positive_int(tournament_id):
            raise TournamentResolutionError(tournament_id, "id must be a positive integer")

        stage = f"resolving canonical tournament {tournament_id}"
        try:
            return self._validated_request(
                "canonical-tournament", f"/tournament/tournament/id/{tournament_id}", stage,
                self.short_cache_ttl, lambda data: _canonical_tournament(tournament_id, data, stage),
            )
        except UpstreamResponseError as error:
            if error.status_code == 404:
                raise TournamentResolutionError(tournament_id, "not found") from error
            raise

    def player_name_search(self, first_name: str, last_name: str) -> list[PlayerInfo]:
        stage = "searching players by name"
        first_name = _require_nonempty_string(first_name, stage, "first name")
        last_name = _require_nonempty_string(last_name, stage, "last name")
        return self._validated_request(
            "player-name-search",
            f"/player/fornamn/{quote(first_name, safe='')}/efternamn/{quote(last_name, safe='')}",
            stage,
            self.short_cache_ttl,
            lambda data: [_player_info(value, stage, "player search result") for value in _require_list(data, stage)],
        )

    def _validated_request(
        self, contract: str, path: str, stage: str, ttl: float, validator: Callable[[Any], Any],
    ) -> Any:
        key = (contract, path)
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None:
                if cached.expires_at > self.clock():
                    return deepcopy(cached.value)
                del self._cache[key]

        value = validator(self.fetch(f"{BASE_URL}{path}", stage))
        if ttl > 0:
            entry = _CacheEntry(self.clock() + ttl, deepcopy(value))
            with self._cache_lock:
                self._cache[key] = entry
        return value


def resolve_canonical_tournaments(
    client: MemberSystemClient,
    tournament_ids: Sequence[int],
    *,
    collect_problems: bool = False,
) -> TournamentResolutionResult:
    """Resolve tournaments once and apply the standings order: start date, then tournament id."""
    tournaments: list[CanonicalTournament] = []
    problems: list[TournamentResolutionProblem] = []
    for tournament_id in tournament_ids:
        try:
            tournaments.append(client.canonical_tournament(tournament_id))
        except TournamentResolutionError as error:
            if not collect_problems:
                raise
            problems.append(TournamentResolutionProblem(
                tournament_id=tournament_id,
                detail=error.detail,
                klass_problems=getattr(error, "klass_problems", ()),
            ))
    tournaments.sort(key=lambda tournament: (tournament.start_date, tournament.tournament_id))
    return TournamentResolutionResult(tuple(tournaments), tuple(problems))


def _canonical_tournament(tournament_id: int, data: Any, stage: str) -> CanonicalTournament:
    tournament = _require_mapping(data, stage)
    _require_fields(tournament, {"id", "name", "start", "end", "rootClasses"}, stage, "tournament")
    resolved_id = _require_positive_int(tournament["id"], stage, "tournament id")
    if resolved_id != tournament_id:
        raise UpstreamDataError(stage, f"response tournament id {resolved_id} does not match requested id")

    name = _require_nonempty_string(tournament["name"], stage, "tournament name")
    start_date = _require_iso_date(tournament["start"], stage, "tournament start")
    end_date = _require_iso_date(tournament["end"], stage, "tournament end")
    if end_date < start_date:
        raise UpstreamDataError(stage, "tournament end cannot be before its start")

    root_classes = _require_list(tournament["rootClasses"], stage)
    for root_class in root_classes:
        root = _require_mapping(root_class, stage)
        _require_fields(root, {"groups"}, stage, "root class")
        for group in _require_list(root["groups"], stage):
            group_object = _require_mapping(group, stage)
            _require_fields(group_object, {"id", "name", "nrofrounds", "pointSystem"}, stage, "tournament group")
            _require_positive_int(group_object["id"], stage, "tournament group id")
            _require_nonempty_string(group_object["name"], stage, "tournament group name")
            _require_nonnegative_int(group_object["nrofrounds"], stage, "tournament group nrofrounds")

    first_root_groups = root_classes[0]["groups"] if root_classes else []
    policy = policy_for(tournament_id)
    if policy:
        try:
            readings, klass_problems = policy.select_groups(tournament), []
        except ValueError as error:
            raise TournamentResolutionError(tournament_id, str(error)) from error
    else:
        readings, klass_problems = read_group_klasses(tournament_id, name, first_root_groups)
    if klass_problems:
        raise TournamentKlassNamingError(tournament_id, klass_problems)
    is_jdm = is_jdm_tournament_name(name)
    processable_groups = tuple(
        ProcessableTournamentGroup(group_id=group["id"], name=group["name"].strip(), klass=klass)
        for group, klass in readings
        if not is_excluded_jgp_klass(klass, is_jdm=is_jdm)
    )
    if not processable_groups:
        raise TournamentResolutionError(tournament_id, "no group competing in the JGP standings")

    return CanonicalTournament(
        tournament_id=resolved_id,
        name=name,
        start_date=start_date,
        end_date=end_date,
        processable_groups=processable_groups,
    )


def _player_info(value: Any, stage: str, subject: str) -> PlayerInfo:
    player = _require_mapping(value, stage)
    _require_fields(player, {"id", "firstName", "lastName", "birthdate"}, stage, subject)
    return PlayerInfo(
        player_id=_require_positive_int(player["id"], stage, f"{subject} id"),
        first_name=_require_nonempty_string(player["firstName"], stage, f"{subject} firstName"),
        last_name=_require_nonempty_string(player["lastName"], stage, f"{subject} lastName"),
        birth_year=_require_birth_year(player["birthdate"], stage, f"{subject} birthdate"),
        current_club_id=_optional_identifier(player, "clubId", stage, f"{subject} clubId"),
        current_club_name=_optional_nonempty_string(player, "club", stage, f"{subject} club"),
        country=_optional_nonempty_string(player, "country", stage, f"{subject} country"),
        fide_id=_optional_identifier(player, "fideid", stage, f"{subject} fideid"),
    )


def _list_of_objects(data: Any, required_fields: set[str], stage: str, subject: str) -> list[Mapping[str, Any]]:
    items = [_require_mapping(value, stage) for value in _require_list(data, stage)]
    for item in items:
        _require_fields(item, required_fields, stage, subject)
    return items


def _require_mapping(value: Any, stage: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise UpstreamDataError(stage, "expected a JSON object")
    return value


def _require_list(value: Any, stage: str) -> list[Any]:
    if not isinstance(value, list):
        raise UpstreamDataError(stage, "expected a JSON array")
    return value


def _require_fields(value: Mapping[str, Any], required: set[str], stage: str, subject: str) -> None:
    missing = sorted(required.difference(value))
    if missing:
        raise UpstreamDataError(stage, f"{subject} is missing required field(s): {', '.join(missing)}")


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _require_positive_int(value: Any, stage: str, subject: str) -> int:
    if not _is_positive_int(value):
        raise UpstreamDataError(stage, f"{subject} must be a positive integer")
    return value


def _require_nonnegative_int(value: Any, stage: str, subject: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise UpstreamDataError(stage, f"{subject} must be a non-negative integer")
    return value


def _require_nonempty_string(value: Any, stage: str, subject: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UpstreamDataError(stage, f"{subject} must be a non-empty string")
    return value.strip()


def _require_iso_date(value: Any, stage: str, subject: str) -> date:
    raw_value = _require_nonempty_string(value, stage, subject)
    try:
        return date.fromisoformat(raw_value)
    except ValueError:
        try:
            return datetime.fromisoformat(raw_value.replace("Z", "+00:00")).date()
        except ValueError as error:
            raise UpstreamDataError(stage, f"{subject} must be an ISO date or date-time") from error


def _require_birth_year(value: Any, stage: str, subject: str) -> int:
    raw_value = _require_nonempty_string(value, stage, subject)
    if len(raw_value) == 4 and raw_value.isascii() and raw_value.isdigit():
        if int(raw_value) > 0:
            return int(raw_value)
        raise UpstreamDataError(stage, f"{subject} must contain a valid year")
    return _require_iso_date(raw_value, stage, subject).year


def _optional_nonempty_string(value: Mapping[str, Any], field: str, stage: str, subject: str) -> str | None:
    raw_value = value.get(field)
    if raw_value is None or raw_value == "":
        return None
    return _require_nonempty_string(raw_value, stage, subject)


def _optional_identifier(value: Mapping[str, Any], field: str, stage: str, subject: str) -> int | None:
    raw_value = value.get(field)
    if raw_value is None or (not isinstance(raw_value, bool) and raw_value == 0):
        return None
    return _require_positive_int(raw_value, stage, subject)
