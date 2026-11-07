import io
import json
import re
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from flickornas_standings.active_configuration import load_configuration
from flickornas_standings.member_system_client import (
    CanonicalClub,
    CanonicalTournament,
    ClubId,
    ProcessableTournamentGroup,
    TournamentGroupId,
    TournamentId,
)
from test_jgp import ImmediateExecutor, jgp_config

from verktyg import create_app

ROOT = Path(__file__).resolve().parents[1] / "tools/flickornas"
PREFIX = "/tools/flickornas"
TOKEN = {"X-CSRF-Token": "test-token"}


class MemberClient:
    """Answers every tournament ID with one two-round girls' group."""

    def canonical_tournament(self, tournament_id):
        return CanonicalTournament(
            tournament_id=TournamentId(tournament_id),
            name=f"Tournament {tournament_id}",
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 1),
            processable_groups=(
                ProcessableTournamentGroup(
                    group_id=TournamentGroupId(tournament_id + 1000),
                    name="Flickor",
                    klass="F",
                    registered_rounds=2,
                ),
            ),
        )

    def district_clubs(self, district_id):
        return [CanonicalClub(ClubId(10), "Wasa SK")]

    def district_rating_list(self, district_id, request_date):
        return [{"id": 100}]

    def group_table(self, group_id):
        return [player(100, 1, 2, "Wasa SK"), player(200, 2, 0, "Gäst SK")]

    def group_round_results(self, group_id):
        return [
            {"roundNr": 1, "games": [{"whiteId": 100, "blackId": 200, "result": 1}]},
            {"roundNr": 2, "games": [{"whiteId": 200, "blackId": 100, "result": -1}]},
        ]


def player(contender, place, points, club):
    return {
        "points": points,
        "secPoints": points,
        "place": place,
        "contenderId": contender,
        "teamNumber": -1,
        "wonGames": points,
        "drawGames": 0,
        "lostGames": 2 - points,
        "groupId": 1,
        "playerInfo": {
            "firstName": f"Spelare {contender}",
            "lastName": "Test",
            "birthdate": "2012",
            "sex": 1,
            "clubId": 10 if club == "Wasa SK" else 20,
            "club": club,
        },
    }


def flickornas_config(tmp_path):
    state = tmp_path / "flickornas/state"
    state.mkdir(parents=True)
    shutil.copy(ROOT / "state/active-configuration.toml", state)
    return {
        "TESTING": True,
        "SECRET_KEY": "test-only",
        "SESSION_COOKIE_SECURE": False,
        "FLICKORNAS_SETTINGS": {
            "STATE_DIRECTORY": state,
            "OUTPUT_DIRECTORY": tmp_path / "flickornas/output",
            "MEMBER_SYSTEM_CLIENT": MemberClient(),
            "CLOCK": lambda: datetime(2026, 8, 2, 12, tzinfo=UTC),
            "GENERATION_EXECUTOR": ImmediateExecutor(),
        },
    }


def development(config):
    return config | {"DEBUG": True, "REQUIRE_AUTH": False}


def dev_client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf_token"] = "test-token"
    return client


