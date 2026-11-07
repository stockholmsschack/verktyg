"""Dispensation lookup for single entries and multiline input.

Each entry requires one matching name and birth year. Players born in the
oldest band are ineligible. Player searches use local fixtures.
"""
from datetime import date
import io
import json
from urllib.parse import quote

import pytest
from typer.testing import CliRunner

from jgp_standings import cli
from jgp_standings.dispensation_entry import DispensationRefused, DispensationRequest, parse_line
from jgp_standings.upstream_client import UpstreamRetryExhausted

SEARCH_URL = "https://member.schack.se/public/api/v1/player/fornamn/{}/efternamn/{}"


def _player(player_id, first_name, last_name, birth_year, club="Stockholms Schacksällskap"):
    return {"id": player_id, "firstName": first_name, "lastName": last_name, "birthdate": str(birth_year),
            "sex": 0, "fideid": 0, "country": "Sverige", "club": club, "clubId": 1, "elo": {}, "lask": None}


SEARCHES = {
    ("Anna", "Svensson"): [_player(546393, "Anna", "Svensson", 2010)],
    ("Lars", "Åberg von Platen"): [_player(726286, "Lars", "Åberg von Platen", 2013, "SK Rockaden")],
    ("Erik", "Johansson"): [_player(1, "Erik", "Johansson", 2011), _player(2, "Erik", "Johansson", 2012)],
    ("Nils", "Okänd"): [],
    ("Maja", "Lind"): [_player(541890, "Maja", "Lind", 2012)],
    ("Olle", "Äldre"): [_player(222, "Olle", "Äldre", 2009)],
}

ANNA = ('# Anna Svensson, born 2010, Stockholms Schacksällskap\n'
        '"546393" = 2009\n')
LARS = ('# Lars Åberg von Platen, born 2013, SK Rockaden\n'
        '"726286" = 2012\n')


@pytest.fixture
def searches(offline_run, monkeypatch):
    """Replay the player searches above; the working directory has no configuration in it."""
    directory = offline_run / "fixtures"
    directory.mkdir()
    responses = {SEARCH_URL.format(quote(first, safe=""), quote(last, safe="")): found
                 for (first, last), found in SEARCHES.items()}
    (directory / "responses.json").write_text(json.dumps(responses, ensure_ascii=False))
    monkeypatch.setenv("JGP_FIXTURES_DIR", str(directory))
    # The season is 2026, whose oldest band is 2006-2009.
    monkeypatch.setattr(cli, "stockholm_today", lambda clock: date(2026, 10, 4))
    return offline_run


def _exit_code(arguments):
    try:
        cli.app(["dispensation", *arguments], standalone_mode=False)
    except SystemExit as exited:
        return exited.code
    return 0


class TestSingleEntry:

    def test_a_verified_entry_prints_the_lines_to_paste_and_reads_no_configuration(self, searches, capsys):
        assert _exit_code(["Anna", "Svensson", "2010", "2009"]) == 0
        assert capsys.readouterr() == (ANNA, "")
        assert list((searches / "cwd").iterdir()) == []

    def test_a_multi_word_last_name_is_one_entry(self, searches, capsys):
        assert _exit_code(["Lars", "Åberg", "von", "Platen", "2013", "2012"]) == 0
        assert capsys.readouterr().out == LARS

    def test_a_quoted_name_is_the_same_entry(self, searches, capsys):
        assert _exit_code(["Lars Åberg von Platen", "2013", "2012"]) == 0
        assert capsys.readouterr().out == LARS

    @pytest.mark.parametrize("entry, reason", [
        (["Anna", "Svensson", "2011", "2009"], "birth year mismatch: entered 2011, upstream 2010"),
        (["Erik", "Johansson", "2011", "2010"], "expected exactly 1 player of that name upstream, found 2"),
        (["Nils", "Okänd", "2014", "2013"], "expected exactly 1 player of that name upstream, found 0"),
        (["Olle", "Äldre", "2009", "2008"],
         "born 2009, in or before the oldest band, which ends 2009: no dispensation applies"),
        (["Anna", "2010", "2009"], "a first name and a last name are needed before the years"),
        (["Anna", "Svensson", "2010"], "expected a birth year and a dispensation year after the name, got '2010'"),
    ])
    def test_an_unverifiable_entry_is_refused_with_its_reason(self, searches, capsys, entry, reason):
        assert _exit_code(entry) == 1
        out, err = capsys.readouterr()
        assert out == ""
        assert err == (f"1 of 1 entries refused; nothing was printed for them:\n"
                       f"  - line 1: {' '.join(entry)!r}: {reason}\n")


