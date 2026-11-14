"""Per-band separator persistence and rendering.

Each age band has its own row list in `[finals_separators]`. A label outside
the run's age bands must prevent generation.
"""
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
import re
from unittest.mock import Mock

import pytest

from jgp_standings.active_configuration import (
    ActiveConfiguration,
    ConfigurationValidationError,
    configuration_path,
    load_configuration,
    save_configuration,
)
from jgp_standings.generation_orchestrator import GenerationValidationError, run_generation
from jgp_standings.web import create_app
from jgp_standings.upstream_client import CanonicalTournament, ProcessableTournamentGroup
from jgp_standings.util import band_labels
from jgp_standings.upstream_client import FixtureReplay

ROOT = Path(__file__).resolve().parent.parent
REPLAY = FixtureReplay(ROOT / "fixtures" / "2026")
TODAY = date(2026, 8, 2)
HLINE = "table.hline(stroke: 1pt + black),"
ENDPOINT = "/api/v1/configuration/separators"


def _configuration(**changes) -> ActiveConfiguration:
    return replace(ActiveConfiguration(
        tournament_ids=(100,),
        contributor={"name": "Contact", "email": "contact@example.com"},
        penalty_dict={6544: (726286,)},
    ), **changes)


def _band_tables(typst_source: str) -> dict[str, list[str]]:
    """Each band's standings-table lines, keyed by the heading's label."""
    tables = {}
    for section in typst_source.split("GP Ställning klass ")[1:]:
        label = section.split("]", 1)[0]
        tables[label] = section.split("table.header(", 1)[1].split("\n")[1:]
    return tables


def _rows_before_lines(table: list[str]) -> list[int]:
    """The data-row numbers a bold line follows."""
    rows, lines = 0, []
    for line in table:
        if line.startswith("  ["):
            rows += 1
        elif line == HLINE:
            lines.append(rows)
    return lines


class TestTheConfiguration:

    def test_separators_round_trip_per_band_and_default_to_none(self, tmp_path):
        assert save_configuration(tmp_path, _configuration()).finals_separators == {}

        separators = {"2010-2012": (10, 20), "2018 onward": (8,)}
        saved = save_configuration(tmp_path, _configuration(finals_separators=separators, revision=1))

        assert load_configuration(tmp_path) == saved
        assert load_configuration(tmp_path).finals_separators == separators
        document = configuration_path(tmp_path).read_text()
        assert '[finals_separators]\n"2010-2012" = [10, 20]\n"2018 onward" = [8]\n' in document

    @pytest.mark.parametrize("separators, problem", [
        ({"2013": (0,)}, "finals separators for '2013' must be distinct positive row numbers"),
        ({"2013": (4, 4)}, "finals separators for '2013' must be distinct positive row numbers"),
        ({"2013": ()}, "finals separators for '2013' must be distinct positive row numbers"),
        ({"2013": (True,)}, "finals separators for '2013' must be distinct positive row numbers"),
        ({" ": (3,)}, "finals separators require an age-band label"),
    ])
    def test_invalid_separators_are_refused(self, separators, problem):
        with pytest.raises(ConfigurationValidationError) as refusal:
            save_configuration(Path("unused"), _configuration(finals_separators=separators))
        assert refusal.value.problems == (problem,)

    def test_a_malformed_table_is_a_load_problem(self, tmp_path):
        save_configuration(tmp_path, _configuration())
        path = configuration_path(tmp_path)
        path.write_text(path.read_text().replace("[finals_separators]\n", '[finals_separators]\n"2013" = 4\n'))

        with pytest.raises(ConfigurationValidationError) as refusal:
            load_configuration(tmp_path)
        assert "finals separators for '2013' must be a list of row numbers" in refusal.value.problems


class TestTheDocument:

    def _typst(self, offline_run, separators):
        configuration = replace(load_configuration(ROOT / "state"), finals_separators=separators)
        return run_generation(
            configuration, fetch=REPLAY, run_record_path=offline_run / "run_input.json", today=TODAY
        ).artifacts.typst_source

    def test_each_band_draws_its_own_lines_after_exactly_its_rows(self, offline_run):
        tables = _band_tables(self._typst(offline_run, {"2010-2012": (1, 3), "2013": (2,)}))

        assert _rows_before_lines(tables["2010-2012"]) == [1, 3]
        assert _rows_before_lines(tables["2013"]) == [2]
        assert all(
            _rows_before_lines(table) == []
            for label, table in tables.items()
            if label not in {"2010-2012", "2013"}
        )

    def test_a_row_beyond_the_table_draws_nothing(self, offline_run):
        tables = _band_tables(self._typst(offline_run, {"2013": (999,)}))

        assert _rows_before_lines(tables["2013"]) == []

    def test_without_separators_no_line_is_drawn(self, offline_run):
        assert HLINE not in self._typst(offline_run, {})

    def test_a_key_naming_no_band_of_the_run_refuses_the_generation(self, offline_run):
        with pytest.raises(GenerationValidationError) as refusal:
            self._typst(offline_run, {"2005-2008": (3,), "2013": (2,)})

        assert refusal.value.problems == ("finals separators key '2005-2008' names no age band of 2026",)


@pytest.fixture
def state_directory(tmp_path):
    return tmp_path / "state"


@pytest.fixture
def saved(state_directory):
    return save_configuration(state_directory, _configuration(finals_separators={"2010-2012": (10, 18)}))


