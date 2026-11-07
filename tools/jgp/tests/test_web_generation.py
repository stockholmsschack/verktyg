"""Generation dialog validation, exclusive execution and progress delivery.

A stub runner avoids member-system and Typst calls. Checks cover runner inputs,
layouts and refusals for wrong-year tournaments, no ended tournaments and
separator labels outside the season's bands.
"""
from base64 import urlsafe_b64decode
from dataclasses import replace
from datetime import UTC, date, datetime
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from jgp_standings.active_configuration import ActiveConfiguration, load_configuration, save_configuration
from jgp_standings.generation_orchestrator import GenerationStageError, KlassNamingError
from jgp_standings.web import create_app
from jgp_standings.web.generation_operations import (
    TOPIC_PREFIX, GenerationBusyError, GenerationOperation, OperationStore, OperationTransition,
)
from jgp_standings.web.localization import load_catalogues
from jgp_standings.web.mercure import DisabledOperationPublisher, MercurePublisher
from jgp_standings.web.operations import LAST_EVENT_ID_HEADER, OPERATION_EVENT_KINDS, OperationEvent
from jgp_standings.klass_naming import KlassNameReason, KlassNamingProblem
from jgp_standings.upstream_client import (
    CanonicalTournament,
    ProcessableTournamentGroup,
    TournamentKlassNamingError,
    TournamentResolutionError,
)

ROOT = Path(__file__).resolve().parent.parent
START = "/api/v1/generation/operations"
INPUTS = "/api/v1/generation/inputs"
DEFAULT_RUN = {"year": None, "tournament_ids": None, "layout": "portrait"}
NAMING_PROBLEM = KlassNamingProblem(100, "March", 18319, "Final", KlassNameReason.NO_KLASS, "detail")


def _tournament(tournament_id: int, name: str, start_date: date, end_date: date | None = None) -> CanonicalTournament:
    return CanonicalTournament(
        tournament_id=tournament_id,
        name=name,
        start_date=start_date,
        end_date=end_date or start_date,
        processable_groups=(ProcessableTournamentGroup(tournament_id + 1000, "Klass A", "a"),),
    )


class MemberSystem:
    def __init__(self):
        self.lookup_ids = []
        self.tournaments = {
            100: _tournament(100, "March", date(2026, 3, 1)),
            200: _tournament(200, "January", date(2026, 1, 10)),
            300: _tournament(300, "New Year 2027", date(2027, 1, 1)),
            400: _tournament(400, "Old tournament", date(2025, 11, 1)),
            500: _tournament(500, "August", date(2026, 8, 1)),
        }
        self.refused = {}

    def canonical_tournament(self, tournament_id):
        self.lookup_ids.append(tournament_id)
        if tournament_id in self.refused:
            raise self.refused[tournament_id]
        try:
            return self.tournaments[tournament_id]
        except KeyError as error:
            raise TournamentResolutionError(tournament_id, "not found") from error


class MutableClock:
    def __init__(self, instant):
        self.instant = instant

    def __call__(self):
        return self.instant


class ImmediateExecutor:
    def submit(self, function, *args, **kwargs):
        function(*args, **kwargs)
        return SimpleNamespace()


class DeferredExecutor:
    def __init__(self):
        self.pending = []

    def submit(self, function, *args):
        self.pending.append((function, args))
        return SimpleNamespace()

    def run_next(self):
        function, args = self.pending.pop(0)
        function(*args)


class RejectingExecutor:
    def submit(self, _function, *_args):
        raise RuntimeError("executor unavailable")


class RecordingRunner:
    """Reports Flickornas-shaped progress, then fails or returns a published run."""

    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []

    def __call__(self, configuration, output_directory, *, progress, **keyword_arguments):
        self.calls.append(SimpleNamespace(
            configuration=configuration, output_directory=output_directory, **keyword_arguments,
        ))
        progress.begin_phase("Fetching tournaments", 2, "tournaments")
        progress.start("Fetching tournament 1/2: January")
        progress.complete()
        if self.failure is not None:
            raise self.failure
        progress.start("Publishing generation")
        progress.complete()
        partial = "-partial" if keyword_arguments["override_tournament_ids"] is not None else ""
        return SimpleNamespace(
            run_id=f"run-{len(self.calls)}",
            pdf_path=Path(f"jgp-standings{partial}-20260701T120000Z.pdf"),
        )


