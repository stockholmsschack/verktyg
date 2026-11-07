"""Birth-year bands and player validation for JGP and JDM events.

Checks at two tournament years verify that the bands advance with the season.
"""

import pytest

from jgp_standings.util import jdm_klass_years, klass_years, validate_players


def band(years):
    return (min(years), max(years))


class TestJgpKlassBands:
    """
    A: year-20 .. year-17    B: year-16 .. year-14
    C: year-13 .. year-11    D: year-10 .. year (open-ended downward)
    AB: year-20 .. year-14, for organisers who merge A and B.
    E and above do not exist here; those groups never reach the standings.
    """

    def test_bands_for_2026(self):
        ranges = klass_years(2026)
        assert band(ranges["A"]) == (2006, 2009)
        assert band(ranges["B"]) == (2010, 2012)
        assert band(ranges["AB"]) == (2006, 2012)
        assert band(ranges["C"]) == (2013, 2015)
        assert band(ranges["D"]) == (2016, 2026)

    def test_bands_move_with_the_tournament_year(self):
        ranges = klass_years(2025)
        assert band(ranges["A"]) == (2005, 2008)
        assert band(ranges["B"]) == (2009, 2011)
        assert band(ranges["AB"]) == (2005, 2011)
        assert band(ranges["C"]) == (2012, 2014)
        assert band(ranges["D"]) == (2015, 2025)

    def test_ab_is_exactly_a_and_b_joined(self):
        ranges = klass_years(2026)
        assert set(ranges["AB"]) == set(ranges["A"]) | set(ranges["B"])

    def test_d_accepts_players_younger_than_its_nominal_bottom(self):
        # Younger players may play up, so D is open-ended downward.
        assert 2026 in klass_years(2026)["D"]

    def test_e_and_above_have_no_jgp_band(self):
        ranges = klass_years(2026)
        assert "E" not in ranges
        assert "F" not in ranges

    def test_the_bands_do_not_overlap(self):
        ranges = klass_years(2026)
        a, b, c, d = ranges["A"], ranges["B"], ranges["C"], ranges["D"]
        assert set(a) & set(b) == set()
        assert set(b) & set(c) == set()
        assert set(c) & set(d) == set()

    def test_the_month_argument_does_not_move_the_bands(self):
        # A player stays in the klass they had in January even after the school
        # year turns in August.
        assert klass_years(2026, 1) == klass_years(2026, 9)


class TestJdmKlassBands:
    """
    A: year-20 .. year-17    B: year-16 .. year-14
    C: year-13 .. year-12    D: year-11 .. year-10
    E: year-9 .. year (open-ended downward)
    C and D are narrower than JGP's, and E competes.
    """

    def test_bands_for_2026(self):
        ranges = jdm_klass_years(2026)
        assert band(ranges["A"]) == (2006, 2009)
        assert band(ranges["B"]) == (2010, 2012)
        assert band(ranges["C"]) == (2013, 2014)
        assert band(ranges["D"]) == (2015, 2016)
        assert band(ranges["E"]) == (2017, 2026)

    def test_bands_move_with_the_tournament_year(self):
        ranges = jdm_klass_years(2025)
        assert band(ranges["C"]) == (2012, 2013)
        assert band(ranges["D"]) == (2014, 2015)
        assert band(ranges["E"]) == (2016, 2025)

    def test_jdm_has_no_combined_ab_band(self):
        assert "AB" not in jdm_klass_years(2026)

    def test_c_and_d_differ_from_the_jgp_bands(self):
        jgp, jdm = klass_years(2026), jdm_klass_years(2026)
        assert jgp["C"] != jdm["C"]
        assert jgp["D"] != jdm["D"]
        # A 2015-born player is C under JGP but D under JDM.
        assert 2015 in jgp["C"] and 2015 in jdm["D"]

    def test_a_and_b_are_the_same_as_jgp(self):
        jgp, jdm = klass_years(2026), jdm_klass_years(2026)
        assert jgp["A"] == jdm["A"]
        assert jgp["B"] == jdm["B"]


def player(contender_id, birth_year, klass, points=4.0, sec_points=8.0):
    return {
        "contenderId": contender_id,
        "playerInfo": {"firstName": "Test", "lastName": f"Player{contender_id}", "birthdate": str(birth_year)},
        "klass": klass,
        "points": points,
        "secPoints": sec_points,
    }


