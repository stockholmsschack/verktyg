"""Season rollover clears separators and advances dispensation years.

Players born in Y - 17 or earlier are dropped. Contributor, orientation, club
names and penalties are preserved.
"""
from datetime import UTC, date, datetime
import re
from unittest.mock import Mock

import pytest

from jgp_standings.active_configuration import ActiveConfiguration, DispensationPlayer, load_configuration, save_configuration
from jgp_standings.dispensation_entry import carry_dispensations_forward
from jgp_standings.web import create_app
from jgp_standings.upstream_client import CanonicalTournament, ProcessableTournamentGroup

DECEMBER_31_2026 = datetime(2026, 12, 31, 22, 59, tzinfo=UTC)
JANUARY_1_2027 = datetime(2026, 12, 31, 23, 0, tzinfo=UTC)


def _tournament(tournament_id: int, name: str, start_date: date) -> CanonicalTournament:
    return CanonicalTournament(
        tournament_id=tournament_id,
        name=name,
        start_date=start_date,
        end_date=start_date,
        processable_groups=(ProcessableTournamentGroup(tournament_id + 1000, "Klass A", "a"),),
    )


TOURNAMENTS = {
    100: _tournament(100, "Saved 2026 tournament", date(2026, 10, 1)),
    200: _tournament(200, "January 2027 tournament", date(2027, 1, 9)),
    400: _tournament(400, "Future tournament", date(2028, 2, 1)),
}


def _configuration() -> ActiveConfiguration:
    return ActiveConfiguration(
        tournament_ids=(100,),
        contributor={"name": "Test Contributor", "email": "test@example.com"},
        landscape=True,
        club_display_names={"Kista SC KFUM Schacksektion": "Kista SC KFUM"},
        finals_separators={"2010-2012": (10, 18)},
        dispensation_dict={11: 2012, 22: 2009, 33: 2008},
        dispensation_players={
            11: DispensationPlayer("Young Player", 2011, "SS 4 Springare"),
            22: DispensationPlayer("Oldest Band Player", 2010, "Wasa SK"),
            33: DispensationPlayer("Aged Out Player", 2007, "Wasa SK"),
        },
        penalty_dict={100: (726286,)},
    )


class MutableClock:
    def __init__(self, instant: datetime) -> None:
        self.instant = instant

    def __call__(self) -> datetime:
        return self.instant


@pytest.fixture
def state_directory(tmp_path):
    return tmp_path / "state"


@pytest.fixture
def saved(state_directory):
    return save_configuration(state_directory, _configuration())


@pytest.fixture
def clock():
    return MutableClock(DECEMBER_31_2026)


@pytest.fixture
def app(tmp_path, state_directory, saved, clock):
    member_system = Mock()
    member_system.canonical_tournament.side_effect = lambda tournament_id: TOURNAMENTS[tournament_id]
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "calendar-rollover-tests",
        "STATE_DIRECTORY": state_directory,
        "OUTPUT_DIRECTORY": tmp_path / "output",
        "MEMBER_SYSTEM_CLIENT": member_system,
        "CLOCK": clock,
    })


@pytest.fixture
def client(app):
    return app.test_client()


def _start(client, revision):
    return client.post("/api/v1/configuration/tournaments/rollover", json={"revision": revision})


def _save(client, revision, tournament_ids):
    return client.post(
        "/api/v1/configuration/tournaments",
        json={"revision": revision, "tournament_ids": tournament_ids},
    )


def _draft(client, revision, tournament_ids):
    return client.put(
        "/api/v1/configuration/tournaments/draft",
        json={
            "revision": revision,
            "tournaments": [
                {
                    "tournament_id": tournament_id,
                    "name": TOURNAMENTS[tournament_id].name,
                    "start_date": TOURNAMENTS[tournament_id].start_date.isoformat(),
                    "end_date": TOURNAMENTS[tournament_id].end_date.isoformat(),
                }
                for tournament_id in tournament_ids
            ],
        },
    )


def _page(client, language="en"):
    return client.get(f"/configuration?lang={language}").get_data(as_text=True)


