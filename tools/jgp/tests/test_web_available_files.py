"""Download and report classification.

Downloads include the PDF and standings and validation workbooks. Reports
include statistics, standings data and diagnostics. Rendering assets stay hidden.
"""
from dataclasses import replace
from datetime import date
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from jgp_standings.active_configuration import ActiveConfiguration, load_configuration, save_configuration
from jgp_standings.artifact_publication import PUBLIC_JSON_LINK, generate_and_publish, read_public_snapshot
from jgp_standings.web import create_app
from jgp_standings.web.available_files import list_available_files, list_report_files
from jgp_standings.upstream_client import FixtureReplay

ROOT = Path(__file__).resolve().parent.parent
REPLAY = FixtureReplay(ROOT / "fixtures" / "2026")
FIXTURE_TOURNAMENTS = tuple(json.loads((ROOT / "fixtures" / "2026" / "manifest.json").read_text())["tournament_ids"])
STAMP = "20260801T120000Z"
DOWNLOAD_LABELS = {
    "available_files.names.pdf",
    "available_files.names.standings_workbook",
    "available_files.names.validation_workbook",
}
REPORT_LABELS = {
    "available_files.names.statistics_workbook",
    "available_files.names.json",
    "available_files.names.rejected_players",
    "available_files.names.walkovers",
    "available_files.names.no_matches_played",
    "available_files.names.penalties",
}


@pytest.fixture
def output_directory(tmp_path):
    return tmp_path / "output"


def _write(*paths: Path) -> None:
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")


def _run_directory(output_directory: Path, run_id: str = f"{STAMP}-example") -> Path:
    """A run directory holding every file a JGP run writes, with the Typst support beside them."""
    run = output_directory / "runs" / run_id
    _write(
        run / f"jgp-standings-{STAMP}.pdf",
        run / f"jgp-standings-{STAMP}.xlsx",
        run / f"jgp-validations-{STAMP}.xlsx",
        run / f"Stats-{STAMP}.xlsx",
        run / f"standings-{STAMP}.json",
        run / "diagnostics" / f"rejected_players-{STAMP}.txt",
        run / "diagnostics" / f"walkovers-{STAMP}.txt",
        run / "diagnostics" / f"no_matches_played-{STAMP}.txt",
        run / "diagnostics" / f"penalties-{STAMP}.txt",
        run / "standings.typ",
        run / "lib.typ",
        run / "details.typ",
        run / "rules.typ",
        run / "stsf.png",
        run / "fonts" / "Roboto-Regular.ttf",
    )
    return run


