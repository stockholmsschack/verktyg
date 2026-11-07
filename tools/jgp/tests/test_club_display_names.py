"""Shortened club names appear only in Typst standings tables.

Workbooks and public JSON retain canonical names. The configured Kista alias
must reproduce the golden source.
"""
from dataclasses import replace
from datetime import date
from io import BytesIO
from pathlib import Path
import zipfile

from jgp_standings.active_configuration import load_configuration
from jgp_standings.generation_orchestrator import run_generation
from jgp_standings.upstream_client import FixtureReplay

ROOT = Path(__file__).resolve().parent.parent
REPLAY = FixtureReplay(ROOT / "fixtures" / "2026")
TODAY = date(2026, 8, 2)
KISTA = "Kista SC KFUM Schacksektion"


def _workbook_strings(workbook: bytes) -> str:
    with zipfile.ZipFile(BytesIO(workbook)) as archive:
        return archive.read("xl/sharedStrings.xml").decode("utf-8")


def _generate(offline_run, club_display_names):
    configuration = replace(load_configuration(ROOT / "state"), club_display_names=club_display_names)
    return run_generation(
        configuration, fetch=REPLAY, run_record_path=offline_run / "run_input.json", today=TODAY
    ).artifacts


class TestShortenedClubNames:

    def test_the_committed_configuration_holds_the_entry_that_replaced_the_kista_rewrite(self):
        assert load_configuration(ROOT / "state").club_display_names == {KISTA: "Kista SC KFUM"}
        assert "Kista" not in (ROOT / "jgp_standings" / "generation_core.py").read_text()

    def test_a_shortened_name_appears_in_the_standings_tables_only(self, offline_run):
        artifacts = _generate(offline_run, {KISTA: "Kista KFUM", "Wasa SK": "Wasa"})

        assert '[Kista KFUM]' in artifacts.typst_source
        assert '[Wasa]' in artifacts.typst_source
        assert f'[{KISTA}]' not in artifacts.typst_source
        assert '[Wasa SK]' not in artifacts.typst_source
        assert f'"club": "{KISTA}"' in artifacts.standings_json
        assert '"club": "Kista KFUM"' not in artifacts.standings_json
        for workbook in (artifacts.validation_workbook, artifacts.standings_workbook):
            strings = _workbook_strings(workbook)
            assert KISTA in strings
            assert "Kista KFUM<" not in strings

    def test_without_entries_every_artifact_keeps_the_canonical_name(self, offline_run):
        artifacts = _generate(offline_run, {})

        assert f'[{KISTA}]' in artifacts.typst_source
        assert '[Kista SC KFUM]' not in artifacts.typst_source
        assert f'"club": "{KISTA}"' in artifacts.standings_json
