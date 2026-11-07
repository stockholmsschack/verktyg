"""JGP registered into a host's own Flask application, under the manifest's prefix."""
from datetime import UTC, datetime
import io
from pathlib import Path
import re
import shutil

from flask import Blueprint, Flask, abort, g, request
import pytest
from werkzeug.exceptions import HTTPException

from jgp_standings.active_configuration import load_configuration
from jgp_standings.upstream_client import FixtureReplay, MemberSystemClient, TournamentGroupSearchHit
from jgp_standings.web import MANIFEST, create_app, register

from tests.test_web_generation import (
    DEFAULT_RUN, START, DeferredExecutor, FailingPublisher, ImmediateExecutor, RecordingRunner,
)

ROOT = Path(__file__).resolve().parent.parent


HOST_SHELL = """<html lang="{{ locale }}"><head><title>{% block title %}{% endblock %}</title>
{% block head %}<link href="/host/bootstrap.css" rel="stylesheet">{% endblock %}</head>
<body><nav>Host navbar</nav>{% block content %}{% endblock %}
{% block scripts %}<script src="/host/bootstrap.bundle.js"></script>{% endblock %}</body></html>"""
HOST_USER = "X-Host-User"


def _host(tmp_path, state_directory, offline_run, **settings):
    """A host with its own `pages` Blueprint that lets only a signed-in user reach the mount."""
    host = Flask("host", template_folder=tmp_path)
    host.config["SECRET_KEY"] = "host"
    host.register_blueprint(Blueprint("pages", "host"))

    @host.before_request
    def guard_the_mount():
        if request.path.startswith(MANIFEST["url_prefix"]) and HOST_USER not in request.headers:
            abort(401)

    register(host, {
        "STATE_DIRECTORY": state_directory,
        "OUTPUT_DIRECTORY": offline_run / "output",
        "MEMBER_SYSTEM_FETCH": FixtureReplay(ROOT / "fixtures" / "2026"),
        "FILES_PUBLIC_ORIGIN": "https://files.example",
        **settings,
    })
    client = host.test_client()
    client.environ_base[f"HTTP_{HOST_USER.upper().replace('-', '_')}"] = "admin@example.com"
    return client


@pytest.mark.parametrize("shell", [None, "host_shell.jinja2"])
@pytest.mark.parametrize("lang, heading", [("sv", "Konfiguration"), ("en", "Configuration")])
def test_the_landing_and_configuration_pages_load_under_the_prefix(
    state_directory, offline_run, tmp_path, shell, lang, heading,
):
    (tmp_path / "host_shell.jinja2").write_text(HOST_SHELL)
    client = _host(tmp_path, state_directory, offline_run, LOCALE=lambda: lang, **({"BASE_TEMPLATE": shell} if shell else {}))

    for page in ("/", "/configuration"):
        response = client.get(f"{MANIFEST['url_prefix']}{page}")
        assert response.status_code == 200, page
        body = response.get_data(as_text=True)
        assert ("Host navbar" in body) == bool(shell)
        if shell:
            # JGP's dialogs need the shell's Bootstrap, loaded before JGP's own scripts.
            assert '<link href="/host/bootstrap.css"' in body
            assert body.index('src="/host/bootstrap.bundle.js"') < body.index('id="locale-catalogue"')
        assert 'id="group-names-help" rel="help" href="/tools/jgp/help/group-names"' in body
        assets = re.findall(r'(?:href|src)="(/[^"]*/static/[^"]+)"', body)
        assert assets and all(asset.startswith("/tools/jgp/static/") for asset in assets)
        assert all(client.get(asset).status_code == 200 for asset in assets)
    assert heading in body


def test_a_rejected_mutation_changes_neither_the_configuration_nor_the_rules_file(
    state_directory, offline_run, tmp_path,
):
    client = _host(tmp_path, state_directory, offline_run, MUTATION_PROTECTION=lambda: False)
    before = sorted((path.name, path.read_bytes()) for path in state_directory.iterdir())
    prefix = f"{MANIFEST['url_prefix']}/api/v1/configuration"

    contributor = client.post(f"{prefix}/contributor", json={"name": "X", "email": "x@example.com"})
    upload = client.post(f"{prefix}/rules", data={"rules": (io.BytesIO(b"= X\n"), "rules.typ")})

    assert (contributor.status_code, upload.status_code) == (403, 403)
    assert contributor.json["error"]["code"] == "http_403"
    assert sorted((path.name, path.read_bytes()) for path in state_directory.iterdir()) == before
    assert client.get(f"{MANIFEST['url_prefix']}/configuration").status_code == 200


