"""Response validation for the member-system client.

An injected fetch keeps tests offline. Tournament fixtures contain real 2026
group names. Transport retries are covered in `test_upstream_client`.
"""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path

import pytest

from jgp_standings.klass_naming import KlassNameReason
from jgp_standings.upstream_client import (
    BASE_URL,
    CanonicalClub,
    MemberSystemClient,
    PlayerInfo,
    TournamentKlassNamingError,
    TournamentResolutionError,
    UpstreamDataError,
    UpstreamResponseError,
    UpstreamRetryExhausted,
    resolve_canonical_tournaments,
)

ROOT = Path(__file__).resolve().parent.parent
RESPONSES = json.loads((ROOT / "fixtures" / "2026" / "responses.json").read_text())


def tournament_fixture(tournament_id: int) -> dict:
    return deepcopy(RESPONSES[f"{BASE_URL}/tournament/tournament/id/{tournament_id}"])


class FakeFetch:
    """Answers every URL with one response, or each from a table; an exception value is raised."""

    def __init__(self, response=None, *, table=None):
        self.answer = (lambda url: table[url]) if table is not None else (lambda url: response)
        self.calls = []

    def __call__(self, url, stage):
        self.calls.append((url, stage))
        response = self.answer(url)
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)


def client_answering(response, **kwargs) -> tuple[MemberSystemClient, FakeFetch]:
    fetch = FakeFetch(response)
    return MemberSystemClient(fetch=fetch, **kwargs), fetch


def client_over(table, **kwargs) -> tuple[MemberSystemClient, FakeFetch]:
    fetch = FakeFetch(table=table)
    return MemberSystemClient(fetch=fetch, **kwargs), fetch


PLAYER = {
    "id": 123, "firstName": "Ada", "lastName": "Lovelace", "birthdate": "2015",
    "clubId": 10, "club": "Stockholms SK", "country": "SWE", "fideid": 1700000,
}


class TestDistrictClubs:

    def test_the_committed_district_list_becomes_canonical_typed_values(self):
        client, fetch = client_over(RESPONSES)

        clubs = client.district_clubs()

        assert len(clubs) == 3055
        assert clubs[0] == CanonicalClub(club_id=47233, name="!!")
        assert fetch.calls[0][0] == f"{BASE_URL}/organisation/district/clubs/5821"

    def test_an_empty_list_is_successful(self):
        client, _ = client_answering([])
        assert client.district_clubs() == []

    def test_a_malformed_club_is_rejected(self):
        client, _ = client_answering([{"id": 10, "name": ""}])

        with pytest.raises(UpstreamDataError, match=r"fetching district club list.*club name.*non-empty string"):
            client.district_clubs()


class TestPlayerNameSearch:

    def test_it_returns_validated_identities_and_encodes_the_names(self):
        client, fetch = client_answering([PLAYER])

        players = client.player_name_search("My", "Ek Lund")

        assert players == [PlayerInfo(
            player_id=123, first_name="Ada", last_name="Lovelace", birth_year=2015,
            current_club_id=10, current_club_name="Stockholms SK", country="SWE", fide_id=1700000,
        )]
        assert fetch.calls[0][0].endswith("/player/fornamn/My/efternamn/Ek%20Lund")

    def test_an_empty_search_is_successful(self):
        client, _ = client_answering([])
        assert client.player_name_search("My", "Ek") == []

    def test_a_malformed_result_is_rejected(self):
        malformed = {key: value for key, value in PLAYER.items() if key != "lastName"}
        client, _ = client_answering([malformed])

        with pytest.raises(UpstreamDataError, match=r"searching players by name.*missing required field.*lastName"):
            client.player_name_search("My", "Ek")

    def test_a_birthdate_written_as_a_date_time_gives_its_year(self):
        client, _ = client_answering([{**PLAYER, "birthdate": "2015-04-12T00:00:00Z"}])
        assert client.player_name_search("Ada", "Lovelace")[0].birth_year == 2015

    def test_a_zero_club_id_means_no_current_club(self):
        client, _ = client_answering([{**PLAYER, "clubId": 0, "club": ""}])

        player = client.player_name_search("Ada", "Lovelace")[0]

        assert player.current_club_id is None
        assert player.current_club_name is None

    def test_an_upstream_failure_is_preserved(self):
        failure = UpstreamRetryExhausted("searching players by name", "HTTP 503; exhausted 2 attempts")
        client, _ = client_answering(failure)

        with pytest.raises(UpstreamRetryExhausted, match=r"searching players by name.*HTTP 503"):
            client.player_name_search("My", "Ek")


