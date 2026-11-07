"""Server-rendered pages and browser API endpoints."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from io import BytesIO
import ipaddress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file, session

from ..active_configuration import (
    ActiveConfiguration,
    ConfigurationValidationError,
    DEFAULT_CONTRIBUTOR_EMAIL,
    DEFAULT_CONTRIBUTOR_NAME,
    DispensationPlayer,
    StaleConfigurationError,
    load_configuration,
    save_configuration,
)
from ..dispensation_pdf import render_dispensations
from ..dispensation_entry import (
    DispensationRefused,
    carry_dispensations_forward,
    oldest_band_end,
    refuse_oldest_band,
)
from ..tournament_calendar import (
    CalendarAssessment,
    TournamentDates,
    assess_calendar,
    latest_ended_end_date,
    stockholm_today,
)
from ..typst_rules import (
    MAX_RULES_FILE_BYTES,
    RulesFileValidationError,
    custom_rules_path,
    effective_rules_path,
    restore_built_in_rules,
    save_rules_file,
)
from ..upstream_client import (
    DISTRICT_ID,
    CanonicalClub,
    CanonicalTournament,
    MemberSystemClient,
    PlayerInfo,
    TournamentGroupSearchHit,
    TournamentKlassNamingError,
    TournamentResolutionError,
    UpstreamError,
    resolve_canonical_tournaments,
)
from ..util import band_labels

from .available_files import AvailableFile, list_available_files, list_report_files
from .errors import json_error, register_error_handlers
from .generation_operations import (
    GenerationBusyError,
    GenerationInputs,
    GenerationOperationManager,
)
from .klass_naming import klass_naming_problems
from .localization import register_localization


pages = Blueprint("jgp", __name__, template_folder="templates", static_folder="static")
register_localization(pages)
register_error_handlers(pages)

TOURNAMENT_DRAFT_SESSION_KEY = "jgp.tournament_draft"


def _jgp() -> dict[str, Any]:
    return current_app.extensions["jgp"]


@pages.before_request
def _protect_mutations():
    """Let the host's `MUTATION_PROTECTION`, when supplied, reject any JGP POST, PUT or DELETE."""
    protect = _jgp()["settings"].get("MUTATION_PROTECTION")
    if protect and request.method not in ("GET", "HEAD", "OPTIONS") and not protect():
        abort(403)


@pages.context_processor
def _base_template() -> dict[str, str]:
    settings = _jgp()["settings"]
    return {"jgp_base_template": settings["BASE_TEMPLATE"], "jgp_mercure_hub": settings["MERCURE_PUBLIC_URL"]}


def _state_directory() -> Path:
    return Path(_jgp()["settings"]["STATE_DIRECTORY"])


def _member_system_client() -> MemberSystemClient:
    return _jgp()["member_system_client"]


def _district_clubs() -> tuple[CanonicalClub, ...]:
    client = _member_system_client()
    if isinstance(client, MemberSystemClient):
        return tuple(client.district_clubs(DISTRICT_ID))

    cached = _jgp().get("injected_client_clubs")
    if cached is None:
        cached = tuple(client.district_clubs(DISTRICT_ID))
        _jgp()["injected_client_clubs"] = cached
    return cached


def _generation_operations() -> GenerationOperationManager:
    return _jgp()["generation_operations"]


def _current_stockholm_date() -> date:
    return stockholm_today(_jgp()["settings"]["CLOCK"])


def _actor() -> str | None:
    actor = _jgp()["settings"].get("ACTOR")
    return actor() if actor else None


def _can_select_generation_tournaments() -> bool:
    # Require an administrator check from the host or standalone `create_app`.
    authorizer = _jgp()["settings"].get(
        "GENERATION_TOURNAMENT_SELECTION_AUTHORIZER"
    )
    if authorizer is None:
        return False
    return bool(authorizer()) if callable(authorizer) else bool(authorizer)


def local_host_request() -> bool:
    """Standalone's administrator decision: the request's Host header names a local machine."""
    hostname = urlsplit(f"//{request.host}").hostname
    if hostname is not None:
        normalized_hostname = hostname.rstrip(".").lower()
        if (
            normalized_hostname == "localhost"
            or normalized_hostname.endswith(".localhost")
        ):
            return True
        try:
            if ipaddress.ip_address(normalized_hostname).is_loopback:
                return True
        except ValueError:
            pass
    return False


def _configuration_state() -> tuple[ActiveConfiguration | None, tuple[str, ...] | None]:
    try:
        return load_configuration(_state_directory()), None
    except ConfigurationValidationError as error:
        return None, tuple(error.problems)


def _current_configuration(revision: int) -> ActiveConfiguration:
    """Load configuration and enforce a submitted revision before mutation."""
    configuration = load_configuration(_state_directory())
    if revision != configuration.revision:
        raise StaleConfigurationError(
            f"configuration revision {revision} is stale; current revision is "
            f"{configuration.revision}",
            current_revision=configuration.revision,
        )
    return configuration


def _search_result(
    tournament_id: int,
    tournament_name: str,
    matches: list[TournamentGroupSearchHit],
) -> dict[str, Any]:
    return {
        "tournament_id": tournament_id,
        "name": tournament_name,
        "matching_groups": [
            {
                "group_id": match.matching_group_id,
                "name": match.matching_group_name,
                "latest_updated_game": match.latest_updated_game,
            }
            for match in matches
        ],
    }