def state_files(app, tool):
    root = app.config[f"{tool.upper()}_SETTINGS"]["STATE_DIRECTORY"]
    return sorted(
        (path.relative_to(root), path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    )


@pytest.fixture
def both(tmp_path):
    (tmp_path / "jgp").mkdir()
    config = jgp_config(tmp_path / "jgp") | flickornas_config(tmp_path)
    return create_app(development(config))


def test_production_boundary_requires_identity_and_permission(tmp_path):
    app = create_app(
        flickornas_config(tmp_path)
        | {
            "GOOGLE_OAUTH_CLIENT_ID": "test",
            "GOOGLE_OAUTH_CLIENT_SECRET": "test",
        }
    )
    outsider = app.test_client()
    with outsider.session_transaction() as session:
        session["identity"] = {"email": "outsider@example.com"}
        session["tools"] = ["jgp"]
    for path in (
        f"{PREFIX}/configuration",
        f"{PREFIX}/api/v1/generation/defaults",
        "/files/flickornas/reports/run/a.pdf",
        "/files/flickornas/downloads/run/a.pdf",
    ):
        assert app.test_client().get(path).status_code in (302, 401)
        assert outsider.get(path).status_code == 403


def test_overlapping_tool_directories_are_refused(tmp_path):
    (tmp_path / "jgp").mkdir()
    config = jgp_config(tmp_path / "jgp") | flickornas_config(tmp_path)
    config["FLICKORNAS_SETTINGS"]["OUTPUT_DIRECTORY"] = tmp_path / "jgp/output/f"
    with pytest.raises(ValueError, match="overlaps"):
        create_app(development(config))


def test_startup_preserves_existing_configuration(tmp_path):
    config = development(flickornas_config(tmp_path))
    before = state_files(create_app(config), "flickornas")
    assert state_files(create_app(config), "flickornas") == before


def test_configuration_edit_and_rules_upload_persist_through_the_portal(both, caplog):
    client = dev_client(both)
    jgp_before = state_files(both, "jgp")
    state = both.config["FLICKORNAS_SETTINGS"]["STATE_DIRECTORY"]
    assert "Tournament 6336" in client.get(f"{PREFIX}/configuration").text
    body = {
        "revision": load_configuration(state).revision,
        "name": "Portal",
        "email": "portal@example.com",
    }
    rules = f"{PREFIX}/api/v1/configuration/rules"
    contributor = f"{PREFIX}/api/v1/configuration/contributor"

    before = state_files(both, "flickornas")
    for headers in ({}, {"X-CSRF-Token": "wrong"}):
        assert client.post(contributor, json=body, headers=headers).status_code == 403
        upload = {"rules": (io.BytesIO(b"= Regler\n"), "rules.typ")}
        assert client.post(rules, data=upload, headers=headers).status_code == 403
    assert state_files(both, "flickornas") == before

    assert client.post(contributor, json=body, headers=TOKEN).status_code == 200
    assert load_configuration(state).contributor["name"] == "Portal"
    upload = {"rules": (io.BytesIO(b"= Regler\n"), "rules.typ")}
    assert client.post(rules, data=upload, headers=TOKEN).status_code == 200
    assert (state / "typst/rules.typ").read_bytes() == b"= Regler\n"
    assert '"tool": "flickornas"' in caplog.text
    assert "dev@example.com" in caplog.text
    assert state_files(both, "jgp") == jgp_before


def test_locales_assets_help_and_drafts_stay_under_the_prefix(both):
    client = dev_client(both)
    page = client.get(f"{PREFIX}/configuration?lang=en").text
    assert '<html lang="en">' in page and "Configuration" in page
    assert page.index("csrf.js") < page.index('id="locale-catalogue"')
    assert f'href="{PREFIX}/help/group-names"' in page
    assert client.get(f"{PREFIX}/help/group-names").status_code == 200
    assert "Konfiguration" in client.get(f"{PREFIX}/configuration?lang=sv").text
    assets = re.findall(r'(?:href|src)="([^"]*/static/[^"]+)"', page)
    assert any(asset.startswith(f"{PREFIX}/static/") for asset in assets)
    for asset in assets:
        assert asset.startswith((f"{PREFIX}/static/", "/static/")), asset
        assert client.get(asset).status_code == 200, asset

    jgp_state = both.config["JGP_SETTINGS"]["STATE_DIRECTORY"]
    from jgp_standings.active_configuration import load_configuration as load_jgp

    jgp_before = state_files(both, "jgp")
    assert (
        client.put(
            "/tools/jgp/api/v1/configuration/tournaments/draft",
            json={"revision": load_jgp(jgp_state).revision, "tournaments": []},
            headers=TOKEN,
        ).status_code
        == 200
    )
    state = both.config["FLICKORNAS_SETTINGS"]["STATE_DIRECTORY"]
    draft = {
        "tournament_id": 7,
        "name": "Tournament 7",
        "start_date": "2026-03-01",
        "end_date": "2026-03-01",
    }
    response = client.put(
        f"{PREFIX}/api/v1/configuration/tournaments/draft",
        json={"revision": load_configuration(state).revision, "tournaments": [draft]},
        headers=TOKEN,
    )
    assert response.status_code == 200
    assert "Tournament 7" in client.get(f"{PREFIX}/configuration").text
    assert "Tournament 7" not in client.get("/tools/jgp/configuration").text
    with client.session_transaction() as session:
        drafts = [key for key in session if "draft" in key]
    assert any("jgp" in key for key in drafts)
    assert any("flickornas" in key for key in drafts)
    assert state_files(both, "jgp") == jgp_before


class FailingPublisher:
    def publish(self, *args, **kwargs):
        raise RuntimeError("Mercure is unavailable")


class FixtureRenderer:
    def render(self, source, destination, *, root, font_directory):
        destination.write_bytes(b"%PDF-fixture\n")


def output_files(app, tool):
    root = app.config[f"{tool.upper()}_SETTINGS"]["OUTPUT_DIRECTORY"]
    return sorted(
        (path.relative_to(root), path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    )


def generate(client, prefix=PREFIX, **body):
    body = {"year": None, "tournament_ids": None, "layout": "auto"} | body
    response = client.post(
        f"{prefix}/api/v1/generation/operations", json=body, headers=TOKEN
    )
    assert response.status_code == 202, response.json
    status = client.get(response.json["data"]["status_url"]).json["data"]
    assert status["state"] == "completed", status
    return status


def test_failed_publisher_still_permits_polling(tmp_path):
    config = development(flickornas_config(tmp_path))
    config["FLICKORNAS_SETTINGS"] |= {
        "MERCURE_PUBLISHER": FailingPublisher(),
        "GENERATION_RENDERER": FixtureRenderer(),
    }
    status = generate(dev_client(create_app(config)))
    assert status["status_url"].startswith(f"{PREFIX}/api/")
    assert status["links"]["files"] == f"{PREFIX}/downloads"


def test_files_serve_only_runs_and_reject_traversal_and_symlinks(tmp_path):
    app = create_app(development(flickornas_config(tmp_path)))
    output = app.config["FLICKORNAS_SETTINGS"]["OUTPUT_DIRECTORY"]
    (output / "runs/run").mkdir(parents=True)
    (output / "runs/run/standings.pdf").write_bytes(b"%PDF-test")
    (output / "public-standings.json").write_text("[]")
    (output / "runs/run/escape.json").symlink_to(output / "public-standings.json")
    client = dev_client(app)
    for category in ("downloads", "reports"):
        response = client.get(f"/files/flickornas/{category}/run/standings.pdf")
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "private, no-store"
        for path in (
            "run/escape.json",
            "../public-standings.json",
            "%2e%2e/public-standings.json",
        ):
            assert client.get(f"/files/flickornas/{category}/{path}").status_code == 404
    assert client.get("/files/flickornas/other/run/standings.pdf").status_code == 404


def test_generation_publishes_serves_preserves_and_clears(both, caplog):
    if not shutil.which("typst"):
        pytest.skip("Typst is required for the full PDF generation check")
    client = dev_client(both)
    public = both.test_client()
    generate(client, "/tools/jgp", layout="portrait")
    jgp_output = output_files(both, "jgp")
    jgp_snapshot = public.get("/api/v1/standings/jgp").data
    assert public.get("/api/v1/standings/flickornas").status_code == 404

    status = generate(client)
    assert status["actor"]["email"] == "dev@example.com"
    assert status["links"]["files"] == f"{PREFIX}/downloads"
    assert status["links"]["pdf"].startswith("/files/flickornas/downloads/")
    pdf = client.get(status["links"]["pdf"])
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")
    for category in ("downloads", "reports"):
        html = client.get(f"{PREFIX}/{category}").text
        links = set(re.findall(r'href="(/files/flickornas/[^"]+)"', html))
        assert links and all(f"/{category}/" in link for link in links)
        for link in links:
            assert client.get(link).status_code == 200, link

    snapshot = public.get("/api/v1/standings/flickornas")
    assert snapshot.status_code == 200
    assert snapshot.mimetype == "application/json"
    assert json.loads(snapshot.data)

    reloaded = create_app(dict(both.config)).test_client()
    assert reloaded.get(status["status_url"]).json["data"]["links"] == status["links"]

    partial = generate(client, year=2026, tournament_ids=[6336])
    assert partial["is_partial"]
    assert client.get(partial["links"]["pdf"]).status_code == 200
    assert public.get("/api/v1/standings/flickornas").data == snapshot.data

    clear = client.post(f"{PREFIX}/api/v1/downloads/clear", headers=TOKEN)
    assert clear.status_code == 200
    assert public.get("/api/v1/standings/flickornas").status_code == 404
    assert client.get(status["links"]["pdf"]).status_code == 404
    assert output_files(both, "jgp") == jgp_output
    assert public.get("/api/v1/standings/jgp").data == jgp_snapshot

    audits = [
        json.loads(record.getMessage().removeprefix("tool_mutation "))
        for record in caplog.records
        if record.getMessage().startswith("tool_mutation")
    ]
    starts = [a for a in audits if a["endpoint"].endswith("start_generation_operation")]
    assert {a["tool"] for a in starts} == {"jgp", "flickornas"}
    for audit in audits:
        assert set(audit) == {"tool", "actor", "method", "endpoint", "status"}
        assert audit["actor"]["email"] == "dev@example.com"