class TestCarryDispensationsForward:

    def test_each_year_moves_on_and_the_oldest_band_is_dropped(self):
        carried = carry_dispensations_forward(_configuration(), 2027)

        assert carried.dispensation_dict == {11: 2013}
        assert carried.dispensation_players == {
            11: DispensationPlayer("Young Player", 2011, "SS 4 Springare"),
        }

    def test_everything_else_is_left_alone(self):
        configuration = _configuration()

        carried = carry_dispensations_forward(configuration, 2027)

        assert carried.tournament_ids == configuration.tournament_ids
        assert carried.finals_separators == configuration.finals_separators
        assert carried.penalty_dict == configuration.penalty_dict


class TestRolloverOffer:

    def test_the_offer_appears_at_stockholm_midnight(self, client, clock):
        assert "Start 2027 season" not in _page(client)

        clock.instant = JANUARY_1_2027
        page = _page(client)

        assert "Start 2027 season" in page
        assert 'data-start-endpoint="/api/v1/configuration/tournaments/rollover"' in page
        assert 'src="/static/js/calendar-rollover.js"' in page

    def test_the_offer_remains_after_january(self, client, clock):
        clock.instant = datetime(2027, 8, 1, 10, tzinfo=UTC)

        assert "Starta säsongen 2027" in _page(client, "sv")

    def test_a_current_selection_cannot_start_a_rollover(self, client, saved, state_directory):
        before = (state_directory / "active-configuration.toml").read_bytes()

        response = _start(client, saved.revision)

        assert response.status_code == 409
        assert response.get_json()["error"]["code"] == "rollover_unavailable"
        assert response.get_json()["error"]["details"]["state"] == "current"
        assert (state_directory / "active-configuration.toml").read_bytes() == before


