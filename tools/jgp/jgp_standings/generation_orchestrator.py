"""Fetch inputs, validate tournament entries and generate standings in memory.

`run_generation` applies eligibility rules, klass validation, dispensations,
penalties and duplicate resolution before collating age bands and producing
artifacts. `artifact_publication` persists the result.

Failures raise `GenerationStageError` with the failing stage. Invalid inputs
raise `GenerationValidationError`; invalid group names are collected across
tournaments before calculation. An optional `ProgressReporter` receives stages.
"""
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from functools import partial
from pathlib import Path

from .active_configuration import ActiveConfiguration
from .generation_core import (
    GenerationDiagnostics,
    GenerationResult,
    TournamentEntries,
    build_generation_result,
    collate_standings,
)
from .generation_progress import ProgressReporter, operation
from .klass_naming import (
    KlassNamingProblem,
    is_excluded_jgp_klass,
    is_jdm_tournament_name,
    read_group_klasses,
)
from .run_record import load_or_resolve_run_input, resolve_run_input
from .upstream_client import Fetch
from .tournament_exceptions import policy_for
from .util import (
    band_labels,
    fetch_group_details,
    fetch_tournament_data,
    filter_non_local_players,
    validate_players,
)


class GenerationStageError(RuntimeError):
    """A terminal generation failure attributed to one pipeline stage."""

    def __init__(self, stage: str, detail: str) -> None:
        self.stage = stage
        super().__init__(f"generation failed during {stage}: {detail}")