class TestTournamentGroupSearch:

    HITS = [
        {"id": 18319, "name": "Klass A (2006-2009)", "tournamentid": 6685,
         "tournamentname": "Wasa JGP 2026", "latestUpdatedGame": "2026-04-26T16:05:00Z"},
        {"id": 18318, "name": "Klass B (2010-2012)", "tournamentid": 6685, "tournamentname": "Wasa JGP 2026"},
    ]

    def test_the_search_word_is_one_encoded_path_segment(self):
        client, fetch = client_answering(self.HITS)

        hits = client.tournament_group_search("Wasa JGP/2026")

        assert fetch.calls[0][0].endswith("/tournament/group/search/Wasa%20JGP%2F2026")
        assert [(hit.matching_group_id, hit.tournament_id) for hit in hits] == [(18319, 6685), (18318, 6685)]
        assert hits[0].latest_updated_game == "2026-04-26T16:05:00Z"
        assert hits[1].latest_updated_game is None

    def test_an_empty_search_is_successful(self):
        client, _ = client_answering([])
        assert client.tournament_group_search("saknas") == []

    def test_a_malformed_result_is_rejected(self):
        client, _ = client_answering([{"id": 1, "name": "Klass A", "tournamentid": 2}])

        with pytest.raises(UpstreamDataError, match=r"searching tournament groups.*missing required field.*tournamentname"):
            client.tournament_group_search("Wasa")


class TestCanonicalTournament:

    def test_an_ordinary_jgp_keeps_klass_a_to_d(self):
        client, _ = client_over(RESPONSES)

        tournament = client.canonical_tournament(6685)

        assert (tournament.name, tournament.start_date, tournament.end_date) == (
            "Wasa JGP 2026", date(2026, 4, 25), date(2026, 4, 26),
        )
        assert [(group.group_id, group.klass) for group in tournament.processable_groups] == [
            (18319, "a"), (18318, "b"), (18320, "c"), (18321, "d"),
        ]

    def test_a_merged_group_reads_as_ab(self):
        client, _ = client_over(RESPONSES)

        groups = client.canonical_tournament(6534).processable_groups

        assert [(group.name, group.klass) for group in groups][0] == ("Klass A/B (2006-2012)", "ab")

    def test_a_jdm_event_keeps_klass_e(self):
        client, _ = client_over(RESPONSES)

        groups = client.canonical_tournament(6853).processable_groups

        assert [group.klass for group in groups] == ["a", "b", "c", "d", "e"]

    def test_a_refused_group_name_refuses_the_tournament_naming_every_refused_group(self):
        tournament = tournament_fixture(6685)
        tournament["rootClasses"][0]["groups"][0]["name"] = "Final"
        tournament["rootClasses"][0]["groups"][1]["name"] = "KLASS B"
        client, _ = client_answering(tournament)

        with pytest.raises(TournamentKlassNamingError) as raised:
            client.canonical_tournament(6685)

        assert [(problem.group_id, problem.reason) for problem in raised.value.klass_problems] == [
            (18319, KlassNameReason.NO_KLASS), (18318, KlassNameReason.KEYWORD_SPELLING),
        ]

    def test_only_the_first_root_class_is_read(self):
        tournament = tournament_fixture(6685)
        tournament["rootClasses"] = [
            {"groups": [{**group, "name": "Klass E"} for group in tournament["rootClasses"][0]["groups"]]},
            tournament["rootClasses"][0],
        ]
        client, _ = client_answering(tournament)

        with pytest.raises(TournamentResolutionError, match=r"tournament 6685.*no group competing in the JGP standings"):
            client.canonical_tournament(6685)

    def test_an_unknown_tournament_is_unresolvable(self):
        client, fetch = client_answering(UpstreamResponseError("x", "HTTP 404 (not retried)", status_code=404))

        with pytest.raises(TournamentResolutionError, match=r"tournament 999 cannot be resolved: not found"):
            client.canonical_tournament(999)

        assert len(fetch.calls) == 1

    def test_a_non_positive_id_is_refused_without_a_request(self):
        client, fetch = client_over(RESPONSES)

        with pytest.raises(TournamentResolutionError, match="positive integer"):
            client.canonical_tournament(0)

        assert fetch.calls == []

    def test_a_response_for_another_tournament_is_rejected(self):
        client, _ = client_answering(tournament_fixture(6540))

        with pytest.raises(UpstreamDataError, match="does not match requested id"):
            client.canonical_tournament(6685)

    def test_a_malformed_date_is_rejected(self):
        tournament = tournament_fixture(6685)
        tournament["end"] = "not-a-date"
        client, _ = client_answering(tournament)

        with pytest.raises(UpstreamDataError, match=r"resolving canonical tournament 6685.*tournament end.*ISO date"):
            client.canonical_tournament(6685)

    def test_a_malformed_group_is_rejected(self):
        tournament = tournament_fixture(6685)
        del tournament["rootClasses"][0]["groups"][0]["name"]
        client, _ = client_answering(tournament)

        with pytest.raises(UpstreamDataError, match=r"resolving canonical tournament 6685.*tournament group.*name"):
            client.canonical_tournament(6685)

    def test_an_upstream_failure_is_preserved(self):
        client, _ = client_answering(UpstreamRetryExhausted("resolving canonical tournament 6685", "HTTP 503"))

        with pytest.raises(UpstreamRetryExhausted, match=r"resolving canonical tournament 6685.*HTTP 503"):
            client.canonical_tournament(6685)


