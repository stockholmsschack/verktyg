"""Player and club selector APIs and browser state."""
from datetime import UTC, datetime
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock

import pytest

from jgp_standings.active_configuration import ActiveConfiguration, save_configuration
from jgp_standings.web import create_app
from jgp_standings.upstream_client import CanonicalClub, PlayerInfo, UpstreamRetryExhausted

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "jgp_standings" / "web" / "static" / "js"


@pytest.fixture
def member_system():
    return Mock()


@pytest.fixture
def client(tmp_path, member_system):
    state_directory = tmp_path / "state"
    save_configuration(state_directory, ActiveConfiguration(
        tournament_ids=(100,),
        contributor={"name": "Test Contributor", "email": "test@example.com"},
    ))
    app = create_app({
        "TESTING": True,
        "STATE_DIRECTORY": state_directory,
        "OUTPUT_DIRECTORY": tmp_path / "output",
        "MEMBER_SYSTEM_CLIENT": member_system,
        "CLOCK": lambda: datetime(2026, 10, 4, 12, tzinfo=UTC),
    })
    return app.test_client()


def _run_node(script: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    completed = subprocess.run([node, script], cwd=ROOT, check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr


class TestPlayerSearch:

    def test_it_returns_the_identity_without_selecting_a_club(self, client, member_system):
        member_system.player_name_search.return_value = [
            PlayerInfo(
                player_id=123,
                first_name="My",
                last_name="Ek Lund",
                birth_year=2014,
                current_club_id=99,
                current_club_name="Annan klubb",
                country="SWE",
                fide_id=1712345,
            )
        ]

        response = client.get("/api/v1/players/search?first_name=%20My%20&last_name=Ek%20Lund")

        assert response.status_code == 200
        member_system.player_name_search.assert_called_once_with("My", "Ek Lund")
        member_system.district_clubs.assert_not_called()
        assert response.get_json() == {
            "data": {
                "state": "results",
                "query": {"first_name": "My", "last_name": "Ek Lund"},
                "players": [
                    {
                        "player_id": 123,
                        "first_name": "My",
                        "last_name": "Ek Lund",
                        "birth_year": 2014,
                        "current_club": {"club_id": 99, "name": "Annan klubb"},
                        "country": "SWE",
                        "fide_id": 1712345,
                    }
                ],
            }
        }

    def test_no_matches_is_a_distinct_state(self, client, member_system):
        member_system.player_name_search.return_value = []

        response = client.get("/api/v1/players/search?first_name=My&last_name=Ek")

        assert response.status_code == 200
        assert response.get_json() == {
            "data": {
                "state": "no_matches",
                "query": {"first_name": "My", "last_name": "Ek"},
                "players": [],
            }
        }

    @pytest.mark.parametrize("query", [
        "",
        "?first_name=My",
        "?last_name=Ek",
        "?first_name=%20&last_name=Ek",
        "?first_name=My&last_name=%20",
    ])
    def test_a_missing_or_blank_name_is_refused(self, client, member_system, query):
        response = client.get(f"/api/v1/players/search{query}")

        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "invalid_input"
        assert response.get_json()["error"]["details"] == {"fields": ["first_name", "last_name"]}
        member_system.player_name_search.assert_not_called()
        member_system.district_clubs.assert_not_called()

    def test_an_upstream_failure_is_reported(self, client, member_system):
        member_system.player_name_search.side_effect = UpstreamRetryExhausted(
            "searching players by name", "timed out"
        )

        response = client.get("/api/v1/players/search?first_name=My&last_name=Ek")

        assert response.status_code == 502
        assert response.get_json()["error"]["code"] == "upstream_failure"
        member_system.district_clubs.assert_not_called()

    def test_an_arbitrary_player_id_is_not_a_route(self, client, member_system):
        response = client.get("/api/v1/players/123?date=2026-05-30")

        assert response.status_code == 404
        assert member_system.mock_calls == []


class TestClubList:

    def test_it_returns_the_canonical_district_clubs_once(self, client, member_system):
        member_system.district_clubs.return_value = [
            CanonicalClub(10, "Stockholms SK"),
            CanonicalClub(20, "Farsta SK"),
        ]

        response = client.get("/api/v1/clubs")

        assert response.status_code == 200
        member_system.district_clubs.assert_called_once_with(5821)
        member_system.player_name_search.assert_not_called()
        assert response.get_json() == {
            "data": {
                "state": "results",
                "clubs": [
                    {"club_id": 10, "name": "Stockholms SK"},
                    {"club_id": 20, "name": "Farsta SK"},
                ],
            }
        }

        assert client.get("/api/v1/clubs").status_code == 200
        member_system.district_clubs.assert_called_once_with(5821)

    def test_no_clubs_is_a_distinct_state(self, client, member_system):
        member_system.district_clubs.return_value = []

        response = client.get("/api/v1/clubs")

        assert response.status_code == 200
        assert response.get_json() == {"data": {"state": "no_matches", "clubs": []}}
        member_system.player_name_search.assert_not_called()

    def test_an_upstream_failure_is_reported(self, client, member_system):
        member_system.district_clubs.side_effect = UpstreamRetryExhausted(
            "fetching district club list", "timed out"
        )

        response = client.get("/api/v1/clubs")

        assert response.status_code == 502
        assert response.get_json()["error"]["code"] == "upstream_failure"
        member_system.player_name_search.assert_not_called()


class TestTheComponents:

    def test_the_player_search_state_machine(self):
        _run_node("tests/browser/player-name-search.mjs")

    def test_the_club_selector_state_machine(self):
        _run_node("tests/browser/club-selector.mjs")

    @pytest.mark.parametrize("path", [
        "/static/js/player-name-search-state.js",
        "/static/js/player-name-selector.js",
        "/static/js/club-selector-state.js",
        "/static/js/club-selector.js",
    ])
    def test_the_scripts_are_served_locally(self, client, path):
        response = client.get(path)
        try:
            assert response.status_code == 200
            assert len(response.data) > 100
        finally:
            response.close()

    def test_the_player_selector_is_lit_html_and_emits_the_chosen_result(self):
        source = (JS / "player-name-selector.js").read_text(encoding="utf-8")

        assert 'import { html, nothing, render } from "./lit-html.js";' in source
        for absent in ("document.createElement", "replaceChildren", "setTimeout"):
            assert absent not in source
        assert "debounce" not in source.lower()
        assert '"jgp:player-selection-changed"' in source
        assert "this.selectedPlayer = player" in source
        assert "data-selected-player-id" in source

    def test_the_club_selector_is_lit_html_and_independent_of_player_state(self):
        source = (JS / "club-selector.js").read_text(encoding="utf-8")

        assert 'import { html, nothing, render } from "./lit-html.js";' in source
        assert '"jgp:club-selection-changed"' in source
        assert "player" not in source.lower()
        assert "document.createElement" not in source
        assert "replaceChildren" not in source
        assert "const clubLoads = new Map();" in source
        assert "clubLoads.has(endpoint)" in source