def test_every_configuration_tab_edits_under_the_prefix_as_it_does_standalone(
    state_directory, offline_run, tmp_path,
):
    def clock():
        return datetime(2027, 1, 10, 12, tzinfo=UTC)  # the 2026 season is over, so a rollover can start

    member_system = MemberSystemClient(fetch=FixtureReplay(ROOT / "fixtures" / "2026"))
    member_system.tournament_group_search = lambda query: [TournamentGroupSearchHit(8101, 6336, "Klass A", query)]
    standalone_state = shutil.copytree(state_directory, tmp_path / "standalone-state")
    standalone = create_app({
        "STATE_DIRECTORY": standalone_state,
        "OUTPUT_DIRECTORY": offline_run / "standalone-output",
        "MEMBER_SYSTEM_FETCH": member_system.fetch,
        "MEMBER_SYSTEM_CLIENT": member_system,
        "CLOCK": clock,
    }).test_client()
    hosted = _host(tmp_path, state_directory, offline_run, MEMBER_SYSTEM_CLIENT=member_system, CLOCK=clock)
    configuration = load_configuration(state_directory)
    contributor = {"name": "X", "email": "x@example.com"}
    dispensations = [
        {"player_id": player_id, "dispensation_year": year}
        for player_id, year in list(configuration.dispensation_dict.items())[1:]
    ]
    club = {"action": "upsert", "canonical_name": "Kista SC KFUM Schacksektion", "display_name": "Kista"}

    requests = [
        ("get", "/api/v1/tournaments/search?q=Wasa", lambda revision: {}),
        ("put", "/api/v1/configuration/tournaments/draft",
         lambda revision: {"json": {"revision": revision, "tournaments": []}}),
        ("post", "/api/v1/configuration/tournaments/rollover", lambda revision: {"json": {"revision": revision}}),
        ("delete", "/api/v1/configuration/tournaments/rollover", lambda revision: {"json": {"revision": revision}}),
        ("post", "/api/v1/configuration/tournaments",
         lambda revision: {"json": {"revision": revision, "tournament_ids": list(configuration.tournament_ids)}}),
        ("post", "/api/v1/configuration/dispensations",
         lambda revision: {"json": {"revision": revision, "dispensations": dispensations}}),
        ("post", "/api/v1/configuration/separators",
         lambda revision: {"json": {"revision": revision, "finals_separators": {"2014": [3]}}}),
        ("post", "/api/v1/configuration/club-display-names", lambda revision: {"json": {"revision": revision, **club}}),
        ("post", "/api/v1/configuration/contributor",
         lambda revision: {"json": {"revision": revision - 1, **contributor}}),
        ("post", "/api/v1/configuration/contributor", lambda revision: {"json": {"revision": revision, **contributor}}),
        ("post", "/api/v1/configuration/rules",
         lambda revision: {"data": {"rules": (io.BytesIO(b"= X\n"), "rules.typ")}}),
        ("delete", "/api/v1/configuration/rules", lambda revision: {}),
    ]

    def responses(client, prefix):
        revision, results = configuration.revision, []
        for method, path, options in requests:
            response = getattr(client, method)(f"{prefix}{path}", **options(revision))
            revision = (response.json.get("data") or {}).get("revision", revision)
            results.append((response.status_code, response.json))
        return results

    expected = responses(standalone, "")
    # Every request but the deliberately stale contributor save succeeds.
    assert [status for status, _ in expected] == [200] * 8 + [409] + [200] * 3
    assert responses(hosted, MANIFEST["url_prefix"]) == expected
    saved = load_configuration(state_directory)
    assert saved == load_configuration(standalone_state)
    assert saved.revision > configuration.revision and saved.finals_separators == {"2014": (3,)}


def test_the_host_guard_keeps_a_signed_out_user_from_the_mount(state_directory, offline_run, tmp_path):
    client = _host(tmp_path, state_directory, offline_run)
    client.environ_base.clear()
    before = sorted((path.name, path.read_bytes()) for path in state_directory.iterdir())
    prefix = MANIFEST["url_prefix"]

    assert client.get(f"{prefix}/configuration").status_code == 401
    assert client.get(f"{prefix}/api/v1/generation/defaults").status_code == 401
    assert client.post(f"{prefix}/api/v1/configuration/contributor", json={"name": "X"}).status_code == 401
    assert sorted((path.name, path.read_bytes()) for path in state_directory.iterdir()) == before


