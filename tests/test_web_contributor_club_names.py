"""Contributor and club display-name editing."""
from datetime import UTC, date, datetime
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock

import pytest

from jgp_standings.active_configuration import (
    DEFAULT_CONTRIBUTOR_EMAIL,
    DEFAULT_CONTRIBUTOR_NAME,
    ActiveConfiguration,
    load_configuration,
    save_configuration,
)
from jgp_standings.web import create_app
from jgp_standings.upstream_client import CanonicalClub, CanonicalTournament, ProcessableTournamentGroup

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "jgp_standings" / "web" / "static" / "js"
PENALTIES = {6544: (726286,)}


@pytest.fixture
def state_directory(tmp_path):
    return tmp_path / "state"


@pytest.fixture
def saved(state_directory):
    return save_configuration(state_directory, ActiveConfiguration(
        tournament_ids=(100,),
        contributor={"name": "Current feedback contact", "email": "current@example.com"},
        club_display_names={"Stockholms Schacksällskap": "SSS"},
        penalty_dict=PENALTIES,
    ))


@pytest.fixture
def member_system():
    member_system = Mock()
    member_system.canonical_tournament.return_value = CanonicalTournament(
        tournament_id=100,
        name="Tournament",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 1),
        processable_groups=(ProcessableTournamentGroup(1000, "Klass A", "a"),),
    )
    return member_system


@pytest.fixture
def client(tmp_path, state_directory, saved, member_system):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "contributor-club-name-tests",
        "STATE_DIRECTORY": state_directory,
        "OUTPUT_DIRECTORY": tmp_path / "output",
        "MEMBER_SYSTEM_CLIENT": member_system,
        "CLOCK": lambda: datetime(2026, 10, 4, 12, tzinfo=UTC),
    })
    return app.test_client()


def _page(client, language="en") -> str:
    return client.get(f"/configuration?lang={language}").get_data(as_text=True)


def _panel(body: str, tab: str, next_tab: str) -> str:
    return body.split(f'id="{tab}-panel"')[1].split(f'id="{next_tab}-panel"')[0]


class TestTheContributorTab:

    def test_the_tab_holds_the_localized_contributor_form(self, client):
        english, swedish = _page(client, "en"), _page(client, "sv")

        assert "Save feedback contact" in _panel(english, "contributor", "club-display-names")
        assert "Spara kontakt" in _panel(swedish, "contributor", "club-display-names")
        for body in (english, swedish):
            panel = _panel(body, "contributor", "club-display-names")
            assert f'placeholder="{DEFAULT_CONTRIBUTOR_NAME}"' in panel
            assert f'placeholder="{DEFAULT_CONTRIBUTOR_EMAIL}"' in panel
            assert 'value="Current feedback contact"' in panel
            assert 'value="current@example.com"' in panel
            for marker in ("data-save-contributor", "data-clear-contributor-name", "data-clear-contributor-email"):
                assert marker in panel
            assert 'data-save-endpoint="/api/v1/configuration/contributor"' in panel
            assert 'src="/static/js/contributor-editor.js"' in body

    def test_blank_values_save_the_defaults(self, client, state_directory, saved):
        response = client.post(
            "/api/v1/configuration/contributor",
            json={"revision": saved.revision, "name": " ", "email": ""},
        )

        assert response.status_code == 200
        data = response.get_json()["data"]
        assert data["changed"] is True
        assert data["contributor"] == {"name": DEFAULT_CONTRIBUTOR_NAME, "email": DEFAULT_CONTRIBUTOR_EMAIL}
        current = load_configuration(state_directory)
        assert dict(current.contributor) == data["contributor"]
        assert current.penalty_dict == PENALTIES

    def test_unchanged_values_do_not_advance_the_revision(self, client, state_directory, saved):
        response = client.post(
            "/api/v1/configuration/contributor",
            json={"revision": saved.revision, "name": " Current feedback contact ", "email": " current@example.com "},
        )

        assert response.status_code == 200
        assert response.get_json()["data"]["changed"] is False
        assert load_configuration(state_directory).revision == saved.revision

    @pytest.mark.parametrize("email", ["invalid", "a@b", "a b@example.com", "a@@example.com", "@example.com"])
    def test_an_invalid_email_is_refused(self, client, state_directory, saved, email):
        response = client.post(
            "/api/v1/configuration/contributor",
            json={"revision": saved.revision, "name": "Name", "email": email},
        )

        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "invalid_input"
        assert load_configuration(state_directory) == saved

    def test_a_stale_revision_is_refused(self, client, state_directory, saved):
        response = client.post(
            "/api/v1/configuration/contributor",
            json={"revision": 0, "name": "Name", "email": "valid@example.com"},
        )

        assert response.status_code == 409
        assert response.get_json()["error"]["code"] == "stale_configuration"
        assert load_configuration(state_directory) == saved

    def test_the_editor_guards_pristine_invalid_and_saving_states(self):
        source = (JS / "contributor-editor.js").read_text(encoding="utf-8")

        assert "saving || !valid || !changed" in source
        assert "form.checkValidity()" in source
        assert "nameInput.value.trim() || defaults.name" in source
        assert "emailInput.value.trim() || defaults.email" in source
        assert 'new Event("input", { bubbles: true })' in source
        assert '"jgp:configuration-revision-changed"' in source


