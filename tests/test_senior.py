import json
import re
import shutil
import zipfile
from datetime import UTC, date, datetime
from functools import partial
from io import BytesIO
from pathlib import Path

import pytest
from senior_gp import web as senior_web
from senior_gp.cli import app as senior_cli
from senior_gp.configuration import (
    DM,
    GP,
    MEMBER_SYSTEM,
    RILTON,
    ActiveConfiguration,
    Tournament,
    load_configuration,
    save_configuration,
)
from senior_gp.publication import latest_run
from senior_gp.service import run_generation
from test_flickornas import TOKEN, dev_client, development
from test_jgp import ImmediateExecutor
from typer.testing import CliRunner

from verktyg import create_app

PREFIX = "/tools/senior"
CONFIGURATION = ActiveConfiguration(
    2025, (Tournament(MEMBER_SYSTEM, "5366", "Stockholmsmästerskapet", DM),)
)


def senior_config(tmp_path):
    state = tmp_path / "senior/state"
    save_configuration(state, CONFIGURATION)
    return {
        "TESTING": True,
        "SECRET_KEY": "test-only",
        "SESSION_COOKIE_SECURE": False,
        "SENIOR_SETTINGS": {
            "STATE_DIRECTORY": state,
            "OUTPUT_DIRECTORY": tmp_path / "senior/output",
            # 31 May 2026 in Stockholm: 2025 is still the default season.
            "CLOCK": lambda: datetime(2026, 5, 31, 12, tzinfo=UTC),
            "GENERATION_EXECUTOR": ImmediateExecutor(),
        },
    }


@pytest.fixture
def app(tmp_path):
    return create_app(
        senior_config(tmp_path)
        | {"GOOGLE_OAUTH_CLIENT_ID": "test", "GOOGLE_OAUTH_CLIENT_SECRET": "test"}
    )


def signed_in(app, tools=("senior",)):
    client = app.test_client()
    with client.session_transaction() as session:
        session["identity"] = {"email": "operator@example.com", "name": "Operator"}
        session["tools"] = list(tools)
        session["csrf_token"] = "test-token"
    return client


def save(client, scoring, headers=TOKEN):
    return client.post(
        f"{PREFIX}/api/v1/configuration/tournaments",
        headers=headers,
        json={
            "revision": 1,
            "tournaments": [{"tournament_id": 5366, "scoring": scoring}],
        },
    )


@pytest.mark.parametrize(
    "path",
    [
        f"{PREFIX}/",
        f"{PREFIX}/downloads",
        f"{PREFIX}/reports",
        f"{PREFIX}/static/css/app.css",
        f"{PREFIX}/api/v1/generation/operations/missing",
        "/files/senior/downloads/2025/run/file.xlsx",
        "/files/senior/reports/2025/run/report.txt",
    ],
)
def test_entire_operator_boundary_requires_identity_and_permission(app, path):
    assert app.test_client().get(path).status_code in (302, 401)
    assert signed_in(app, ["jgp", "flickornas"]).get(path).status_code == 403


def test_signed_out_overview_redirects_to_sign_in(app):
    response = app.test_client().get(f"{PREFIX}/")
    assert response.status_code == 302
    assert response.location == f"/login?next={PREFIX}/"


def test_overview_uses_the_portal_shell_locale_and_prefix(app):
    client = signed_in(app)
    page = client.get(f"{PREFIX}/?lang=en").text
    assert '<html lang="en">' in page and "<h1>Senior GP 2025</h1>" in page
    # The portal's shell, its CSRF script and its language form.
    assert "Stockholms Schackförbund" in page and "js/csrf.js" in page
    assert re.search(r'<form method="get" action="[^"]*" data-senior-navigation>', page)
    # Senior's navigation lists the pages it has, all under the prefix.
    nav = page[page.index('aria-label="Senior GP') :]
    nav = nav[: nav.index("</nav>")]
    assert re.findall(r'href="([^"]+)"', nav) == [
        f"{PREFIX}/",
        f"{PREFIX}/downloads",
        f"{PREFIX}/reports",
    ]
    assets = re.findall(r'(?:href|src)="(/tools/[^"]*/static/[^"]+)"', page)
    assert assets and all(asset.startswith(f"{PREFIX}/static/") for asset in assets)
    assert all(client.get(asset).status_code == 200 for asset in assets)
    # The language switch keeps the season.
    form = client.get(f"{PREFIX}/?season=2025&lang=en").text
    form = form[form.index("data-senior-navigation") : form.index('id="locale-choice"')]
    assert re.findall(r'name="(\w+)" value="(\w+)"', form) == [
        ("season", "2025"),
        ("lang", "sv"),
    ]
    # The portal's language choice wins and lasts for the session.
    assert '<html lang="en">' in client.get(f"{PREFIX}/downloads").text


