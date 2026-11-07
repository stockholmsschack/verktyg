"""Tournament search, selection, persistence and naming-help integration."""
from datetime import UTC, date, datetime
from pathlib import Path
import re
import shutil
import subprocess
from unittest.mock import Mock

import pytest

from jgp_standings.active_configuration import ActiveConfiguration, DispensationPlayer, load_configuration, save_configuration
from jgp_standings.web import create_app
from jgp_standings.klass_naming import KlassNameReason, KlassNameRefused, KlassNamingProblem, read_klass
from jgp_standings.upstream_client import (
    CanonicalTournament,
    ProcessableTournamentGroup,
    TournamentGroupSearchHit,
    TournamentKlassNamingError,
    TournamentResolutionError,
    UpstreamRetryExhausted,
)

ROOT = Path(__file__).resolve().parent.parent
OCTOBER_2026 = datetime(2026, 10, 4, 12, tzinfo=UTC)


def _tournament(tournament_id: int, name: str, start_date: date) -> CanonicalTournament:
    return CanonicalTournament(
        tournament_id=tournament_id,
        name=name,
        start_date=start_date,
        end_date=start_date,
        processable_groups=(ProcessableTournamentGroup(tournament_id + 1000, "Klass A", "a"),),
    )


def _configuration() -> ActiveConfiguration:
    return ActiveConfiguration(
        tournament_ids=(100, 200),
        contributor={"name": "Test Contributor", "email": "test@example.com"},
        landscape=True,
        dispensation_dict={546393: 2009},
        dispensation_players={546393: DispensationPlayer("Pratyush Tripathi", 2010, "SS 4 Springare")},
        penalty_dict={6544: (726286,)},
    )


@pytest.fixture
def state_directory(tmp_path):
    return tmp_path / "state"


@pytest.fixture
def saved(state_directory):
    return save_configuration(state_directory, _configuration())


@pytest.fixture
def member_system():
    member_system = Mock()
    tournaments = {
        100: _tournament(100, "Saved January", date(2026, 1, 5)),
        200: _tournament(200, "Saved February", date(2026, 2, 5)),
    }
    member_system.canonical_tournament.side_effect = lambda tournament_id: tournaments[tournament_id]
    return member_system


@pytest.fixture
def client(tmp_path, state_directory, saved, member_system):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "tournament-tests",
        "STATE_DIRECTORY": state_directory,
        "OUTPUT_DIRECTORY": tmp_path / "output",
        "MEMBER_SYSTEM_CLIENT": member_system,
        "CLOCK": lambda: OCTOBER_2026,
    })
    return app.test_client()


def _refusal(tournament_id=6544, group_name="Klass Junior"):
    problem = KlassNamingProblem(
        tournament_id, "Tyresö JGP 2026", 31877, group_name,
        KlassNameReason.UNREADABLE_KLASS, "detail",
    )
    return TournamentKlassNamingError(tournament_id, [problem])


def _save(client, revision, tournament_ids):
    return client.post(
        "/api/v1/configuration/tournaments",
        json={"revision": revision, "tournament_ids": tournament_ids},
    )