def _group_search_hits(
    hits: list[TournamentGroupSearchHit],
) -> list[dict[str, Any]]:
    grouped: dict[int, list[TournamentGroupSearchHit]] = {}
    for hit in hits:
        grouped.setdefault(hit.tournament_id, []).append(hit)
    return [
        _search_result(tournament_id, matches[0].tournament_name, matches)
        for tournament_id, matches in sorted(grouped.items(), reverse=True)
    ]


def _canonical_tournament_result(
    tournament: CanonicalTournament,
) -> dict[str, Any]:
    return {
        **_tournament_summary(tournament),
        "processable_groups": [
            {
                "group_id": group.group_id,
                "name": group.name,
                "klass": group.klass,
            }
            for group in tournament.processable_groups
        ],
    }


def _player_result(player: PlayerInfo) -> dict[str, Any]:
    return {
        "player_id": player.player_id,
        "first_name": player.first_name,
        "last_name": player.last_name,
        "birth_year": player.birth_year,
        "current_club": {
            "club_id": player.current_club_id,
            "name": player.current_club_name,
        },
        "country": player.country,
        "fide_id": player.fide_id,
    }


def _dispensation_result(
    player_id: int, player: DispensationPlayer, dispensation_year: int,
) -> dict[str, Any]:
    return {
        "player_id": player_id,
        "name": player.name,
        "birth_year": player.birth_year,
        "club": player.club,
        "dispensation_year": dispensation_year,
    }


def _dispensation_results(configuration: ActiveConfiguration) -> list[dict[str, Any]]:
    """The stored dispensations with their verified players, in name order."""
    return sorted(
        (
            _dispensation_result(player_id, configuration.dispensation_players[player_id], year)
            for player_id, year in configuration.dispensation_dict.items()
        ),
        key=lambda dispensation: (dispensation["name"].casefold(), dispensation["player_id"]),
    )


def _club_result(club: CanonicalClub) -> dict[str, Any]:
    return {
        "club_id": club.club_id,
        "name": club.name,
    }


def _tournament_summary(tournament: CanonicalTournament) -> dict[str, Any]:
    return {
        "tournament_id": tournament.tournament_id,
        "name": tournament.name,
        "start_date": tournament.start_date.isoformat(),
        "end_date": tournament.end_date.isoformat(),
    }


def _klass_naming_error(error: TournamentKlassNamingError):
    return json_error(
        "klass_naming", "errors.klass_naming.heading", 422,
        details={"problems": klass_naming_problems(error.klass_problems)},
    )


def _invalid_configuration_input():
    return json_error(
        "invalid_input",
        "errors.configuration.invalid_input",
        400,
    )


def _resolve_tournaments(
    tournament_ids: tuple[int, ...],
) -> tuple[CanonicalTournament, ...]:
    return resolve_canonical_tournaments(
        _member_system_client(), tournament_ids, collect_problems=True
    ).require_all()


def _tournament_dates(
    tournaments: tuple[CanonicalTournament, ...],
) -> list[TournamentDates]:
    return [
        TournamentDates(tournament.tournament_id, tournament.start_date, tournament.end_date)
        for tournament in tournaments
    ]


def _calendar_assessment(
    tournaments: tuple[CanonicalTournament, ...],
    today: date,
) -> CalendarAssessment:
    return assess_calendar(_tournament_dates(tournaments), today)


