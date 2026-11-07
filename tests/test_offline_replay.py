"""Offline generation, rendering and atomic publication using the 2026 fixtures.

Network access is blocked. Checks cover capture metadata, output paths, bundled
Typst assets, shared filename timestamps and rollback on publication failure.
"""
import json
import os
import re
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from jgp_standings.artifact_publication import PUBLIC_JSON_LINK, TypstRenderer, generate_and_publish, read_public_snapshot
from jgp_standings.generation_core import PUBLIC_STANDINGS_FIELDS, produce_artifacts
from jgp_standings.generation_orchestrator import GenerationStageError
from jgp_standings.active_configuration import load_configuration
from jgp_standings.run_record import RunInput, read_run_record, write_run_record
from jgp_standings.upstream_client import FixtureReplay

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 8, 2)
FIXTURES_DIR = ROOT / "fixtures" / "2026"
REPLAY = FixtureReplay(FIXTURES_DIR)
COMMITTED_STATE = ROOT / "state"


def _generate(root, run_id=None, **kwargs):
    return generate_and_publish(
        load_configuration(COMMITTED_STATE), root / "output", fetch=REPLAY, state_directory=root / "state",
        today=TODAY, run_id=run_id, **kwargs,
    )


class FailingRenderer:
    def render(self, source, destination, *, root, font_directory):
        destination.write_bytes(b"%PDF-partial")
        raise RuntimeError("Typst exited with status 1: error: unknown font family")


def _files(directory):
    return sorted(str(path.relative_to(directory)) for path in directory.rglob("*") if path.is_file())


def _names(directory):
    return sorted(path.name for path in directory.iterdir())


class TestManifest:

    def test_manifest_records_source_capture_date_purpose_tournaments_and_rating_list_date(self):
        manifest = json.loads((FIXTURES_DIR / "manifest.json").read_text())
        assert manifest["source"].startswith("https://")
        assert manifest["captured_at"]
        assert manifest["purpose"]
        assert manifest["tournament_ids"]
        assert manifest["rating_list_date"]


class TestOneGenerationRunsOffline:

    def test_one_call_produces_every_artifact_from_one_result(self, offline_run):
        published = _generate(offline_run)

        artifacts = published.result.artifacts
        assert len(published.result.standings.tournaments) == 6
        assert artifacts.typst_source == (published.run_directory / "standings.typ").read_text()
        assert set(artifacts.diagnostic_reports) == {
            "rejected_players.txt", "walkovers.txt", "no_matches_played.txt", "penalties.txt",
        }
        suffix = published.run_id.split("-")[0]
        files = _files(published.run_directory)
        for deliverable in (
            f"jgp-validations-{suffix}.xlsx", f"jgp-standings-{suffix}.xlsx", f"Stats-{suffix}.xlsx",
            f"standings-{suffix}.json", f"jgp-standings-{suffix}.pdf",
            f"diagnostics/rejected_players-{suffix}.txt", f"diagnostics/walkovers-{suffix}.txt",
            f"diagnostics/no_matches_played-{suffix}.txt", f"diagnostics/penalties-{suffix}.txt",
        ):
            assert deliverable in files
        assert "standings.typ" in files
        assert published.pdf_path == published.run_directory / f"jgp-standings-{suffix}.pdf"
        assert published.pdf_path.read_bytes().startswith(b"%PDF-")

    def test_every_operator_facing_file_carries_the_same_utc_suffix(self, offline_run):
        published = _generate(offline_run, run_id="named")

        internal = {"standings.typ", "lib.typ", "details.typ", "rules.typ", "stsf.png"}
        operator_facing = [
            path for path in published.run_directory.rglob("*")
            if path.is_file() and path.name not in internal and "fonts" not in path.parts
        ]
        suffixes = {path.stem.rsplit("-", 1)[1] for path in operator_facing}
        assert len(operator_facing) == 9
        assert len(suffixes) == 1
        assert re.fullmatch(r"\d{8}T\d{6}Z", suffixes.pop())

    def test_it_writes_only_the_run_directory_and_the_run_record(self, offline_run):
        published = _generate(offline_run, run_id="first")

        assert list((offline_run / "cwd").iterdir()) == []
        assert _files(offline_run / "state") == ["run_input.json"]
        assert _names(offline_run / "output") == [PUBLIC_JSON_LINK, "runs"]
        assert _names(offline_run / "output" / "runs") == ["first"]
        assert published.run_directory == offline_run / "output" / "runs" / "first"

    def test_the_run_record_lives_in_the_state_directory_with_its_shape_intact(self, offline_run):
        _generate(offline_run)

        record_path = offline_run / "state" / "run_input.json"
        assert set(json.loads(record_path.read_text())) == {
            "rating_list_date", "effective_year", "tournament_ids",
        }
        manifest = json.loads((FIXTURES_DIR / "manifest.json").read_text())
        run_input = read_run_record(record_path)
        assert run_input.rating_list_date.isoformat() == manifest["rating_list_date"]
        assert list(run_input.tournament_ids) == manifest["tournament_ids"]

    def test_a_second_run_replays_the_record_and_leaves_earlier_runs_alone(self, offline_run):
        first = _generate(offline_run, run_id="first")
        before = {name: (first.run_directory / name).read_bytes() for name in _files(first.run_directory)}
        (offline_run / "output" / "operator-note.txt").write_text("keep me")

        second = _generate(offline_run, run_id="second")

        assert second.result.standings.run_input == first.result.standings.run_input
        assert second.result.artifacts.typst_source == first.result.artifacts.typst_source
        assert {name: (first.run_directory / name).read_bytes() for name in before} == before
        assert (offline_run / "output" / "operator-note.txt").read_text() == "keep me"

    def test_an_existing_run_directory_is_refused_and_left_untouched(self, offline_run):
        existing = offline_run / "output" / "runs" / "taken"
        existing.mkdir(parents=True)
        (existing / "marker").write_text("earlier run")

        with pytest.raises(GenerationStageError, match="already exists"):
            _generate(offline_run, run_id="taken")

        assert _files(existing) == ["marker"]
        assert _names(offline_run / "output") == ["runs"]

    def test_a_failure_while_staging_leaves_no_partial_run_and_no_record(self, offline_run, monkeypatch):
        from jgp_standings import artifact_publication

        def fail_after_one_file(artifacts, staging, typst_assets, *, rules_source, partial, deliverable_timestamp):
            (staging / "standings.typ").write_text(artifacts.typst_source)
            raise OSError("disk full")

        monkeypatch.setattr(artifact_publication, "_stage_generation_artifacts", fail_after_one_file)

        with pytest.raises(GenerationStageError, match="artifact staging: disk full"):
            _generate(offline_run)

        assert list((offline_run / "output").iterdir()) == []
        assert not (offline_run / "state").exists()