class TestClassification:

    def test_downloads_include_only_the_three_operator_deliverables(self, output_directory):
        _run_directory(output_directory)

        available = list_available_files(output_directory)

        assert {item.label_key for item in available} == DOWNLOAD_LABELS
        assert len(available) == 3
        urls = " ".join(item.download_url for item in available)
        for internal in ("Stats", ".json", "diagnostics", ".typ", "stsf.png", "fonts"):
            assert internal not in urls
        assert all(STAMP in item.download_url for item in available)
        assert {item.generated_at.isoformat() for item in available} == {"2026-08-01T14:00:00+02:00"}

    def test_reports_include_statistics_data_and_diagnostics_but_not_typst(self, output_directory):
        _run_directory(output_directory)

        reports = list_report_files(output_directory)

        assert {item.label_key for item in reports} == REPORT_LABELS
        assert len(reports) == 6
        assert all(item.download_url.startswith("/reports/") for item in reports)
        urls = " ".join(item.download_url for item in reports)
        for internal in (".pdf", "jgp-", ".typ", "stsf.png", "fonts"):
            assert internal not in urls

    def test_files_are_listed_in_catalogue_order_within_a_run(self, output_directory):
        _run_directory(output_directory)

        assert [item.label_key for item in list_report_files(output_directory)] == [
            "available_files.names.statistics_workbook",
            "available_files.names.json",
            "available_files.names.rejected_players",
            "available_files.names.walkovers",
            "available_files.names.no_matches_played",
            "available_files.names.penalties",
        ]

    def test_partial_files_are_recognized(self, output_directory):
        run = output_directory / "runs" / "one-off"
        _write(
            run / f"jgp-standings-partial-{STAMP}.pdf",
            run / f"Stats-partial-{STAMP}.xlsx",
            run / "diagnostics" / f"walkovers-partial-{STAMP}.txt",
            run / "standings-partial.typ",
        )

        available = list_available_files(output_directory)
        reports = list_report_files(output_directory)

        assert [item.download_url for item in available] == [f"/downloads/one-off/jgp-standings-partial-{STAMP}.pdf"]
        assert available[0].partial
        assert {item.label_key for item in reports} == {
            "available_files.names.statistics_workbook", "available_files.names.walkovers",
        }
        assert all(item.partial for item in reports)

    def test_a_similar_name_is_not_mistaken_for_a_deliverable(self, output_directory):
        run = output_directory / "runs" / "lookalike"
        _write(
            run / f"standings-{STAMP}.xlsx",
            run / f"validations-{STAMP}.xlsx",
            run / f"jgp-standings-{STAMP}.json",
            run / f"walkovers-{STAMP}.txt",
            run / "diagnostics" / f"dropouts-{STAMP}.txt",
        )

        assert list_available_files(output_directory) == ()
        assert list_report_files(output_directory) == ()

    def test_newest_files_come_first(self, output_directory):
        older = output_directory / "runs" / "older"
        newer = output_directory / "runs" / "newer"
        _write(older / "standings-20260801T120000Z.pdf", newer / "standings-20260802T120000Z.pdf")
        _write(older / "standings.pdf")
        os.utime(older / "standings.pdf", (100, 100))

        assert [item.download_url for item in list_available_files(output_directory)] == [
            "/downloads/newer/standings-20260802T120000Z.pdf",
            "/downloads/older/standings-20260801T120000Z.pdf",
            "/downloads/older/standings.pdf",
        ]

    def test_url_segments_are_encoded_and_the_run_id_is_not_a_label(self, output_directory):
        _write(output_directory / "runs" / "mapp å" / f"jgp-standings-{STAMP}.pdf")

        (available,) = list_available_files(output_directory)

        assert available.download_url == f"/downloads/mapp%20%C3%A5/jgp-standings-{STAMP}.pdf"
        assert "mapp å" not in available.label_key

    def test_a_symlink_cannot_escape_the_runs_directory(self, tmp_path, output_directory):
        outside = tmp_path / "private"
        _write(outside / f"jgp-standings-{STAMP}.pdf", outside / f"walkovers-{STAMP}.txt")
        run = output_directory / "runs" / "unsafe"
        run.mkdir(parents=True)
        (run / f"jgp-standings-{STAMP}.pdf").symlink_to(outside / f"jgp-standings-{STAMP}.pdf")
        (run / "diagnostics").symlink_to(outside, target_is_directory=True)
        (output_directory / "runs" / "linked").symlink_to(outside, target_is_directory=True)

        assert list_available_files(output_directory) == ()
        assert list_report_files(output_directory) == ()

    def test_a_missing_runs_directory_is_an_empty_collection(self, output_directory):
        assert list_available_files(output_directory) == ()
        assert list_report_files(output_directory) == ()

    def test_a_malformed_timestamp_is_not_listed(self, output_directory):
        _write(output_directory / "runs" / "malformed" / "standings-20269999T999999Z.pdf")

        assert list_available_files(output_directory) == ()


class StubRenderer:
    def render(self, source, destination, *, root, font_directory):
        destination.write_bytes(b"%PDF-stub")