def _is_positive_integer(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _submitted_revision(payload: object) -> int | None:
    if not isinstance(payload, dict):
        return None
    revision = payload.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return None
    return revision


def _submitted_tournament_ids(payload: object) -> tuple[int, ...] | None:
    if not isinstance(payload, dict):
        return None
    values = payload.get("tournament_ids")
    if not isinstance(values, list) or not values:
        return None
    if not all(_is_positive_integer(value) for value in values):
        return None
    if len(set(values)) != len(values):
        return None
    return tuple(values)


def _submitted_contributor(payload: object) -> dict[str, str] | None:
    if not isinstance(payload, dict):
        return None
    name = payload.get("name")
    email = payload.get("email")
    if not isinstance(name, str) or not isinstance(email, str):
        return None
    normalized_name = name.strip() or DEFAULT_CONTRIBUTOR_NAME
    normalized_email = email.strip() or DEFAULT_CONTRIBUTOR_EMAIL
    if (
        len(normalized_name) > 200
        or len(normalized_email) > 320
        or normalized_email.count("@") != 1
        or any(character.isspace() for character in normalized_email)
    ):
        return None
    local_part, domain = normalized_email.split("@")
    if (
        not local_part
        or "." not in domain
        or domain.startswith(".")
        or domain.endswith(".")
    ):
        return None
    return {"name": normalized_name, "email": normalized_email}


def _submitted_club_display_name_change(
    payload: object,
) -> tuple[str, str, str | None] | None:
    if not isinstance(payload, dict):
        return None
    action = payload.get("action")
    canonical_name = payload.get("canonical_name")
    display_name = payload.get("display_name")
    if (
        action not in {"upsert", "remove"}
        or not isinstance(canonical_name, str)
        or not canonical_name.strip()
        or len(canonical_name.strip()) > 200
    ):
        return None
    normalized_canonical_name = canonical_name.strip()
    if action == "remove":
        if display_name is not None:
            return None
        return action, normalized_canonical_name, None
    if (
        not isinstance(display_name, str)
        or not display_name.strip()
        or len(display_name.strip()) > 200
    ):
        return None
    return action, normalized_canonical_name, display_name.strip()


def _separator_labels(configuration: ActiveConfiguration, year: int) -> list[str]:
    """The year's age bands, oldest first, then any stored label they do not name, so it can be cleared."""
    labels = band_labels(year)
    return labels + sorted(label for label in configuration.finals_separators if label not in labels)


def _submitted_finals_separators(
    payload: object, labels: list[str],
) -> dict[str, tuple[int, ...]] | None:
    """Each group's distinct rows in ascending order, in `labels` order, leaving out empty groups."""
    if not isinstance(payload, dict):
        return None
    groups = payload.get("finals_separators")
    if not isinstance(groups, dict) or not set(groups) <= set(labels):
        return None
    if not all(
        isinstance(rows, list) and all(_is_positive_integer(row) for row in rows)
        for rows in groups.values()
    ):
        return None
    return {
        label: tuple(sorted(set(groups[label])))
        for label in labels
        if groups.get(label)
    }


def _is_dispensation_year(value: object) -> bool:
    return _is_positive_integer(value) and 1000 <= value <= 9999


def _submitted_dispensations(payload: object) -> list[dict[str, Any]] | None:
    """
    The submitted list, each entry a player id and a dispensation year, with the first and last
    name to look the player up by when the id is not stored yet.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("dispensations"), list):
        return None
    dispensations = []
    for value in payload["dispensations"]:
        if (
            not isinstance(value, dict)
            or not _is_positive_integer(value.get("player_id"))
            or not _is_dispensation_year(value.get("dispensation_year"))
        ):
            return None
        names = [value.get("first_name"), value.get("last_name")]
        if names != [None, None] and not all(
            isinstance(name, str) and name.strip() for name in names
        ):
            return None
        dispensations.append({
            "player_id": value["player_id"],
            "dispensation_year": value["dispensation_year"],
            "names": None if names == [None, None] else tuple(name.strip() for name in names),
        })
    player_ids = [dispensation["player_id"] for dispensation in dispensations]
    if len(set(player_ids)) != len(player_ids):
        return None
    return dispensations


def _draft_tournament(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    tournament_id = value.get("tournament_id")
    name = value.get("name")
    start_date = value.get("start_date")
    end_date = value.get("end_date")
    if (
        not _is_positive_integer(tournament_id)
        or not isinstance(name, str)
        or not name.strip()
        or not isinstance(start_date, str)
        or not isinstance(end_date, str)
    ):
        return None
    try:
        parsed_start = date.fromisoformat(start_date)
        parsed_end = date.fromisoformat(end_date)
    except ValueError:
        return None
    if parsed_end < parsed_start:
        return None
    return {
        "tournament_id": tournament_id,
        "name": name.strip(),
        "start_date": parsed_start.isoformat(),
        "end_date": parsed_end.isoformat(),
    }


def _tournament_draft_for(
    configuration: ActiveConfiguration,
    current_year: int,
) -> dict[str, Any] | None:
    """
    The session's tournament draft, dropped once the configuration revision moves on or, for a
    rollover, once the calendar year does.
    """
    draft = session.get(TOURNAMENT_DRAFT_SESSION_KEY)
    if (
        not isinstance(draft, dict)
        or draft.get("revision") != configuration.revision
        or not isinstance(draft.get("tournaments"), list)
    ):
        session.pop(TOURNAMENT_DRAFT_SESSION_KEY, None)
        return None
    rollover_year = draft.get("rollover_year")
    if rollover_year is not None and rollover_year != current_year:
        session.pop(TOURNAMENT_DRAFT_SESSION_KEY, None)
        return None
    tournaments = [_draft_tournament(value) for value in draft["tournaments"]]
    if None in tournaments:
        session.pop(TOURNAMENT_DRAFT_SESSION_KEY, None)
        return None
    return {"tournaments": tuple(tournaments), "rollover_year": rollover_year}


def _draft_rollover_year(revision: int) -> int | None:
    """The year of the rollover the session's draft holds for `revision`, if it holds one."""
    draft = session.get(TOURNAMENT_DRAFT_SESSION_KEY)
    if not isinstance(draft, dict) or draft.get("revision") != revision:
        return None
    rollover_year = draft.get("rollover_year")
    return rollover_year if isinstance(rollover_year, int) else None


def _generation_blocked_reason(
    configuration: ActiveConfiguration | None,
    selected_tournaments: tuple[dict[str, Any], ...],
    rollover_active: bool = False,
) -> str | None:
    """The catalogue key saying why Generate is disabled, or `None` when it is enabled."""
    if configuration is None:
        return "generation.blocked.invalid_configuration"
    if rollover_active:
        return "generation.blocked.rollover_active"
    if not selected_tournaments:
        return "generation.blocked.no_verified_tournaments"
    return None


@dataclass(frozen=True)
class _ResolvedGenerationSubmission:
    configuration: ActiveConfiguration
    inputs: GenerationInputs
    tournaments: tuple[CanonicalTournament, ...]


def _generation_problem(
    field: str,
    message_key: str,
    **parameters: object,
) -> dict[str, Any]:
    return {
        "field": field,
        "message_key": message_key,
        "parameters": parameters,
    }


def _generation_input_error(problems: list[dict[str, Any]]):
    return json_error(
        "invalid_generation_inputs",
        "errors.generation.invalid_input",
        422,
        details={"problems": problems},
    )


def _resolve_generation_submission(payload: object):
    """
    Validate one dialog submission and resolve its tournaments upstream, refusing with every
    problem at once. A run given its own tournament list is partial; any other run uses the
    configured tournaments, which must belong to the current year.
    """
    if not isinstance(payload, dict):
        return _generation_input_error(
            [_generation_problem("form", "errors.generation.invalid_input")]
        )

    problems: list[dict[str, Any]] = []
    explicit_year = payload.get("year")
    valid_explicit_year = (
        explicit_year is not None
        and not isinstance(explicit_year, bool)
        and isinstance(explicit_year, int)
        and 1900 <= explicit_year <= 9999
    )
    if explicit_year is not None and not valid_explicit_year:
        problems.append(_generation_problem("year", "errors.generation.year"))

    layout = payload.get("layout")
    if not isinstance(layout, str) or layout not in {"portrait", "landscape"}:
        problems.append(_generation_problem("layout", "errors.generation.layout"))

    raw_tournament_ids = payload.get("tournament_ids")
    if (
        raw_tournament_ids is not None
        and not _can_select_generation_tournaments()
    ):
        return json_error(
            "generation_tournament_selection_forbidden",
            "errors.generation.tournament_selection_forbidden",
            403,
        )
    explicit_tournament_ids: tuple[int, ...] | None = None
    if raw_tournament_ids is not None:
        if not isinstance(raw_tournament_ids, list) or not all(
            _is_positive_integer(value) for value in raw_tournament_ids
        ):
            problems.append(_generation_problem("tournaments", "errors.generation.tournaments"))
        elif not raw_tournament_ids:
            problems.append(
                _generation_problem("tournaments", "errors.generation.tournaments_required")
            )
        elif len(set(raw_tournament_ids)) != len(raw_tournament_ids):
            problems.append(
                _generation_problem("tournaments", "errors.generation.tournaments_unique")
            )
        else:
            explicit_tournament_ids = tuple(raw_tournament_ids)

    today = _current_stockholm_date()
    if (
        valid_explicit_year
        and explicit_year != today.year
        and raw_tournament_ids is None
    ):
        problems.append(
            _generation_problem(
                "tournaments",
                "errors.generation.non_current_tournaments",
                year=explicit_year,
            )
        )
    if problems:
        return _generation_input_error(problems)

    configuration = load_configuration(_state_directory())
    is_partial = explicit_tournament_ids is not None
    effective_year = explicit_year if valid_explicit_year else today.year
    tournament_ids = (
        explicit_tournament_ids if is_partial else configuration.tournament_ids
    )
    if not tournament_ids:
        return _generation_input_error(
            [_generation_problem("tournaments", "errors.generation.tournaments_required")]
        )

    resolution = resolve_canonical_tournaments(
        _member_system_client(), tournament_ids, collect_problems=True
    )
    if resolution.problems:
        for problem in resolution.problems:
            if problem.klass_problems:
                problems.extend(klass_naming_problems(problem.klass_problems))
            else:
                problems.append(_generation_problem(
                    "tournaments", "errors.generation.tournament_unresolvable",
                    tournament_id=problem.tournament_id,
                ))
        return _generation_input_error(problems)
    tournaments = resolution.tournaments

    assessment = _calendar_assessment(tournaments, today)
    if not is_partial and assessment.status != "current":
        problems.append(_generation_problem(
            "tournaments",
            f"errors.generation.defaults_{assessment.status}",
            year=effective_year,
        ))
    if is_partial and assessment.years != (effective_year,):
        problems.append(_generation_problem(
            "tournaments", "errors.generation.tournament_years", year=effective_year,
        ))
    if latest_ended_end_date(_tournament_dates(tournaments), today) is None:
        problems.append(_generation_problem("tournaments", "errors.generation.none_ended"))
    bands = band_labels(effective_year)
    problems.extend(
        _generation_problem(
            "separators", "errors.generation.unknown_separator_band",
            label=label, year=effective_year,
        )
        for label in sorted(set(configuration.finals_separators) - set(bands))
    )
    if problems:
        return _generation_input_error(problems)

    return _ResolvedGenerationSubmission(
        configuration,
        GenerationInputs(
            today=today,
            effective_year=effective_year,
            tournament_ids=tuple(tournament.tournament_id for tournament in tournaments),
            is_partial=is_partial,
            layout=layout,
        ),
        tournaments,
    )


def _generated_files(listing: Callable[[Path, str], tuple[AvailableFile, ...]]) -> tuple[AvailableFile, ...]:
    return listing(Path(_jgp()["settings"]["OUTPUT_DIRECTORY"]), _jgp()["settings"]["FILES_PUBLIC_ORIGIN"])


@pages.get("/")
def landing():
    configuration, configuration_problems = _configuration_state()
    return render_template(
        "jgp/landing.jinja2",
        configuration=configuration,
        configuration_problems=configuration_problems,
        available_files=_generated_files(list_available_files),
    )


@pages.get("/configuration")
def configuration():
    active_configuration, problems = _configuration_state()
    today = _current_stockholm_date()
    selected_tournaments: tuple[dict[str, Any], ...] = ()
    selection_problem: str | None = None
    selection_klass_problems = []
    calendar_status: str | None = None
    rollover_active = False
    if active_configuration is not None:
        draft = _tournament_draft_for(active_configuration, today.year)
        if draft is not None:
            selected_tournaments = draft["tournaments"]
            rollover_active = draft["rollover_year"] is not None
            if rollover_active:
                calendar_status = "stale"
        else:
            try:
                tournaments = _resolve_tournaments(active_configuration.tournament_ids)
                calendar_status = _calendar_assessment(tournaments, today).status
                selected_tournaments = tuple(
                    _tournament_summary(tournament) for tournament in tournaments
                )
            except TournamentKlassNamingError as error:
                selection_problem = "errors.klass_naming.heading"
                selection_klass_problems = klass_naming_problems(error.klass_problems)
            except (TournamentResolutionError, UpstreamError):
                selection_problem = "errors.member_system.unavailable"
    return render_template(
        "jgp/configuration.jinja2",
        configuration=active_configuration,
        configuration_problems=problems,
        selected_tournaments=selected_tournaments,
        selection_problem=selection_problem,
        selection_klass_problems=selection_klass_problems,
        club_display_names=(
            dict(active_configuration.club_display_names)
            if active_configuration is not None
            else {}
        ),
        dispensations=(
            _dispensation_results(active_configuration)
            if active_configuration is not None
            else []
        ),
        separator_labels=(
            _separator_labels(active_configuration, today.year)
            if active_configuration is not None
            else []
        ),
        current_year=today.year,
        oldest_band_end=oldest_band_end(today.year),
        calendar_status=calendar_status,
        rollover_active=rollover_active,
        default_contributor_name=DEFAULT_CONTRIBUTOR_NAME,
        default_contributor_email=DEFAULT_CONTRIBUTOR_EMAIL,
        generation_blocked_reason=_generation_blocked_reason(
            active_configuration, selected_tournaments, rollover_active
        ),
        can_select_generation_tournaments=_can_select_generation_tournaments(),
        has_custom_rules_file=custom_rules_path(_state_directory()).is_file(),
    )


@pages.get("/api/v1/configuration/rules")
def download_rules_file():
    return send_file(
        effective_rules_path(_state_directory()),
        as_attachment=True,
        download_name="rules.typ",
        mimetype="text/plain; charset=utf-8",
    )


@pages.post("/api/v1/configuration/rules")
def upload_rules_file():
    uploaded = request.files.get("rules")
    if (
        uploaded is None
        or not uploaded.filename
        or Path(uploaded.filename).suffix.lower() != ".typ"
    ):
        return json_error("invalid_rules_file", "errors.rules_file.invalid", 400)

    content = uploaded.stream.read(MAX_RULES_FILE_BYTES + 1)
    try:
        save_rules_file(_state_directory(), content)
    except RulesFileValidationError as error:
        return json_error(
            "invalid_rules_file",
            "errors.rules_file.invalid",
            400,
            details={"reason": str(error)},
        )
    return jsonify({"data": {"filename": "rules.typ"}})


@pages.delete("/api/v1/configuration/rules")
def restore_rules_file():
    restored = restore_built_in_rules(_state_directory())
    return jsonify({"data": {"restored": restored}})


@pages.get("/downloads")
def available_files():
    return render_template(
        "jgp/available_files.jinja2",
        available_files=_generated_files(list_available_files),
    )


@pages.get("/reports")
def reports():
    return render_template(
        "jgp/reports.jinja2",
        report_files=_generated_files(list_report_files),
    )


@pages.post("/api/v1/downloads/clear")
def clear_available_files():
    """Remove every generated download without changing configuration."""
    try:
        _generation_operations().clear_downloads()
    except GenerationBusyError as error:
        return json_error(
            "downloads_busy",
            "errors.downloads.operation_busy",
            409,
            details={"active_operation_id": error.operation_id},
        )
    except Exception:
        return json_error(
            "downloads_clear_failed",
            "errors.downloads.clear_failed",
            500,
        )

    return jsonify({"data": {"cleared": True}})


@pages.get("/help/group-names")
def klass_naming_help():
    """Show naming conventions in the organiser's chosen language."""
    return render_template("jgp/klass_naming.jinja2")


@pages.get("/api/v1/generation/defaults")
def generation_defaults():
    configuration = load_configuration(_state_directory())

    try:
        tournaments = _resolve_tournaments(configuration.tournament_ids)
    except TournamentKlassNamingError as error:
        return _klass_naming_error(error)
    except TournamentResolutionError:
        return json_error(
            "tournament_unresolvable",
            "errors.tournament_lookup.unresolvable",
            422,
        )

    return jsonify(
        {
            "data": {
                "current_year": _current_stockholm_date().year,
                "configuration_revision": configuration.revision,
                "tournaments": [
                    _tournament_summary(tournament)
                    for tournament in tournaments
                ],
            }
        }
    )


@pages.post("/api/v1/generation/inputs")
def resolve_generation_request():
    submission = _resolve_generation_submission(request.get_json(silent=True))
    if not isinstance(submission, _ResolvedGenerationSubmission):
        return submission

    inputs = submission.inputs
    publishes_by_default = not inputs.is_partial
    return jsonify(
        {
            "data": {
                "inputs": {
                    "effective_year": inputs.effective_year,
                    "tournament_ids": list(inputs.tournament_ids),
                    "is_partial": inputs.is_partial,
                    "layout": inputs.layout,
                },
                "tournaments": [
                    _tournament_summary(tournament)
                    for tournament in submission.tournaments
                ],
                "publication": {
                    "pdf": publishes_by_default,
                    "json": publishes_by_default,
                },
            }
        }
    )


@pages.post("/api/v1/generation/operations")
def start_generation_operation():
    """Validate one request and start its exclusive background operation."""
    submission = _resolve_generation_submission(request.get_json(silent=True))
    if not isinstance(submission, _ResolvedGenerationSubmission):
        return submission

    try:
        operation = _generation_operations().start(
            submission.configuration,
            submission.inputs,
            # Capture the host's actor before starting the background run, which has no request context.
            actor=_actor(),
        )
    except GenerationBusyError as error:
        return json_error(
            "generation_busy",
            "errors.generation.busy",
            409,
            details={
                "active_operation_id": error.operation_id,
                "status_url": _generation_operations().store.status_url(error.operation_id),
            },
        )
    except Exception:
        return json_error(
            "generation_start_failed",
            "errors.generation.start_failed",
            500,
        )

    return jsonify({"data": operation.as_dict()}), 202


@pages.get("/api/v1/generation/operations/<operation_id>")
def generation_operation_status(operation_id: str):
    """Return the durable latest state without relying on event delivery."""
    try:
        operation = _generation_operations().store.load(operation_id)
    except FileNotFoundError:
        return json_error(
            "operation_not_found",
            "errors.generation.operation_not_found",
            404,
        )
    return jsonify({"data": operation.as_dict()})


@pages.get("/api/v1/tournaments/search")
def tournament_search():
    search_text = request.args.get("q")
    if search_text is None or not search_text.strip():
        return json_error(
            "invalid_input",
            "errors.tournament_search.invalid_input",
            400,
            details={"field": "q"},
        )

    query = search_text.strip()
    results = _group_search_hits(_member_system_client().tournament_group_search(query))
    return jsonify(
        {
            "data": {
                "state": "results" if results else "no_matches",
                "query": query,
                "tournaments": results,
            }
        }
    )


@pages.get("/api/v1/tournaments/<tournament_id>")
def canonical_tournament(tournament_id: str):
    parsed_tournament_id = int(tournament_id) if tournament_id.isdecimal() else 0
    if parsed_tournament_id <= 0 or str(parsed_tournament_id) != tournament_id:
        return json_error(
            "invalid_input",
            "errors.tournament_lookup.invalid_input",
            400,
            details={"field": "tournament_id"},
        )

    try:
        tournament = _member_system_client().canonical_tournament(parsed_tournament_id)
    except TournamentKlassNamingError as error:
        return _klass_naming_error(error)
    except TournamentResolutionError:
        return json_error(
            "tournament_unresolvable",
            "errors.tournament_lookup.unresolvable",
            422,
            details={"tournament_id": parsed_tournament_id},
        )

    return jsonify({"data": {"tournament": _canonical_tournament_result(tournament)}})


@pages.get("/api/v1/players/search")
def player_search():
    first_name = request.args.get("first_name")
    last_name = request.args.get("last_name")
    if (
        first_name is None
        or not first_name.strip()
        or last_name is None
        or not last_name.strip()
    ):
        return json_error(
            "invalid_input",
            "errors.player_search.invalid_input",
            400,
            details={"fields": ["first_name", "last_name"]},
        )

    normalized_first_name = first_name.strip()
    normalized_last_name = last_name.strip()
    players = [
        _player_result(player)
        for player in _member_system_client().player_name_search(
            normalized_first_name,
            normalized_last_name,
        )
    ]

    return jsonify(
        {
            "data": {
                "state": "results" if players else "no_matches",
                "query": {
                    "first_name": normalized_first_name,
                    "last_name": normalized_last_name,
                },
                "players": players,
            }
        }
    )


@pages.get("/api/v1/clubs")
def district_clubs():
    clubs = [_club_result(club) for club in _district_clubs()]

    return jsonify(
        {
            "data": {
                "state": "results" if clubs else "no_matches",
                "clubs": clubs,
            }
        }
    )


@pages.put("/api/v1/configuration/tournaments/draft")
def save_tournament_draft():
    payload = request.get_json(silent=True)
    revision = _submitted_revision(payload)
    raw_tournaments = payload.get("tournaments") if isinstance(payload, dict) else None
    if revision is None or not isinstance(raw_tournaments, list):
        return _invalid_configuration_input()

    tournaments = [_draft_tournament(value) for value in raw_tournaments]
    if None in tournaments:
        return _invalid_configuration_input()
    tournament_ids = [tournament["tournament_id"] for tournament in tournaments]
    if len(set(tournament_ids)) != len(tournament_ids):
        return _invalid_configuration_input()

    tournaments.sort(
        key=lambda tournament: (tournament["start_date"], tournament["tournament_id"])
    )
    rollover_year = _draft_rollover_year(revision)
    session[TOURNAMENT_DRAFT_SESSION_KEY] = {
        "revision": revision,
        "tournaments": tournaments,
        **({"rollover_year": rollover_year} if rollover_year is not None else {}),
    }
    return jsonify({"data": {"saved": True}})


@pages.post("/api/v1/configuration/tournaments/rollover")
def start_tournament_rollover():
    payload = request.get_json(silent=True)
    revision = _submitted_revision(payload)
    if revision is None:
        return _invalid_configuration_input()

    configuration = _current_configuration(revision)

    today = _current_stockholm_date()
    try:
        tournaments = _resolve_tournaments(configuration.tournament_ids)
    except TournamentKlassNamingError as error:
        return _klass_naming_error(error)
    except TournamentResolutionError:
        return json_error(
            "tournament_unresolvable",
            "errors.tournament_lookup.unresolvable",
            422,
        )

    assessment = _calendar_assessment(tournaments, today)
    if assessment.status != "stale":
        return json_error(
            "rollover_unavailable",
            "errors.configuration.rollover_unavailable",
            409,
            details={
                "current_year": today.year,
                "configured_years": list(assessment.years),
                "state": assessment.status,
            },
        )

    session[TOURNAMENT_DRAFT_SESSION_KEY] = {
        "revision": configuration.revision,
        "rollover_year": today.year,
        "tournaments": [],
    }
    return jsonify(
        {
            "data": {
                "revision": configuration.revision,
                "rollover_year": today.year,
                "started": True,
            }
        }
    )


@pages.delete("/api/v1/configuration/tournaments/rollover")
def cancel_tournament_rollover():
    payload = request.get_json(silent=True)
    revision = _submitted_revision(payload)
    if revision is None:
        return _invalid_configuration_input()

    configuration = _current_configuration(revision)

    cancelled = _draft_rollover_year(revision) is not None
    if cancelled:
        session.pop(TOURNAMENT_DRAFT_SESSION_KEY, None)
    return jsonify(
        {
            "data": {
                "revision": configuration.revision,
                "cancelled": cancelled,
            }
        }
    )


@pages.post("/api/v1/configuration/tournaments")
def save_tournaments():
    payload = request.get_json(silent=True)
    revision = _submitted_revision(payload)
    tournament_ids = _submitted_tournament_ids(payload)
    if revision is None or tournament_ids is None:
        return _invalid_configuration_input()

    configuration = _current_configuration(revision)

    def unchanged(ids: tuple[int, ...]):
        session.pop(TOURNAMENT_DRAFT_SESSION_KEY, None)
        return jsonify(
            {
                "data": {
                    "revision": configuration.revision,
                    "tournament_ids": list(ids),
                    "changed": False,
                }
            }
        )

    rollover_year = _draft_rollover_year(revision)
    if rollover_year is None and tournament_ids == configuration.tournament_ids:
        return unchanged(tournament_ids)

    try:
        tournaments = _resolve_tournaments(tournament_ids)
    except TournamentKlassNamingError as error:
        return _klass_naming_error(error)
    except TournamentResolutionError:
        return json_error(
            "tournament_unresolvable",
            "errors.tournament_lookup.unresolvable",
            422,
        )

    today = _current_stockholm_date()
    assessment = _calendar_assessment(tournaments, today)
    if assessment.status == "mixed":
        return json_error(
            "mixed_tournament_years",
            "errors.configuration.tournament_years_mixed",
            422,
            details={"configured_years": list(assessment.years)},
        )
    if assessment.status == "future":
        return json_error(
            "future_tournament_year",
            "errors.configuration.tournament_year_future",
            422,
            details={
                "current_year": today.year,
                "configured_years": list(assessment.years),
            },
        )

    if rollover_year is not None and (
        rollover_year != today.year
        or any(tournament.start_date.year != rollover_year for tournament in tournaments)
    ):
        return json_error(
            "invalid_rollover_tournament_year",
            "errors.configuration.rollover_tournament_year",
            422,
            details={"rollover_year": rollover_year},
        )

    # Tournaments are sorted by start date, then tournament id.
    ordered_ids = tuple(tournament.tournament_id for tournament in tournaments)
    if ordered_ids == configuration.tournament_ids:
        return unchanged(ordered_ids)

    updated = replace(configuration, tournament_ids=ordered_ids)
    if rollover_year is not None:
        # Clear finals separators and advance dispensation years for the new season.
        updated = carry_dispensations_forward(
            replace(updated, finals_separators={}), rollover_year
        )
    saved = save_configuration(_state_directory(), updated, expected_revision=revision)
    session.pop(TOURNAMENT_DRAFT_SESSION_KEY, None)
    data = {
        "revision": saved.revision,
        "tournament_ids": list(saved.tournament_ids),
        "changed": True,
    }
    if rollover_year is not None:
        data["rollover_completed"] = True
        data["dispensations"] = _dispensation_results(saved)
    return jsonify({"data": data})


@pages.post("/api/v1/configuration/contributor")
def save_contributor():
    payload = request.get_json(silent=True)
    revision = _submitted_revision(payload)
    contributor = _submitted_contributor(payload)
    if revision is None or contributor is None:
        return json_error(
            "invalid_input",
            "errors.contributor.invalid_input",
            400,
        )

    configuration = _current_configuration(revision)
    if contributor == dict(configuration.contributor):
        return jsonify(
            {
                "data": {
                    "revision": configuration.revision,
                    "contributor": contributor,
                    "changed": False,
                }
            }
        )

    saved = save_configuration(
        _state_directory(),
        replace(configuration, contributor=contributor),
        expected_revision=revision,
    )

    return jsonify(
        {
            "data": {
                "revision": saved.revision,
                "contributor": dict(saved.contributor),
                "changed": True,
            }
        }
    )


@pages.post("/api/v1/configuration/separators")
def save_finals_separators():
    payload = request.get_json(silent=True)
    revision = _submitted_revision(payload)
    if revision is None:
        return json_error("invalid_input", "errors.finals.invalid_input", 400)

    configuration = _current_configuration(revision)
    labels = _separator_labels(configuration, _current_stockholm_date().year)
    separators = _submitted_finals_separators(payload, labels)
    if separators is None:
        return json_error("invalid_input", "errors.finals.invalid_input", 400)

    changed = separators != configuration.finals_separators
    if changed:
        configuration = save_configuration(
            _state_directory(),
            replace(configuration, finals_separators=separators),
            expected_revision=revision,
        )
    return jsonify({"data": {
        "revision": configuration.revision,
        "finals_separators": {label: list(rows) for label, rows in configuration.finals_separators.items()},
        "changed": changed,
    }})


@pages.post("/api/v1/configuration/club-display-names")
def save_club_display_name():
    payload = request.get_json(silent=True)
    revision = _submitted_revision(payload)
    change = _submitted_club_display_name_change(payload)
    if revision is None or change is None:
        return json_error(
            "invalid_input",
            "errors.club_display_names.invalid_input",
            400,
        )

    configuration = _current_configuration(revision)

    action, canonical_name, display_name = change
    aliases = dict(configuration.club_display_names)
    if action == "remove":
        if canonical_name not in aliases:
            return jsonify(
                {
                    "data": {
                        "revision": configuration.revision,
                        "club_display_names": aliases,
                        "changed": False,
                    }
                }
            )
        del aliases[canonical_name]
    else:
        if aliases.get(canonical_name) == display_name:
            return jsonify(
                {
                    "data": {
                        "revision": configuration.revision,
                        "club_display_names": aliases,
                        "changed": False,
                    }
                }
            )
        if canonical_name not in aliases:
            canonical_club_names = {
                club.name for club in _district_clubs()
            }
            if canonical_name not in canonical_club_names:
                return json_error(
                    "noncanonical_club",
                    "errors.club_display_names.noncanonical_club",
                    422,
                )
        aliases[canonical_name] = display_name

    saved = save_configuration(
        _state_directory(),
        replace(configuration, club_display_names=aliases),
        expected_revision=revision,
    )

    return jsonify(
        {
            "data": {
                "revision": saved.revision,
                "club_display_names": dict(saved.club_display_names),
                "changed": True,
            }
        }
    )


@pages.get("/api/v1/configuration/dispensations.pdf")
def export_dispensations():
    revision = request.args.get("revision", type=int)
    if revision is None:
        return json_error("invalid_input", "errors.dispensations.invalid_input", 400)
    configuration = _current_configuration(revision)
    if not configuration.dispensation_dict:
        return json_error("empty_dispensations", "dispensations.empty", 422)
    exported_on = _current_stockholm_date()
    try:
        pdf = render_dispensations(configuration, exported_on)
    except RuntimeError:
        current_app.logger.exception("Dispensation PDF export failed")
        return json_error("export_failed", "dispensations.export_failed", 500)
    response = send_file(
        BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
        download_name=f"Spelardispenser-JGP-{exported_on.year}.pdf",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@pages.post("/api/v1/configuration/dispensations")
def save_dispensations():
    """
    Store the submitted list whole. A player id that is not stored yet is looked up again by the
    submitted name, and is stored only when the member system returns that id; the verified player
    comes from that answer, never from the submission. A new player born in this season's oldest band
    is refused, since no dispensation applies to them.
    """
    payload = request.get_json(silent=True)
    revision = _submitted_revision(payload)
    submitted = _submitted_dispensations(payload)
    if revision is None or submitted is None:
        return json_error(
            "invalid_input",
            "errors.dispensations.invalid_input",
            400,
        )

    configuration = _current_configuration(revision)
    dispensations = {
        dispensation["player_id"]: dispensation["dispensation_year"] for dispensation in submitted
    }
    players = {
        player_id: player
        for player_id, player in configuration.dispensation_players.items()
        if player_id in dispensations
    }
    season_year = _current_stockholm_date().year
    unresolved = []
    for dispensation in submitted:
        player_id = dispensation["player_id"]
        if player_id in players:
            continue
        if dispensation["names"] is None:
            return json_error(
                "invalid_input",
                "errors.dispensations.invalid_input",
                400,
            )
        match = next(
            (
                player
                for player in _member_system_client().player_name_search(*dispensation["names"])
                if player.player_id == player_id
            ),
            None,
        )
        if match is None:
            unresolved.append(player_id)
            continue
        try:
            refuse_oldest_band(match.birth_year, season_year)
        except DispensationRefused as refusal:
            return json_error(
                "oldest_band",
                "errors.dispensations.oldest_band",
                422,
                details={"player_id": player_id, **refusal.parameters},
            )
        players[player_id] = DispensationPlayer(
            name=f"{match.first_name} {match.last_name}",
            birth_year=match.birth_year,
            club=match.current_club_name or "",
        )
    if unresolved:
        return json_error(
            "unresolved_player",
            "errors.dispensations.unresolved_player",
            422,
            details={"player_ids": unresolved},
        )

    changed = dispensations != dict(configuration.dispensation_dict)
    if changed:
        configuration = save_configuration(
            _state_directory(),
            replace(configuration, dispensation_dict=dispensations, dispensation_players=players),
            expected_revision=revision,
        )
    return jsonify(
        {
            "data": {
                "revision": configuration.revision,
                "dispensations": _dispensation_results(configuration),
                "changed": changed,
            }
        }
    )