class FailingPublisher:
    def publish(self, *_args, **_kwargs):
        raise RuntimeError("Mercure is unavailable")


@pytest.fixture
def state_directory(tmp_path):
    return tmp_path / "state"


@pytest.fixture
def saved(state_directory):
    return save_configuration(state_directory, ActiveConfiguration(
        tournament_ids=(200, 100),
        contributor={"name": "Contact", "email": "contact@example.com"},
        penalty_dict={200: (726286,)},
    ))


@pytest.fixture
def member_system():
    return MemberSystem()


@pytest.fixture
def clock():
    return MutableClock(datetime(2026, 7, 1, 12, tzinfo=UTC))


@pytest.fixture
def make_app(tmp_path, state_directory, saved, member_system, clock):
    def make(executor=None, runner=None, **configuration):
        return create_app({
            "TESTING": True,
            "SECRET_KEY": "generation-tests",
            "STATE_DIRECTORY": state_directory,
            "OUTPUT_DIRECTORY": tmp_path / "output",
            "MEMBER_SYSTEM_CLIENT": member_system,
            "CLOCK": clock,
            "GENERATION_EXECUTOR": executor or ImmediateExecutor(),
            "GENERATION_RUNNER": runner or RecordingRunner(),
            **configuration,
        })
    return make


@pytest.fixture
def client(make_app):
    return make_app().test_client()


def _problem_keys(response):
    return [problem["message_key"] for problem in response.get_json()["error"]["details"]["problems"]]


def _operation(client, started):
    return client.get(started.get_json()["data"]["status_url"]).get_json()["data"]