class TestTheClubNamesTab:

    def test_the_tab_holds_the_editor_and_the_saved_names(self, client):
        body = _page(client)
        panel = body.split('id="club-display-names-panel"')[1].split('id="separators-panel"')[0]

        assert panel.index("Add a shortened club name") < panel.index("Shortened club names")
        assert '"Stockholms Schacks\\u00e4llskap": "SSS"' in body
        assert "data-club-display-name-selector" in panel
        assert 'data-clubs-endpoint="/api/v1/clubs"' in panel
        assert 'data-save-endpoint="/api/v1/configuration/club-display-names"' in panel
        assert 'src="/static/js/club-display-name-editor.js"' in body
        assert "Lägg till ett förkortat klubbnamn" in _page(client, "sv")

    def test_the_summary_counts_the_names(self, client):
        summary = _page(client).split("data-configuration-club-alias-count>")[1].split("</dd>")[0]

        assert summary.strip() == "1"

    def test_an_existing_name_is_edited_without_a_club_lookup(self, client, state_directory, saved, member_system):
        response = client.post("/api/v1/configuration/club-display-names", json={
            "revision": saved.revision,
            "action": "upsert",
            "canonical_name": "Stockholms Schacksällskap",
            "display_name": "Stockholms SS",
        })

        assert response.status_code == 200
        assert response.get_json()["data"]["changed"] is True
        current = load_configuration(state_directory)
        assert current.club_display_names == {"Stockholms Schacksällskap": "Stockholms SS"}
        assert current.penalty_dict == PENALTIES
        member_system.district_clubs.assert_not_called()

    def test_a_name_is_removed_without_a_club_lookup(self, client, state_directory, saved, member_system):
        response = client.post("/api/v1/configuration/club-display-names", json={
            "revision": saved.revision,
            "action": "remove",
            "canonical_name": "Stockholms Schacksällskap",
        })

        assert response.status_code == 200
        assert response.get_json()["data"]["club_display_names"] == {}
        assert load_configuration(state_directory).club_display_names == {}
        member_system.district_clubs.assert_not_called()

    def test_unchanged_and_absent_changes_do_not_advance_the_revision(self, client, state_directory, saved):
        for change in (
            {"action": "upsert", "canonical_name": "Stockholms Schacksällskap", "display_name": " SSS "},
            {"action": "remove", "canonical_name": "Wasa SK"},
        ):
            response = client.post(
                "/api/v1/configuration/club-display-names", json={"revision": saved.revision, **change}
            )
            assert response.get_json()["data"]["changed"] is False
        assert load_configuration(state_directory).revision == saved.revision

    def test_a_new_name_is_verified_against_the_district_clubs(self, client, saved, member_system):
        member_system.district_clubs.return_value = [CanonicalClub(10, "Wasa SK")]

        response = client.post("/api/v1/configuration/club-display-names", json={
            "revision": saved.revision,
            "action": "upsert",
            "canonical_name": "Wasa SK",
            "display_name": "Wasa",
        })

        assert response.status_code == 200
        assert response.get_json()["data"]["club_display_names"] == {
            "Stockholms Schacksällskap": "SSS",
            "Wasa SK": "Wasa",
        }
        member_system.district_clubs.assert_called_once_with(5821)

    def test_two_adds_reuse_the_club_list_loaded_for_the_selector(self, client, saved, member_system):
        member_system.district_clubs.return_value = [CanonicalClub(10, "Wasa SK"), CanonicalClub(20, "Farsta SK")]
        clubs = client.get("/api/v1/clubs").get_json()["data"]["clubs"]

        first = client.post("/api/v1/configuration/club-display-names", json={
            "revision": saved.revision, "action": "upsert", "canonical_name": clubs[0]["name"], "display_name": "Wasa",
        })
        second = client.post("/api/v1/configuration/club-display-names", json={
            "revision": first.get_json()["data"]["revision"],
            "action": "upsert",
            "canonical_name": clubs[1]["name"],
            "display_name": "Farsta",
        })

        assert first.status_code == 200
        assert second.status_code == 200
        member_system.district_clubs.assert_called_once_with(5821)

    def test_a_club_outside_the_district_is_refused(self, client, state_directory, saved, member_system):
        member_system.district_clubs.return_value = [CanonicalClub(10, "Wasa SK")]

        response = client.post("/api/v1/configuration/club-display-names", json={
            "revision": saved.revision,
            "action": "upsert",
            "canonical_name": "Invented Club",
            "display_name": "Invented",
        })

        assert response.status_code == 422
        assert response.get_json()["error"]["code"] == "noncanonical_club"
        assert load_configuration(state_directory) == saved

    @pytest.mark.parametrize("change", [
        {"action": "rename", "canonical_name": "Wasa SK", "display_name": "Wasa"},
        {"action": "upsert", "canonical_name": " ", "display_name": "Wasa"},
        {"action": "upsert", "canonical_name": "Wasa SK", "display_name": " "},
        {"action": "upsert", "canonical_name": "Wasa SK", "display_name": "W" * 201},
        {"action": "remove", "canonical_name": "Wasa SK", "display_name": "Wasa"},
    ])
    def test_an_invalid_change_is_refused(self, client, state_directory, saved, change):
        response = client.post("/api/v1/configuration/club-display-names", json={"revision": saved.revision, **change})

        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "invalid_input"
        assert load_configuration(state_directory) == saved

    def test_a_stale_revision_is_refused(self, client, state_directory, saved):
        response = client.post("/api/v1/configuration/club-display-names", json={
            "revision": 0, "action": "remove", "canonical_name": "Stockholms Schacksällskap",
        })

        assert response.status_code == 409
        assert load_configuration(state_directory) == saved

    def test_the_editor_guards_its_actions_and_reuses_the_club_request(self):
        source = (JS / "club-display-name-editor.js").read_text(encoding="utf-8")

        assert "mountClubSelector" in source
        assert "loadClubs(root.dataset.clubsEndpoint)" in source
        assert "editingCanonicalName" in source
        assert "data-edited-club-display-name" in source
        assert "operation === null" in source
        assert "club_display_names.short_name_required" in source
        assert "club_display_names.already_configured" in source
        assert "newDisplayName.focus()" in source

    @pytest.mark.parametrize("module", ["club-display-name-editor.js", "contributor-editor.js"])
    def test_the_editor_modules_parse(self, module):
        node = shutil.which("node")
        if node is None:
            pytest.skip("Node is not installed")
        completed = subprocess.run(
            [node, "--check", f"jgp_standings/web/static/js/{module}"], cwd=ROOT, check=False, capture_output=True, text=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