class TestTournamentSearch:

    def test_search_decodes_text_and_groups_rows_by_tournament_id(self, client, member_system):
        member_system.tournament_group_search.return_value = [
            TournamentGroupSearchHit(8101, 6336, "Klass A", "Wasa JGP 2026", "2026-03-08T14:30:00Z"),
            TournamentGroupSearchHit(8102, 6336, "Klass B", "Wasa JGP 2026"),
            TournamentGroupSearchHit(9201, 7447, "Klass C", "Tyresö JGP 2026"),
        ]

        response = client.get("/api/v1/tournaments/search?q=Wasa%20JGP%2FFinal")

        assert response.status_code == 200
        member_system.tournament_group_search.assert_called_once_with("Wasa JGP/Final")
        assert response.get_json() == {
            "data": {
                "state": "results",
                "query": "Wasa JGP/Final",
                "tournaments": [
                    {
                        "tournament_id": 7447,
                        "name": "Tyresö JGP 2026",
                        "matching_groups": [
                            {"group_id": 9201, "name": "Klass C", "latest_updated_game": None},
                        ],
                    },
                    {
                        "tournament_id": 6336,
                        "name": "Wasa JGP 2026",
                        "matching_groups": [
                            {"group_id": 8101, "name": "Klass A", "latest_updated_game": "2026-03-08T14:30:00Z"},
                            {"group_id": 8102, "name": "Klass B", "latest_updated_game": None},
                        ],
                    },
                ],
            }
        }

    def test_search_sorts_tournaments_by_descending_tournament_id(self, client, member_system):
        member_system.tournament_group_search.return_value = [
            TournamentGroupSearchHit(1, 5225, "Klass A", "Wasa JGP 2024"),
            TournamentGroupSearchHit(2, 6336, "Klass A", "Wasa JGP 2025"),
            TournamentGroupSearchHit(3, 7447, "Klass A", "Wasa JGP 2026"),
            TournamentGroupSearchHit(4, 7447, "Klass B", "Wasa JGP 2026"),
        ]

        tournaments = client.get("/api/v1/tournaments/search?q=Wasa").get_json()["data"]["tournaments"]

        assert [tournament["tournament_id"] for tournament in tournaments] == [7447, 6336, 5225]

    def test_an_empty_search_has_a_distinct_success_state(self, client, member_system):
        member_system.tournament_group_search.return_value = []

        response = client.get("/api/v1/tournaments/search?q=saknas")

        assert response.status_code == 200
        assert response.get_json() == {"data": {"state": "no_matches", "query": "saknas", "tournaments": []}}

    @pytest.mark.parametrize("path", ["/api/v1/tournaments/search", "/api/v1/tournaments/search?q=%20%20"])
    def test_missing_or_blank_text_is_refused_without_calling_upstream(self, client, member_system, path):
        response = client.get(path)

        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "invalid_input"
        member_system.tournament_group_search.assert_not_called()

    def test_an_upstream_failure_is_reported(self, client, member_system):
        member_system.tournament_group_search.side_effect = UpstreamRetryExhausted(
            "searching tournament groups", "timed out"
        )

        response = client.get("/api/v1/tournaments/search?q=Wasa")

        assert response.status_code == 502
        assert response.get_json()["error"]["code"] == "upstream_failure"


class TestCanonicalTournamentLookup:

    def test_the_lookup_returns_the_verified_tournament(self, client, member_system):
        response = client.get("/api/v1/tournaments/100")

        assert response.status_code == 200
        member_system.canonical_tournament.assert_called_once_with(100)
        assert response.get_json() == {
            "data": {
                "tournament": {
                    "tournament_id": 100,
                    "name": "Saved January",
                    "start_date": "2026-01-05",
                    "end_date": "2026-01-05",
                    "processable_groups": [{"group_id": 1100, "name": "Klass A", "klass": "a"}],
                }
            }
        }

    @pytest.mark.parametrize("tournament_id", ["abc", "0", "-1", "01", "١٢"])
    def test_invalid_input_is_refused_without_calling_upstream(self, client, member_system, tournament_id):
        response = client.get(f"/api/v1/tournaments/{tournament_id}")

        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "invalid_input"
        member_system.canonical_tournament.assert_not_called()

    def test_an_unresolvable_tournament_is_reported(self, client, member_system):
        member_system.canonical_tournament.side_effect = TournamentResolutionError(999, "not found")

        response = client.get("/api/v1/tournaments/999")

        assert response.status_code == 422
        assert response.get_json() == {
            "error": {
                "code": "tournament_unresolvable",
                "message_key": "errors.tournament_lookup.unresolvable",
                "details": {"tournament_id": 999},
            }
        }

    def test_refused_group_names_are_listed(self, client, member_system):
        member_system.canonical_tournament.side_effect = _refusal()

        response = client.get("/api/v1/tournaments/6544")

        assert response.status_code == 422
        error = response.get_json()["error"]
        assert error["code"] == "klass_naming"
        assert error["message_key"] == "errors.klass_naming.heading"
        assert error["details"]["problems"] == [{
            "field": "tournaments",
            "message_key": "errors.klass_naming.unreadable_klass",
            "parameters": {
                "tournament_id": 6544,
                "tournament_name": "Tyresö JGP 2026",
                "group_id": 31877,
                "group_name": "Klass Junior",
            },
        }]

    def test_an_upstream_failure_is_reported(self, client, member_system):
        member_system.canonical_tournament.side_effect = UpstreamRetryExhausted(
            "resolving canonical tournament 100", "timed out"
        )

        response = client.get("/api/v1/tournaments/100")

        assert response.status_code == 502
        assert response.get_json()["error"]["code"] == "upstream_failure"


