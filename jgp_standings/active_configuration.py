"""Validate and persist `state/active-configuration.toml`.

Loading returns a frozen `ActiveConfiguration`. Saving validates the whole
configuration, checks its revision and atomically replaces the file with the
next revision. Saving rewrites the TOML and discards comments.

Operators edit `[penalty_dict]` by hand. These edits leave the revision unchanged
and survive saves because each save updates one field of the configuration
read from disk.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
import tomllib

from .atomic_write import atomic_replace_text

ACTIVE_CONFIGURATION_FILENAME = "active-configuration.toml"
DEFAULT_CONTRIBUTOR_NAME = "Stockholms Schackförbunds Ungdomsutskott"
DEFAULT_CONTRIBUTOR_EMAIL = "uu@stockholmsschack.se"


class ConfigurationValidationError(ValueError):
    """Raised with every problem found in an active configuration, not just the first."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(self.problems))


class StaleConfigurationError(RuntimeError):
    """Raised when a save is based on an obsolete configuration revision."""

    def __init__(self, message: str, *, current_revision: int | None = None) -> None:
        self.current_revision = current_revision
        super().__init__(message)


@dataclass(frozen=True)
class DispensationPlayer:
    """The player a dispensation was verified against, with the club they played for at the time."""
    name: str
    birth_year: int
    club: str


@dataclass(frozen=True)
class ActiveConfiguration:
    """
    `dispensation_dict` maps a contender id to the birth year the player competes as,
    `dispensation_players` maps the same ids to the verified player behind each entry, and
    `penalty_dict` maps a tournament id to the contender ids penalised in it. All three are keyed by
    `int`, as the calculations look them up. `club_display_names` maps a canonical club name to the
    shortened name the document's standings tables show. `finals_separators` maps an age-band label
    to the rows of that band's standings table a bold line is drawn after.
    """
    tournament_ids: tuple[int, ...]
    contributor: Mapping[str, str]
    landscape: bool = False
    dispensation_dict: Mapping[int, int] = field(default_factory=dict)
    dispensation_players: Mapping[int, DispensationPlayer] = field(default_factory=dict)
    penalty_dict: Mapping[int, tuple[int, ...]] = field(default_factory=dict)
    club_display_names: Mapping[str, str] = field(default_factory=dict)
    finals_separators: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    revision: int = 0


def configuration_path(state_directory: Path) -> Path:
    """Return the sole active-configuration path for a state directory."""
    return Path(state_directory) / ACTIVE_CONFIGURATION_FILENAME


def load_configuration(state_directory: Path) -> ActiveConfiguration:
    """Load and validate the active configuration, reporting every problem at once."""
    path = configuration_path(state_directory)
    try:
        with path.open("rb") as config_file:
            data = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationValidationError((f"could not read active configuration: {error}",)) from error

    configuration, structural_problems = _configuration_from_document(data)
    problems = [*structural_problems, *_configuration_problems(configuration)]
    if problems:
        raise ConfigurationValidationError(problems)
    return configuration


def save_configuration(
    state_directory: Path,
    configuration: ActiveConfiguration,
    *,
    expected_revision: int | None = None,
) -> ActiveConfiguration:
    """
    Validate and atomically replace the active configuration. The configuration's own revision is
    the optimistic-lock revision unless an explicit expected revision is supplied.
    """
    validate_active_configuration(configuration)
    path = configuration_path(state_directory)
    expected = configuration.revision if expected_revision is None else expected_revision
    current_revision = _current_revision(path)
    if expected != current_revision:
        raise StaleConfigurationError(
            f"configuration revision {expected} is stale; current revision is {current_revision}",
            current_revision=current_revision,
        )

    saved = replace(configuration, revision=current_revision + 1)
    atomic_replace_text(path, _configuration_document(saved), replace=os.replace)
    return saved


def validate_active_configuration(configuration: ActiveConfiguration) -> None:
    """Report all active-configuration problems in one exception."""
    problems = _configuration_problems(configuration)
    if problems:
        raise ConfigurationValidationError(problems)


