import shutil
from concurrent.futures import Future
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

import pytest
from jgp_standings.upstream_client import FixtureReplay

from verktyg import create_app

ROOT = Path(__file__).resolve().parents[1] / "tools/jgp"


class ImmediateExecutor:
    def submit(self, function, *args, **kwargs):
        future = Future()
        try:
            future.set_result(function(*args, **kwargs))
        except Exception as error:  # noqa: BLE001 - mirror Executor's exception transport
            future.set_exception(error)
        return future


def jgp_config(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    shutil.copy(ROOT / "state/active-configuration.toml", state)
    return {
        "TESTING": True,
        "SECRET_KEY": "test-only",
        "SESSION_COOKIE_SECURE": False,
        "JGP_SETTINGS": {
            "STATE_DIRECTORY": state,
            "OUTPUT_DIRECTORY": tmp_path / "output",
            "MEMBER_SYSTEM_FETCH": FixtureReplay(ROOT / "fixtures/2026"),
            "CLOCK": lambda: datetime(2026, 8, 2, 12, tzinfo=UTC),
            "GENERATION_EXECUTOR": ImmediateExecutor(),
        },
    }


@pytest.fixture
def app(tmp_path):
    return create_app(
        jgp_config(tmp_path)
        | {
            "GOOGLE_OAUTH_CLIENT_ID": "test",
            "GOOGLE_OAUTH_CLIENT_SECRET": "test",
        }
    )


def signed_in(app, email="operator@example.com", access=True):
    client = app.test_client()
    with client.session_transaction() as session:
        session["identity"] = {"email": email, "name": "Operator"}
        session["tools"] = ["jgp", "flickornas"] if access else []
        session["csrf_token"] = "test-token"
    return client


@pytest.mark.parametrize(
    "path",
    [
        "/tools/jgp/",
        "/tools/jgp/configuration",
        "/tools/jgp/static/js/locale.js",
        "/tools/jgp/help/group-names",
        "/tools/jgp/api/v1/generation/defaults",
        "/tools/jgp/api/v1/generation/operations/missing",
        "/files/jgp/downloads/run/file.pdf",
    ],
)
def test_entire_operator_boundary_requires_identity_and_permission(app, path):
    assert app.test_client().get(path).status_code in (302, 401)
    assert signed_in(app, "outsider@example.com", False).get(path).status_code == 403


@pytest.mark.parametrize(
    "method, path",
    [("post", "rules"), ("put", "tournaments/draft"), ("delete", "rules")],
)
def test_mutations_reject_absent_and_wrong_tokens(app, method, path):
    client = signed_in(app)
    for token in (None, "wrong", "å"):
        response = getattr(client, method)(
            f"/tools/jgp/api/v1/configuration/{path}",
            headers={"X-CSRF-Token": token} if token else {},
        )
        assert response.status_code == 403


def test_contributor_mutation_persists_and_records_actor_in_audit(app, caplog):
    client = signed_in(app)
    path = app.config["JGP_SETTINGS"]["STATE_DIRECTORY"] / "active-configuration.toml"
    before = path.read_bytes()
    response = client.post(
        "/tools/jgp/api/v1/configuration/contributor",
        json={"revision": 1, "name": "Portal", "email": "portal@example.com"},
    )
    assert response.status_code == 403
    assert path.read_bytes() == before
    from jgp_standings.active_configuration import load_configuration

    revision = load_configuration(path.parent).revision
    response = client.post(
        "/tools/jgp/api/v1/configuration/contributor",
        headers={"X-CSRF-Token": "test-token"},
        json={"revision": revision, "name": "Portal", "email": "portal@example.com"},
    )
    assert response.status_code == 200
    assert path.read_bytes() != before
    assert "operator@example.com" in caplog.text
    assert '"status": 200' in caplog.text


def test_tournament_selection_follows_tool_access(app):
    client = signed_in(app)
    response = client.get("/tools/jgp/configuration?lang=en")
    assert 'id="generation-explicit-tournaments"' in response.text
    response = client.post(
        "/tools/jgp/api/v1/generation/inputs",
        json={"year": 2026, "tournament_ids": [6544], "layout": "portrait"},
        headers={"X-CSRF-Token": "test-token"},
    )
    assert response.status_code == 200, response.text
    response = signed_in(app, "outsider@example.com", False).post(
        "/tools/jgp/api/v1/generation/inputs",
        json={"year": 2025, "tournament_ids": [400], "layout": "portrait"},
        headers={"X-CSRF-Token": "test-token"},
    )
    assert response.status_code == 403


def test_host_shell_assets_locale_and_session_draft(app):
    client = signed_in(app)
    page = client.get("/tools/jgp/configuration?lang=en").text
    assert '<html lang="en">' in page
    assert "Stockholms Schackförbund" in page
    assert page.index("bootstrap.bundle.min.js") < page.index('id="locale-catalogue"')
    assert page.index("csrf.js") < page.index('id="locale-catalogue"')
    assert "Configuration" in client.get("/tools/jgp/configuration").text
    # The shell carries the tool's pages and a language switch for the current path.
    assert 'action="/tools/jgp/configuration"' in page
    assert 'id="locale-choice" lang="sv"' in page
    for path in ("/tools/jgp/", "/tools/jgp/downloads", "/tools/jgp/reports"):
        assert f'href="{path}"' in page
    # The tool inherits a choice made on a portal page.
    client.get("/?lang=sv")
    assert "Konfiguration" in client.get("/tools/jgp/configuration").text

    class Assets(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key in ("href", "src") and "/static/" in value:
                    assert client.get(value).status_code == 200

    Assets().feed(page)
    from jgp_standings.active_configuration import load_configuration

    revision = load_configuration(
        app.config["JGP_SETTINGS"]["STATE_DIRECTORY"]
    ).revision
    assert (
        client.put(
            "/tools/jgp/api/v1/configuration/tournaments/draft",
            json={"revision": revision, "tournaments": []},
            headers={"X-CSRF-Token": "test-token"},
        ).status_code
        == 200
    )
    with client.session_transaction() as session:
        assert session["identity"]["email"] == "operator@example.com"
        assert any("draft" in key for key in session)


def test_downloads_reject_traversal_and_symlinks(app, tmp_path):
    root = app.config["JGP_SETTINGS"]["OUTPUT_DIRECTORY"] / "runs"
    (root / "run").mkdir(parents=True)
    (root / "run/result.pdf").write_bytes(b"%PDF-test")
    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    (root / "run/escape.txt").symlink_to(secret)
    client = signed_in(app)
    for category in ("downloads", "reports"):
        response = client.get(f"/files/jgp/{category}/run/result.pdf")
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "private, no-store"
        for path in ("run/escape.txt", "../../secret.txt", "%2e%2e/%2e%2e/secret.txt"):
            assert client.get(f"/files/jgp/{category}/{path}").status_code == 404


def test_index_shows_only_the_tools_the_visitor_may_use(app):
    page = app.test_client().get("/?lang=en").text
    assert "Bookmarklets" in page and "More tools" in page
    assert "/tools/jgp/" not in page and "/tools/flickornas/" not in page
    page = signed_in(app).get("/?lang=en").text
    assert "More tools" not in page
    assert 'href="/tools/jgp/"' in page and 'href="/tools/flickornas/"' in page
    assert "Senior" not in page
    page = signed_in(app, "outsider@example.com", False).get("/?lang=en").text
    assert "Sign out" in page and "Operator" in page
    assert "More tools" not in page and "/tools/jgp/" not in page


def test_logout_requires_csrf_and_ends_operator_access(app):
    client = signed_in(app)
    assert client.post("/logout", data={"csrf_token": "wrong"}).status_code == 403
    assert client.get("/tools/jgp/").status_code == 200
    assert client.post("/logout", data={"csrf_token": "test-token"}).status_code == 302
    with client.session_transaction() as session:
        assert not session
    assert client.get("/tools/jgp/").status_code == 302


def test_mount_requires_explicit_auth_configuration():
    with pytest.raises(ValueError, match="SECRET_KEY"):
        create_app({})


def test_fixture_generation_public_snapshot_protected_pdf_and_clear(app):
    if not shutil.which("typst"):
        pytest.skip("Typst is required for the full PDF generation check")
    client = signed_in(app)
    assert app.test_client().get("/api/v1/standings/jgp").status_code == 404
    response = client.post(
        "/tools/jgp/api/v1/generation/operations",
        json={"year": None, "tournament_ids": None, "layout": "portrait"},
        headers={"X-CSRF-Token": "test-token"},
    )
    assert response.status_code == 202
    status = client.get(response.json["data"]["status_url"]).json["data"]
    assert status["state"] == "completed", status
    assert status["actor"]["email"] == "operator@example.com"
    pdf = client.get(status["links"]["pdf"])
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")
    assert app.test_client().get(status["links"]["pdf"]).status_code == 401
    snapshot = app.test_client().get("/api/v1/standings/jgp")
    assert snapshot.status_code == 200 and snapshot.is_json
    assert snapshot.json
    assert (
        client.post(
            "/tools/jgp/api/v1/downloads/clear", headers={"X-CSRF-Token": "test-token"}
        ).status_code
        == 200
    )
    assert app.test_client().get("/api/v1/standings/jgp").status_code == 404


@pytest.fixture
def dev_app(tmp_path):
    return create_app(
        jgp_config(tmp_path)
        | {"DEBUG": True, "REQUIRE_AUTH": False, "DEV_USER_EMAIL": "Dev@Example.com"}
    )


def dev_client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf_token"] = "test-token"
    return client


def test_development_mode_is_refused_without_debug():
    with pytest.raises(ValueError, match="DEBUG"):
        create_app({"REQUIRE_AUTH": False, "SECRET_KEY": "test-only"})


def test_debug_keeps_sign_in_unless_explicitly_disabled():
    with pytest.raises(ValueError, match="VERKTYG_REQUIRE_AUTH=false"):
        create_app({"DEBUG": True})
    app = create_app(
        {
            "DEBUG": True,
            "GOOGLE_OAUTH_CLIENT_ID": "id",
            "GOOGLE_OAUTH_CLIENT_SECRET": "s",
        }
    )
    assert app.config["REQUIRE_AUTH"] and app.secret_key
    assert app.test_client().get("/tools/jgp/").status_code == 302


def test_debug_without_sign_in_needs_no_configuration():
    app = create_app({"DEBUG": True, "REQUIRE_AUTH": False})
    assert app.secret_key
    for tool in ("jgp", "flickornas"):
        state = app.config["DATA_DIRECTORY"] + f"/{tool}/state"
        assert (Path(state) / "active-configuration.toml").is_file()
    assert app.test_client().get("/login").status_code == 404


def test_development_identity_serves_only_this_computer(dev_app):
    client = dev_client(dev_app)
    assert client.get("/tools/jgp/").status_code == 200
    remote = {"REMOTE_ADDR": "192.0.2.10"}
    assert client.get("/tools/jgp/", environ_base=remote).status_code == 403
    assert dev_app.test_client().get("/", environ_base=remote).status_code == 403


def test_development_identity_opens_tools_and_tournament_selection(dev_app):
    client = dev_client(dev_app)
    page = client.get("/").text
    assert 'href="/tools/jgp/"' in page and "Logga in" not in page
    response = client.get("/tools/jgp/configuration?lang=en")
    assert response.status_code == 200
    assert 'id="generation-explicit-tournaments"' in response.text
    assert '<meta name="csrf-token" content="test-token">' in response.text
    with client.session_transaction() as session:
        assert session["identity"] == {
            "email": "dev@example.com",
            "name": "Dev User",
        }


def test_development_mutations_require_csrf_and_record_identity(dev_app, caplog):
    client = dev_client(dev_app)
    path = (
        dev_app.config["JGP_SETTINGS"]["STATE_DIRECTORY"] / "active-configuration.toml"
    )
    before = path.read_bytes()
    from jgp_standings.active_configuration import load_configuration

    body = {
        "revision": load_configuration(path.parent).revision,
        "name": "Portal",
        "email": "portal@example.com",
    }
    url = "/tools/jgp/api/v1/configuration/contributor"
    for headers in ({}, {"X-CSRF-Token": "wrong"}):
        assert client.post(url, json=body, headers=headers).status_code == 403
    assert path.read_bytes() == before
    response = client.post(url, json=body, headers={"X-CSRF-Token": "test-token"})
    assert response.status_code == 200
    assert path.read_bytes() != before
    assert "dev@example.com" in caplog.text

    response = client.post(
        "/tools/jgp/api/v1/generation/operations",
        json={"year": None, "tournament_ids": None, "layout": "portrait"},
        headers={"X-CSRF-Token": "test-token"},
    )
    assert response.status_code == 202
    status = client.get(response.json["data"]["status_url"]).json["data"]
    assert status["actor"]["email"] == "dev@example.com"


def test_public_pages_need_no_session_in_development_mode(dev_app):
    client = dev_app.test_client()
    for path in ("/bookmarklets", "/health", "/static/js/bookmarklets.js"):
        assert client.get(path).status_code == 200