class TestEveryGeneratedFileIsClassified:

    @pytest.mark.parametrize("partial", [False, True], ids=["full", "partial"])
    def test_a_run_offers_every_deliverable_once_and_nothing_internal(self, offline_run, partial):
        configuration = replace(load_configuration(ROOT / "state"), tournament_ids=FIXTURE_TOURNAMENTS)
        overrides = {"override_year": 2026, "override_tournament_ids": list(FIXTURE_TOURNAMENTS)} if partial else {}
        published = generate_and_publish(
            configuration, offline_run / "output", fetch=REPLAY, state_directory=offline_run / "state",
            today=date(2026, 8, 2), run_id="run", renderer=StubRenderer(), **overrides,
        )

        available = list_available_files(offline_run / "output")
        reports = list_report_files(offline_run / "output")

        assert {item.label_key for item in available} == DOWNLOAD_LABELS
        assert {item.label_key for item in reports} == REPORT_LABELS
        assert all(item.partial is partial for item in (*available, *reports))
        listed = {item.download_url.split("/run/", 1)[1] for item in (*available, *reports)}
        written = {
            path.relative_to(published.run_directory).as_posix()
            for path in published.run_directory.rglob("*") if path.is_file()
        }
        assert len(listed) == len(available) + len(reports) == 9
        assert all(name.endswith(".typ") or name.startswith("fonts/") or name == "stsf.png"
                   for name in written - listed)


class TestPages:

    @pytest.fixture
    def client(self, tmp_path, output_directory):
        return create_app({
            "TESTING": True,
            "STATE_DIRECTORY": tmp_path / "state",
            "OUTPUT_DIRECTORY": output_directory,
        }).test_client()

    def test_the_two_pages_list_their_own_files(self, client, output_directory):
        _run_directory(output_directory, "generated")

        downloads = client.get("/downloads?lang=en").get_data(as_text=True)
        reports = client.get("/reports?lang=en").get_data(as_text=True)

        for label in ("Standings PDF", "Standings workbook", "Validation workbook"):
            assert label in downloads
        assert f'href="http://jgp.localhost:5000/downloads/generated/jgp-standings-{STAMP}.pdf"' in downloads
        assert "/reports/" not in downloads
        for label in ("Statistics workbook", "Standings data", "Players outside the district",
                      "Walkovers", "Players without played games", "Penalized players"):
            assert label in reports
        assert f'href="http://jgp.localhost:5000/reports/generated/Stats-{STAMP}.xlsx"' in reports
        assert "/downloads/generated" not in reports
        for page in (downloads, reports):
            assert ".typ" not in re.sub(r'<script id="locale-catalogue".*?</script>', "", page, flags=re.S)
            assert 'class="alert alert-light border"' not in page  # a visible empty state

    def test_the_pages_render_in_swedish(self, client, output_directory):
        _run_directory(output_directory, "generated")

        assert "Ställning som PDF" in client.get("/downloads?lang=sv").get_data(as_text=True)
        assert "Spelare utanför distriktet" in client.get("/reports?lang=sv").get_data(as_text=True)

    def test_a_partial_file_is_marked(self, client, output_directory):
        _write(output_directory / "runs" / "one-off" / f"jgp-standings-partial-{STAMP}.pdf")

        assert "Standings PDF (partial)" in client.get("/downloads?lang=en").get_data(as_text=True)

    def test_home_counts_the_available_files(self, client, output_directory):
        _run_directory(output_directory, "generated")

        assert "3 files are available." in client.get("/?lang=en").get_data(as_text=True)


