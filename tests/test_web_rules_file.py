"""Rules-page download, upload and restoration.

Web and CLI generations must render with the uploaded override.
"""
from dataclasses import replace
from datetime import date
import io
from pathlib import Path
import re
import shutil
import subprocess
from unittest.mock import Mock

import pytest

from jgp_standings.active_configuration import ActiveConfiguration, load_configuration, save_configuration
from jgp_standings.artifact_publication import generate_and_publish
from jgp_standings.typst_rules import BUILT_IN_RULES_PATH, MAX_RULES_FILE_BYTES, custom_rules_path
from jgp_standings.upstream_client import CanonicalTournament, ProcessableTournamentGroup
from jgp_standings.web import create_app
from jgp_standings.upstream_client import FixtureReplay

from tests.test_web_available_files import FIXTURE_TOURNAMENTS, StubRenderer

ROOT = Path(__file__).resolve().parent.parent
REPLAY = FixtureReplay(ROOT / "fixtures" / "2026")
RULES = "/api/v1/configuration/rules"
REPLACEMENT = b"= Updated rules\n"


@pytest.fixture
def state_directory(tmp_path):
    state_directory = tmp_path / "state"
    save_configuration(state_directory, ActiveConfiguration(
        tournament_ids=(11,), contributor={"name": "Fixture", "email": "f@example.com"},
    ))
    return state_directory


@pytest.fixture
def client(tmp_path, state_directory):
    return create_app({
        "TESTING": True,
        "STATE_DIRECTORY": state_directory,
        "OUTPUT_DIRECTORY": tmp_path / "output",
        "MEMBER_SYSTEM_CLIENT": Mock(**{"canonical_tournament.return_value": CanonicalTournament(
            tournament_id=11, name="Tournament", start_date=date(2026, 1, 1), end_date=date(2026, 1, 1),
            processable_groups=(ProcessableTournamentGroup(1000, "Klass A", "a"),),
        )}),
    }).test_client()


def _upload(client, content=REPLACEMENT, filename="rules.typ"):
    return client.post(
        RULES,
        data={"rules": (io.BytesIO(content), filename)},
        content_type="multipart/form-data",
    )


def _restore_button(body: str) -> str:
    return re.search(r"<button[^>]*data-restore-rules-file[^>]*>", body).group()


class TestTheTab:

    @pytest.mark.parametrize("language, title, download", [
        ("en", "PDF rules page", "Download current rules.typ"),
        ("sv", "Regelsida i PDF", "Ladda ner aktuell rules.typ"),
    ])
    def test_it_offers_download_restore_and_upload(self, client, language, title, download):
        body = client.get(f"/configuration?lang={language}").get_data(as_text=True)
        panel = body.split('id="rules-file-panel"')[1].split("</section>")[0]

        assert title in body.split('id="rules-file-tab"')[1].split("</button>")[0]
        assert download in panel
        assert f'href="{RULES}"' in panel
        assert "disabled" in _restore_button(panel)
        assert 'accept=".typ"' in panel
        assert "data-upload-rules-file" in panel
        assert 'src="/static/js/rules-file-editor.js"' in body

    def test_the_summary_links_to_the_tab(self, client):
        body = client.get("/configuration?lang=en").get_data(as_text=True)
        summary = body.split('data-configuration-tab-link="rules-file-tab"')[1].split("</dd>")[0]

        assert re.sub(r"\s+", " ", summary).endswith("<dd class=\"col-sm-8\">rules.typ")

    def test_the_editor_module_parses(self):
        node = shutil.which("node")
        if node is None:
            pytest.skip("Node is not installed")
        completed = subprocess.run(
            [node, "--check", "jgp_standings/web/static/js/rules-file-editor.js"],
            cwd=ROOT, check=False, capture_output=True, text=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr


class TestTheRulesFile:

    def test_the_download_is_the_bundled_file_until_one_is_uploaded(self, client, state_directory):
        with client.get(RULES) as initial:
            assert initial.status_code == 200
            assert initial.data == BUILT_IN_RULES_PATH.read_bytes()

        assert _upload(client).status_code == 200

        assert custom_rules_path(state_directory).read_bytes() == REPLACEMENT
        assert "disabled" not in _restore_button(client.get("/configuration?lang=en").get_data(as_text=True))
        with client.get(RULES) as current:
            assert current.data == REPLACEMENT

    @pytest.mark.parametrize("content, filename", [
        (b"rules", "rules.txt"),
        (b"", "rules.typ"),
        (b"\xff", "rules.typ"),
        (b"x" * (MAX_RULES_FILE_BYTES + 1), "rules.typ"),
    ], ids=["suffix", "empty", "not-utf8", "too-large"])
    def test_an_unusable_upload_is_refused(self, client, state_directory, content, filename):
        response = _upload(client, content, filename)

        assert response.status_code == 400
        assert response.get_json()["error"]["message_key"] == "errors.rules_file.invalid"
        assert not custom_rules_path(state_directory).exists()

    def test_restore_returns_to_the_bundled_file(self, client):
        _upload(client)

        restored = client.delete(RULES)

        assert restored.status_code == 200
        assert restored.get_json()["data"]["restored"] is True
        with client.get(RULES) as current:
            assert current.data == BUILT_IN_RULES_PATH.read_bytes()
        assert client.delete(RULES).get_json()["data"]["restored"] is False

    def test_the_configuration_is_untouched(self, client, state_directory):
        before = load_configuration(state_directory)

        _upload(client)
        client.delete(RULES)

        assert load_configuration(state_directory) == before


class TestLaterRuns:

    def test_a_run_renders_with_the_uploaded_file_and_then_the_restored_one(self, offline_run):
        state_directory = offline_run / "state"
        configuration = replace(load_configuration(ROOT / "state"), tournament_ids=FIXTURE_TOURNAMENTS)
        client = create_app({
            "TESTING": True,
            "STATE_DIRECTORY": state_directory,
            "OUTPUT_DIRECTORY": offline_run / "output",
        }).test_client()

        def run(run_id):
            return generate_and_publish(
                configuration, offline_run / "output", fetch=REPLAY, state_directory=state_directory,
                today=date(2026, 8, 2), run_id=run_id, renderer=StubRenderer(),
            ).run_directory

        _upload(client)
        assert (run("uploaded") / "rules.typ").read_bytes() == REPLACEMENT
        client.delete(RULES)
        assert (run("restored") / "rules.typ").read_bytes() == BUILT_IN_RULES_PATH.read_bytes()