def test_save_requires_csrf_and_records_the_actor(app, caplog):
    client = signed_in(app)
    state = app.config["SENIOR_SETTINGS"]["STATE_DIRECTORY"]
    for headers in ({}, {"X-CSRF-Token": "wrong"}):
        assert save(client, GP, headers).status_code == 403
    assert load_configuration(state, 2025).revision == 1

    response = save(client, GP)
    assert response.status_code == 200 and response.json["data"]["revision"] == 2
    assert load_configuration(state, 2025).tournaments[0].scoring == GP
    assert '"tool": "senior"' in caplog.text
    assert "operator@example.com" in caplog.text and '"status": 200' in caplog.text


def test_file_origin_and_no_public_standings_route(app):
    settings = app.extensions["senior"]["settings"]
    assert settings["FILES_PUBLIC_ORIGIN"] == "/files/senior"
    assert "public_senior_standings" not in app.view_functions


def test_files_serve_only_runs_and_reject_traversal_and_symlinks(app, tmp_path):
    client = signed_in(app)
    runs = app.config["SENIOR_SETTINGS"]["OUTPUT_DIRECTORY"] / "runs/2025/run"
    runs.mkdir(parents=True)
    (runs / "standings.xlsx").write_bytes(b"workbook")
    (runs / "link.xlsx").symlink_to(tmp_path / "outside.xlsx")
    (tmp_path / "outside.xlsx").write_bytes(b"secret")

    response = client.get("/files/senior/downloads/2025/run/standings.xlsx")
    assert response.data == b"workbook"
    assert "attachment" in response.headers["Content-Disposition"]
    for path in ("2025/run/link.xlsx", "../state/x", "2025/run/missing.xlsx"):
        assert client.get(f"/files/senior/downloads/{path}").status_code == 404


def test_development_identity_opens_senior(tmp_path):
    client = dev_client(create_app(development(senior_config(tmp_path))))
    assert client.get(f"{PREFIX}/").status_code == 200
    assert save(client, GP).status_code == 200


def test_debug_without_sign_in_mounts_senior_with_no_configuration():
    app = create_app({"DEBUG": True, "REQUIRE_AUTH": False})
    assert app.test_client().get(f"{PREFIX}/").status_code == 200


FIXTURES = Path(__file__).resolve().parents[1] / "tools/senior/reference-artifacts"
CAPTURE = FIXTURES / "current-config-2025"
ACCEPTED = FIXTURES / "accepted-2025/expected"
# 25 October 2026 in Stockholm, the day the accepted outputs were made.
CAPTURE_TIME = datetime(2026, 10, 25, 12, tzinfo=UTC)
RUN_TIME = re.compile(r"-\d{8}T\d{6}Z")


def retain_accepted_fixture(state):
    """Save the accepted 2025 configuration in `state` and copy its retained inputs there."""
    captured = json.loads((CAPTURE / "inputs/tournaments.json").read_text())[
        "tournaments"
    ]
    # The first days of play that the member system and chess-results give.
    starts = {
        "5366": date(2025, 3, 3),
        "5382": date(2025, 3, 21),
        "5383": date(2025, 5, 18),
    }
    member = tuple(
        Tournament(
            MEMBER_SYSTEM,
            t["id"],
            t["name"],
            DM if t["dm"] else GP,
            start_date=starts[t["id"]],
        )
        for t in captured
    )
    rilton = (
        Tournament(RILTON, "1301929", "Rilton U1800", start_date=date(2026, 1, 2)),
        Tournament(
            RILTON,
            "1301930",
            "Rilton U2200",
            rating_floor=1800,
            start_date=date(2025, 12, 27),
        ),
    )
    save_configuration(state, ActiveConfiguration(2025, rilton + member, revision=0))
    shutil.copytree(CAPTURE / "inputs/http", state / "inputs/http")
    (state / "inputs/rilton").mkdir()
    for identifier, name in (
        ("1301929", "rilton_u1800_2025_2026_1301929.TUNX"),
        ("1301930", "rilton_u2200_2025_2026_1301930.TUNX"),
    ):
        shutil.copy(
            CAPTURE / "inputs/rilton" / name, state / f"inputs/rilton/{identifier}.TUNX"
        )