def _configuration_problems(configuration: ActiveConfiguration) -> list[str]:
    problems: list[str] = []

    tournament_ids = configuration.tournament_ids
    if not tournament_ids:
        problems.append("at least one tournament id is required")
    if not all(_is_positive_int(tournament_id) for tournament_id in tournament_ids):
        problems.append("tournament ids must be positive integers")
    elif len(set(tournament_ids)) != len(tournament_ids):
        problems.append("tournament ids must be unique")

    if not isinstance(configuration.landscape, bool):
        problems.append("landscape must be true or false")

    if not isinstance(configuration.contributor, Mapping):
        problems.append("contributor must be a table")
    else:
        for field_name in ("name", "email"):
            value = configuration.contributor.get(field_name)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"contributor {field_name} is required")

    if not isinstance(configuration.club_display_names, Mapping):
        problems.append("club_display_names must be a table")
    else:
        for canonical_name, display_name in configuration.club_display_names.items():
            if not isinstance(canonical_name, str) or not canonical_name.strip():
                problems.append("club display aliases require a canonical club name")
            if not isinstance(display_name, str) or not display_name.strip():
                problems.append(f"club display alias {canonical_name!r} requires a display name")

    if not isinstance(configuration.finals_separators, Mapping):
        problems.append("finals_separators must be a table")
    else:
        for label, rows in configuration.finals_separators.items():
            if not isinstance(label, str) or not label.strip():
                problems.append("finals separators require an age-band label")
            elif (
                not isinstance(rows, tuple)
                or not rows
                or not all(_is_positive_int(row) for row in rows)
                or len(set(rows)) != len(rows)
            ):
                problems.append(f"finals separators for {label!r} must be distinct positive row numbers")

    for player_id, year in configuration.dispensation_dict.items():
        if not _is_positive_int(player_id):
            problems.append(f"dispensation player id {player_id!r} must be a positive integer")
        elif not _is_positive_int(year):
            problems.append(f"dispensation for {player_id} must be a birth year, not {year!r}")
        elif player_id not in configuration.dispensation_players:
            problems.append(f"dispensation for {player_id} has no verified player")
    for player_id, player in configuration.dispensation_players.items():
        if player_id not in configuration.dispensation_dict:
            problems.append(f"dispensation player {player_id!r} has no dispensation")
        elif (
            not isinstance(player, DispensationPlayer)
            or not isinstance(player.name, str)
            or not player.name.strip()
            or not _is_positive_int(player.birth_year)
            or not isinstance(player.club, str)
        ):
            problems.append(f"dispensation player {player_id} needs a name, a birth year and a club")

    for tournament_id, player_ids in configuration.penalty_dict.items():
        if not _is_positive_int(tournament_id):
            problems.append(f"penalty tournament id {tournament_id!r} must be a positive integer")
        elif not isinstance(player_ids, tuple) or not all(_is_positive_int(p) for p in player_ids):
            problems.append(f"penalties for tournament {tournament_id} must be a list of player ids")

    if not isinstance(configuration.revision, int) or configuration.revision < 0:
        problems.append("revision must be a non-negative integer")
    return problems


