"""CLI, standalone app and mounted app must produce identical Typst and public JSON.

All use the 2026 fixtures and a fixed date. The CLI selects fixtures through
JGP_FIXTURES_DIR; applications receive a `FixtureReplay`.
"""
from datetime import UTC, date, datetime
from functools import partial
import json
from pathlib import Path
import shutil

from flask import Flask

from jgp_standings import cli
from jgp_standings.artifact_publication import TYPST_SOURCE_FILE, generate_and_publish
from jgp_standings.upstream_client import FixtureReplay
from jgp_standings.web import MANIFEST, create_app, register

from tests.test_web_available_files import StubRenderer
from tests.test_web_generation import DEFAULT_RUN, START, ImmediateExecutor

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 8, 2)


class _FixedDate(date):

    @classmethod
    def today(cls):
        return TODAY


def _state(directory: Path) -> Path:
    directory.mkdir()
    shutil.copy(ROOT / "state" / "active-configuration.toml", directory)
    return directory


def _contract(output_directory: Path) -> tuple[bytes, dict]:
    (run_directory,) = (output_directory / "runs").iterdir()
    (standings_json,) = run_directory.glob("standings-*.json")
    snapshot = json.loads(standings_json.read_bytes())
    del snapshot["last_updated"]
    return (run_directory / TYPST_SOURCE_FILE).read_bytes(), snapshot


def test_the_command_the_standalone_and_the_hosted_application_produce_the_same_standings(offline_run, monkeypatch):
    monkeypatch.setattr(cli, "date", _FixedDate)
    monkeypatch.setattr(cli, "generate_and_publish", partial(generate_and_publish, renderer=StubRenderer()))
    cli.app([
        "--state-dir", str(_state(offline_run / "cli-state")),
        "--output-dir", str(offline_run / "cli-output"),
        "generate",
    ], standalone_mode=False)

    # The applications replay only what they are handed; a live request would hit the socket guard.
    monkeypatch.delenv("JGP_FIXTURES_DIR")
    for name, prefix in (("web", ""), ("hosted", MANIFEST["url_prefix"])):
        settings = {
            "STATE_DIRECTORY": _state(offline_run / f"{name}-state"),
            "OUTPUT_DIRECTORY": offline_run / f"{name}-output",
            "MEMBER_SYSTEM_FETCH": FixtureReplay(ROOT / "fixtures" / "2026"),
            "FILES_PUBLIC_ORIGIN": "https://files.example",
            "CLOCK": lambda: datetime(2026, 8, 2, 12, tzinfo=UTC),
            "GENERATION_EXECUTOR": ImmediateExecutor(),
            "GENERATION_RENDERER": StubRenderer(),
        }
        if prefix:
            app = Flask("host")
            register(app, settings)
        else:
            app = create_app(settings)
        client = app.test_client()
        started = client.post(f"{prefix}{START}", json=DEFAULT_RUN)
        assert started.status_code == 202
        status_url = started.get_json()["data"]["status_url"]
        assert status_url.startswith(f"{prefix}/api/")
        assert client.get(status_url).get_json()["data"]["state"] == "completed"

        assert _contract(offline_run / "cli-output") == _contract(offline_run / f"{name}-output")