class GenerationValidationError(GenerationStageError):
    """Raised when configuration or resolved inputs cannot start a generation."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("validation", "; ".join(self.problems))


class KlassNamingError(GenerationValidationError):
    """Collect invalid group names across the run before calculation.

    `klass_problems` contains tournament, group and reason codes for display.
    """

    def __init__(self, problems: Sequence[KlassNamingProblem]) -> None:
        self.klass_problems = tuple(problems)
        super().__init__(tuple(str(problem) for problem in self.klass_problems))

    def report(self) -> str:
        """The refusal as an operator reads it on a terminal, one group per line."""
        if len(self.klass_problems) == 1:
            summary = "1 group name does not follow"
        else:
            summary = f"{len(self.klass_problems)} group names do not follow"
        lines = [
            f"{summary} the klass naming rules in KLASS_NAMING.md.",
            "Ask the organiser to rename them in the member system, then run again:",
        ]
        lines.extend(f"  - {problem}" for problem in self.klass_problems)
        return "\n".join(lines)


def fetch_rating_list_ids(fetch: Fetch, rating_list_date: date) -> set:
    BASE_URL = "https://member.schack.se/public/api/v1/ratinglist/district/5821/date"
    url = f"{BASE_URL}/{rating_list_date.isoformat()}/ratingtype/1/category/0"

    all_stsf_players = fetch(url, "fetching the district rating list")
    return {player['id'] for player in all_stsf_players}


def fetch_club_ids(fetch: Fetch) -> set:
    url = "https://member.schack.se/public/api/v1/organisation/district/clubs/5821"

    data = fetch(url, "fetching the district club list")
    return {club['id'] for club in data}


def fetch_tournament_info(fetch: Fetch, tournaments, progress: ProgressReporter | None = None) -> tuple[dict, list]:
    """
    Return each tournament's competing groups, keyed by tournament id, and the ids of the
    tournaments that are JDM events. Every group name is read, in every tournament, before any
    refused name is raised as one `KlassNamingError`.
    """
    interesting_groups = {}
    jdm_tournament_ids = []
    klass_problems = []
    for tournament in tournaments:
        if progress is not None:
            progress.start(f"Fetching metadata for tournament {tournament}")
        data = fetch_tournament_data(fetch, tournament)
        if data:
            interesting_groups[tournament] = []
            tournament_name = data["name"]
            tournament_date = data['start'].replace('-', '')
            policy = policy_for(tournament)
            exceptional_readings = policy.select_groups(data) if policy else None
            groups = data['rootClasses'][0]['groups']
            tournament_year = int(tournament_date[:4])
            tournament_month = int(tournament_date[4:6])
            is_jdm = is_jdm_tournament_name(tournament_name)
            if is_jdm:
                jdm_tournament_ids.append(tournament)
            if policy:
                readings, problems = exceptional_readings, []
            else:
                readings, problems = read_group_klasses(tournament, tournament_name, groups)
            klass_problems.extend(problems)
            for group, klass in readings:
                name: str = group['name']
                nrofrounds: int = group['nrofrounds']

                if is_excluded_jgp_klass(klass, is_jdm=is_jdm):
                    continue
                group_id: int = group['id']
                interesting_groups[tournament].append(
                    {'date': tournament_date, 'tournament_id': tournament, 'tournament_name': tournament_name, 'group_id': group_id,
                     'klass': klass, 'nrofrounds': nrofrounds, 'group_name': name, 'year': tournament_year, 'month': tournament_month})
    if klass_problems:
        raise KlassNamingError(klass_problems)
    return interesting_groups, jdm_tournament_ids


def remove_duplicates_by_secpoints(player_list):
    """Returns a new list with duplicates removed based on lowest secPoints per contenderId."""
    best_entries = {}
    for player in player_list:
        cid = player['contenderId']
        if cid not in best_entries or player['secPoints'] < best_entries[cid]['secPoints']:
            best_entries[cid] = player
    return list(best_entries.values())


def run_generation(
    configuration: ActiveConfiguration,
    *,
    fetch: Fetch,
    run_record_path: Path | None,
    today: date,
    override_year: int | None = None,
    override_tournament_ids: list[int] | None = None,
    progress: ProgressReporter | None = None,
) -> GenerationResult:
    """Generate standings using the supplied fetcher and report progress.

    Replay `run_record_path` when its tournament ids match. Without a matching
    record, resolve fresh inputs. Return artifacts in memory.
    """
    run_tournament_ids = (override_tournament_ids if override_tournament_ids is not None
                          else list(configuration.tournament_ids))
    recorded = None
    if any(policy_for(tid) for tid in run_tournament_ids):
        from .tournament_exceptions.audit import RecordingFetch
        recorded = RecordingFetch(fetch)
        fetch = recorded
    try:
        run_input, tournaments, player_list, diagnostics = _fetch_inputs(
            configuration, fetch, run_tournament_ids, run_record_path, today, override_year, progress
        )
    except GenerationStageError:
        raise
    except Exception as error:
        raise GenerationStageError("fetching", str(error)) from error

    unknown_labels = sorted(set(configuration.finals_separators) - set(band_labels(run_input.effective_year)))
    if unknown_labels:
        raise GenerationValidationError(tuple(
            f"finals separators key {label!r} names no age band of {run_input.effective_year}"
            for label in unknown_labels
        ))

    if progress is not None:
        progress.begin_phase("Processing results", 5, "steps")

    try:
        with operation(progress, "Calculating standings"):
            standings = collate_standings(
                run_input=run_input,
                tournaments=tournaments,
                player_list=player_list,
                dispensation_dict=configuration.dispensation_dict,
                diagnostics=diagnostics,
            )
    except Exception as error:
        raise GenerationStageError("calculation", str(error)) from error

    try:
        with operation(progress, "Producing output artifacts"):
            result = build_generation_result(
                standings,
                contributor=configuration.contributor,
                landscape=configuration.landscape,
                club_display_names=configuration.club_display_names,
                finals_separators=configuration.finals_separators,
            )
            if recorded is not None:
                from .tournament_exceptions.audit import reports
                result = replace(result, artifacts=replace(
                    result.artifacts,
                    diagnostic_reports={**result.artifacts.diagnostic_reports,
                                        **reports(standings, configuration, recorded)},
                ))
            return result
    except Exception as error:
        raise GenerationStageError("artifact production", str(error)) from error


def _fetch_inputs(configuration, fetch, run_tournament_ids, run_record_path, today, override_year, progress):
    if progress is not None:
        progress.start("Fetching district players and clubs")
    ssf_club_ids = fetch_club_ids(fetch)
    tournament_data = partial(fetch_tournament_data, fetch)
    if run_record_path is None:
        run_input = resolve_run_input(run_tournament_ids, tournament_data, today, override_year)
    else:
        run_input = load_or_resolve_run_input(
            run_tournament_ids, tournament_data, today, run_record_path, override_year
        )
    stsf_player_ids = fetch_rating_list_ids(fetch, run_input.rating_list_date)
    groups_by_tournament, jdm_tournament_ids = fetch_tournament_info(fetch, run_tournament_ids, progress)

    player_list = {}
    rejected_players = []
    walkovers = {}
    no_matches_played = {}
    penalized_players = {}
    tournaments = []
    tournament_count = len(run_tournament_ids)
    if progress is not None:
        progress.begin_phase("Fetching tournaments", tournament_count, "tournaments")
    for tournament_number, tournament in enumerate(run_tournament_ids, start=1):
        groups = groups_by_tournament[tournament]
        if not groups:
            raise ValueError(f"tournament {tournament} has no group competing in the JGP standings")
        tournament_name = groups[0]['tournament_name']
        if progress is not None:
            progress.start(f"Fetching tournament {tournament_number}/{tournament_count}: {tournament_name}")
        results = fetch_group_details(
            fetch, groups, tournament_name,
            ssf_club_ids, stsf_player_ids, player_list, walkovers, no_matches_played
        )
        local_list = filter_non_local_players(results, tournament_name, player_list, rejected_players)
        penalties = configuration.penalty_dict.get(tournament) or []
        policy = policy_for(tournament)
        if policy:
            policy.validate_season(run_input.effective_year)
            policy.reject_duplicates(results)
            wrong_group_players = policy.validate_players(local_list, configuration.dispensation_dict, penalties)
        else:
            wrong_group_players = validate_players(local_list, groups[0]['year'], groups[0]['month'],
                                                   configuration.dispensation_dict, penalties,
                                                   tournament in jdm_tournament_ids, tournament_name)
        if wrong_group_players:
            penalized_players[tournament] = wrong_group_players

        tournaments.append(TournamentEntries(
            tournament_id=tournament,
            name=tournament_name,
            date=groups[0]['date'],
            starters=len(results),
            entries=tuple(remove_duplicates_by_secpoints(local_list)),
        ))
        if progress is not None:
            progress.complete()

    diagnostics = GenerationDiagnostics(
        rejected_players=tuple(rejected_players),
        walkovers=walkovers,
        no_matches_played=no_matches_played,
        penalized_players=penalized_players,
    )
    return run_input, tuple(tournaments), player_list, diagnostics