class TestTournamentSave:

    def test_a_changed_selection_is_reverified_sorted_and_saved_keeping_everything_else(
        self, client, member_system, saved, state_directory
    ):
        tournaments = {
            300: _tournament(300, "March later id", date(2026, 3, 1)),
            250: _tournament(250, "January", date(2026, 1, 10)),
            100: _tournament(100, "March earlier id", date(2026, 3, 1)),
        }
        member_system.canonical_tournament.side_effect = lambda tournament_id: tournaments[tournament_id]

        response = _save(client, saved.revision, [300, 250, 100])

        assert response.status_code == 200
        assert response.get_json()["data"] == {
            "revision": saved.revision + 1,
            "tournament_ids": [250, 100, 300],
            "changed": True,
        }
        loaded = load_configuration(state_directory)
        assert loaded.tournament_ids == (250, 100, 300)
        assert loaded.contributor == saved.contributor
        assert loaded.landscape == saved.landscape
        assert loaded.dispensation_dict == saved.dispensation_dict
        assert loaded.penalty_dict == saved.penalty_dict

    def test_an_unchanged_selection_saves_without_upstream_or_a_new_revision(
        self, client, member_system, saved, state_directory
    ):
        response = _save(client, saved.revision, [100, 200])

        assert response.status_code == 200
        assert response.get_json()["data"]["changed"] is False
        assert load_configuration(state_directory).revision == saved.revision
        member_system.canonical_tournament.assert_not_called()

    def test_a_reordering_alone_is_unchanged_once_sorted(self, client, saved, state_directory):
        response = _save(client, saved.revision, [200, 100])

        assert response.status_code == 200
        assert response.get_json()["data"] == {
            "revision": saved.revision,
            "tournament_ids": [100, 200],
            "changed": False,
        }
        assert load_configuration(state_directory) == saved

    def test_a_stale_save_is_refused_before_upstream_resolution(
        self, client, member_system, saved, state_directory
    ):
        response = _save(client, saved.revision - 1, [300])

        assert response.status_code == 409
        assert response.get_json()["error"]["code"] == "stale_configuration"
        assert response.get_json()["error"]["details"] == {"current_revision": saved.revision}
        member_system.canonical_tournament.assert_not_called()
        assert load_configuration(state_directory) == saved

    def test_an_unresolvable_id_blocks_the_save(self, client, member_system, saved, state_directory):
        member_system.canonical_tournament.side_effect = TournamentResolutionError(1100, "group id")

        response = _save(client, saved.revision, [1100])

        assert response.status_code == 422
        assert response.get_json()["error"]["code"] == "tournament_unresolvable"
        assert load_configuration(state_directory) == saved

    def test_refused_group_names_block_the_save(self, client, member_system, saved, state_directory):
        member_system.canonical_tournament.side_effect = _refusal()

        response = _save(client, saved.revision, [6544])

        assert response.status_code == 422
        assert response.get_json()["error"]["code"] == "klass_naming"
        assert load_configuration(state_directory) == saved

    def test_tournaments_from_different_years_are_refused(self, client, member_system, saved, state_directory):
        tournaments = {
            100: _tournament(100, "Last year", date(2025, 3, 1)),
            300: _tournament(300, "This year", date(2026, 3, 1)),
        }
        member_system.canonical_tournament.side_effect = lambda tournament_id: tournaments[tournament_id]

        response = _save(client, saved.revision, [100, 300])

        assert response.status_code == 422
        assert response.get_json()["error"] == {
            "code": "mixed_tournament_years",
            "message_key": "errors.configuration.tournament_years_mixed",
            "details": {"configured_years": [2025, 2026]},
        }
        assert load_configuration(state_directory) == saved

    def test_next_years_tournaments_are_refused(self, client, member_system, saved, state_directory):
        member_system.canonical_tournament.side_effect = (
            lambda tournament_id: _tournament(tournament_id, "Next year", date(2027, 1, 9))
        )

        response = _save(client, saved.revision, [300])

        assert response.status_code == 422
        assert response.get_json()["error"]["details"] == {
            "current_year": 2026,
            "configured_years": [2027],
        }
        assert load_configuration(state_directory) == saved

    def test_an_earlier_years_selection_can_still_be_saved(self, client, member_system, saved, state_directory):
        member_system.canonical_tournament.side_effect = (
            lambda tournament_id: _tournament(tournament_id, "Last year", date(2025, 1, 9))
        )

        response = _save(client, saved.revision, [300])

        assert response.status_code == 200
        assert load_configuration(state_directory).tournament_ids == (300,)

    @pytest.mark.parametrize("payload", [
        {"tournament_ids": [300]},
        {"revision": -1, "tournament_ids": [300]},
        {"revision": True, "tournament_ids": [300]},
        {"revision": 1, "tournament_ids": []},
        {"revision": 1, "tournament_ids": [0]},
        {"revision": 1, "tournament_ids": [300, 300]},
        {"revision": 1, "tournament_ids": [True]},
        {"revision": 1, "tournament_ids": ["300"]},
        {"revision": 1, "tournament_ids": "300"},
        [300],
    ])
    def test_invalid_or_duplicate_ids_do_not_reach_upstream(self, client, member_system, payload):
        response = client.post("/api/v1/configuration/tournaments", json=payload)

        assert response.status_code == 400
        assert response.get_json()["error"]["message_key"] == "errors.configuration.invalid_input"
        member_system.canonical_tournament.assert_not_called()


