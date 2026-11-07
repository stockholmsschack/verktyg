"""Dispensation editing with verified player identities.

Saves replace the stored list. New ids require member-system verification;
players born in the season's oldest band are ineligible.
"""
from datetime import UTC, datetime
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock

import pytest

from jgp_standings.active_configuration import ActiveConfiguration, DispensationPlayer, load_configuration, save_configuration
from jgp_standings.web import create_app
from jgp_standings.upstream_client import PlayerInfo, UpstreamRetryExhausted

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "jgp_standings" / "web" / "static" / "js"
PRATYUSH = DispensationPlayer("Pratyush Tripathi", 2010, "SS 4 Springare")


def _player(player_id, first_name, last_name, birth_year, club="Wasa SK"):
    return PlayerInfo(
        player_id=player_id, first_name=first_name, last_name=last_name, birth_year=birth_year,
        current_club_id=10, current_club_name=club,
    )


SEARCHES = {
    ("Anna", "Svensson"): [_player(111, "Anna", "Svensson", 2012)],
    ("Erik", "Johansson"): [_player(1, "Erik", "Johansson", 2011), _player(2, "Erik", "Johansson", 2012)],
    ("Nils", "Okänd"): [],
    ("Olle", "Äldre"): [_player(222, "Olle", "Äldre", 2009)],
}


@pytest.fixture
def member_system():
    member_system = Mock()
    member_system.player_name_search.side_effect = lambda first, last: list(SEARCHES.get((first, last), []))
    return member_system


@pytest.fixture
def state_directory(tmp_path):
    state_directory = tmp_path / "state"
    save_configuration(state_directory, ActiveConfiguration(
        tournament_ids=(100,),
        contributor={"name": "Test Contributor", "email": "test@example.com"},
        dispensation_dict={546393: 2009},
        dispensation_players={546393: PRATYUSH},
        penalty_dict={6544: (726286,)},
    ))
    return state_directory


@pytest.fixture
def client(tmp_path, state_directory, member_system):
    member_system.canonical_tournament.side_effect = UpstreamRetryExhausted("fetching tournament", "offline")
    app = create_app({
        "TESTING": True,
        "STATE_DIRECTORY": state_directory,
        "OUTPUT_DIRECTORY": tmp_path / "output",
        "MEMBER_SYSTEM_CLIENT": member_system,
        "CLOCK": lambda: datetime(2026, 10, 4, 12, tzinfo=UTC),
    })
    return app.test_client()


def _save(client, dispensations, revision=1):
    return client.post("/api/v1/configuration/dispensations", json={
        "revision": revision, "dispensations": dispensations,
    })


