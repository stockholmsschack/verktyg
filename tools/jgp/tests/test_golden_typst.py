"""Offline generation must match the accepted Typst source byte for byte.

The generated PDF is rendered but not compared. Rendering assets are outside
this comparison; `reference-artifacts/README.md` describes their review process.
"""
from datetime import date
from pathlib import Path

import pytest

from jgp_standings.active_configuration import load_configuration
from jgp_standings.artifact_publication import TYPST_SOURCE_FILE, generate_and_publish
from jgp_standings.upstream_client import FixtureReplay

from compare_typst import TypstMismatch, compare_typst

ROOT = Path(__file__).resolve().parent.parent
REPLAY = FixtureReplay(ROOT / "fixtures" / "2026")
TODAY = date(2026, 8, 2)
GOLDEN_TYPST = ROOT / "reference-artifacts" / "2026-standings-golden.typ"


class TestGeneratedSourceMatchesTheBaseline:

    def test_an_offline_run_reproduces_the_golden_typst_source(self, offline_run):
        published = generate_and_publish(
            load_configuration(ROOT / "state"),
            offline_run / "output",
            fetch=REPLAY,
            state_directory=offline_run / "state",
            today=TODAY,
        )
        compare_typst(GOLDEN_TYPST, published.run_directory / TYPST_SOURCE_FILE)


class TestTheComparisonFailsOnAChangedSource:

    def test_identical_files_pass(self):
        compare_typst(GOLDEN_TYPST, GOLDEN_TYPST)

    def test_an_injected_change_fails_with_a_diff(self, tmp_path):
        mutated = GOLDEN_TYPST.read_text().replace("JGP Ställning", "Ändrad ställning", 1)
        assert mutated != GOLDEN_TYPST.read_text(), "the mutation has to change something"

        mutated_path = tmp_path / "mutated.typ"
        mutated_path.write_text(mutated)

        with pytest.raises(TypstMismatch) as raised:
            compare_typst(GOLDEN_TYPST, mutated_path)

        message = str(raised.value)
        assert "generated Typst differs from the golden baseline" in message
        assert "-#set document(title: [JGP Ställning]," in message
        assert "+#set document(title: [Ändrad ställning]," in message
