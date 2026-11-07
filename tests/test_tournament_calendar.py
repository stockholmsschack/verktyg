"""Rating-list dates and calendar status derived from supplied tournament metadata."""
from datetime import date

import pytest

from jgp_standings.tournament_calendar import (
    assess_calendar,
    collect_tournament_dates,
    latest_ended_end_date,
    start_years,
    tournament_dates,
)


def upstream(start, end):
    return {"start": start, "end": end}


def fetch_from(catalogue):
    def fetch(tournament_id):
        return catalogue[tournament_id]
    return fetch


class TestTournamentDates:

    def test_a_valid_tournament_parses_start_and_end(self):
        dates = tournament_dates(1, upstream("2026-03-07", "2026-03-08"))
        assert dates.tournament_id == 1
        assert dates.start == date(2026, 3, 7)
        assert dates.end == date(2026, 3, 8)

    def test_a_single_day_tournament_has_equal_start_and_end(self):
        dates = tournament_dates(1, upstream("2026-03-07", "2026-03-07"))
        assert dates.start == dates.end

    @pytest.mark.parametrize("field", ["start", "end"])
    def test_a_missing_date_field_is_a_hard_error(self, field):
        data = upstream("2026-03-07", "2026-03-07")
        del data[field]
        with pytest.raises(ValueError, match=field):
            tournament_dates(1, data)

    @pytest.mark.parametrize("field", ["start", "end"])
    def test_a_null_date_field_is_a_hard_error(self, field):
        data = upstream("2026-03-07", "2026-03-07")
        data[field] = None
        with pytest.raises(ValueError, match=field):
            tournament_dates(1, data)

    @pytest.mark.parametrize("field", ["start", "end"])
    def test_a_malformed_date_field_is_a_hard_error(self, field):
        data = upstream("2026-03-07", "2026-03-07")
        data[field] = "07/03/2026"
        with pytest.raises(ValueError, match=field):
            tournament_dates(1, data)

    def test_the_tournament_id_appears_in_the_error(self):
        with pytest.raises(ValueError, match="42"):
            tournament_dates(42, upstream("2026-03-07", None))


class TestCollectTournamentDates:

    def test_dates_are_collected_in_the_given_order(self):
        catalogue = {
            1: upstream("2026-03-07", "2026-03-07"),
            2: upstream("2026-03-14", "2026-03-14"),
        }
        dates = collect_tournament_dates([2, 1], fetch_from(catalogue))
        assert [d.tournament_id for d in dates] == [2, 1]

    def test_an_empty_tournament_list_is_a_hard_error(self):
        with pytest.raises(ValueError, match="no tournaments"):
            collect_tournament_dates([], fetch_from({}))

    def test_a_malformed_date_from_one_tournament_fails_the_whole_collection(self):
        catalogue = {
            1: upstream("2026-03-07", "2026-03-07"),
            2: upstream("2026-03-14", None),
        }
        with pytest.raises(ValueError):
            collect_tournament_dates([1, 2], fetch_from(catalogue))


class TestLatestEndedEndDate:

    def test_the_date_is_the_latest_end_among_ended_tournaments(self):
        dates = [
            tournament_dates(1, upstream("2026-03-07", "2026-03-07")),
            tournament_dates(2, upstream("2026-05-30", "2026-05-30")),
            tournament_dates(3, upstream("2026-03-14", "2026-03-14")),
        ]
        assert latest_ended_end_date(dates, date(2026, 6, 1)) == date(2026, 5, 30)

    def test_a_multi_day_tournament_counts_by_its_end_date(self):
        dates = [tournament_dates(1, upstream("2026-04-25", "2026-04-26"))]
        assert latest_ended_end_date(dates, date(2026, 4, 26)) == date(2026, 4, 26)
        assert latest_ended_end_date(dates, date(2026, 4, 25)) is None

    def test_a_tournament_ending_today_counts_as_ended(self):
        dates = [tournament_dates(1, upstream("2026-03-07", "2026-03-07"))]
        assert latest_ended_end_date(dates, date(2026, 3, 7)) == date(2026, 3, 7)

    def test_a_tournament_ending_in_the_future_does_not_move_the_date(self):
        dates = [
            tournament_dates(1, upstream("2026-03-07", "2026-03-07")),
            tournament_dates(2, upstream("2026-12-31", "2026-12-31")),
        ]
        assert latest_ended_end_date(dates, date(2026, 6, 1)) == date(2026, 3, 7)

    def test_a_wholly_future_list_has_no_date(self):
        dates = [tournament_dates(1, upstream("2026-12-31", "2026-12-31"))]
        assert latest_ended_end_date(dates, date(2026, 1, 1)) is None

    def test_changing_which_tournament_ends_last_changes_the_date(self):
        dates = [
            tournament_dates(1, upstream("2026-03-07", "2026-03-07")),
            tournament_dates(2, upstream("2026-05-30", "2026-05-30")),
        ]
        before = latest_ended_end_date(dates, date(2026, 6, 1))
        dates[1] = tournament_dates(2, upstream("2026-04-01", "2026-04-01"))
        after = latest_ended_end_date(dates, date(2026, 6, 1))
        assert before == date(2026, 5, 30)
        assert after == date(2026, 4, 1)
        assert before != after


