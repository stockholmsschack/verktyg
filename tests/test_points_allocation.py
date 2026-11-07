"""Standings-point allocation within one tournament and age band.

Ties consume successive slots, ordered by descending player id. Null places
and penalised players consume no slots. Ranked penalties score zero; entries
past the list score one. Unassigned entries with quality points receive one
point in the fallback pass.
"""

from itertools import permutations

import polars as pl
import pytest

from jgp_standings.points import assign_standings_points

POINTS_LIST = [25, 22, 20, 18, 16, 14, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]

SCHEMA = {
    "id": pl.Int64,
    "place": pl.Int64,
    "points": pl.Float64,
    "secPoints": pl.Float64,
}


def allocate(rows):
    """Allocate over one group and return {player id: standings points}."""
    result = assign_standings_points(
        pl.DataFrame(rows, schema=SCHEMA), "place", "points", "secPoints", "STD", "id"
    )
    return {row["id"]: row["STD"] for row in result.to_dicts()}


def entrant(id, place, points, sec_points):
    return {"id": id, "place": place, "points": points, "secPoints": sec_points}


class TestTheStandingsPointsList:

    def test_the_list_and_its_length(self):
        awarded = allocate([
            entrant(index, index, float(100 - index), float(100 - index))
            for index in range(1, len(POINTS_LIST) + 1)
        ])
        assert [awarded[index] for index in range(1, len(POINTS_LIST) + 1)] == [
            float(value) for value in POINTS_LIST
        ]

    def test_a_player_past_the_end_of_the_list_scores_one(self):
        count = len(POINTS_LIST) + 3
        awarded = allocate([
            entrant(index, index, float(100 - index), float(100 - index))
            for index in range(1, count + 1)
        ])
        assert awarded[len(POINTS_LIST)] == 1.0
        assert [awarded[index] for index in range(len(POINTS_LIST) + 1, count + 1)] == [1.0, 1.0, 1.0]

    def test_the_point_past_the_end_is_awarded_by_the_allocation_itself(self):
        # Omit quality points so the fallback pass cannot award this player a point.
        # The ranked allocation must award it after exhausting the points list.
        rows = [entrant(index, index, float(100 - index), float(100 - index))
                for index in range(1, len(POINTS_LIST) + 1)]
        rows.append(entrant(99, len(POINTS_LIST) + 1, 0.0, None))

        assert allocate(rows)[99] == 1.0


class TestOrdering:

    def test_ranking_is_by_points_then_quality_points_descending(self):
        awarded = allocate([
            entrant(1, 3, 3.0, 5.0),
            entrant(2, 1, 4.0, 6.0),
            entrant(3, 2, 3.0, 9.0),
        ])
        assert awarded == {2: 25.0, 3: 22.0, 1: 20.0}

    def test_the_place_column_does_not_decide_the_order(self):
        # `place` only indicates whether the player took part.
        awarded = allocate([
            entrant(1, 1, 2.0, 2.0),
            entrant(2, 9, 5.0, 5.0),
        ])
        assert awarded == {2: 25.0, 1: 22.0}


class TestTiesAreNotAveraged:

    def test_players_level_on_points_and_quality_points_consume_successive_slots(self):
        awarded = allocate([
            entrant(1, 1, 4.0, 8.0),
            entrant(2, 2, 4.0, 8.0),
            entrant(3, 3, 4.0, 8.0),
        ])
        assert awarded == {3: 25.0, 2: 22.0, 1: 20.0}

    def test_no_tied_player_receives_an_averaged_value(self):
        awarded = allocate([entrant(1, 1, 4.0, 8.0), entrant(2, 2, 4.0, 8.0)])
        # Averaging would give both 23.5.
        assert awarded == {2: 25.0, 1: 22.0}


class TestTheOrderIsTotal:
    """
    Ranking on points and quality points alone leaves tied players in whatever order
    the rows arrived in. The id breaks the tie, higher first, as the band ranking does.
    """

    ROWS = [
        entrant(1, 1, 4.0, 8.0),
        entrant(2, 2, 4.0, 8.0),
        entrant(3, 3, 4.0, 8.0),
        entrant(4, 4, 5.0, 9.0),
        entrant(5, 5, -4.0, -8.0),
        entrant(6, None, 2.0, 4.0),
    ]

    def test_every_input_order_gives_the_same_allocation(self):
        expected = {4: 25.0, 3: 22.0, 2: 20.0, 1: 18.0, 5: 0.0, 6: 1.0}

        for ordering in permutations(self.ROWS):
            assert allocate(list(ordering)) == expected