class TestTheOperation:

    def test_one_core_pass_persists_one_recoverable_terminal_outcome(self, make_app, state_directory):
        runner = RecordingRunner()
        client = make_app(runner=runner).test_client()

        started = client.post(START, json=DEFAULT_RUN)

        assert started.status_code == 202
        accepted = started.get_json()["data"]
        assert accepted["state"] == "accepted"
        assert accepted["latest_event_id"] == 1
        assert accepted["topic"] == f"https://stockholmsschack.se/jgp/operations/{accepted['operation_id']}"
        operation = _operation(client, started)
        assert operation["state"] == "completed"
        assert operation["configuration_revision"] == load_configuration(state_directory).revision
        assert operation["terminal"] is True
        assert operation["run_id"] == "run-1"
        assert operation["links"] == {
            "pdf": "http://jgp.localhost:5000/downloads/run-1/jgp-standings-20260701T120000Z.pdf",
            "files": "/downloads",
        }
        assert len(runner.calls) == 1
        event_ids = [event["event_id"] for event in operation["events"]]
        assert event_ids == list(range(1, len(event_ids) + 1))
        assert [event["kind"] for event in operation["events"] if event["kind"] in {"completed", "failed"}] == [
            "completed"
        ]
        assert not (state_directory / "operations" / "generation.lease").exists()

        restarted = make_app().test_client()
        assert restarted.get(accepted["status_url"]).get_json()["data"] == operation

    def test_a_default_run_hands_the_runner_the_stockholm_date_and_the_chosen_layout(
        self, make_app, tmp_path, state_directory, saved,
    ):
        runner = RecordingRunner()
        client = make_app(runner=runner).test_client()

        client.post(START, json={**DEFAULT_RUN, "layout": "landscape"})

        call = runner.calls[0]
        assert call.today == date(2026, 7, 1)
        assert call.override_year is None
        assert call.override_tournament_ids is None
        assert call.configuration == replace(saved, landscape=True)
        assert call.output_directory == tmp_path / "output"
        assert call.state_directory == state_directory
        assert load_configuration(state_directory) == saved  # the layout is for this run only

    def test_explicit_browser_inputs_start_a_partial_generation(self, make_app):
        runner = RecordingRunner()
        client = make_app(runner=runner).test_client()

        started = client.post(START, json={"year": 2025, "tournament_ids": [400], "layout": "landscape"})

        assert started.status_code == 202
        operation = _operation(client, started)
        assert operation["is_partial"] is True
        assert operation["effective_year"] == 2025
        assert operation["tournament_ids"] == [400]
        assert operation["layout"] == "landscape"
        assert operation["links"]["pdf"].endswith("/run-1/jgp-standings-partial-20260701T120000Z.pdf")
        assert runner.calls[0].override_year == 2025
        assert runner.calls[0].override_tournament_ids == [400]

    def test_a_current_year_run_over_its_own_tournaments_is_partial_for_the_current_year(self, make_app):
        runner = RecordingRunner()
        client = make_app(runner=runner).test_client()

        operation = _operation(client, client.post(START, json={**DEFAULT_RUN, "tournament_ids": [100]}))

        assert operation["is_partial"] is True
        assert runner.calls[0].override_year == 2026
        assert runner.calls[0].override_tournament_ids == [100]

    def test_flask_does_not_transfer_completed_pdf_contents(self, client):
        assert client.get("/downloads/run-1/standings.pdf").status_code == 404

    def test_a_concurrent_request_gets_the_active_operation_without_queueing(self, make_app):
        executor, runner = DeferredExecutor(), RecordingRunner()
        client = make_app(executor, runner).test_client()

        first = client.post(START, json=DEFAULT_RUN)
        second = client.post(START, json=DEFAULT_RUN)

        assert first.status_code == 202
        assert second.status_code == 409
        busy = second.get_json()["error"]
        assert busy["code"] == "generation_busy"
        assert busy["message_key"] == "errors.generation.busy"
        operation_id = first.get_json()["data"]["operation_id"]
        assert busy["details"] == {
            "active_operation_id": operation_id,
            "status_url": f"/api/v1/generation/operations/{operation_id}",
        }
        assert len(executor.pending) == 1
        assert runner.calls == []

        executor.run_next()
        assert len(runner.calls) == 1
        assert _operation(client, first)["state"] == "completed"

    def test_the_ui_locale_is_not_a_generation_input(self, make_app):
        runner = RecordingRunner()
        client = make_app(runner=runner).test_client()

        client.get("/?lang=en")
        client.post(START, json=DEFAULT_RUN)
        client.get("/?lang=sv")
        client.post(START, json=DEFAULT_RUN)

        assert runner.calls[0] == runner.calls[1]
        assert not {"locale", "language"} & set(vars(runner.calls[0]))

    def test_a_failure_is_terminal_and_releases_the_lease_for_the_next_run(self, make_app):
        client = make_app(runner=RecordingRunner(RuntimeError("fixture renderer failed"))).test_client()

        failed_start = client.post(START, json=DEFAULT_RUN)
        failed = _operation(client, failed_start)

        assert failed["state"] == "failed"
        assert failed["terminal"] is True
        assert failed["failure_stage"] == "Fetching tournament 1/2: January"
        assert failed["events"][-1]["kind"] == "failed"
        next_start = client.post(START, json=DEFAULT_RUN)
        assert next_start.status_code == 202
        assert next_start.get_json()["data"]["operation_id"] != failed["operation_id"]

    def test_a_stage_error_names_its_stage(self, make_app):
        client = make_app(runner=RecordingRunner(GenerationStageError("rendering", "Typst exited"))).test_client()

        failed = _operation(client, client.post(START, json=DEFAULT_RUN))

        assert failed["failure_stage"] == "rendering"
        assert failed["events"][-1]["parameters"] == {
            "stage": "rendering", "detail": "generation failed during rendering: Typst exited",
        }

    def test_refused_group_names_found_during_the_run_are_listed(self, make_app):
        client = make_app(runner=RecordingRunner(KlassNamingError([NAMING_PROBLEM]))).test_client()

        failed = _operation(client, client.post(START, json=DEFAULT_RUN))

        assert failed["failure_stage"] == "validation"
        problems = failed["events"][-1]["parameters"]["problems"]
        assert [problem["message_key"] for problem in problems] == ["errors.klass_naming.no_klass"]
        assert problems[0]["parameters"]["group_name"] == "Final"

    def test_a_scheduling_failure_is_durable_and_does_not_strand_the_lease(self, make_app, state_directory):
        client = make_app(RejectingExecutor()).test_client()

        response = client.post(START, json=DEFAULT_RUN)

        assert response.status_code == 500
        assert response.get_json()["error"]["code"] == "generation_start_failed"
        records = list((state_directory / "operations").glob("*.json"))
        assert len(records) == 1
        status = client.get(f"/api/v1/generation/operations/{records[0].stem}").get_json()["data"]
        assert status["state"] == "failed"
        assert status["failure_stage"] == "scheduling"
        assert not (state_directory / "operations" / "generation.lease").exists()

    @pytest.mark.parametrize("finished", [False, True])
    def test_another_process_keeps_its_lease_while_alive_and_loses_it_when_it_dies(self, state_directory, finished):
        store = OperationStore(state_directory)
        operation_id = "a" * 32
        store.create(GenerationOperation(
            operation_id, "topic", "", "accepted", 2026, (), False, "portrait", 1, False,
            events=(OperationTransition(1, "accepted", "operations.accepted"),),
        ))
        if finished:
            store.transition(operation_id, kind="completed", message_key="operations.completed")
        hold = (
            "import sys, time; from jgp_standings.web.generation_operations import OperationStore; "
            "OperationStore(sys.argv[1]).acquire(sys.argv[2]); print('held', flush=True); time.sleep(60)"
        )
        owner = subprocess.Popen(
            [sys.executable, "-c", hold, str(state_directory), operation_id], stdout=subprocess.PIPE, text=True,
        )
        try:
            assert owner.stdout.readline() == "held\n"
            with pytest.raises(GenerationBusyError, match=operation_id):
                store.acquire("b" * 32)
            assert store.load(operation_id).state == ("completed" if finished else "accepted")
        finally:
            owner.kill()
            owner.communicate()

        store.acquire("b" * 32)
        assert store.load(operation_id).state == ("completed" if finished else "failed")

    def test_an_invalid_request_creates_neither_operation_nor_lease(self, make_app, state_directory):
        runner = RecordingRunner()
        client = make_app(runner=runner).test_client()

        response = client.post(START, json={"year": "invalid", "tournament_ids": [], "layout": "wide"})

        assert response.status_code == 422
        assert runner.calls == []
        assert not (state_directory / "operations").exists()

    @pytest.mark.parametrize("operation_id", ["0" * 32, "not-hex", "generation.lease"])
    def test_an_unknown_status_is_a_structured_not_found(self, client, operation_id):
        response = client.get(f"/api/v1/generation/operations/{operation_id}")

        assert response.status_code == 404
        assert response.get_json()["error"]["code"] == "operation_not_found"

    def test_the_operation_event_contract_is_localizable_and_replayable(self):
        event = OperationEvent(
            event_id=3,
            operation_id="operation-1",
            kind="progress",
            message_key="operations.progress",
            parameters={"stage": "Fetching tournaments"},
            progress={"completed": 1, "total": 2, "unit": "tournaments"},
        )

        event_lines = event.as_sse().splitlines()
        payload = json.loads(event_lines[2].removeprefix("data: "))
        assert event_lines[:2] == ["id: 3", "event: progress"]
        assert payload["message_key"] == "operations.progress"
        assert payload["terminal"] is False
        assert OPERATION_EVENT_KINDS == ("accepted", "stage", "progress", "completed", "failed")
        assert LAST_EVENT_ID_HEADER == "Last-Event-ID"