def _configuration_from_document(data: object) -> tuple[ActiveConfiguration, list[str]]:
    """Read configuration fields, converting table keys to integer ids.

    Report unreadable keys and values and omit them from the result.
    """
    problems: list[str] = []
    if not isinstance(data, dict):
        data = {}
        problems.append("active configuration must be a TOML document")

    allowed_keys = {
        "revision", "tournament_ids", "landscape", "contributor", "club_display_names", "finals_separators",
        "dispensation_dict", "dispensation_players", "penalty_dict",
    }
    unknown_keys = sorted(set(data) - allowed_keys)
    if unknown_keys:
        problems.append("unknown active configuration fields: " + ", ".join(unknown_keys))

    revision = data.get("revision")
    if revision is None:
        problems.append("stored configuration revision is required")
        revision = 0
    elif not _is_positive_int(revision):
        problems.append("stored configuration revision must be a positive integer")
        revision = 0

    tournament_ids = data.get("tournament_ids", [])
    if not isinstance(tournament_ids, list):
        problems.append("tournament_ids must be an array")
        tournament_ids = []

    contributor = data.get("contributor", {})
    if isinstance(contributor, dict):
        unknown_contributor_keys = sorted(set(contributor) - {"name", "email"})
        if unknown_contributor_keys:
            problems.append("unknown contributor fields: " + ", ".join(unknown_contributor_keys))

    finals_separators = {}
    for label, rows in _table(data, "finals_separators", problems).items():
        if not isinstance(rows, list):
            problems.append(f"finals separators for {label!r} must be a list of row numbers")
        else:
            finals_separators[label] = tuple(rows)

    dispensations = {}
    for player_id, year in _table(data, "dispensation_dict", problems).items():
        if not _is_id_key(player_id):
            problems.append(f"dispensation player id {player_id!r} must be a positive integer")
        else:
            dispensations[int(player_id)] = year

    dispensation_players = {}
    for player_id, player in _table(data, "dispensation_players", problems).items():
        if not _is_id_key(player_id):
            problems.append(f"dispensation player id {player_id!r} must be a positive integer")
        elif not isinstance(player, dict) or set(player) != {"name", "birth_year", "club"}:
            problems.append(f"dispensation player {player_id} needs exactly a name, a birth year and a club")
        else:
            dispensation_players[int(player_id)] = DispensationPlayer(**player)

    penalties = {}
    for tournament_id, player_ids in _table(data, "penalty_dict", problems).items():
        if not _is_id_key(tournament_id):
            problems.append(f"penalty tournament id {tournament_id!r} must be a positive integer")
        elif not isinstance(player_ids, list):
            problems.append(f"penalties for tournament {tournament_id} must be a list of player ids")
        else:
            penalties[int(tournament_id)] = tuple(player_ids)

    return (
        ActiveConfiguration(
            tournament_ids=tuple(tournament_ids),
            contributor=contributor,
            landscape=data.get("landscape", False),
            dispensation_dict=dispensations,
            dispensation_players=dispensation_players,
            penalty_dict=penalties,
            club_display_names=data.get("club_display_names", {}),
            finals_separators=finals_separators,
            revision=revision,
        ),
        problems,
    )


def _configuration_document(configuration: ActiveConfiguration) -> str:
    lines = [
        f"revision = {configuration.revision}",
        "tournament_ids = [" + ", ".join(str(value) for value in configuration.tournament_ids) + "]",
        f"landscape = {'true' if configuration.landscape else 'false'}",
        "",
        "[contributor]",
        f"name = {_toml_string(configuration.contributor['name'])}",
        f"email = {_toml_string(configuration.contributor['email'])}",
        "",
        "[club_display_names]",
    ]
    lines.extend(
        f"{_toml_string(canonical)} = {_toml_string(display)}"
        for canonical, display in sorted(configuration.club_display_names.items())
    )
    lines.extend(["", "[finals_separators]"])
    lines.extend(
        f"{_toml_string(label)} = [" + ", ".join(str(row) for row in rows) + "]"
        for label, rows in sorted(configuration.finals_separators.items())
    )
    lines.extend(["", "[dispensation_dict]"])
    lines.extend(
        f'"{player_id}" = {year}' for player_id, year in sorted(configuration.dispensation_dict.items())
    )
    for player_id, player in sorted(configuration.dispensation_players.items()):
        lines.extend([
            "",
            f'[dispensation_players."{player_id}"]',
            f"name = {_toml_string(player.name)}",
            f"birth_year = {player.birth_year}",
            f"club = {_toml_string(player.club)}",
        ])
    lines.extend(["", "[penalty_dict]"])
    lines.extend(
        f'"{tournament_id}" = [' + ", ".join(str(player_id) for player_id in player_ids) + "]"
        for tournament_id, player_ids in sorted(configuration.penalty_dict.items())
    )
    return "\n".join(lines) + "\n"


def _current_revision(path: Path) -> int:
    if not path.exists():
        return 0
    return load_configuration(path.parent).revision


def _table(data: Mapping, key: str, problems: list[str]) -> Mapping:
    value = data.get(key, {})
    if not isinstance(value, Mapping):
        problems.append(f"{key} must be a table")
        return {}
    return value


def _is_positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_id_key(key: str) -> bool:
    return key.isascii() and key.isdigit() and int(key) > 0


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)
