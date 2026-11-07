"""CLI validation, generation and paired year/tournament overrides.

Generation replays the 2026 fixtures and must reproduce the accepted baseline.
The check-tournament and dispensation commands have separate test modules.
"""
import subprocess
import sys
from datetime import date
from importlib.metadata import entry_points
from pathlib import Path

import pytest
from typer.testing import CliRunner

from jgp_standings import cli
from jgp_standings.artifact_publication import TYPST_SOURCE_FILE
from jgp_standings.generation_orchestrator import GenerationStageError

from compare_typst import compare_typst

ROOT = Path(__file__).resolve().parent.parent
CONFIGURATION = ROOT / "state" / "active-configuration.toml"
GOLDEN_TYPST = ROOT / "reference-artifacts" / "2026-standings-golden.typ"


def _write_configuration(working_directory, document):
    """Write `document` as the configuration the command finds by default, in `state/`."""
    state = working_directory / "state"
    state.mkdir()
    (state / "active-configuration.toml").write_text(document)


class TestThePackage:

    def test_jgp_is_installed_as_a_console_script(self):
        (script,) = entry_points(group="console_scripts", name="jgp")
        assert script.value == "jgp_standings.cli:main"

    def test_the_installed_command_runs_without_network_or_credentials(self, offline_run):
        completed = subprocess.run(
            [Path(sys.executable).parent / "jgp", "--help"], capture_output=True, text=True, check=False,
        )
        assert completed.returncode == 0
        for command in ("validate", "generate", "check-tournament", "dispensation"):
            assert command in completed.stdout


class TestCommandLine:

    @pytest.mark.parametrize("arguments, message", [
        ([], "Missing command"),
        (["generate", "--season", "2025"], "No such option"),
        (["--config=jgp_config.toml", "validate"], "No such option"),
        (["generate", "--year", "2025"], "together"),
        (["generate", "--tournaments", "5429,5475"], "together"),
    ])
    def test_invalid_arguments_fail_before_configuration_or_network(
        self, arguments, message, monkeypatch,
    ):
        monkeypatch.setattr(cli, "load_configuration", pytest.fail)
        monkeypatch.setattr(cli, "standalone_fetch", pytest.fail)
        result = CliRunner().invoke(cli.app, arguments)
        assert result.exit_code == 2
        assert message in result.output

    @pytest.mark.parametrize("tournaments", [
        "5429,abc", "5429,,5475", "0", "-5429", "5429,5429", "", "１２３",
    ])
    def test_a_malformed_tournament_list_is_refused(self, tournaments, monkeypatch):
        monkeypatch.setattr(cli, "load_configuration", pytest.fail)
        result = CliRunner().invoke(
            cli.app, ["generate", "--year", "2025", "--tournaments", tournaments],
        )
        assert result.exit_code == 2
        assert "--tournaments" in result.output

    @pytest.mark.parametrize("arguments, year, tournaments", [
        ([], None, None),
        (["--year", "2025", "--tournaments", "5429, 5475"], 2025, [5429, 5475]),
    ])
    def test_generation_receives_defaults_and_parsed_overrides(
        self, arguments, year, tournaments, monkeypatch,
    ):
        from types import SimpleNamespace

        calls = []
        states = []

        def record(configuration, output, **kwargs):
            calls.append((output, kwargs))
            return SimpleNamespace(pdf_path=Path("standings.pdf"))

        monkeypatch.setattr(cli, "load_configuration", lambda state: states.append(state))
        monkeypatch.setattr(cli, "standalone_fetch", lambda: None)
        monkeypatch.setattr(cli, "generate_and_publish", record)
        result = CliRunner().invoke(cli.app, ["generate", *arguments])

        assert result.exit_code == 0, result.output
        assert states == [Path("state")]
        output, kwargs = calls[0]
        assert output == Path("output")
        assert kwargs["state_directory"] == Path("state")
        assert kwargs["override_year"] == year
        assert kwargs["override_tournament_ids"] == tournaments

    @pytest.mark.parametrize("command", ["validate", "generate", "check-tournament", "dispensation"])
    def test_command_help_needs_no_configuration_or_network(self, command, monkeypatch):
        monkeypatch.setattr(cli, "load_configuration", pytest.fail)
        monkeypatch.setattr(cli, "standalone_fetch", pytest.fail)
        result = CliRunner().invoke(cli.app, [command, "--help"])
        assert result.exit_code == 0
        assert "Usage:" in result.output