class TestBulkEntry:

    LIST = (
        "# dispensations agreed at the March meeting\n"
        "Anna Svensson 2010 2009\n"
        "\n"
        "Erik Johansson 2011 2010\n"
        "Lars Åberg von Platen 2013 2012\n"
        "Maja Lind 2013 2011\n"
        "Anna Svensson 2010 2008\n"
        "Nils\n"
    )

    def _assert_the_list_was_resolved(self, capsys):
        out, err = capsys.readouterr()
        assert out == ANNA + LARS
        assert err == (
            "4 of 6 entries refused; nothing was printed for them:\n"
            "  - line 4: 'Erik Johansson 2011 2010': expected exactly 1 player of that name upstream, found 2\n"
            "  - line 6: 'Maja Lind 2013 2011': birth year mismatch: entered 2013, upstream 2012\n"
            "  - line 7: 'Anna Svensson 2010 2008': player 546393 was already resolved on line 2\n"
            "  - line 8: 'Nils': no birth year and dispensation year found\n"
        )

    def test_a_file_runs_every_line_through_the_same_resolution(self, searches, capsys):
        entries = searches / "dispens.txt"
        entries.write_text(self.LIST)

        assert _exit_code(["--file", str(entries)]) == 1
        self._assert_the_list_was_resolved(capsys)

    def test_a_pasted_list_is_read_from_standard_input(self, searches, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin", io.StringIO(self.LIST))

        assert _exit_code(["--file", "-"]) == 1
        self._assert_the_list_was_resolved(capsys)

    def test_a_list_that_verifies_completely_exits_zero(self, searches, capsys):
        entries = searches / "dispens.txt"
        entries.write_text("Anna Svensson 2010 2009\nLars Åberg von Platen 2013 2012\n")

        assert _exit_code(["--file", str(entries)]) == 0
        assert capsys.readouterr() == (ANNA + LARS, "")

    def test_a_missing_file_names_its_path(self, searches, capsys):
        assert _exit_code(["--file", "missing.txt"]) == 1
        assert capsys.readouterr().err.startswith("Could not read missing.txt: ")

    def test_an_upstream_failure_refuses_only_its_line(self, searches, monkeypatch, capsys):
        entries = searches / "dispens.txt"
        entries.write_text("Okänd Spelare 2010 2009\nAnna Svensson 2010 2009\n")

        def fail(url, stage):
            if "Spelare" in url:
                raise UpstreamRetryExhausted(stage, "HTTP 503; exhausted 3 attempts")
            return [_player(546393, "Anna", "Svensson", 2010)]

        monkeypatch.setattr(cli, "standalone_fetch", lambda: fail)

        assert _exit_code(["--file", str(entries)]) == 1
        out, err = capsys.readouterr()
        assert out == ANNA
        assert ("  - line 1: 'Okänd Spelare 2010 2009': upstream failure while searching "
                "players by name: HTTP 503; exhausted 3 attempts\n") in err


class TestTheCommandLine:

    @pytest.mark.parametrize("arguments", [[], ["Anna", "Svensson", "2010", "2009", "--file", "dispens.txt"]])
    def test_exactly_one_of_an_entry_and_a_file_is_required(self, arguments):
        result = CliRunner().invoke(cli.app, ["dispensation", *arguments])
        assert result.exit_code == 2
        assert "either one entry or --file" in result.output


class TestParseLine:

    def test_the_first_name_is_one_word_and_the_last_name_is_the_rest(self):
        assert parse_line("Lars Åberg von Platen 2013 2012") == DispensationRequest("Lars", "Åberg von Platen", 2013, 2012)

    @pytest.mark.parametrize("line, reason", [
        ("Anna Svensson", "no birth year and dispensation year found"),
        ("2010 2009", "a first name and a last name are needed before the years"),
        ("Anna Svensson 2010 2009 2008", "expected a birth year and a dispensation year after the name, got '2010 2009 2008'"),
        ("Anna Svensson 2010 x", "expected a birth year and a dispensation year after the name, got '2010 x'"),
        ("Anna Svensson 210 2009", "'210' is not a four-digit year"),
        ("Anna Svensson ２０１０ 2009", "expected a birth year and a dispensation year after the name, got '2009'"),
    ])
    def test_a_malformed_line_is_refused_with_its_reason(self, line, reason):
        with pytest.raises(DispensationRefused) as refused:
            parse_line(line)
        assert str(refused.value) == reason