class TestClearDownloads:

    @pytest.fixture
    def state_directory(self, tmp_path):
        return tmp_path / "state"

    @pytest.fixture
    def app(self, state_directory, output_directory):
        return create_app({
            "TESTING": True,
            "STATE_DIRECTORY": state_directory,
            "OUTPUT_DIRECTORY": output_directory,
        })

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    @pytest.fixture
    def published(self, output_directory):
        """A run with its public snapshot, as a full generation leaves it."""
        run = _run_directory(output_directory, "generated")
        (output_directory / PUBLIC_JSON_LINK).symlink_to(Path("runs") / "generated" / f"standings-{STAMP}.json")
        return run

    def test_the_page_protects_the_clear_with_a_localized_dialog(self, client, published):
        english = client.get("/downloads?lang=en").get_data(as_text=True)
        swedish = client.get("/downloads?lang=sv").get_data(as_text=True)

        assert "Clear downloads" in english
        assert "Every available file and report will be removed." in english
        assert "Your configuration will be retained." in english
        assert "Rensa nedladdningar" in swedish
        assert "Din konfiguration behålls." in swedish
        assert 'class="modal"' in english
        assert 'data-clear-endpoint="/api/v1/downloads/clear"' in english
        assert 'src="/static/js/available-files.js"' in english

    def test_an_empty_page_does_not_offer_the_clear(self, client):
        body = client.get("/downloads?lang=en").get_data(as_text=True)

        assert "No files available." in body
        assert "data-clear-open" not in body

    def test_the_clear_removes_every_run_and_the_snapshot_and_keeps_the_state(
        self, client, published, state_directory, output_directory,
    ):
        configuration = save_configuration(
            state_directory,
            ActiveConfiguration(
                tournament_ids=(11,),
                contributor={"name": "Fixture", "email": "test@example.com"},
                penalty_dict={11: (546393,)},
            ),
        )
        configuration_bytes = (state_directory / "active-configuration.toml").read_bytes()
        records = {
            state_directory / "run_input.json": "{}",
            state_directory / "operations" / "finished.json": "{}",
        }
        for path, text in records.items():
            path.parent.mkdir(exist_ok=True)
            path.write_text(text, encoding="utf-8")
        unrelated = output_directory / "operator-note.txt"
        unrelated.write_text("keep", encoding="utf-8")
        assert read_public_snapshot(output_directory) is not None

        response = client.post("/api/v1/downloads/clear", headers={"Accept": "application/json"})

        assert response.status_code == 200
        assert response.get_json() == {"data": {"cleared": True}}
        assert not published.exists()
        assert not (output_directory / "runs").exists()
        assert not (output_directory / PUBLIC_JSON_LINK).is_symlink()
        assert read_public_snapshot(output_directory) is None
        assert unrelated.read_text(encoding="utf-8") == "keep"
        assert (state_directory / "active-configuration.toml").read_bytes() == configuration_bytes
        assert load_configuration(state_directory) == configuration
        assert all(path.read_text(encoding="utf-8") == text for path, text in records.items())
        assert list_available_files(output_directory) == ()
        assert list_report_files(output_directory) == ()
        assert "3 files are available." not in client.get("/?lang=en").get_data(as_text=True)

    def test_a_repeated_clear_is_safe(self, client, published):
        first = client.post("/api/v1/downloads/clear")
        second = client.post("/api/v1/downloads/clear")

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.get_json()["data"]["cleared"] is True

    def test_the_clear_is_refused_while_a_generation_holds_the_lease(self, app, client, published):
        manager = app.extensions["jgp"]["generation_operations"]
        manager.store.acquire("active-generation")
        try:
            response = client.post("/api/v1/downloads/clear")
        finally:
            manager.store.release("active-generation")

        assert response.status_code == 409
        error = response.get_json()["error"]
        assert error["code"] == "downloads_busy"
        assert error["message_key"] == "errors.downloads.operation_busy"
        assert error["details"] == {"active_operation_id": "active-generation"}
        assert published.exists()

    def test_a_generation_is_refused_while_the_clear_holds_the_lease(self, app, monkeypatch, published):
        manager = app.extensions["jgp"]["generation_operations"]
        attempts = []

        def clear_while_generating(output_directory):
            with pytest.raises(Exception) as refused:
                manager.store.acquire("generation")
            attempts.append(type(refused.value).__name__)

        monkeypatch.setattr("jgp_standings.web.generation_operations.clear_downloads", clear_while_generating)
        manager.clear_downloads()

        assert attempts == ["GenerationBusyError"]
        assert not (manager.store.directory / "generation.lease").exists()

    def test_a_failing_clear_answers_with_its_message_key(self, client, monkeypatch, published):
        def fail(output_directory):
            raise OSError("permission denied")

        monkeypatch.setattr("jgp_standings.web.generation_operations.clear_downloads", fail)
        response = client.post("/api/v1/downloads/clear")

        assert response.status_code == 500
        assert response.get_json()["error"]["message_key"] == "errors.downloads.clear_failed"

    def test_the_clear_script_guards_a_double_submit_and_recovers_from_failure(self):
        node = shutil.which("node")
        if node is None:
            pytest.skip("Node is not installed")
        completed = subprocess.run(
            [node, "tests/browser/available-files.mjs"], check=False, capture_output=True, text=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
