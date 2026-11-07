"""Standings order must be independent of full-join row order.

Permutations of tied players must produce identical rankings, with descending
contenderId as the final tie-break.
"""
import polars as pl

from jgp_standings.generation_core import rank_band

TOURNAMENT_COUNT = 2


def _tied_group():
    """
    Four players in two tied pairs. Both play both tournaments and finish in exactly inverted
    orders, so each pair shares a Σ (25+18 and 22+20) as well as a tournament count, a birth year,
    and a last scoring round — every ranking key the standings sort has. Only contenderId separates
    them, and the higher id places first.
    """
    players = [
        # Names sort in reverse id order, so sorting by name would reverse both pairs.
        (401, "Alma Berg", 4.0, 1.0),
        (402, "Bo Dahl", 3.0, 2.0),
        (403, "Cleo Ek", 2.0, 3.0),
        (404, "Dag Frisk", 1.0, 4.0),
    ]
    return pl.DataFrame([
        {
            "contenderId": contender_id,
            "Namn": name,
            "playerInfo.birthdate": 2014,
            "year_group": "2014",
            "place_1": 1, "p_1": first, "kvp_1": first,
            "place_2": 1, "p_2": second, "kvp_2": second,
        }
        for contender_id, name, first, second in players
    ])


class TestOrderIsIndependentOfInputOrder:

    def test_shuffled_copies_of_one_group_produce_one_order(self):
        group = _tied_group()
        results = []
        for seed in range(5):
            ranked = rank_band(group.sample(fraction=1.0, shuffle=True, seed=seed), TOURNAMENT_COUNT)
            results.append(ranked["contenderId"].to_list())

        assert all(order == results[0] for order in results), results

    def test_ties_are_broken_by_contender_id_highest_first(self):
        result = rank_band(_tied_group(), TOURNAMENT_COUNT)

        assert result["Σ"].to_list() == [43.0, 43.0, 42.0, 42.0]
        assert result["contenderId"].to_list() == [404, 401, 403, 402]
        assert result["Namn"].to_list() == ["Dag Frisk", "Alma Berg", "Cleo Ek", "Bo Dahl"]
        assert result["Placering"].to_list() == [1, 2, 3, 4]