def _run_node(script: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    completed = subprocess.run([node, script], cwd=ROOT, check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr


class TestTheTab:

    def test_it_mounts_the_player_selector_and_the_editor_with_the_stored_players(self, client):
        body = client.get("/configuration?lang=en").get_data(as_text=True)

        assert "data-dispensation-editor" in body
        assert 'data-save-endpoint="/api/v1/configuration/dispensations"' in body
        assert "data-verify-endpoint" not in body
        assert "data-dispensation-paste" not in body
        assert 'data-search-endpoint="/api/v1/players/search"' in body
        assert "data-dispensation-removal-dialog" in body
        assert "Dispensation year" in body
        assert 'data-oldest-band-end="2009"' in body
        assert ('{"birth_year": 2010, "club": "SS 4 Springare", "dispensation_year": 2009, '
                '"name": "Pratyush Tripathi", "player_id": 546393}') in body
        for script in ("player-name-selector.js", "dispensation-editor.js"):
            assert f'src="/static/js/{script}"' in body

    def test_it_is_localized(self, client):
        body = client.get("/configuration?lang=sv").get_data(as_text=True)

        assert "Aktuella dispenser" in body
        assert "Dispensår" in body


class TestSaving:

    def test_a_new_player_is_looked_up_again_and_stored_with_the_member_systems_identity(
        self, client, state_directory, member_system,
    ):
        response = _save(client, [
            {"player_id": 546393, "dispensation_year": 2009},
            {"player_id": 2, "dispensation_year": 2011, "first_name": " Erik ", "last_name": "Johansson"},
        ])

        assert response.status_code == 200
        assert response.get_json()["data"] == {
            "revision": 2,
            "changed": True,
            "dispensations": [
                {"player_id": 2, "name": "Erik Johansson", "birth_year": 2012, "club": "Wasa SK",
                 "dispensation_year": 2011},
                {"player_id": 546393, "name": "Pratyush Tripathi", "birth_year": 2010, "club": "SS 4 Springare",
                 "dispensation_year": 2009},
            ],
        }
        member_system.player_name_search.assert_called_once_with("Erik", "Johansson")
        saved = load_configuration(state_directory)
        assert saved.dispensation_dict == {546393: 2009, 2: 2011}
        assert saved.dispensation_players == {546393: PRATYUSH, 2: DispensationPlayer("Erik Johansson", 2012, "Wasa SK")}
        assert saved.penalty_dict == {6544: (726286,)}

    def test_an_id_the_member_system_does_not_return_for_that_name_is_refused(self, client, state_directory):
        response = _save(client, [
            {"player_id": 546393, "dispensation_year": 2009},
            {"player_id": 999, "dispensation_year": 2011, "first_name": "Anna", "last_name": "Svensson"},
        ])

        assert response.status_code == 422
        assert response.get_json()["error"] == {
            "code": "unresolved_player",
            "message_key": "errors.dispensations.unresolved_player",
            "details": {"player_ids": [999]},
        }
        assert load_configuration(state_directory).revision == 1

    def test_a_new_player_born_in_the_oldest_band_is_refused(self, client, state_directory):
        response = _save(client, [
            {"player_id": 222, "dispensation_year": 2008, "first_name": "Olle", "last_name": "Äldre"},
        ])

        assert response.status_code == 422
        assert response.get_json()["error"] == {
            "code": "oldest_band",
            "message_key": "errors.dispensations.oldest_band",
            "details": {"player_id": 222, "birth_year": 2009, "last_year": 2009},
        }
        assert load_configuration(state_directory).revision == 1

    def test_a_new_player_without_a_name_to_look_up_is_refused(self, client, state_directory, member_system):
        response = _save(client, [{"player_id": 111, "dispensation_year": 2011}])

        assert response.status_code == 400
        member_system.player_name_search.assert_not_called()
        assert load_configuration(state_directory).revision == 1

    def test_a_stored_player_changes_year_and_is_removed_without_a_lookup(
        self, client, state_directory, member_system,
    ):
        changed = _save(client, [{"player_id": 546393, "dispensation_year": 2008}])
        removed = _save(client, [], revision=2)

        assert changed.get_json()["data"]["dispensations"][0]["dispensation_year"] == 2008
        assert removed.get_json()["data"] == {"revision": 3, "changed": True, "dispensations": []}
        member_system.player_name_search.assert_not_called()
        saved = load_configuration(state_directory)
        assert saved.dispensation_dict == {}
        assert saved.dispensation_players == {}
        assert saved.penalty_dict == {6544: (726286,)}

    def test_an_unchanged_list_writes_nothing(self, client, state_directory):
        response = _save(client, [{"player_id": 546393, "dispensation_year": 2009}])

        assert response.get_json()["data"]["changed"] is False
        assert load_configuration(state_directory).revision == 1

    def test_a_stale_revision_is_refused(self, client, state_directory):
        response = _save(client, [], revision=0)

        assert response.status_code == 409
        assert load_configuration(state_directory).dispensation_dict == {546393: 2009}

    @pytest.mark.parametrize("dispensations", [
        None,
        [{"player_id": 0, "dispensation_year": 2009}],
        [{"player_id": True, "dispensation_year": 2009}],
        [{"player_id": 546393, "dispensation_year": 209}],
        [{"player_id": 546393, "dispensation_year": "2009"}],
        [{"player_id": 546393, "dispensation_year": 2009}, {"player_id": 546393, "dispensation_year": 2008}],
        [{"player_id": 111, "dispensation_year": 2011, "first_name": "Anna"}],
        [{"player_id": 111, "dispensation_year": 2011, "first_name": " ", "last_name": "Svensson"}],
    ])
    def test_an_invalid_list_is_refused_before_any_lookup(self, client, member_system, dispensations):
        response = _save(client, dispensations)

        assert response.status_code == 400
        assert response.get_json()["error"]["message_key"] == "errors.dispensations.invalid_input"
        member_system.player_name_search.assert_not_called()


class TestTheEditor:

    def test_the_state_machine(self):
        _run_node("tests/browser/dispensation-editor.mjs")

    @pytest.mark.parametrize("path", ["/static/js/dispensation-editor-state.js", "/static/js/dispensation-editor.js"])
    def test_the_scripts_are_served_locally(self, client, path):
        response = client.get(path)
        try:
            assert response.status_code == 200
            assert len(response.data) > 100
        finally:
            response.close()

    def test_it_uses_the_selector_events_and_bootstrap_protections(self):
        source = (JS / "dispensation-editor.js").read_text(encoding="utf-8")

        assert 'import { html, nothing, render } from "./lit-html.js";' in source
        assert '"jgp:player-selection-changed"' in source
        assert '"jgp:player-selector-reset"' in source
        assert '"jgp:configuration-revision-changed"' in source
        assert '"dispensations.oldest_band"' in source
        assert "new globalThis.bootstrap.Modal" in source
        assert "new globalThis.bootstrap.Toast" in source
        assert "editor.replaceDispensations(previousDispensations)" in source
        assert 'from "./player-name-selector.js"' not in source
        assert "replaceChildren" not in source


class TestExport:
    def test_download_renders_pdf_without_persisting_or_fetching(
        self, client, state_directory, member_system,
    ):
        if shutil.which("typst") is None:
            pytest.skip("Typst is not installed")
        before = {p: p.read_bytes() for p in state_directory.rglob("*") if p.is_file()}
        response = client.get("/api/v1/configuration/dispensations.pdf?revision=1")
        assert response.status_code == 200
        assert response.mimetype == "application/pdf"
        assert response.data.startswith(b"%PDF-")
        assert "Spelardispenser-JGP-2026.pdf" in response.headers["Content-Disposition"]
        assert response.headers["Cache-Control"] == "no-store"
        assert before == {p: p.read_bytes() for p in state_directory.rglob("*") if p.is_file()}
        assert not (state_directory.parent / "output").exists()
        member_system.player_name_search.assert_not_called()

    def test_empty_list_is_refused(self, client):
        _save(client, [])
        response = client.get("/api/v1/configuration/dispensations.pdf?revision=2")
        assert response.status_code == 422
        assert response.json["error"]["message_key"] == "dispensations.empty"

    def test_stale_list_is_refused(self, client):
        response = client.get("/api/v1/configuration/dispensations.pdf?revision=0")
        assert response.status_code == 409

    def test_renderer_failure_has_a_retryable_message(self, client, monkeypatch):
        def fail(*args):
            raise RuntimeError("Typst unavailable")
        monkeypatch.setattr("jgp_standings.web.routes.render_dispensations", fail)
        response = client.get("/api/v1/configuration/dispensations.pdf?revision=1")
        assert response.status_code == 500
        assert response.json["error"]["message_key"] == "dispensations.export_failed"