class TestRolloverJourney:

    @pytest.fixture(autouse=True)
    def new_year(self, clock):
        clock.instant = JANUARY_1_2027

    def test_starting_persists_nothing_and_blocks_generation(self, client, saved, state_directory):
        before = (state_directory / "active-configuration.toml").read_bytes()

        started = _start(client, saved.revision)

        assert started.status_code == 200
        assert started.get_json()["data"] == {"revision": saved.revision, "rollover_year": 2027, "started": True}
        assert (state_directory / "active-configuration.toml").read_bytes() == before
        page = _page(client)
        assert "Choose at least one verified 2027 tournament" in page
        assert "data-cancel-calendar-rollover" in page
        assert "Saved 2026 tournament" not in page
        assert 'data-rollover-active="true"' in page
        assert re.search(r"<button(?=[^>]*data-generation-open)(?=[^>]*disabled)[^>]*>", page)
        assert "Generation is unavailable until the new season's tournaments are saved." in page

    def test_an_empty_or_earlier_year_selection_is_refused(self, client, saved, state_directory):
        _start(client, saved.revision)
        before = (state_directory / "active-configuration.toml").read_bytes()

        empty = _save(client, saved.revision, [])
        earlier = _save(client, saved.revision, [100])

        assert empty.status_code == 400
        assert earlier.status_code == 422
        assert earlier.get_json()["error"]["code"] == "invalid_rollover_tournament_year"
        assert earlier.get_json()["error"]["message_key"] == "errors.configuration.rollover_tournament_year"
        assert (state_directory / "active-configuration.toml").read_bytes() == before

    def test_a_verified_save_completes_the_rollover(self, client, saved, state_directory):
        _start(client, saved.revision)
        assert _draft(client, saved.revision, [200]).status_code == 200

        response = _save(client, saved.revision, [200])

        assert response.status_code == 200
        data = response.get_json()["data"]
        assert data["rollover_completed"] is True
        assert data["dispensations"] == [{
            "player_id": 11, "name": "Young Player", "birth_year": 2011,
            "club": "SS 4 Springare", "dispensation_year": 2013,
        }]
        persisted = load_configuration(state_directory)
        assert persisted.tournament_ids == (200,)
        assert persisted.finals_separators == {}
        assert persisted.dispensation_dict == {11: 2013}
        assert persisted.dispensation_players == {
            11: DispensationPlayer("Young Player", 2011, "SS 4 Springare"),
        }
        assert persisted.contributor == saved.contributor
        assert persisted.landscape is True
        assert persisted.club_display_names == saved.club_display_names
        assert persisted.penalty_dict == saved.penalty_dict
        page = _page(client)
        assert "data-calendar-rollover" not in page
        assert 'data-rollover-active="false"' in page

    def test_a_carried_dispensation_is_ordinary_configuration(self, client, saved, state_directory):
        _start(client, saved.revision)
        rolled = _save(client, saved.revision, [200]).get_json()["data"]

        edited = client.post("/api/v1/configuration/dispensations", json={
            "revision": rolled["revision"],
            "dispensations": [{"player_id": 11, "dispensation_year": 2014}],
        })
        removed = client.post("/api/v1/configuration/dispensations", json={
            "revision": edited.get_json()["data"]["revision"], "dispensations": [],
        })

        assert edited.status_code == 200
        assert removed.status_code == 200
        assert load_configuration(state_directory).dispensation_dict == {}

    def test_cancelling_restores_the_saved_selection(self, client, saved, state_directory):
        before = (state_directory / "active-configuration.toml").read_bytes()
        _start(client, saved.revision)

        cancelled = client.delete(
            "/api/v1/configuration/tournaments/rollover", json={"revision": saved.revision}
        )

        assert cancelled.status_code == 200
        assert cancelled.get_json()["data"] == {"revision": saved.revision, "cancelled": True}
        assert (state_directory / "active-configuration.toml").read_bytes() == before
        page = _page(client)
        assert "Saved 2026 tournament" in page
        assert "Start 2027 season" in page

    def test_a_stale_revision_is_refused(self, client, saved, state_directory):
        before = (state_directory / "active-configuration.toml").read_bytes()

        response = _start(client, saved.revision + 1)

        assert response.status_code == 409
        assert response.get_json()["error"]["code"] == "stale_configuration"
        assert (state_directory / "active-configuration.toml").read_bytes() == before

    def test_a_draft_whose_revision_has_moved_on_is_dropped(self, client, saved, state_directory):
        _start(client, saved.revision)
        save_configuration(
            state_directory,
            load_configuration(state_directory),
            expected_revision=saved.revision,
        )

        page = _page(client)

        assert "data-cancel-calendar-rollover" not in page
        assert "Start 2027 season" in page

    def test_a_rollover_begun_in_an_earlier_year_is_refused_and_dropped(self, client, saved, clock):
        _start(client, saved.revision)
        clock.instant = datetime(2028, 1, 2, 10, tzinfo=UTC)

        response = _save(client, saved.revision, [200])
        page = _page(client)

        assert response.status_code == 422
        assert response.get_json()["error"]["code"] == "invalid_rollover_tournament_year"
        assert "data-cancel-calendar-rollover" not in page
        assert "Start 2028 season" in page


class TestOrdinarySave:

    def test_a_save_outside_a_rollover_keeps_separators_and_dispensations(
        self, client, saved, state_directory, clock,
    ):
        clock.instant = datetime(2027, 1, 10, 10, tzinfo=UTC)

        response = _save(client, saved.revision, [200])

        assert response.status_code == 200
        assert "rollover_completed" not in response.get_json()["data"]
        persisted = load_configuration(state_directory)
        assert persisted.finals_separators == saved.finals_separators
        assert persisted.dispensation_dict == saved.dispensation_dict

    def test_mixed_and_future_selections_are_not_saved(self, client, saved, state_directory, clock):
        clock.instant = datetime(2026, 8, 1, 10, tzinfo=UTC)
        before = load_configuration(state_directory)

        for tournament_ids, expected_code in (
            ([100, 200], "mixed_tournament_years"),
            ([400], "future_tournament_year"),
        ):
            response = _save(client, saved.revision, tournament_ids)
            assert response.status_code == 422
            assert response.get_json()["error"]["code"] == expected_code
            assert load_configuration(state_directory) == before