class TestTheDialog:

    def test_the_configuration_page_contains_the_localized_generation_dialog(self, client):
        body = client.get("/configuration?lang=en").get_data(as_text=True)

        assert "Generate standings" in body
        assert re.search(r'id="generation-year"[^>]*value="2026"', body)
        assert "Choose tournaments for this run" in body
        assert 'data-tournament-consumer="run"' in body
        assert 'src="/static/js/generation-form.js"' in body
        assert 'data-has-selected-tournaments="true"' in body
        assert 'data-start-endpoint="/api/v1/generation/operations"' in body
        assert 'data-defaults-endpoint="/api/v1/generation/defaults"' in body
        assert 'data-mercure-hub="/.well-known/mercure"' in body
        assert "data-generation-announcement" in body
        assert "data-generation-submit-hint" in body
        assert '"generation.again": "Generate again"' in body
        assert 'class="modal-footer generation-modal-actions"' in body
        assert 'class="btn btn-secondary generation-modal-cancel"' in body
        assert 'class="btn btn-primary generation-modal-primary"' in body
        source = (ROOT / "jgp_standings" / "web" / "static" / "js" / "generation-form.js").read_text(encoding="utf-8")
        assert "data-generate-again" in source
        assert "@click=${startGeneration}" in source

    def test_the_layout_offers_portrait_and_landscape_defaulting_to_the_configuration(
        self, make_app, state_directory, saved,
    ):
        portrait = make_app().test_client().get("/configuration?lang=en").get_data(as_text=True)
        save_configuration(state_directory, replace(saved, landscape=True))
        landscape = make_app().test_client().get("/configuration?lang=sv").get_data(as_text=True)

        def options(body):
            select = body.split('id="generation-layout"')[1].split("</select>")[0]
            return re.findall(r'value="(\w+)"\s*(selected)?\s*>\s*([^<]+)<', select)

        assert options(portrait) == [("portrait", "selected", "Portrait"), ("landscape", "", "Landscape")]
        assert options(landscape) == [("portrait", "", "Stående"), ("landscape", "selected", "Liggande")]

    def test_a_non_local_operator_cannot_see_or_submit_a_tournament_override(self, client, member_system):
        body = client.get("/configuration?lang=en", base_url="https://standings.example").get_data(as_text=True)

        assert 'id="generation-explicit-tournaments"' not in body
        assert 'data-tournament-consumer="run"' not in body
        assert 'aria-readonly="true"' in body
        assert "data-generation-defaults-status" in body
        member_system.lookup_ids.clear()
        response = client.post(
            INPUTS, json={"year": 2025, "tournament_ids": [400], "layout": "portrait"},
            base_url="https://standings.example",
        )
        assert response.status_code == 403
        assert response.get_json()["error"]["code"] == "generation_tournament_selection_forbidden"
        assert member_system.lookup_ids == []
        assert client.post(INPUTS, json=DEFAULT_RUN, base_url="https://standings.example").status_code == 200

    def test_a_host_admin_capability_allows_a_non_local_tournament_override(self, make_app):
        app = make_app(GENERATION_TOURNAMENT_SELECTION_AUTHORIZER=lambda: True)
        client = app.test_client()

        page = client.get("/configuration?lang=en", base_url="https://standings.example").get_data(as_text=True)
        response = client.post(
            INPUTS, json={"year": 2025, "tournament_ids": [400], "layout": "portrait"},
            base_url="https://standings.example",
        )

        assert 'id="generation-explicit-tournaments"' in page
        assert 'aria-readonly="true"' not in page
        assert response.status_code == 200

    def test_a_localhost_subdomain_can_select_tournaments(self, client):
        body = client.get("/configuration?lang=en", base_url="http://jgp.localhost:5000").get_data(as_text=True)

        assert 'id="generation-explicit-tournaments"' in body

    def test_a_localhost_host_header_cannot_bypass_the_hosts_decision(self, make_app):
        client = make_app(GENERATION_TOURNAMENT_SELECTION_AUTHORIZER=lambda: False).test_client()
        body = client.get("/configuration?lang=en", base_url="http://localhost:5000").get_data(as_text=True)

        assert 'id="generation-explicit-tournaments"' not in body

    def test_the_defaults_use_the_saved_configuration_not_the_tournament_draft(self, client, member_system, saved):
        drafted = member_system.tournaments[300]
        client.put("/api/v1/configuration/tournaments/draft", json={
            "revision": saved.revision,
            "tournaments": [{
                "tournament_id": 300,
                "name": drafted.name,
                "start_date": drafted.start_date.isoformat(),
                "end_date": drafted.end_date.isoformat(),
            }],
        })

        response = client.get("/api/v1/generation/defaults")

        assert response.status_code == 200
        assert response.get_json()["data"]["current_year"] == 2026
        assert response.get_json()["data"]["configuration_revision"] == saved.revision
        assert [t["tournament_id"] for t in response.get_json()["data"]["tournaments"]] == [200, 100]
        with client.session_transaction() as session:
            assert session["jgp.tournament_draft"]["tournaments"][0]["tournament_id"] == 300

    def test_refused_group_names_refuse_the_defaults_with_every_problem(self, client, member_system):
        member_system.refused[100] = TournamentKlassNamingError(100, [NAMING_PROBLEM])

        response = client.get("/api/v1/generation/defaults")

        assert response.status_code == 422
        assert _problem_keys(response) == ["errors.klass_naming.no_klass"]

    def test_full_and_partial_inputs_resolve_with_their_publication_defaults(self, client, tmp_path, state_directory):
        full = client.post(INPUTS, json=DEFAULT_RUN)
        partial = client.post(INPUTS, json={"year": 2025, "tournament_ids": [400], "layout": "landscape"})

        assert full.status_code == 200
        assert full.get_json()["data"] == {
            "inputs": {"effective_year": 2026, "tournament_ids": [200, 100], "is_partial": False, "layout": "portrait"},
            "tournaments": [
                {"tournament_id": 200, "name": "January", "start_date": "2026-01-10", "end_date": "2026-01-10"},
                {"tournament_id": 100, "name": "March", "start_date": "2026-03-01", "end_date": "2026-03-01"},
            ],
            "publication": {"pdf": True, "json": True},
        }
        assert partial.status_code == 200
        assert partial.get_json()["data"]["inputs"] == {
            "effective_year": 2025, "tournament_ids": [400], "is_partial": True, "layout": "landscape",
        }
        assert partial.get_json()["data"]["publication"] == {"pdf": False, "json": False}
        assert not (tmp_path / "output").exists()
        assert sorted(path.name for path in state_directory.iterdir()) == ["active-configuration.toml"]

    def test_a_runs_tournaments_come_back_in_start_date_order(self, client):
        response = client.post(INPUTS, json={**DEFAULT_RUN, "tournament_ids": [100, 200]})

        assert response.get_json()["data"]["inputs"]["tournament_ids"] == [200, 100]

    def test_every_structural_problem_is_reported_before_any_upstream_request(self, client, member_system):
        member_system.lookup_ids.clear()

        response = client.post(INPUTS, json={"year": "twenty twenty-six", "tournament_ids": [], "layout": "auto"})

        assert response.status_code == 422
        assert set(_problem_keys(response)) == {
            "errors.generation.year", "errors.generation.layout", "errors.generation.tournaments_required",
        }
        assert member_system.lookup_ids == []

    @pytest.mark.parametrize("tournament_ids, key", [
        ([100, 100], "errors.generation.tournaments_unique"),
        ([100, "200"], "errors.generation.tournaments"),
        ([True], "errors.generation.tournaments"),
        ("100", "errors.generation.tournaments"),
    ])
    def test_a_malformed_tournament_list_is_refused(self, client, tournament_ids, key):
        response = client.post(INPUTS, json={**DEFAULT_RUN, "tournament_ids": tournament_ids})

        assert _problem_keys(response) == [key]

    def test_a_non_current_year_requires_explicit_tournaments(self, client, member_system):
        member_system.lookup_ids.clear()

        response = client.post(INPUTS, json={**DEFAULT_RUN, "year": 2025})

        assert response.status_code == 422
        assert member_system.lookup_ids == []
        assert _problem_keys(response) == ["errors.generation.non_current_tournaments"]

    def test_an_untouched_year_is_resolved_again_after_stockholm_midnight(self, client, clock):
        page = client.get("/configuration?lang=en").get_data(as_text=True)
        assert re.search(r'id="generation-year"[^>]*value="2026"', page)

        clock.instant = datetime(2026, 12, 31, 23, 0, tzinfo=UTC)
        response = client.post(INPUTS, json={**DEFAULT_RUN, "tournament_ids": [300]})

        assert response.status_code == 200
        assert response.get_json()["data"]["inputs"]["effective_year"] == 2027

    @pytest.mark.parametrize("tournament_ids, keys", [
        ((400,), ["errors.generation.defaults_stale"]),
        ((400, 100), ["errors.generation.defaults_mixed"]),
        ((300,), ["errors.generation.defaults_future", "errors.generation.none_ended"]),
    ])
    def test_a_default_run_refuses_configured_tournaments_of_another_year(
        self, make_app, state_directory, saved, tournament_ids, keys,
    ):
        save_configuration(state_directory, replace(saved, tournament_ids=tournament_ids))

        response = make_app().test_client().post(INPUTS, json=DEFAULT_RUN)

        assert response.status_code == 422
        assert _problem_keys(response) == keys
        assert response.get_json()["error"]["details"]["problems"][0]["parameters"] == {"year": 2026}

    def test_a_runs_own_tournaments_must_start_in_its_year(self, client):
        response = client.post(INPUTS, json={"year": 2025, "tournament_ids": [400, 100], "layout": "portrait"})

        assert _problem_keys(response) == ["errors.generation.tournament_years"]

    def test_a_run_needs_a_tournament_that_has_ended(self, client):
        response = client.post(INPUTS, json={**DEFAULT_RUN, "tournament_ids": [500]})

        assert _problem_keys(response) == ["errors.generation.none_ended"]

    def test_separators_naming_no_band_of_the_year_are_refused_before_the_run(
        self, make_app, state_directory, saved,
    ):
        save_configuration(state_directory, replace(saved, finals_separators={"2005-2008": (3,), "2013": (2,)}))
        runner = RecordingRunner()
        client = make_app(runner=runner).test_client()

        response = client.post(START, json=DEFAULT_RUN)

        assert response.status_code == 422
        problems = response.get_json()["error"]["details"]["problems"]
        assert problems == [{
            "field": "separators",
            "message_key": "errors.generation.unknown_separator_band",
            "parameters": {"label": "2005-2008", "year": 2026},
        }]
        assert runner.calls == []

    def test_an_unresolvable_or_refused_tournament_lists_every_problem(self, client, member_system):
        member_system.refused[100] = TournamentKlassNamingError(100, [NAMING_PROBLEM])

        response = client.post(INPUTS, json={**DEFAULT_RUN, "tournament_ids": [100, 999]})

        assert _problem_keys(response) == ["errors.klass_naming.no_klass", "errors.generation.tournament_unresolvable"]
        assert response.get_json()["error"]["details"]["problems"][1]["parameters"] == {"tournament_id": 999}

    def test_the_retry_sentence_says_how_to_reload_the_defaults(self):
        catalogues = load_catalogues()

        assert catalogues["en"]["klass_naming.retry"].endswith("close and reopen the generation dialog.")
        assert "Stäng och öppna genereringsdialogen igen" in catalogues["sv"]["klass_naming.retry"]