class TestNullPlace:

    def test_a_null_place_consumes_no_slot(self):
        awarded = allocate([
            entrant(1, 1, 5.0, 9.0),
            entrant(2, None, 4.0, 8.0),
            entrant(3, 2, 3.0, 7.0),
        ])
        assert awarded[1] == 25.0
        assert awarded[3] == 22.0

    def test_a_player_with_quality_points_but_no_place_scores_one(self):
        awarded = allocate([
            entrant(1, 1, 5.0, 9.0),
            entrant(2, None, 4.0, 8.0),
        ])
        assert awarded[2] == 1.0

    def test_a_player_with_neither_place_nor_quality_points_scores_nothing(self):
        # Did not take part in this tournament at all.
        awarded = allocate([
            entrant(1, 1, 5.0, 9.0),
            entrant(2, None, None, None),
        ])
        assert awarded[2] is None


class TestPenalisedPlayers:

    def test_a_penalised_player_scores_zero(self):
        awarded = allocate([
            entrant(1, 1, 5.0, 9.0),
            entrant(2, 2, -4.0, -8.0),
        ])
        assert awarded[2] == 0.0

    def test_a_penalised_player_consumes_no_slot(self):
        awarded = allocate([
            entrant(1, 1, 5.0, 9.0),
            entrant(2, 2, -4.0, -8.0),
            entrant(3, 3, 3.0, 7.0),
        ])
        assert awarded[1] == 25.0
        assert awarded[3] == 22.0
        assert awarded[2] == 0.0

    def test_the_player_below_a_penalised_player_takes_the_slot_they_left(self):
        # Match points rank the penalised player second; negative quality points
        # mark the penalty. If that player consumed a slot, the next would score 20.
        awarded = allocate([
            entrant(1, 1, 5.0, 9.0),
            entrant(2, 2, 4.0, -1.0),
            entrant(3, 3, 3.0, 7.0),
        ])
        assert awarded == {1: 25.0, 2: 0.0, 3: 22.0}

    def test_a_penalised_player_at_the_top_of_the_order_still_consumes_no_slot(self):
        awarded = allocate([
            entrant(1, 1, -9.0, -9.0),
            entrant(2, 2, 1.0, 1.0),
        ])
        assert awarded == {2: 25.0, 1: 0.0}

    def test_the_penalty_is_recognised_by_negative_quality_points(self):
        # Only negative secPoints trigger a penalty.
        awarded = allocate([
            entrant(1, 1, -4.0, 8.0),
            entrant(2, 2, 3.0, 7.0),
        ])
        assert awarded[1] == 22.0
        assert awarded[2] == 25.0

    def test_a_penalised_player_who_did_not_place_is_not_zeroed(self):
        # Pass one requires a place, so a penalised entry without one falls
        # through to the one-point pass instead.
        awarded = allocate([
            entrant(1, 1, 5.0, 9.0),
            entrant(2, None, -4.0, -8.0),
        ])
        assert awarded[2] == 1.0


class TestTheAllocationAsAWhole:

    def test_a_mixed_group(self):
        awarded = allocate([
            entrant(1, 1, 5.0, 10.0),   # winner
            entrant(2, 2, 4.0, 9.0),    # runner-up
            entrant(3, 3, 4.0, 9.0),    # level with the runner-up, no averaging
            entrant(4, 4, -3.0, -6.0),  # penalised
            entrant(5, None, 2.0, 4.0),  # no place, has quality points
            entrant(6, None, None, None),  # absent
            entrant(7, 5, 1.0, 2.0),    # last of the placed players
        ])
        assert awarded[1] == 25.0
        assert (awarded[3], awarded[2]) == (22.0, 20.0)
        assert awarded[4] == 0.0
        assert awarded[5] == 1.0
        assert awarded[6] is None
        assert awarded[7] == 18.0

    @pytest.mark.parametrize("column", ["STD"])
    def test_the_result_column_is_float(self, column):
        result = assign_standings_points(
            pl.DataFrame([entrant(1, 1, 5.0, 9.0)], schema=SCHEMA),
            "place", "points", "secPoints", column, "id",
        )
        assert result.schema[column] == pl.Float64
