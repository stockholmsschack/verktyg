"""Resolve, persist and replay rating-list dates, season years and tournament ids."""
from datetime import date

import pytest

from jgp_standings.run_record import (
    RunInput,
    load_or_resolve_run_input,
    read_run_record,
    resolve_run_input,
    write_run_record,
)


def upstream(start, end):
    return {"start": start, "end": end}


def fetch_from(catalogue):
    def fetch(tournament_id):
        return catalogue[tournament_id]
    return fetch


BASELINE = {
    6544: upstream("2026-03-07", "2026-03-07"),
    6540: upstream("2026-03-14", "2026-03-14"),
    6534: upstream("2026-03-28", "2026-03-28"),
    6685: upstream("2026-04-25", "2026-04-26"),
    6826: upstream("2026-05-23", "2026-05-23"),
    6853: upstream("2026-05-30", "2026-05-30"),
}
BASELINE_IDS = list(BASELINE)


class TestResolveRunInput:

    def test_resolves_the_rating_list_date_and_effective_year(self):
        run_input = resolve_run_input(BASELINE_IDS, fetch_from(BASELINE), date(2026, 8, 2))
        assert run_input.rating_list_date == date(2026, 5, 30)
        assert run_input.effective_year == 2026
        assert run_input.tournament_ids == tuple(BASELINE_IDS)

    def test_standings_content_is_identical_under_two_different_simulated_clocks(self):
        first = resolve_run_input(BASELINE_IDS, fetch_from(BASELINE), date(2026, 8, 2))
        second = resolve_run_input(BASELINE_IDS, fetch_from(BASELINE), date(2026, 12, 25))
        assert first == second

    def test_no_ended_tournament_is_a_hard_error(self):
        catalogue = {1: upstream("2026-12-31", "2026-12-31")}
        with pytest.raises(ValueError, match="no configured tournament has ended"):
            resolve_run_input([1], fetch_from(catalogue), date(2026, 1, 1))

    def test_disagreeing_start_years_is_a_hard_error(self):
        catalogue = {
            1: upstream("2025-03-07", "2025-03-07"),
            2: upstream("2026-03-07", "2026-03-07"),
        }
        with pytest.raises(ValueError, match="mixed"):
            resolve_run_input([1, 2], fetch_from(catalogue), date(2026, 6, 1))

    def test_an_empty_tournament_list_is_a_hard_error(self):
        with pytest.raises(ValueError, match="no tournaments"):
            resolve_run_input([], fetch_from({}), date(2026, 6, 1))


class TestCalendarGuardAndOverride:

    def test_a_stale_configuration_is_refused_by_default_and_names_the_year(self):
        catalogue = {1: upstream("2025-03-07", "2025-03-07")}
        with pytest.raises(ValueError, match=r"stale.*\[2025\]"):
            resolve_run_input([1], fetch_from(catalogue), date(2026, 6, 1))

    def test_a_future_configuration_is_refused_by_default_and_names_the_year(self):
        catalogue = {1: upstream("2027-03-07", "2026-01-01")}
        with pytest.raises(ValueError, match=r"future.*\[2027\]"):
            resolve_run_input([1], fetch_from(catalogue), date(2026, 6, 1))

    def test_a_mixed_configuration_is_refused_by_default_and_names_the_years(self):
        catalogue = {
            1: upstream("2025-03-07", "2025-03-07"),
            2: upstream("2026-03-07", "2026-03-07"),
        }
        with pytest.raises(ValueError, match=r"mixed.*\[2025, 2026\]"):
            resolve_run_input([1, 2], fetch_from(catalogue), date(2026, 6, 1))

    def test_an_override_naming_the_actual_stale_year_runs(self):
        catalogue = {1: upstream("2025-03-07", "2025-03-07")}
        run_input = resolve_run_input([1], fetch_from(catalogue), date(2026, 6, 1), override_year=2025)
        assert run_input.effective_year == 2025
        assert run_input.tournament_ids == (1,)

    def test_an_override_naming_the_wrong_year_is_refused(self):
        catalogue = {1: upstream("2025-03-07", "2025-03-07")}
        with pytest.raises(ValueError, match=r"2024.*\[2025\]"):
            resolve_run_input([1], fetch_from(catalogue), date(2026, 6, 1), override_year=2024)

    def test_an_override_cannot_paper_over_a_mixed_list(self):
        catalogue = {
            1: upstream("2025-03-07", "2025-03-07"),
            2: upstream("2026-03-07", "2026-03-07"),
        }
        with pytest.raises(ValueError, match=r"2025.*\[2025, 2026\]"):
            resolve_run_input([1, 2], fetch_from(catalogue), date(2026, 6, 1), override_year=2025)

    def test_an_overridden_run_records_its_year_and_list_and_replays_them(self, tmp_path):
        path = tmp_path / "run_input.json"
        catalogue = {1: upstream("2025-03-07", "2025-03-07")}
        run_input = load_or_resolve_run_input(
            [1], fetch_from(catalogue), date(2026, 6, 1), path, override_year=2025
        )
        write_run_record(run_input, path)
        assert read_run_record(path) == run_input
        assert run_input.effective_year == 2025

        def fail(_tournament_id):
            raise AssertionError("should not fetch when replaying a matching record")

        replayed = load_or_resolve_run_input([1], fail, date(2026, 6, 1), path, override_year=2025)
        assert replayed == run_input


class TestRecordRoundTrip:

    def test_a_written_record_reads_back_identically(self, tmp_path):
        run_input = RunInput(date(2026, 5, 30), 2026, (6544, 6540))
        path = tmp_path / "run_input.json"
        write_run_record(run_input, path)
        assert read_run_record(path) == run_input


class TestLoadOrResolve:

    def test_a_missing_record_is_resolved_fresh(self, tmp_path):
        path = tmp_path / "run_input.json"
        run_input = load_or_resolve_run_input(BASELINE_IDS, fetch_from(BASELINE), date(2026, 8, 2), path)
        assert run_input.rating_list_date == date(2026, 5, 30)
        assert not path.exists()  # resolving does not write; the caller decides when

    def test_a_matching_record_is_replayed_without_re_deriving(self, tmp_path):
        path = tmp_path / "run_input.json"
        recorded = RunInput(date(2020, 1, 1), 1999, tuple(BASELINE_IDS))
        write_run_record(recorded, path)

        def fail(_tournament_id):
            raise AssertionError("should not fetch when replaying a matching record")

        run_input = load_or_resolve_run_input(BASELINE_IDS, fail, date(2026, 8, 2), path)
        assert run_input == recorded

    def test_a_record_for_other_tournaments_is_resolved_afresh(self, tmp_path):
        path = tmp_path / "run_input.json"
        write_run_record(RunInput(date(2020, 1, 1), 1999, (6544,)), path)

        run_input = load_or_resolve_run_input(BASELINE_IDS, fetch_from(BASELINE), date(2026, 8, 2), path)

        assert run_input == RunInput(date(2026, 5, 30), 2026, tuple(BASELINE_IDS))
        assert read_run_record(path) == RunInput(date(2020, 1, 1), 1999, (6544,))  # the caller writes