class TestValidate:

    def test_the_committed_configuration_is_valid_and_nothing_is_written(self, offline_run, capsys):
        cli.app(["--state-dir", str(CONFIGURATION.parent), "validate"], standalone_mode=False)

        assert capsys.readouterr().out == (
            f"The configuration {CONFIGURATION} is valid: 6 tournaments, 15 dispensations, 0 penalties.\n"
        )
        assert list(offline_run.rglob("*")) == [offline_run / "cwd"]

    def test_every_problem_is_listed_and_the_exit_status_is_one(self, offline_run, capsys):
        _write_configuration(
            offline_run / "cwd", 'revision = 1\ntournament_ids = [1, 1]\nlandscape = "yes"\nseason = 2025\n'
        )

        with pytest.raises(SystemExit) as exited:
            cli.app(["validate"], standalone_mode=False)

        assert exited.value.code == 1
        stderr = capsys.readouterr().err
        assert stderr.startswith("The configuration state/active-configuration.toml is not valid:\n")
        for problem in ("unknown active configuration fields: season", "tournament ids must be unique",
                        "landscape must be true or false", "contributor name is required"):
            assert f"  - {problem}\n" in stderr

    def test_a_missing_configuration_names_the_path_and_creates_nothing(self, offline_run, capsys):
        with pytest.raises(SystemExit) as exited:
            cli.app(["--state-dir", "missing", "validate"], standalone_mode=False)

        assert exited.value.code == 1
        stderr = capsys.readouterr().err
        assert stderr.startswith("The configuration missing/active-configuration.toml is not valid:\n")
        assert "could not read active configuration" in stderr
        assert list((offline_run / "cwd").iterdir()) == []


class _FixedDate(date):
    """The day the baseline was accepted, standing in for the clock the CLI reads."""

    @classmethod
    def today(cls):
        return date(2026, 8, 2)


class TestGenerate:

    def test_a_cli_generation_against_the_fixtures_reproduces_the_baseline(
        self, offline_run, state_directory, monkeypatch, capsys,
    ):
        monkeypatch.setattr(cli, "date", _FixedDate)

        cli.app([
            "--state-dir", str(state_directory),
            "--output-dir", str(offline_run / "output"),
            "generate",
        ], standalone_mode=False)

        out, err = capsys.readouterr()
        pdf = Path(out.removesuffix("\n"))
        assert "\n" not in out.removesuffix("\n")  # the stages go to standard error
        assert err.splitlines()[0] == "Fetching district players and clubs..."
        assert "Rendering PDF..." in err.splitlines()
        assert pdf.is_absolute() and pdf.is_file()
        assert pdf.parent.parent == (offline_run / "output" / "runs").resolve()
        compare_typst(GOLDEN_TYPST, pdf.parent / TYPST_SOURCE_FILE)
        assert list((offline_run / "cwd").iterdir()) == []

    def test_the_overrides_reach_the_generation(self, monkeypatch):
        calls = []

        def record(*_args, **kwargs):
            calls.append(kwargs)
            raise GenerationStageError("fetching", "stopped by the test")

        monkeypatch.setattr(cli, "generate_and_publish", record)

        with pytest.raises(SystemExit):
            cli.app(["--state-dir", str(CONFIGURATION.parent), "generate", "--year", "2025",
                          "--tournaments", "5475,5429"], standalone_mode=False)

        assert calls[0]["override_year"] == 2025
        assert calls[0]["override_tournament_ids"] == [5475, 5429]

    def test_a_failed_generation_names_its_stage_and_exits_with_status_one(self, monkeypatch, capsys):
        def fail(*_args, **_kwargs):
            raise GenerationStageError("rendering", "Typst exited with status 1: boom")

        monkeypatch.setattr(cli, "generate_and_publish", fail)

        with pytest.raises(SystemExit) as exited:
            cli.app(["--state-dir", str(CONFIGURATION.parent), "generate"], standalone_mode=False)

        assert exited.value.code == 1
        assert capsys.readouterr().err == (
            "The generation failed during rendering: Typst exited with status 1: boom.\n"
            "Nothing was published; the earlier standings are unchanged.\n"
        )

    def test_an_invalid_configuration_stops_before_any_generation(self, offline_run, monkeypatch):
        _write_configuration(offline_run / "cwd", "revision = 1\ntournament_ids = 6544\n")
        monkeypatch.setattr(cli, "generate_and_publish", pytest.fail)

        with pytest.raises(SystemExit) as exited:
            cli.app(["generate"], standalone_mode=False)

        assert exited.value.code == 1