class TestTournamentDraft:

    DRAFT = [
        {"tournament_id": 300, "name": "March draft", "start_date": "2026-03-01", "end_date": "2026-03-02"},
        {"tournament_id": 250, "name": "January draft", "start_date": "2026-01-10", "end_date": "2026-01-10"},
    ]

    def _put(self, client, revision, tournaments):
        return client.put(
            "/api/v1/configuration/tournaments/draft",
            json={"revision": revision, "tournaments": tournaments},
        )

    def test_the_draft_survives_a_language_switch_without_upstream_lookups(self, client, member_system, saved):
        assert self._put(client, saved.revision, self.DRAFT).status_code == 200

        english = client.get("/configuration?lang=en").get_data(as_text=True)
        swedish = client.get("/configuration?lang=sv").get_data(as_text=True)

        for body in (english, swedish):
            assert body.index("January draft") < body.index("March draft")
            assert "Saved January" not in body
        assert "Tournament name, group, or location" in english
        assert "Turneringsnamn" in swedish
        member_system.canonical_tournament.assert_not_called()

    def test_a_draft_for_an_earlier_revision_is_dropped(self, client, member_system, saved, state_directory):
        assert self._put(client, saved.revision, self.DRAFT).status_code == 200
        save_configuration(state_directory, load_configuration(state_directory), expected_revision=saved.revision)

        body = client.get("/configuration").get_data(as_text=True)

        assert "March draft" not in body
        assert "Saved January" in body

    def test_a_successful_save_clears_the_draft(self, client, member_system, saved):
        assert self._put(client, saved.revision, self.DRAFT).status_code == 200
        assert _save(client, saved.revision, [100, 200]).status_code == 200

        body = client.get("/configuration").get_data(as_text=True)

        assert "March draft" not in body

    @pytest.mark.parametrize("tournaments", [
        [{**DRAFT[0], "tournament_id": 0}],
        [{**DRAFT[0], "name": "  "}],
        [{**DRAFT[0], "start_date": "2026-13-01"}],
        [{**DRAFT[0], "end_date": "2026-02-28"}],
        [DRAFT[0], DRAFT[0]],
        "not a list",
    ])
    def test_an_invalid_draft_is_refused(self, client, saved, tournaments):
        response = self._put(client, saved.revision, tournaments)

        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "invalid_input"


