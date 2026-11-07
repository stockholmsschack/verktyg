"""Single-tournament reports and klass-name validation using Tyresö JGP 2026 (6544).

The 2026 fixtures supply standings groups; a synthetic table supplies klass E.
"""
import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from jgp_standings import cli
from jgp_standings.upstream_client import UpstreamResponseError

ROOT = Path(__file__).resolve().parent.parent
STATE_DIRECTORY = ROOT / "state"
TOURNAMENT_URL = "https://member.schack.se/public/api/v1/tournament/tournament/id/{}"
TABLE_URL = "https://member.schack.se/public/api/v1/tournamentresults/table/id/{}"
KLASS_E_GROUP = 18082


def _registration(contender_id, first_name, last_name, birth_year):
    return {
        "contenderId": contender_id,
        "playerInfo": {"id": contender_id, "firstName": first_name, "lastName": last_name,
                       "birthdate": str(birth_year), "sex": 0, "club": "Testklubben"},
    }


@pytest.fixture
def fixtures(offline_run, monkeypatch):
    """A copy of `fixtures/2026` with a stand-in table for 6544's klass E group, to edit per test."""
    directory = offline_run / "fixtures"
    shutil.copytree(ROOT / "fixtures" / "2026", directory)
    responses_file = directory / "responses.json"
    responses = json.loads(responses_file.read_text())
    responses[TABLE_URL.format(KLASS_E_GROUP)] = [
        _registration(900002, "Ella", "Berg", 2017),
        _registration(900001, "Adam", "Öst", 2016),
        _registration(900003, "Alva", "Ek", 2017),
    ]
    responses_file.write_text(json.dumps(responses, ensure_ascii=False))
    monkeypatch.setenv("JGP_FIXTURES_DIR", str(directory))

    def edit(change):
        edited = json.loads(responses_file.read_text())
        change(edited)
        responses_file.write_text(json.dumps(edited, ensure_ascii=False))

    return edit


def _check(*arguments):
    cli.app(["--state-dir", str(STATE_DIRECTORY), "check-tournament", *arguments], standalone_mode=False)


class TestTheReport:

    def test_players_in_the_wrong_klass_are_reported_and_the_check_succeeds(self, fixtures, offline_run, capsys):
        _check("6544")

        out = capsys.readouterr().out
        assert "Tournament: Tyresö JGP 2026\n" in out
        assert "Found 5 groups to validate\n" in out
        assert ("  ℹ️  Player has dispensation: Samuel Najafi (born 2016, dispensation allows 2015) "
                "⚠️  dispensation year 2015 not valid for klass D\n") in out
        assert "    - Christopher Björkgren (born 2015, should be 2016-2026)\n" in out
        assert "❌ Found 2 players in incorrect klasses:\n" in out
        assert "Warning: No validation rules for klass 'e'\n" in out

    def test_the_player_information_is_written_to_the_working_directory(self, fixtures, offline_run, capsys):
        _check("6544")

        written = offline_run / "cwd" / "6544_playerinfo.json"
        assert "Player info saved to 6544_playerinfo.json" in capsys.readouterr().out
        players = json.loads(written.read_text())
        assert players["900001"] == {"name": "Adam Öst", "club": "Testklubben", "birthYear": 2016, "gender": 0}
        assert list((offline_run / "cwd").iterdir()) == [written]

    def test_print_names_lists_the_d_and_e_groups_by_birth_year_then_name(self, fixtures, capsys):
        _check("6544", "--print-names")

        out = capsys.readouterr().out
        assert "Klass D — 34 players\n" in out
        names = out.split("Klass E — 3 players\n", 1)[1]
        assert names == ("========================================\n"
                         "\n  Born 2016:\n    Adam Öst\n"
                         "\n  Born 2017:\n    Alva Ek\n    Ella Berg\n")

    def test_without_print_names_no_lists_are_printed(self, fixtures, capsys):
        _check("6544")
        assert "Klass E — " not in capsys.readouterr().out


class TestRefusals:

    def test_a_refused_group_name_lists_every_refused_group_and_exits_one(self, fixtures, offline_run, capsys):
        def rename(responses):
            groups = responses[TOURNAMENT_URL.format(6544)]["rootClasses"][0]["groups"]
            groups[1]["name"] = "Klass Junior"
            groups[2]["name"] = "Öppen grupp"

        fixtures(rename)

        with pytest.raises(SystemExit) as exited:
            _check("6544")

        assert exited.value.code == 1
        stderr = capsys.readouterr().err
        assert "2 group names do not follow the klass naming rules in KLASS_NAMING.md." in stderr
        assert "Tyresö JGP 2026 (6544), group 'Klass Junior' (id 18079)" in stderr
        assert "Tyresö JGP 2026 (6544), group 'Öppen grupp' (id 18080)" in stderr
        assert list((offline_run / "cwd").iterdir()) == []

    def test_a_tournament_that_is_not_a_jgp_is_refused(self, fixtures, capsys):
        with pytest.raises(SystemExit) as exited:
            _check("6853")

        assert exited.value.code == 1
        assert capsys.readouterr().err == (
            "Error: Tournament: Junior-DM i Snabbschack 2026 is not a JGP tournament. Halting.\n"
        )

    def test_an_upstream_failure_is_reported_and_exits_one(self, offline_run, monkeypatch, capsys):
        def fail(url, stage):
            raise UpstreamResponseError(stage, "HTTP 404 (not retried)", status_code=404)

        monkeypatch.setattr(cli, "standalone_fetch", lambda: fail)

        with pytest.raises(SystemExit) as exited:
            _check("1234")

        assert exited.value.code == 1
        assert capsys.readouterr().err == (
            "Error: upstream failure while fetching tournament 1234: HTTP 404 (not retried)\n"
        )

    @pytest.mark.parametrize("tournament_id", ["abc", "0", "-6544", "6544,6540"])
    def test_a_malformed_tournament_id_is_refused_by_the_parser(self, tournament_id):
        result = CliRunner().invoke(cli.app, ["check-tournament", "--", tournament_id])
        assert result.exit_code == 2
        assert "tournament_id" in result.output

    def test_a_tournament_id_is_required(self):
        result = CliRunner().invoke(cli.app, ["check-tournament"])
        assert result.exit_code == 2
        assert "Missing argument" in result.output