@pytest.fixture
def client(tmp_path, state_directory, saved):
    member_system = Mock()
    member_system.canonical_tournament.return_value = CanonicalTournament(
        tournament_id=100,
        name="Tournament",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 1),
        processable_groups=(ProcessableTournamentGroup(1000, "Klass A", "a"),),
    )
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "finals-separator-tests",
        "STATE_DIRECTORY": state_directory,
        "OUTPUT_DIRECTORY": tmp_path / "output",
        "MEMBER_SYSTEM_CLIENT": member_system,
        "CLOCK": lambda: datetime(2026, 10, 4, 12, tzinfo=UTC),
    })
    return app.test_client()


def _summary(page: str) -> list[str]:
    summary = page.split("data-configuration-separator-summary>")[1].split("</dd>")[0]
    return [line.strip() for line in re.sub(r"</?div>", "\n", summary).split("\n") if line.strip()]


def _save(client, revision, separators):
    return client.post(ENDPOINT, json={"revision": revision, "finals_separators": separators})


class TestTheSeparatorsTab:

    def test_the_tab_shows_one_group_per_band_of_the_year_and_the_summary_per_group(self, client):
        english = client.get("/configuration?lang=en").get_data(as_text=True)
        swedish = client.get("/configuration?lang=sv").get_data(as_text=True)

        panel = english.split('id="separators-panel"')[1].split("</section>")[0]
        assert re.findall(r'data-finals-group="([^"]+)"', panel) == band_labels(2026)
        assert 'data-finals-heading="2018 and later"' in panel
        assert "Group 2010-2012" in panel
        assert f'data-save-endpoint="{ENDPOINT}"' in panel
        assert panel.count("data-clear-all-finals-separators") == 8
        assert panel.count("data-clear-all-finals-groups") == 1
        assert "Clear all groups" in panel
        assert "Rensa alla grupper" in swedish
        assert "Klass 2018 och senare" in swedish
        assert 'src="/static/js/finals-editor.js"' in english
        assert _summary(english) == ["2010-2012: 10, 18"]

    def test_a_stored_label_of_another_year_is_shown_so_it_can_be_cleared(self, client, state_directory, saved):
        save_configuration(state_directory, replace(saved, finals_separators={"2005-2008": (4,)}))

        page = client.get("/configuration?lang=en").get_data(as_text=True)

        assert re.findall(r'data-finals-group="([^"]+)"', page) == [*band_labels(2026), "2005-2008"]
        response = _save(client, saved.revision + 1, {"2005-2008": []})
        assert response.status_code == 200
        assert load_configuration(state_directory).finals_separators == {}

    def test_a_save_normalizes_every_group_and_advances_the_revision(self, client, state_directory, saved):
        response = _save(client, saved.revision, {"2013": [24, 8, 16, 8], "2010-2012": [], "2006-2009": [5]})

        assert response.status_code == 200
        data = response.get_json()["data"]
        assert data == {
            "revision": saved.revision + 1,
            "finals_separators": {"2006-2009": [5], "2013": [8, 16, 24]},
            "changed": True,
        }
        stored = load_configuration(state_directory)
        assert stored.finals_separators == {"2006-2009": (5,), "2013": (8, 16, 24)}
        assert stored.penalty_dict == saved.penalty_dict
        page = client.get("/configuration?lang=en").get_data(as_text=True)
        assert _summary(page) == ["2006-2009: 5", "2013: 8, 16, 24"]

    def test_unchanged_separators_do_not_advance_the_revision(self, client, saved):
        data = _save(client, saved.revision, {"2010-2012": [18, 10], "2013": []}).get_json()["data"]

        assert data == {"revision": saved.revision, "finals_separators": {"2010-2012": [10, 18]}, "changed": False}

    def test_clearing_every_group_stores_none(self, client, state_directory, saved):
        response = _save(client, saved.revision, {})

        assert response.get_json()["data"]["finals_separators"] == {}
        assert load_configuration(state_directory).finals_separators == {}
        assert _summary(client.get("/configuration?lang=en").get_data(as_text=True)) == ["None"]

    @pytest.mark.parametrize("separators", [
        {"2013": [0]}, {"2013": [1.5]}, {"2013": ["3"]}, {"2013": [True]}, {"2013": 8},
        {"2005-2008": [3]}, ["2013"], None,
    ])
    def test_an_invalid_save_is_refused_whole(self, client, state_directory, saved, separators):
        response = _save(client, saved.revision, separators)

        assert response.status_code == 400
        assert response.get_json()["error"]["message_key"] == "errors.finals.invalid_input"
        assert load_configuration(state_directory) == saved

    def test_a_stale_save_is_refused(self, client, state_directory, saved):
        assert _save(client, 0, {"2013": [3]}).status_code == 409
        assert load_configuration(state_directory) == saved

    def test_the_editor_guards_its_clear_controls(self):
        source = (ROOT / "jgp_standings" / "web" / "static" / "js" / "finals-editor.js").read_text()

        assert "button.disabled = saving || empty;" in source
        assert '"finals.all_groups_clear"' in source
        assert '"finals.all_clear"' in source
        assert '"finals.already_clear"' in source


def test_generated_json_reflects_configured_then_cleared_separators(offline_run):
    import json

    configuration = replace(
        load_configuration(ROOT / "state"),
        finals_separators={"2010-2012": (1, 3), "2013": (2,)},
    )
    for separators in (configuration.finals_separators, {}):
        result = run_generation(
            replace(configuration, finals_separators=separators),
            fetch=REPLAY,
            run_record_path=offline_run / "run_input.json",
            today=TODAY,
        )
        snapshot = json.loads(result.artifacts.standings_json)
        if separators:
            assert snapshot["finals_separators"] == {"2010-2012": [1, 3], "2013": [2]}
        else:
            assert "finals_separators" not in snapshot