class TestRenderingInsideThePass:

    def test_the_source_is_rendered_beside_copies_of_the_bundled_assets(self, offline_run):
        published = _generate(offline_run)

        files = _files(published.run_directory)
        for asset in ("lib.typ", "details.typ", "rules.typ", "stsf.png", "fonts/Roboto-Regular.ttf"):
            assert asset in files
            assert (published.run_directory / asset).read_bytes() == (ROOT / "typst" / asset).read_bytes()

    def test_a_render_failure_fails_the_run_and_leaves_earlier_runs_untouched(self, offline_run):
        first = _generate(offline_run, run_id="first")
        before = {name: (first.run_directory / name).read_bytes() for name in _files(first.run_directory)}
        record_before = (offline_run / "state" / "run_input.json").read_bytes()
        snapshot_before = read_public_snapshot(offline_run / "output")

        with pytest.raises(GenerationStageError, match="rendering: Typst exited with status 1"):
            _generate(offline_run, run_id="second", renderer=FailingRenderer())

        assert _names(offline_run / "output") == [PUBLIC_JSON_LINK, "runs"]
        assert _names(offline_run / "output" / "runs") == ["first"]
        assert {name: (first.run_directory / name).read_bytes() for name in before} == before
        assert (offline_run / "state" / "run_input.json").read_bytes() == record_before
        assert read_public_snapshot(offline_run / "output") == snapshot_before

    def test_a_missing_bundled_asset_fails_staging(self, offline_run, tmp_path):
        assets = tmp_path / "assets"
        assets.mkdir()
        for name in ("lib.typ", "details.typ", "rules.typ"):
            (assets / name).write_bytes((ROOT / "typst" / name).read_bytes())

        with pytest.raises(GenerationStageError, match="artifact staging: missing bundled Typst asset"):
            _generate(offline_run, typst_assets=assets)

        assert list((offline_run / "output").iterdir()) == []

    def test_the_typst_command_names_the_bundled_fonts_and_ignores_system_fonts(self, tmp_path, monkeypatch):
        import subprocess

        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            (tmp_path / "out.pdf").write_bytes(b"%PDF-1.7")
            return subprocess.CompletedProcess(command, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        TypstRenderer().render(
            tmp_path / "standings.typ", tmp_path / "out.pdf", root=tmp_path, font_directory=tmp_path / "fonts",
        )

        command, kwargs = calls[0]
        assert "--ignore-system-fonts" in command
        assert command[command.index("--font-path") + 1] == str(tmp_path / "fonts")
        assert command[command.index("--root") + 1] == str(tmp_path)
        assert kwargs["cwd"] == tmp_path


class TestThePublicSnapshotCommitsWithTheRun:

    def test_the_snapshot_is_the_runs_standings_json(self, offline_run):
        published = _generate(offline_run, run_id="first")

        assert re.fullmatch(
            r"runs/first/standings-\d{8}T\d{6}Z\.json", os.readlink(offline_run / "output" / PUBLIC_JSON_LINK)
        )
        assert read_public_snapshot(offline_run / "output") == published.result.artifacts.standings_json.encode()

    def test_the_snapshot_is_keyed_by_age_band_in_standings_order_with_placement_per_band(self, offline_run):
        published = _generate(offline_run)

        snapshot = json.loads(read_public_snapshot(offline_run / "output"))
        bands = published.result.standings.bands
        tournament_ids = {str(tournament.tournament_id) for tournament in published.result.standings.tournaments}
        ordered = sorted(published.result.standings.tournaments, key=lambda t: (t.date, str(t.tournament_id)))
        assert snapshot["tournament_ids"] == [t.tournament_id for t in ordered]
        snapshot = snapshot["standings"]
        assert list(snapshot) == list(bands)
        for label, rows in snapshot.items():
            assert [row["placement"] for row in rows] == list(range(1, bands[label].height + 1))
            assert [row["member_id"] for row in rows] == bands[label]["contenderId"].to_list()
            for row in rows:
                assert set(row) == PUBLIC_STANDINGS_FIELDS
                assert set(row["tournaments"]) <= tournament_ids
                assert row["total_points"] == sum(row["tournaments"].values())

    def test_json_tournaments_and_points_follow_table_columns_when_metadata_is_reversed(self, offline_run):
        published = _generate(offline_run)
        standings = published.result.standings
        reversed_standings = replace(standings, tournaments=tuple(reversed(standings.tournaments)))

        artifacts = produce_artifacts(
            reversed_standings, contributor={"name": "Contact", "email": "contact@example.com"}, landscape=False,
        )

        snapshot = json.loads(artifacts.standings_json)
        original = json.loads(published.result.artifacts.standings_json)
        del snapshot["last_updated"], original["last_updated"]
        assert snapshot == original

    def test_a_second_run_advances_the_snapshot(self, offline_run):
        _generate(offline_run, run_id="first")
        _generate(offline_run, run_id="second")

        assert os.readlink(offline_run / "output" / PUBLIC_JSON_LINK).startswith("runs/second/")

    def test_a_failure_advancing_the_snapshot_undoes_the_run_and_the_record(self, offline_run, monkeypatch):
        from jgp_standings import artifact_publication

        first = _generate(offline_run, run_id="first")
        before = {name: (first.run_directory / name).read_bytes() for name in _files(first.run_directory)}
        record_path = offline_run / "state" / "run_input.json"
        record_path.write_text(record_path.read_text() + "\n")  # so a rewrite would show
        record_before = record_path.read_bytes()
        link_before = os.readlink(offline_run / "output" / PUBLIC_JSON_LINK)

        def fail(path, target):
            raise OSError("link refused")

        monkeypatch.setattr(artifact_publication, "_replace_link", fail)
        with pytest.raises(GenerationStageError, match="committing: link refused"):
            _generate(offline_run, run_id="second")

        assert _names(offline_run / "output") == [PUBLIC_JSON_LINK, "runs"]
        assert _names(offline_run / "output" / "runs") == ["first"]
        assert {name: (first.run_directory / name).read_bytes() for name in before} == before
        assert record_path.read_bytes() == record_before
        assert os.readlink(offline_run / "output" / PUBLIC_JSON_LINK) == link_before

    def test_a_failure_recording_the_first_run_leaves_no_run_record_or_snapshot(self, offline_run, monkeypatch):
        from jgp_standings import artifact_publication

        def fail(run_input, path):
            path.write_text("{")
            raise OSError("disk full")

        monkeypatch.setattr(artifact_publication, "write_run_record", fail)
        with pytest.raises(GenerationStageError, match="committing: disk full"):
            _generate(offline_run)

        assert _names(offline_run / "output") == ["runs"]
        assert _names(offline_run / "output" / "runs") == []
        assert _files(offline_run / "state") == []

    def test_a_snapshot_that_is_not_a_symlink_is_refused_and_left_alone(self, offline_run):
        (offline_run / "output").mkdir()
        (offline_run / "output" / PUBLIC_JSON_LINK).write_text("hand-placed")

        with pytest.raises(GenerationStageError, match="publication reference is not a symlink"):
            _generate(offline_run)

        assert (offline_run / "output" / PUBLIC_JSON_LINK).read_text() == "hand-placed"
        assert _names(offline_run / "output" / "runs") == []
        assert not (offline_run / "state" / "run_input.json").exists()


class RecordingProgress:
    def __init__(self):
        self.events = []

    def begin_phase(self, description, total, unit):
        self.events.append(("phase", description, total, unit))

    def start(self, operation):
        self.events.append(("start", operation))

    def complete(self):
        self.events.append(("complete",))


class TestRunInputsAndProgress:
    """Partial runs, run-record resolution and progress reporting."""

    MANIFEST_IDS = json.loads((FIXTURES_DIR / "manifest.json").read_text())["tournament_ids"]

    def test_a_run_over_its_own_tournament_list_is_partial(self, offline_run):
        default = _generate(offline_run, run_id="default")
        record_path = offline_run / "state" / "run_input.json"
        record_path.write_text(record_path.read_text() + "\n")  # so a rewrite would show
        record_before = record_path.read_bytes()
        link_before = os.readlink(offline_run / "output" / PUBLIC_JSON_LINK)

        partial = _generate(
            offline_run, run_id="one-off", override_year=2026, override_tournament_ids=self.MANIFEST_IDS,
        )

        names = _files(partial.run_directory)
        operator_facing = [name for name in names if re.search(r"\d{8}T\d{6}Z", name)]
        assert len(operator_facing) == 9
        assert all("-partial-" in name for name in operator_facing)
        assert "standings-partial.typ" in names
        assert partial.pdf_path.name.startswith("jgp-standings-partial-")
        assert partial.result.artifacts.typst_source == default.result.artifacts.typst_source
        assert record_path.read_bytes() == record_before
        assert os.readlink(offline_run / "output" / PUBLIC_JSON_LINK) == link_before

    def test_a_partial_run_neither_replays_nor_records_the_run_input(self, offline_run):
        record_path = offline_run / "state" / "run_input.json"
        record_path.parent.mkdir()
        write_run_record(RunInput(date(2020, 1, 1), 2026, tuple(self.MANIFEST_IDS)), record_path)
        record_before = record_path.read_bytes()

        partial = _generate(offline_run, override_year=2026, override_tournament_ids=self.MANIFEST_IDS)

        assert partial.result.standings.run_input.rating_list_date == date(2026, 5, 30)
        assert record_path.read_bytes() == record_before
        assert not (offline_run / "output" / PUBLIC_JSON_LINK).exists()

    def test_a_record_for_other_tournaments_is_resolved_afresh_and_replaced(self, offline_run):
        record_path = offline_run / "state" / "run_input.json"
        record_path.parent.mkdir()
        write_run_record(RunInput(date(2020, 1, 1), 1999, (6544,)), record_path)

        published = _generate(offline_run)

        fresh = RunInput(date(2026, 5, 30), 2026, tuple(self.MANIFEST_IDS))
        assert published.result.standings.run_input == fresh
        assert read_run_record(record_path) == fresh

    def test_the_stages_are_reported_in_order_and_nothing_is_printed(self, offline_run, capsys):
        progress = RecordingProgress()

        _generate(offline_run, progress=progress)

        assert capsys.readouterr() == ("", "")
        starts = [event[1] for event in progress.events if event[0] == "start"]
        names = [f"Fetching tournament {index}/6: " for index in range(1, 7)]
        assert starts[0] == "Fetching district players and clubs"
        assert starts[1:7] == [f"Fetching metadata for tournament {tid}" for tid in self.MANIFEST_IDS]
        assert [start[:len(name)] for start, name in zip(starts[7:13], names)] == names
        assert starts[13:] == [
            "Calculating standings", "Producing output artifacts",
            "Staging artifacts", "Rendering PDF", "Publishing generation",
        ]
        phases = [event[1:] for event in progress.events if event[0] == "phase"]
        assert phases == [("Fetching tournaments", 6, "tournaments"), ("Processing results", 5, "steps")]
        assert progress.events.count(("complete",)) == 6 + 5
