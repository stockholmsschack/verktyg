"""Command-line entry points for validation, generation and dispensation lookup.

    uv run jgp validate
    uv run jgp generate [--year YEAR --tournaments IDS]
    uv run jgp check-tournament TOURNAMENT_ID [--print-names]
    uv run jgp dispensation NAME BIRTH_YEAR DISPENSATION_YEAR | --file PATH

`--state-dir` and `--output-dir` precede the command and default to `state/`
and `output/`. Command failures exit with status 1; invalid arguments exit with
status 2. Generation
reports stages on stderr and prints the PDF path on success. Dispensation
lookup prints verified configuration entries without reading the configuration.
"""
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from .active_configuration import (
    ActiveConfiguration,
    ConfigurationValidationError,
    configuration_path,
    load_configuration,
)
from .artifact_publication import generate_and_publish
from .dispensation_entry import resolve_lines
from .generation_orchestrator import GenerationStageError, KlassNamingError
from .tournament_calendar import stockholm_today
from .tournament_check import TournamentCheckError, check_tournament
from .upstream_client import MemberSystemClient, UpstreamError, standalone_fetch

STATE_DIRECTORY = Path("state")
OUTPUT_DIRECTORY = Path("output")


class ConsoleProgress:
    """Report generation stages on standard error."""

    def begin_phase(self, description: str, total: int, unit: str) -> None:
        print(f"{description} ({total} {unit})", file=sys.stderr)

    def start(self, operation: str) -> None:
        print(f"{operation}...", file=sys.stderr)

    def complete(self) -> None:
        pass


@dataclass(frozen=True)
class WorkingDirectories:
    """Filesystem locations shared by CLI commands."""

    state: Path
    output: Path


app = typer.Typer(
    help="The Stockholm Junior Grand Prix standings.",
    add_completion=False,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
)


@app.callback()
def configure(
    context: typer.Context,
    state_dir: Annotated[
        Path, typer.Option("--state-dir", help="Where the configuration and the run record are kept.")
    ] = STATE_DIRECTORY,
    output_dir: Annotated[
        Path, typer.Option("--output-dir", help="Where runs and the public snapshot are written.")
    ] = OUTPUT_DIRECTORY,
) -> None:
    context.obj = WorkingDirectories(state_dir, output_dir)


@app.command()
def validate(context: typer.Context) -> None:
    """Check the configuration without fetching or writing anything."""
    directories = context.obj
    configuration = _load_configuration(directories.state)
    path = configuration_path(directories.state)
    print(f"The configuration {path} is valid: "
          f"{len(configuration.tournament_ids)} tournaments, "
          f"{len(configuration.dispensation_dict)} dispensations, "
          f"{sum(map(len, configuration.penalty_dict.values()))} penalties.")


@app.command()
def generate(
    context: typer.Context,
    year: Annotated[int | None, typer.Option(help="The season to run (requires --tournaments).")] = None,
    tournaments: Annotated[
        str | None,
        typer.Option(help="Comma-separated tournament ids instead of the configured list (requires --year)."),
    ] = None,
) -> None:
    """Fetch, calculate, render and publish the standings.

    A default run refuses a stale, mixed or future configuration; an earlier or
    later season needs --year and --tournaments together.
    """
    if (year is None) != (tournaments is None):
        raise typer.BadParameter('--year and --tournaments must be supplied together')
    tournament_ids = _tournament_ids(tournaments) if tournaments is not None else None
    directories = context.obj
    configuration = _load_configuration(directories.state)
    try:
        published = generate_and_publish(
            configuration,
            directories.output,
            fetch=standalone_fetch(),
            state_directory=directories.state,
            today=date.today(),
            override_year=year,
            override_tournament_ids=tournament_ids,
            progress=ConsoleProgress(),
        )
    except KlassNamingError as error:
        _fail(error.report())
    except GenerationStageError as error:
        _fail(f"The {error}.\nNothing was published; the earlier standings are unchanged.")
    print(published.pdf_path.resolve())


@app.command(name="check-tournament")
def check(
    context: typer.Context,
    tournament_id: Annotated[
        str, typer.Argument(help="The tournament's id in the member system.", metavar="tournament_id")
    ],
    print_names: Annotated[
        bool, typer.Option("--print-names", help="Also list D, E and F players by birth year.")
    ] = False,
) -> None:
    """Check one tournament's klass assignments.

    Report players whose birth or dispensation year does not allow their klass,
    and write <tournament id>_playerinfo.json to the working directory.
    A group name that does not follow KLASS_NAMING.md refuses the check.
    """
    identifier = _tournament_id(tournament_id, param_hint="tournament_id")
    configuration = _load_configuration(context.obj.state)
    try:
        check_tournament(identifier, dict(configuration.dispensation_dict),
                         fetch=standalone_fetch(), print_names=print_names)
    except KlassNamingError as error:
        _fail(error.report())
    except (TournamentCheckError, UpstreamError) as error:
        _fail(f"Error: {error}")


@app.command()
def dispensation(
    entry: Annotated[
        list[str] | None,
        typer.Argument(help="One entry: first name, last name, birth year and dispensation year.",
                       metavar="NAME BIRTH_YEAR DISPENSATION_YEAR"),
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option("--file", help="A file of entries, one per line, or '-' for standard input."),
    ] = None,
) -> None:
    """Verify dispensations and print configuration entries.

    Each entry needs exactly one matching upstream player whose birth year
    agrees. Refused entries name the line and reason.
    """
    if bool(entry) == (file is not None):
        raise typer.BadParameter('dispensation takes either one entry or --file, not both or neither')
    _enter_dispensations([' '.join(entry)] if entry else _read_entries(file))


def _load_configuration(state_directory: Path) -> ActiveConfiguration:
    try:
        return load_configuration(state_directory)
    except ConfigurationValidationError as error:
        _fail(f"The configuration {configuration_path(state_directory)} is not valid:", error.problems)


def _enter_dispensations(lines: Iterable[str]) -> None:
    """Print every verified entry to standard output and every refusal to standard error."""
    search = MemberSystemClient(fetch=standalone_fetch()).player_name_search
    verified, refused = resolve_lines(lines, stockholm_today(lambda: datetime.now(UTC)).year, search)
    for dispensation in verified:
        print(dispensation.as_configuration_lines())
    if refused:
        _fail(f"{len(refused)} of {len(verified) + len(refused)} entries refused; nothing was printed for them:",
              refused)


def _read_entries(path: Path) -> list[str]:
    if str(path) == '-':
        return sys.stdin.read().splitlines()
    try:
        return path.read_text(encoding='utf-8').splitlines()
    except OSError as error:
        _fail(f"Could not read {path}: {error.strerror}")


def _tournament_id(value: str, *, param_hint: str) -> int:
    value = value.strip()
    if not (value.isascii() and value.isdigit()) or int(value) == 0:
        raise typer.BadParameter(f"{value!r} is not a tournament id", param_hint=param_hint)
    return int(value)


def _tournament_ids(value: str) -> list[int]:
    ids = [_tournament_id(part, param_hint='--tournaments') for part in value.split(',')]
    if len(set(ids)) != len(ids):
        raise typer.BadParameter("a tournament id is given twice", param_hint="--tournaments")
    return ids


def _fail(message: str, problems=()) -> NoReturn:
    print(message, *(f"  - {problem}" for problem in problems), sep='\n', file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    """Run the packaged command-line application."""
    app(prog_name="jgp")


if __name__ == '__main__':
    main()