class TestTheTournamentsTab:

    def test_the_tab_mounts_the_localized_picker(self, client):
        body = client.get("/configuration?lang=en").get_data(as_text=True)

        assert 'data-search-endpoint="/api/v1/tournaments/search"' in body
        assert 'data-lookup-endpoint-template="/api/v1/tournaments/__tournament_id__"' in body
        assert 'data-save-endpoint="/api/v1/configuration/tournaments"' in body
        assert re.search(r'data-configuration-revision="1"', body)
        for text in (
            "Tournament name, group, or location", "Type at least 3 characters",
            "Start typing to find a tournament.", "Or paste a tournament ID", "Verify ID",
            "Selected tournaments", "Remove tournament?", "Dismiss notification",
        ):
            assert text in body
        for attribute in (
            'data-search-state="idle"', 'data-selection-state="idle"', "data-tournament-removal-dialog",
            "data-tournament-removal-name", "data-confirm-tournament-removal",
            "data-tournament-selection-toast", "data-tournament-save-status",
        ):
            assert attribute in body
        assert 'src="/static/js/tournament-search.js"' in body

        swedish = client.get("/configuration?lang=sv").get_data(as_text=True)
        for text in ("Turneringsnamn, grupp eller plats", "Skriv minst 3 tecken",
                     "Eller klistra in ett turnerings-id", "Valda turneringar", "Ta bort turneringen?"):
            assert text in swedish

    def test_the_selected_list_sits_above_the_tabs_and_carries_the_resolved_tournaments(self, client):
        body = client.get("/configuration?lang=en").get_data(as_text=True)

        assert body.index('id="selected-tournaments-heading"') < body.index('id="tournament-addition-panel"')
        assert body.index("data-selected-tournaments") < body.index("data-tournament-search-input")
        assert body.index("Saved January") < body.index("Saved February")
        assert '"start_date": "2026-01-05"' in body

    def test_there_is_no_reordering_control(self, client):
        body = client.get("/configuration?lang=en").get_data(as_text=True)
        script = (ROOT / "jgp_standings/web/static/js/tournament-search.js").read_text(encoding="utf-8")

        for word in ("draggable", "Move up", "Move down", "data-move"):
            assert word not in body and word not in script

    def test_unresolvable_saved_tournaments_leave_an_empty_selection_with_the_problem(self, client, member_system):
        member_system.canonical_tournament.side_effect = _refusal()

        body = client.get("/configuration?lang=en").get_data(as_text=True)

        assert "data-tournament-selection-problem" in body
        assert "<code>Klass Junior</code>" in body
        assert '/help/group-names?lang=en' in body
        assert re.search(r'id="selected-tournaments-data" type="application/json">\s*\[\]', body)

    @pytest.mark.parametrize("path", [
        "/static/js/tournament-picker.js",
        "/static/js/tournament-search-state.js",
        "/static/js/tournament-search.js",
        "/static/js/klass-naming.js",
    ])
    def test_the_picker_scripts_are_served_locally(self, client, path):
        response = client.get(path)
        try:
            assert response.status_code == 200
            assert len(response.data) > 100
        finally:
            response.close()

    def test_the_picker_renders_with_the_local_lit_renderer(self):
        source = (ROOT / "jgp_standings/web/static/js/tournament-search.js").read_text(encoding="utf-8")

        assert 'import { html, nothing, render } from "./lit-html.js";' in source
        assert "https://member.schack.se/ShowAllTournamentRegistrationsServlet?id=" in source
        assert 'rel="noopener noreferrer"' in source
        assert "flickornas" not in source
        assert "document.createElement" not in source


class TestGroupNamingHelp:

    @pytest.mark.parametrize("lang, title", [("en", "Group naming conventions"), ("sv", "Namnregler för grupper")])
    def test_the_help_is_localized_and_asks_nothing_of_the_member_system(self, client, member_system, lang, title):
        body = client.get(f"/help/group-names?lang={lang}").get_data(as_text=True)

        assert title in body
        assert "<code>Klass A/B</code>" in body
        assert "<code>Klass Junior</code>" in body
        assert "JDM" in body
        member_system.canonical_tournament.assert_not_called()

    def test_the_configuration_page_links_to_the_help(self, client):
        body = client.get("/configuration?lang=sv").get_data(as_text=True)

        assert 'href="/help/group-names?lang=sv"' in body
        assert "Hjälp med gruppnamn" in body

    def test_every_example_reads_as_the_help_says(self, client):
        body = client.get("/help/group-names?lang=en").get_data(as_text=True)
        accepted, refused = body.split('<h2 class="h4">How to correct a refused name')

        readings = re.findall(r"<tr><td><code>([^<]+)</code></td><td>([a-h]+)</td></tr>", accepted)
        assert len(readings) == 13
        for name, klass in readings:
            assert read_klass(_unescaped(name)) == klass, name

        corrections = re.findall(r"<tr><td><code>([^<]+)</code></td><td><code>([^<]+)</code></td></tr>", refused)
        assert len(corrections) == 11
        for bad, replacement in corrections:
            with pytest.raises(KlassNameRefused):
                read_klass(_unescaped(bad))
            read_klass(_unescaped(replacement))


def _unescaped(text: str) -> str:
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


@pytest.mark.parametrize("script", ["tournament-search-state.mjs", "tournament-picker.mjs", "klass-naming.mjs"])
def test_browser_components(script):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")

    completed = subprocess.run(
        [node, f"tests/browser/{script}"], cwd=ROOT, check=False, capture_output=True, text=True,
    )

    assert completed.returncode == 0, completed.stdout[-2000:] + completed.stderr[-2000:]
