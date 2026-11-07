"""Timeouts and bounded retries for upstream requests.

Injected sessions and sleep functions keep transport tests offline.
"""
import json
from pathlib import Path

import pytest
import requests

from jgp_standings import upstream_client
from jgp_standings.upstream_client import (
    FixtureReplay, UpstreamClient, UpstreamDataError, UpstreamResponseError, UpstreamRetryExhausted,
)

ROOT = Path(__file__).resolve().parent.parent
URL = "https://member.schack.se/x"


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, invalid_json=False):
        self.status_code = status_code
        self._payload = payload
        self._invalid_json = invalid_json

    def json(self):
        if self._invalid_json:
            raise requests.JSONDecodeError("Expecting value", "", 0)
        return self._payload


class _FakeSession:
    """Plays back one outcome per attempt: a response, or an exception to raise."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _client(session, sleeps=None):
    """A client over `session` that records each backoff delay into `sleeps` instead of waiting."""
    if sleeps is None:
        sleeps = []
    return UpstreamClient(session=session, sleep=sleeps.append)


class TestTimeout:

    def test_every_request_carries_the_connect_and_response_timeouts(self):
        session = _FakeSession(_FakeResponse(503), _FakeResponse(payload=[]))

        UpstreamClient(session=session, sleep=lambda _: None, connect_timeout=2.0,
                       response_timeout=9.0).get_json(URL, "testing")

        assert [kwargs["timeout"] for _, kwargs in session.calls] == [(2.0, 9.0), (2.0, 9.0)]

    @pytest.mark.parametrize("settings", [
        {"connect_timeout": 0}, {"response_timeout": -1}, {"max_attempts": 0}, {"backoff_seconds": -1},
    ])
    def test_an_unbounded_or_negative_policy_is_refused(self, settings):
        with pytest.raises(ValueError):
            UpstreamClient(session=_FakeSession(), **settings)


class TestRetries:

    @pytest.mark.parametrize("transient", [
        requests.ConnectionError("refused"),
        requests.Timeout("read timed out"),
        _FakeResponse(429),
        _FakeResponse(500),
        _FakeResponse(503),
    ], ids=["network-error", "timeout", "429", "500", "503"])
    def test_a_transient_failure_is_retried_then_succeeds(self, transient):
        session = _FakeSession(transient, transient, _FakeResponse(payload={"ok": True}))
        sleeps = []

        assert _client(session, sleeps).get_json(URL, "testing") == {"ok": True}
        assert len(session.calls) == 3
        assert sleeps == [0.25, 0.5]

    def test_exhaustion_raises_a_named_failure_with_the_stage_and_attempt_count(self):
        session = _FakeSession(_FakeResponse(502), _FakeResponse(502), _FakeResponse(502))
        sleeps = []

        with pytest.raises(UpstreamRetryExhausted) as raised:
            _client(session, sleeps).get_json(URL, "fetching the district club list")

        assert len(session.calls) == 3
        assert sleeps == [0.25, 0.5]  # no delay after the final attempt
        assert raised.value.stage == "fetching the district club list"
        assert str(raised.value) == (
            "upstream failure while fetching the district club list: "
            "HTTP 502; exhausted 3 attempts"
        )

    def test_exhaustion_on_network_errors_reports_the_last_error(self):
        session = _FakeSession(*[requests.ConnectionError("refused")] * 3)

        with pytest.raises(UpstreamRetryExhausted, match="ConnectionError: refused; exhausted 3"):
            _client(session).get_json(URL, "testing")

    @pytest.mark.parametrize("status", [400, 401, 403, 404])
    def test_other_4xx_responses_are_not_retried(self, status):
        session = _FakeSession(_FakeResponse(status))

        with pytest.raises(UpstreamResponseError) as raised:
            _client(session).get_json(URL, "testing")

        assert len(session.calls) == 1
        assert raised.value.status_code == status

    def test_invalid_json_is_a_data_failure_and_is_not_retried(self):
        session = _FakeSession(_FakeResponse(invalid_json=True))

        with pytest.raises(UpstreamDataError, match="not valid JSON"):
            _client(session).get_json(URL, "testing")

        assert len(session.calls) == 1


class TestTheStandaloneFetch:

    def test_with_no_fixtures_dir_set_it_is_the_bounded_client(self, monkeypatch):
        monkeypatch.delenv(upstream_client.FIXTURES_ENV_VAR, raising=False)

        assert isinstance(upstream_client.standalone_fetch().__self__, UpstreamClient)

    def test_with_a_fixtures_dir_set_it_replays_that_set(self, tmp_path, monkeypatch):
        (tmp_path / "responses.json").write_text(json.dumps({URL: {"ok": True}}))
        monkeypatch.setenv(upstream_client.FIXTURES_ENV_VAR, str(tmp_path))

        fetch = upstream_client.standalone_fetch()

        assert isinstance(fetch, FixtureReplay)
        assert fetch(URL, "testing") == {"ok": True}

    def test_no_module_reaches_requests_except_through_the_client(self):
        # The Mercure publisher posts to the hub, not to the member system.
        allowed = {"upstream_client.py", "mercure.py"}
        offenders = [
            path.name for path in (ROOT / "jgp_standings").rglob("*.py")
            if path.name not in allowed and "requests" in path.read_text()
        ]
        assert offenders == []


class TestFixtureReplay:

    @pytest.fixture
    def no_network(self, monkeypatch):
        def fail(*args, **kwargs):
            raise AssertionError("should not touch the network in fixture mode")

        monkeypatch.setattr(UpstreamClient, "get_json", fail)

    def test_a_captured_url_is_replayed_without_a_network_call(self, tmp_path, no_network):
        (tmp_path / "responses.json").write_text(json.dumps({URL: {"ok": True}}))

        assert FixtureReplay(tmp_path)(URL, "testing") == {"ok": True}

    def test_an_uncaptured_url_is_refused_rather_than_fetched(self, tmp_path, no_network):
        (tmp_path / "responses.json").write_text(json.dumps({}))

        with pytest.raises(KeyError, match="no fixture captured"):
            FixtureReplay(tmp_path)("https://member.schack.se/missing", "testing")