def workbook_parts(content):
    """Return a workbook's parts except its creation time."""
    with zipfile.ZipFile(BytesIO(content)) as workbook:
        return {
            name: workbook.read(name)
            for name in workbook.namelist()
            if name != "docProps/core.xml"
        }


def run_contents(output):
    """Map each file of season 2025's latest run, named without the run time, to its comparable content."""
    contents = {}
    for path in latest_run(output, 2025).iterdir():
        name = RUN_TIME.sub("", path.name)
        if path.suffix == ".xlsx":
            contents[name] = workbook_parts(path.read_bytes())
        else:
            contents[name] = RUN_TIME.sub("", path.read_text())
    return contents


def test_hosted_run_of_the_accepted_fixture_matches_cli_and_standalone(tmp_path):
    state = tmp_path / "senior/state"
    retain_accepted_fixture(state)
    offline = {
        "CLOCK": lambda: CAPTURE_TIME,
        "GENERATION_EXECUTOR": ImmediateExecutor(),
        "GENERATION_RUNNER": partial(run_generation, offline=True),
    }
    hosted_output = tmp_path / "senior/output"
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-only",
            "GOOGLE_OAUTH_CLIENT_ID": "test",
            "GOOGLE_OAUTH_CLIENT_SECRET": "test",
            "SENIOR_SETTINGS": {
                "STATE_DIRECTORY": state,
                "OUTPUT_DIRECTORY": hosted_output,
            }
            | offline,
        }
    )
    client = signed_in(app)
    started = client.post(
        f"{PREFIX}/api/v1/generation/operations?season=2025",
        headers=TOKEN,
        json={"revision": 1},
    )
    assert started.status_code == 202
    status_url = started.json["data"]["status_url"]
    assert status_url.startswith(f"{PREFIX}/")
    operation = client.get(status_url).json["data"]
    assert operation["state"] == "completed" and operation["actor"] == {
        "email": "operator@example.com",
        "name": "Operator",
    }
    assert operation["events"][-1]["parameters"] == {
        "run_id": operation["run_id"],
        "warnings": [],
    }

    standalone_output = tmp_path / "standalone/output"
    standalone = senior_web.create_app(
        {
            "TESTING": True,
            "STATE_DIRECTORY": state,
            "OUTPUT_DIRECTORY": standalone_output,
        }
        | offline
    ).test_client()
    started = standalone.post(
        "/api/v1/generation/operations?season=2025", json={"revision": 1}
    )
    assert (
        standalone.get(started.json["data"]["status_url"]).json["data"]["state"]
        == "completed"
    )

    cli_output = tmp_path / "cli/output"
    result = CliRunner().invoke(
        senior_cli,
        [
            *("--state-dir", str(state), "--output-dir", str(cli_output)),
            *("--season", "2025", "generate", "--offline", "--date", "2026-10-25"),
        ],
    )
    assert result.exit_code == 0, result.output

    hosted = run_contents(hosted_output)
    assert hosted == run_contents(standalone_output) == run_contents(cli_output)
    assert hosted["2025-gp-standings.json"] == (ACCEPTED / "standings.json").read_text()
    for workbook in (ACCEPTED / "output").glob("*.xlsx"):
        name = re.sub(r"-\d{8}(?=\.)", "", workbook.name)
        assert hosted[name] == workbook_parts(workbook.read_bytes())

    # The portal serves the run's files.
    run = latest_run(hosted_output, 2025)
    for path in run.glob("*.xlsx"):
        url = f"/files/senior/downloads/2025/{run.name}/{path.name}"
        assert client.get(url).data == path.read_bytes()
    # Clear removes the season's files and keeps the configuration and inputs.
    cleared = client.post(f"{PREFIX}/api/v1/downloads/clear?season=2025", headers=TOKEN)
    assert cleared.status_code == 200
    assert not run.exists()
    assert load_configuration(state, 2025).revision == 1
    assert (state / "inputs/rilton/1301929.TUNX").exists()