class TestValidationAgainstTheBands:
    """
    `validate_players` decides who is in the wrong klass. A player in the wrong
    klass keeps competing but has both points and quality points negated, which
    is what later turns into 0 standings points.
    """

    def test_a_player_in_their_band_is_untouched(self):
        players = [player(1, 2011, "B")]
        assert validate_players(players, 2026, dispensation_dict={}) == []
        assert players[0]["points"] == 4.0
        assert players[0]["secPoints"] == 8.0

    def test_a_player_outside_their_band_is_reported_and_negated(self):
        players = [player(1, 2013, "B")]
        invalid = validate_players(players, 2026, dispensation_dict={})
        assert [entry["contenderId"] for entry in invalid] == [1]
        assert invalid[0] == {"contenderId": 1, "name": "Test Player1", "birthYear": 2013, "klass": "B"}
        assert players[0]["points"] == -4.0
        assert players[0]["secPoints"] == -8.0

    def test_klass_is_matched_case_insensitively(self):
        assert validate_players([player(1, 2011, "b")], 2026, dispensation_dict={}) == []

    def test_a_klass_with_no_band_invalidates_everyone_in_it(self):
        # Klass F has no JGP band, and neither does a group name that produced a
        # nonsense klass. Both are caught here rather than scored.
        players = [player(1, 2020, "F"), player(2, 2011, "JUNIOR")]
        invalid = validate_players(players, 2026, dispensation_dict={})
        assert [entry["contenderId"] for entry in invalid] == [1, 2]

    def test_the_jdm_bands_are_used_for_a_jdm_event(self):
        # 2015 born: C under JGP, D under JDM.
        assert validate_players([player(1, 2015, "C")], 2026, dispensation_dict={}) == []
        assert validate_players([player(1, 2015, "C")], 2026, dispensation_dict={}, is_jdm=True) != []
        assert validate_players([player(1, 2015, "D")], 2026, dispensation_dict={}, is_jdm=True) == []

    def test_klass_e_is_valid_only_at_a_jdm_event(self):
        assert validate_players([player(1, 2018, "E")], 2026, dispensation_dict={}, is_jdm=True) == []
        assert validate_players([player(1, 2018, "E")], 2026, dispensation_dict={}) != []

    def test_a_dispensation_year_inside_the_band_makes_the_entry_valid(self):
        players = [player(1, 2013, "B")]
        assert validate_players(players, 2026, dispensation_dict={1: 2011}) == []
        assert players[0]["points"] == 4.0

    def test_a_dispensation_year_outside_the_band_is_still_invalid(self):
        players = [player(1, 2013, "B")]
        invalid = validate_players(players, 2026, dispensation_dict={1: 2014})
        assert [entry["contenderId"] for entry in invalid] == [1]
        assert players[0]["points"] == -4.0
        # The reported birth year is the real one, not the dispensation year.
        assert invalid[0]["birthYear"] == 2013

    def test_a_dispensation_replaces_the_birth_year_rather_than_widening_the_band(self):
        # The real birth year would have been valid; the dispensation year is not.
        players = [player(1, 2011, "B")]
        assert validate_players(players, 2026, dispensation_dict={1: 2013}) != []

    def test_a_penalty_negates_points_without_a_wrong_klass_report(self):
        players = [player(1, 2011, "B")]
        assert validate_players(players, 2026, dispensation_dict={}, penalties=[1]) == []
        assert players[0]["points"] == -4.0
        assert players[0]["secPoints"] == -8.0

    def test_a_penalty_applies_regardless_of_a_dispensation(self):
        players = [player(1, 2013, "B")]
        assert validate_players(players, 2026, dispensation_dict={1: 2011}, penalties=[1]) == []
        assert players[0]["points"] == -4.0

    def test_a_penalised_player_is_negated_exactly_once(self):
        # Return after applying the penalty to avoid negating the points twice
        # when the player is also in the wrong klass.
        players = [player(1, 2013, "B")]
        validate_players(players, 2026, dispensation_dict={}, penalties=[1])
        assert players[0]["points"] == -4.0

    @pytest.mark.parametrize("klass,birth_year", [("A", 2006), ("A", 2009), ("B", 2010), ("B", 2012),
                                                 ("C", 2013), ("C", 2015), ("D", 2016), ("AB", 2011)])
    def test_band_edges_are_inclusive(self, klass, birth_year):
        assert validate_players([player(1, birth_year, klass)], 2026, dispensation_dict={}) == []
