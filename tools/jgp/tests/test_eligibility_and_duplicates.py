"""Eligibility filtering and duplicate resolution before points allocation.

Non-local players consume no standings slots. Duplicate entries for a player
in one tournament resolve to the entry with the lowest quality points.
"""

import polars as pl

from jgp_standings.generation_orchestrator import remove_duplicates_by_secpoints
from jgp_standings.points import assign_standings_points
from jgp_standings.util import filter_non_local_players


def registration(contender_id, sec_points, points=4.0, place=1):
    return {"contenderId": contender_id, "points": points, "secPoints": sec_points, "place": place}


class TestDuplicateResolution:

    def test_a_duplicate_is_resolved_to_the_lowest_quality_points(self):
        resolved = remove_duplicates_by_secpoints([
            registration(1, 12.0),
            registration(1, 7.5),
            registration(1, 9.0),
        ])
        assert [entry["secPoints"] for entry in resolved] == [7.5]

    def test_the_first_entry_wins_when_quality_points_are_level(self):
        resolved = remove_duplicates_by_secpoints([
            registration(1, 7.0, points=4.0),
            registration(1, 7.0, points=2.0),
        ])
        assert [entry["points"] for entry in resolved] == [4.0]

    def test_distinct_players_are_untouched_and_keep_their_order(self):
        resolved = remove_duplicates_by_secpoints([
            registration(3, 5.0), registration(1, 9.0), registration(2, 7.0),
        ])
        assert [entry["contenderId"] for entry in resolved] == [3, 1, 2]

    def test_resolution_is_per_player(self):
        resolved = remove_duplicates_by_secpoints([
            registration(1, 9.0), registration(2, 4.0), registration(1, 3.0), registration(2, 8.0),
        ])
        assert {entry["contenderId"]: entry["secPoints"] for entry in resolved} == {1: 3.0, 2: 4.0}


def known(contender_id, name, local):
    return {str(contender_id): {"name": name, "local": local, "klubb": "Någon SK"}}


class TestNonLocalFiltering:

    def test_local_players_are_kept_and_non_local_players_are_removed(self):
        player_list = {**known(1, "Local One", True), **known(2, "Visitor Two", False)}
        rejected = []

        kept = filter_non_local_players(
            [registration(1, 9.0), registration(2, 8.0)],
            "Wasa JGP 2026", player_list, rejected,
        )

        assert [entry["contenderId"] for entry in kept] == [1]
        assert rejected == ["Wasa JGP 2026: Visitor Two (2)"]

    def test_there_is_no_override_for_a_player_whose_main_club_is_outside_the_district(self):
        # Multiple club memberships where the main club is outside the district: non-local.
        player_list = known(2, "Visitor Two", False)
        rejected = []

        kept = filter_non_local_players([registration(2, 8.0)], "Wasa JGP 2026", player_list, rejected)

        assert kept == []
        assert rejected == ["Wasa JGP 2026: Visitor Two (2)"]

    def test_the_walkover_dummy_player_is_dropped_silently(self):
        rejected = []
        kept = filter_non_local_players([registration(-100, 0.0)], "Wasa JGP 2026", {}, rejected)
        assert kept == []
        assert rejected == []

    def test_an_unknown_player_is_dropped_without_being_reported_as_non_local(self):
        rejected = []
        kept = filter_non_local_players([registration(99, 5.0)], "Wasa JGP 2026", {}, rejected)
        assert kept == []
        assert rejected == []


class TestNonLocalRemovalPrecedesAllocation:
    """
    The consequence of the ordering: a non-local player who would have finished
    ahead takes no slot, so everyone below them scores higher than they would in
    a series that removed non-locals afterwards.
    """

    @staticmethod
    def _allocate(entries):
        frame = pl.DataFrame(
            [{"id": entry["contenderId"], "place": entry["place"],
              "points": entry["points"], "secPoints": entry["secPoints"]} for entry in entries],
            schema={"id": pl.Int64, "place": pl.Int64, "points": pl.Float64, "secPoints": pl.Float64},
        )
        result = assign_standings_points(frame, "place", "points", "secPoints", "STD", "id")
        return {row["id"]: row["STD"] for row in result.to_dicts()}

    def test_a_removed_non_local_frees_the_slot_they_would_have_taken(self):
        player_list = {**known(1, "Visitor One", False), **known(2, "Local Two", True)}
        entries = [
            registration(1, 10.0, points=5.0, place=1),  # non-local winner
            registration(2, 8.0, points=4.0, place=2),   # local runner-up
        ]

        with_visitor = self._allocate(entries)
        assert with_visitor[2] == 22.0

        locals_only = self._allocate(
            filter_non_local_players(entries, "Wasa JGP 2026", player_list, [])
        )
        assert locals_only == {2: 25.0}