class TestResolvingTheConfiguredTournaments:

    def test_the_committed_tournaments_resolve_in_start_date_order(self):
        client, _ = client_over(RESPONSES)

        tournaments = resolve_canonical_tournaments(client, (6853, 6544, 6685)).require_all()

        assert [tournament.tournament_id for tournament in tournaments] == [6544, 6685, 6853]

    def test_collected_problems_put_naming_refusals_first(self):
        refused = tournament_fixture(6685)
        refused["rootClasses"][0]["groups"][0]["name"] = "Final"
        missing = UpstreamResponseError("x", "HTTP 404 (not retried)", status_code=404)
        client, _ = client_over({
            f"{BASE_URL}/tournament/tournament/id/999": missing,
            f"{BASE_URL}/tournament/tournament/id/6685": refused,
        })

        result = resolve_canonical_tournaments(client, (999, 6685), collect_problems=True)

        assert [problem.tournament_id for problem in result.problems] == [999, 6685]
        with pytest.raises(TournamentKlassNamingError):
            result.require_all()


class TestTheResponseCache:

    def test_a_repeated_request_is_answered_from_the_cache_until_it_expires(self):
        now = [0.0]
        client, fetch = client_over(RESPONSES, clock=lambda: now[0])

        client.canonical_tournament(6685)
        client.canonical_tournament(6685)
        assert len(fetch.calls) == 1

        now[0] = MemberSystemClient.DEFAULT_SHORT_CACHE_TTL + 1
        client.canonical_tournament(6685)
        assert len(fetch.calls) == 2

    def test_the_club_list_is_kept_for_the_longer_reference_ttl(self):
        now = [MemberSystemClient.DEFAULT_SHORT_CACHE_TTL + 1]
        client, fetch = client_over(RESPONSES, clock=lambda: now[0])

        client.district_clubs()
        now[0] *= 2
        client.district_clubs()

        assert len(fetch.calls) == 1

    def test_a_caller_cannot_change_a_cached_value(self):
        client, _ = client_answering([PLAYER])

        client.player_name_search("Ada", "Lovelace").clear()

        assert len(client.player_name_search("Ada", "Lovelace")) == 1

    def test_a_failure_is_not_cached(self):
        fetch = FakeFetch(UpstreamRetryExhausted("searching players by name", "HTTP 503"))
        client = MemberSystemClient(fetch=fetch)

        for _ in range(2):
            with pytest.raises(UpstreamRetryExhausted):
                client.player_name_search("Ada", "Lovelace")

        assert len(fetch.calls) == 2

    def test_negative_ttls_are_refused(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            MemberSystemClient(fetch=None, short_cache_ttl=-1)