def _decode_segment(segment):
    return json.loads(urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


class TestMercure:

    @pytest.fixture
    def session(self):
        calls = []

        class Session:
            def post(self, url, **kwargs):
                calls.append((url, kwargs))
                return SimpleNamespace(raise_for_status=lambda: None)

        return SimpleNamespace(calls=calls, session=Session())

    @pytest.fixture
    def publisher(self, session):
        return MercurePublisher(
            "http://127.0.0.1:5000/.well-known/mercure", "publisher-secret",
            executor=ImmediateExecutor(), session=session.session,
        )

    OPERATION_ID = "a" * 32
    TOPIC = f"{TOPIC_PREFIX}/{'a' * 32}"
    TRANSITION = OperationTransition(
        event_id=7,
        kind="progress",
        message_key="operations.progress",
        parameters={"stage": "Fetching tournaments"},
        progress={"completed": 2, "total": 4, "unit": "tournaments"},
    )
    RUNNING = {"state": "running", "terminal": False, "run_id": None, "links": {}, "failure_stage": None}

    def test_it_posts_the_exact_topic_event_id_and_a_machine_readable_payload(self, publisher, session):
        publisher.publish(self.TOPIC, self.OPERATION_ID, "/status", self.TRANSITION, self.RUNNING)

        assert len(session.calls) == 1
        url, request = session.calls[0]
        assert url == "http://127.0.0.1:5000/.well-known/mercure"
        assert request["data"]["topic"] == self.TOPIC
        assert request["data"]["id"] == "7"
        payload = json.loads(request["data"]["data"])
        assert payload["kind"] == "progress"
        assert payload["event_id"] == 7
        assert payload["data"]["progress"]["completed"] == 2
        assert "publisher-secret" not in request["data"]["data"]

    def test_the_bearer_jwt_grants_only_the_exact_jgp_topic(self, publisher, session):
        publisher.publish(self.TOPIC, self.OPERATION_ID, "/status", self.TRANSITION, self.RUNNING)

        token = session.calls[0][1]["headers"]["Authorization"].removeprefix("Bearer ")
        header, claims, _signature = token.split(".")
        assert _decode_segment(header)["alg"] == "HS256"
        assert _decode_segment(claims)["mercure"]["publish"] == [self.TOPIC]

    @pytest.mark.parametrize("topic", [
        "https://stockholmsschack.se/flickornas/operations/" + "a" * 32,
        f"{TOPIC_PREFIX}/{'b' * 32}",
    ])
    def test_it_refuses_another_application_or_operation_topic(self, publisher, session, topic):
        with pytest.raises(ValueError):
            publisher.publish(topic, self.OPERATION_ID, "/status", self.TRANSITION, self.RUNNING)
        assert session.calls == []

    def test_without_a_secret_publication_is_disabled(self, make_app):
        app = make_app(MERCURE_JWT_SECRET=None)

        assert isinstance(app.extensions["jgp"]["mercure_publisher"], DisabledOperationPublisher)
        client = app.test_client()
        assert _operation(client, client.post(START, json=DEFAULT_RUN))["state"] == "completed"

    def test_a_publish_failure_neither_fails_nor_duplicates_the_generation(self, make_app):
        client = make_app(
            MERCURE_PUBLISHER=FailingPublisher(),
            MERCURE_PUBLIC_URL="/local-mercure",
            MERCURE_JWT_SECRET="must-not-reach-browser",
        ).test_client()

        page = client.get("/configuration?lang=en").get_data(as_text=True)
        assert 'data-mercure-hub="/local-mercure"' in page
        assert "must-not-reach-browser" not in page

        operation = _operation(client, client.post(START, json=DEFAULT_RUN))
        assert operation["state"] == "completed"
        assert [event["kind"] for event in operation["events"]].count("completed") == 1


def _run_node(script):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    completed = subprocess.run([node, script], cwd=ROOT, check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr


class TestTheBrowserModules:

    def test_only_a_running_operation_is_recovered(self):
        _run_node("tests/browser/generation-operation-memory.mjs")

    def test_completed_generation_inputs_are_compared(self):
        _run_node("tests/browser/generation-input-state.mjs")

    def test_mercure_notifications_reconcile_against_the_durable_status(self):
        _run_node("tests/browser/operation-stream.mjs")