class TestStartYears:

    def test_a_single_shared_year(self):
        dates = [
            tournament_dates(1, upstream("2026-03-07", "2026-03-07")),
            tournament_dates(2, upstream("2026-05-30", "2026-05-30")),
        ]
        assert start_years(dates) == frozenset({2026})

    def test_disagreeing_years_are_all_reported(self):
        dates = [
            tournament_dates(1, upstream("2025-03-07", "2025-03-07")),
            tournament_dates(2, upstream("2026-05-30", "2026-05-30")),
        ]
        assert start_years(dates) == frozenset({2025, 2026})


class TestAssessCalendar:

    def test_a_configuration_matching_todays_year_is_current(self):
        dates = [tournament_dates(1, upstream("2026-03-07", "2026-03-07"))]
        assessment = assess_calendar(dates, date(2026, 6, 1))
        assert assessment.status == "current"
        assert assessment.years == (2026,)

    def test_a_configuration_from_an_earlier_year_is_stale(self):
        dates = [tournament_dates(1, upstream("2025-03-07", "2025-03-07"))]
        assessment = assess_calendar(dates, date(2026, 6, 1))
        assert assessment.status == "stale"
        assert assessment.years == (2025,)

    def test_a_configuration_from_a_later_year_is_future(self):
        dates = [tournament_dates(1, upstream("2027-03-07", "2027-03-07"))]
        assessment = assess_calendar(dates, date(2026, 6, 1))
        assert assessment.status == "future"
        assert assessment.years == (2027,)

    def test_disagreeing_start_years_are_mixed(self):
        dates = [
            tournament_dates(1, upstream("2025-12-30", "2025-12-30")),
            tournament_dates(2, upstream("2026-03-07", "2026-03-07")),
        ]
        assessment = assess_calendar(dates, date(2026, 6, 1))
        assert assessment.status == "mixed"

    def test_mixed_names_every_offending_year_sorted(self):
        dates = [
            tournament_dates(1, upstream("2026-03-07", "2026-03-07")),
            tournament_dates(2, upstream("2024-03-07", "2024-03-07")),
            tournament_dates(3, upstream("2025-03-07", "2025-03-07")),
        ]
        assessment = assess_calendar(dates, date(2026, 6, 1))
        assert assessment.years == (2024, 2025, 2026)

    def test_a_year_boundary_is_exact(self):
        dates = [tournament_dates(1, upstream("2026-01-01", "2026-01-01"))]
        assert assess_calendar(dates, date(2026, 12, 31)).status == "current"
        assert assess_calendar(dates, date(2025, 12, 31)).status == "future"
        assert assess_calendar(dates, date(2027, 1, 1)).status == "stale"


class TestAgainstTheRealBaseline:
    """The six 2026-configured tournaments, as returned by the upstream API on 2026-08-02."""

    BASELINE = {
        6544: upstream("2026-03-07", "2026-03-07"),
        6540: upstream("2026-03-14", "2026-03-14"),
        6534: upstream("2026-03-28", "2026-03-28"),
        6685: upstream("2026-04-25", "2026-04-26"),
        6826: upstream("2026-05-23", "2026-05-23"),
        6853: upstream("2026-05-30", "2026-05-30"),
    }

    def test_the_derived_rating_list_date_is_the_jdm_events_end_date(self):
        dates = collect_tournament_dates(list(self.BASELINE), fetch_from(self.BASELINE))
        assert latest_ended_end_date(dates, date(2026, 8, 2)) == date(2026, 5, 30)

    def test_the_two_day_tournament_is_counted_by_its_end_not_its_start(self):
        dates = collect_tournament_dates([6685], fetch_from(self.BASELINE))
        assert latest_ended_end_date(dates, date(2026, 4, 25)) is None
        assert latest_ended_end_date(dates, date(2026, 4, 26)) == date(2026, 4, 26)

    def test_the_baseline_is_a_current_configuration_in_season(self):
        dates = collect_tournament_dates(list(self.BASELINE), fetch_from(self.BASELINE))
        assert assess_calendar(dates, date(2026, 8, 2)).status == "current"