def test_hosted_tournament_selection_is_denied_without_the_administrator_callback(
    state_directory, offline_run, tmp_path,
):
    client = _host(tmp_path, state_directory, offline_run)
    prefix = MANIFEST["url_prefix"]

    page = client.get(f"{prefix}/configuration?lang=en", base_url="http://localhost").get_data(as_text=True)
    response = client.post(
        f"{prefix}/api/v1/generation/inputs", json={"year": 2025, "tournament_ids": [400], "layout": "portrait"},
        base_url="http://localhost",
    )

    assert 'id="generation-explicit-tournaments"' not in page
    assert response.json["error"]["code"] == "generation_tournament_selection_forbidden"


def test_status_busy_and_reloaded_operation_links_keep_the_prefix(state_directory, offline_run, tmp_path):
    prefix = MANIFEST["url_prefix"]
    executor = DeferredExecutor()
    client = _host(
        tmp_path, state_directory, offline_run, GENERATION_EXECUTOR=executor, GENERATION_RUNNER=RecordingRunner(),
        ACTOR=lambda: "admin@example.com",
    )

    accepted = client.post(f"{prefix}{START}", json=DEFAULT_RUN).json["data"]
    busy = client.post(f"{prefix}{START}", json=DEFAULT_RUN).json["error"]["details"]
    status_url = f"{prefix}/api/v1/generation/operations/{accepted['operation_id']}"
    assert accepted["status_url"] == busy["status_url"] == status_url
    executor.run_next()
    assert client.get(status_url).json["data"]["state"] == "completed"

    record = state_directory / "operations" / f"{accepted['operation_id']}.json"
    record.write_text(record.read_text().replace(prefix, ""))  # a record from before the mount
    reloaded = _host(tmp_path, state_directory, offline_run).get(status_url).json["data"]
    assert reloaded["status_url"] == status_url
    assert reloaded["actor"] == "admin@example.com"


@pytest.mark.parametrize("publisher", [{}, {"MERCURE_PUBLISHER": FailingPublisher()}])
def test_generation_and_status_work_without_a_working_publisher(state_directory, offline_run, tmp_path, publisher):
    prefix = MANIFEST["url_prefix"]
    client = _host(
        tmp_path, state_directory, offline_run, MERCURE_PUBLIC_URL="https://hub.example/.well-known/mercure",
        GENERATION_EXECUTOR=ImmediateExecutor(), GENERATION_RUNNER=RecordingRunner(), **publisher,
        FILES_PUBLIC_ORIGIN="https://files.example/tools/jgp/",
    )

    page = client.get(f"{prefix}/configuration").get_data(as_text=True)
    assert 'data-mercure-hub="https://hub.example/.well-known/mercure"' in page
    status_url = client.post(f"{prefix}{START}", json=DEFAULT_RUN).json["data"]["status_url"]
    status = client.get(status_url).json["data"]
    assert status["state"] == "completed"
    assert status["links"]["pdf"].startswith("https://files.example/tools/jgp/downloads/")
    assert status["links"]["files"] == f"{prefix}/downloads"


def test_the_host_keeps_its_own_templates_api_errors_locale_and_hooks(state_directory, offline_run, tmp_path):
    (tmp_path / "configuration.jinja2").write_text("Host configuration")
    client = _host(tmp_path, state_directory, offline_run, MUTATION_PROTECTION=lambda: False)
    host = client.application

    @host.post("/api/v1/host")
    def host_api():
        assert "locale" not in g
        abort(403)

    host.register_error_handler(HTTPException, lambda error: ({"host": error.code}, error.code))

    response = client.post("/api/v1/host?lang=en")
    assert (response.status_code, response.json) == (403, {"host": 403})
    assert "Set-Cookie" not in response.headers
    jgp = client.post(f"{MANIFEST['url_prefix']}/api/v1/configuration/contributor?lang=en", json={})
    assert (jgp.status_code, jgp.json["error"]["code"]) == (403, "http_403")
    assert "Host configuration" not in client.get(f"{MANIFEST['url_prefix']}/configuration").get_data(as_text=True)


@pytest.mark.parametrize("capability", ["STATE_DIRECTORY", "OUTPUT_DIRECTORY", "MEMBER_SYSTEM_FETCH", "FILES_PUBLIC_ORIGIN"])
def test_registration_fails_without_a_required_capability(capability, tmp_path):
    host = Flask("host")
    settings = {
        "STATE_DIRECTORY": tmp_path, "OUTPUT_DIRECTORY": tmp_path, "MEMBER_SYSTEM_FETCH": object(),
        "FILES_PUBLIC_ORIGIN": "https://files.example",
    }
    del settings[capability]

    with pytest.raises(KeyError, match=capability):
        register(host, settings)
    assert "jgp" not in host.extensions and not host.blueprints
