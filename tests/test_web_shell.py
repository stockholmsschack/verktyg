"""Standalone pages with an injected member-system client and fixed clock.

Offline checks use the real client with 2026 fixtures and network access blocked.
"""
from datetime import UTC, date, datetime
from pathlib import Path
import re
import tomllib
from unittest.mock import Mock, patch

from flask import request
from werkzeug.exceptions import NotFound
import pytest

from jgp_standings.active_configuration import (
    ACTIVE_CONFIGURATION_FILENAME,
    ActiveConfiguration,
    ConfigurationValidationError,
    DispensationPlayer,
    StaleConfigurationError,
    save_configuration,
)
from jgp_standings.web import create_app
from jgp_standings.web.__main__ import main
from jgp_standings.web.localization import load_catalogues
from jgp_standings.klass_naming import KlassNameReason, KlassNamingProblem
from jgp_standings.upstream_client import (
    CanonicalTournament,
    ProcessableTournamentGroup,
    TournamentKlassNamingError,
    TournamentResolutionError,
    UpstreamRetryExhausted,
)

ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = ROOT / "jgp_standings" / "web"
OCTOBER_2026 = datetime(2026, 10, 4, 12, tzinfo=UTC)


def _configuration(**changes) -> ActiveConfiguration:
    values = {
        "tournament_ids": (11, 22),
        "contributor": {"name": "Test Contributor", "email": "test@example.com"},
        "dispensation_dict": {546393: 2009, 726286: 2009},
        "dispensation_players": {
            546393: DispensationPlayer("Pratyush Tripathi", 2010, "SS 4 Springare"),
            726286: DispensationPlayer("Igor Markov", 2010, "SK Rockaden"),
        },
    }
    return ActiveConfiguration(**{**values, **changes})


class FakeMemberSystem:
    """Resolves tournament `n` to one that starts on day `n` of January of `years[n]` (2026 by default)."""

    def __init__(self, years=None, failure=None):
        self.years = years or {}
        self.failure = failure

    def canonical_tournament(self, tournament_id):
        if self.failure is not None:
            raise self.failure
        start = date(self.years.get(tournament_id, 2026), 1, tournament_id)
        return CanonicalTournament(
            tournament_id=tournament_id,
            name=f"Tournament {tournament_id}",
            start_date=start,
            end_date=start,
            processable_groups=(ProcessableTournamentGroup(tournament_id + 100, "Klass A", "a"),),
        )


@pytest.fixture
def make_client(tmp_path):
    def make(member_system=None, configuration=None):
        state_directory = tmp_path / "state"
        if configuration is not None:
            save_configuration(state_directory, configuration)
        app = create_app({
            "TESTING": True,
            "STATE_DIRECTORY": state_directory,
            "OUTPUT_DIRECTORY": tmp_path / "output",
            "MEMBER_SYSTEM_CLIENT": member_system or FakeMemberSystem(),
            "CLOCK": lambda: OCTOBER_2026,
        })
        return app.test_client()
    return make


def _body(client, path):
    response = client.get(path)
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_standalone_standings_returns_404_before_publication(make_client):
    assert make_client().get("/api/v1/standings/jgp").status_code == 404


def test_standalone_standings_serves_the_latest_snapshot(make_client, tmp_path):
    client = make_client()
    output = tmp_path / "output"
    run = output / "runs" / "test"
    run.mkdir(parents=True)
    payload = run / "standings.json"
    snapshot = output / "public-standings.json"
    snapshot.symlink_to("runs/test/standings.json")

    for body in (
        b'{"tournament_ids": [6544], "standings": {}}',
        b'{"tournament_ids": [6544, 6540], "standings": {}}',
    ):
        payload.write_bytes(body)
        response = client.get("/api/v1/standings/jgp")
        assert response.status_code == 200
        assert response.mimetype == "application/json"
        assert response.headers["Cache-Control"] == "no-cache"
        assert response.data == body

    payload.unlink()
    assert client.get("/api/v1/standings/jgp").status_code == 404


class TestPages:

    @pytest.mark.parametrize("path, swedish, english", [
        ("/", "Översikt", "Overview"),
        ("/configuration", "Konfiguration", "Configuration"),
        ("/downloads", "Inga filer är tillgängliga.", "No files available."),
        ("/reports", "Inga rapporter är tillgängliga.", "No reports available."),
    ])
    def test_every_page_renders_in_swedish_and_english(self, make_client, path, swedish, english):
        client = make_client(configuration=_configuration())

        swedish_body = _body(client, f"{path}?lang=sv")
        english_body = _body(client, f"{path}?lang=en")

        assert '<html lang="sv">' in swedish_body and swedish in swedish_body
        assert '<html lang="en">' in english_body and english in english_body

    def test_swedish_is_the_default(self, make_client):
        assert '<html lang="sv">' in _body(make_client(), "/")

    def test_the_navbar_has_the_four_entries_and_marks_the_current_page(self, make_client):
        body = _body(make_client(), "/reports?lang=en")

        navigation = body[body.index('id="primary-navigation"'):body.index("locale-choice")]
        assert re.findall(r'href="(/[^"?]*)\?lang=en"', navigation) == ["/", "/configuration", "/downloads", "/reports"]
        assert re.search(r'nav-link active"\s+href="/reports', navigation)

    def test_the_language_choice_is_remembered_and_shared_with_javascript(self, make_client):
        client = make_client()

        response = client.get("/?lang=en")
        body = response.get_data(as_text=True)

        assert "Generate and manage the Junior Grand Prix standings" in body
        assert '"nav.home": "Overview"' in body
        assert "jgp_locale=en" in response.headers["Set-Cookie"]
        # The switch offers the other language by its code and flag.
        assert '<input type="hidden" name="lang" value="sv">' in body
        assert 'lang="sv"\n                    title="Svenska"' in body
        assert "images/flags/se.svg" in body
        assert "No files available." in _body(client, "/downloads")

    def test_home_summarises_a_valid_configuration(self, make_client):
        body = _body(make_client(configuration=_configuration()), "/?lang=en")

        assert "The configuration is valid and contains 2 tournaments." in body
        assert "No files available." in body

    def test_home_says_when_the_configuration_cannot_be_read(self, make_client):
        assert "The active configuration cannot be read." in _body(make_client(), "/?lang=en")


class TestTheConfigurationPage:

    def test_a_valid_configuration_shows_the_summary_list(self, make_client):
        body = _body(make_client(configuration=_configuration()), "/configuration?lang=en")

        summary = body[body.index("data-configuration-summary"):body.index("</dl>")]
        terms = re.findall(r'data-configuration-tab-link="([^"]+)"\s*>([^<]+)<', summary)
        assert terms == [
            ("tournament-addition-tab", "Tournaments"),
            ("contributor-tab", "Feedback"),
            ("dispensations-tab", "Dispensations"),
            ("club-display-names-tab", "Club names"),
            ("separators-tab", "Separators"),
            ("rules-file-tab", "PDF rules page"),
        ]
        values = [re.sub(r"\s+", " ", value).strip() for value in re.findall(r"<dd[^>]*>(.*?)</dd>", summary, re.S)]
        assert values == ["11, 22", "Test Contributor &lt;test@example.com&gt;", "2", "None", "None", "rules.typ"]

    def test_no_summary_entry_shows_penalties_or_the_revision(self, make_client):
        body = _body(make_client(configuration=_configuration(penalty_dict={11: (546393,)})), "/configuration?lang=en")
        body = re.sub(r'<script id="locale-catalogue".*?</script>', "", body, flags=re.S)

        assert "enalt" not in body
        assert "Revision" not in body

    def test_an_invalid_configuration_shows_its_problems_and_no_summary(self, make_client, tmp_path):
        state_directory = tmp_path / "state"
        state_directory.mkdir()
        (state_directory / ACTIVE_CONFIGURATION_FILENAME).write_text("revision = 1\nlandscape = \"yes\"\n")

        body = _body(make_client(), "/configuration?lang=en")

        assert "The configuration cannot be shown until these problems are fixed:" in body
        assert "<li>landscape must be true or false</li>" in body
        assert "<li>at least one tournament id is required</li>" in body
        assert "data-configuration-summary" not in body
        assert 'role="tablist"' not in body
        assert "until the configuration problems are fixed" in body

    def test_the_six_tabs_are_present_and_tournaments_active(self, make_client):
        body = _body(make_client(configuration=_configuration()), "/configuration?lang=en")

        tabs = re.findall(r'id="([a-z-]+)-tab"', body)
        assert tabs == ["tournament-addition", "dispensations", "contributor", "club-display-names", "separators", "rules-file"]
        assert re.search(r'class="nav-link active"\s+id="tournament-addition-tab"', body)
        assert re.search(r'id="tournament-addition-panel"[^>]*>\s*<label', body)
        assert re.search(r'id="dispensations-panel"[^>]*>\s*<div\s+data-dispensation-editor', body)
        assert re.search(r'id="contributor-panel"[^>]*>\s*<div\s+data-contributor-editor', body)
        assert re.search(r'id="club-display-names-panel"[^>]*>\s*<div\s+data-club-display-name-editor', body)
        assert re.search(r'id="rules-file-panel"[^>]*>\s*<div\s+data-rules-file-editor', body)
        assert re.search(r'id="separators-panel"[^>]*>\s*<div\s+data-finals-editor', body)
        assert 'src="/static/js/configuration-tab-navigation.js"' in body
        assert "<textarea" not in body

    def test_generate_opens_the_dialog_once_a_tournament_is_verified(self, make_client):
        body = _body(make_client(configuration=_configuration()), "/configuration?lang=en")

        button = re.search(r"<button[^>]*data-generation-open[^>]*>", body).group()
        assert "disabled" not in button
        assert 'data-bs-target="#generation-form-dialog"' in button
        assert 'aria-describedby="generation-blocked-reason"' in button
        assert re.search(r"data-generation-blocked-reason>\s*</p>", body)

    def test_generate_is_disabled_with_its_reason_while_the_configuration_is_invalid(self, make_client, tmp_path):
        state_directory = tmp_path / "state"
        state_directory.mkdir()
        (state_directory / ACTIVE_CONFIGURATION_FILENAME).write_text("revision = 1\n")

        body = _body(make_client(), "/configuration?lang=en")

        button = re.search(r"<button[^>]*data-generation-open[^>]*>", body).group()
        assert "disabled" in button
        assert "Generation is unavailable until the configuration problems are fixed." in body
        assert "data-generation-form" not in body

    @pytest.mark.parametrize("years, status, message", [
        ({11: 2025, 22: 2025}, "stale", "The configured tournaments belong to a previous year. Replace them before generating standings for 2026."),
        ({11: 2025, 22: 2026}, "mixed", "Configured tournaments have start dates in different calendar years"),
        ({11: 2027, 22: 2027}, "future", "Configured tournaments start after 2026"),
    ])
    def test_the_calendar_alert_follows_the_year_assessment(self, make_client, years, status, message):
        body = _body(make_client(FakeMemberSystem(years), _configuration()), "/configuration?lang=en")

        assert f'data-calendar-alert="{status}"' in body
        assert message in body

    def test_current_year_tournaments_raise_no_calendar_alert(self, make_client):
        assert "data-calendar-alert" not in _body(make_client(configuration=_configuration()), "/configuration")

    def test_the_year_is_the_stockholm_date_not_the_utc_date(self, tmp_path):
        save_configuration(tmp_path / "state", _configuration())
        app = create_app({
            "TESTING": True,
            "STATE_DIRECTORY": tmp_path / "state",
            "MEMBER_SYSTEM_CLIENT": FakeMemberSystem({11: 2026, 22: 2026}),
            # 23:30 UTC on New Year's Eve is already 2027 in Stockholm.
            "CLOCK": lambda: datetime(2026, 12, 31, 23, 30, tzinfo=UTC),
        })

        body = _body(app.test_client(), "/configuration?lang=en")

        assert 'data-calendar-alert="stale"' in body
        assert "standings for 2027" in body

    @pytest.mark.parametrize("failure", [
        UpstreamRetryExhausted("resolving canonical tournament 11", "HTTP 503"),
        TournamentResolutionError(11, "not found"),
    ])
    def test_an_unresolvable_tournament_blocks_generation(self, make_client, failure):
        body = _body(make_client(FakeMemberSystem(failure=failure), _configuration()), "/configuration?lang=en")

        assert "The member system is unavailable." in body
        assert "Generation needs at least one verified tournament." in body
        assert "data-calendar-alert" not in body

    def test_a_refused_group_name_is_listed_in_the_operators_language(self, make_client):
        problem = KlassNamingProblem(11, "Wasa JGP 2026", 18319, "Final", KlassNameReason.NO_KLASS, "detail")
        member_system = FakeMemberSystem(failure=TournamentKlassNamingError(11, [problem]))

        body = _body(make_client(member_system, _configuration()), "/configuration?lang=sv")

        assert "Byt namn på dessa grupper innan tävlingen kan användas" in body
        assert "Wasa JGP 2026: gruppen <code>Final</code> (ID 18319) saknar en klass" in body
        assert "Ställningen kräver minst en verifierad turnering." in body


class TestTheErrorEnvelope:

    @pytest.fixture
    def api_client(self, tmp_path):
        # Raise each error from a JGP API endpoint to test its error envelope.
        failures = {
            "stale": StaleConfigurationError("stale", current_revision=4),
            "invalid": ConfigurationValidationError(["landscape must be true or false"]),
            "upstream": UpstreamRetryExhausted("searching players by name", "HTTP 503"),
            "missing": NotFound("gone"),
        }

        def fail():
            raise failures[request.args["kind"]]

        app = create_app({"TESTING": True, "STATE_DIRECTORY": tmp_path, "MEMBER_SYSTEM_CLIENT": FakeMemberSystem()})
        app.view_functions["jgp.district_clubs"] = fail
        return app.test_client()

    @pytest.mark.parametrize("kind, status, code, message_key, details", [
        ("stale", 409, "stale_configuration", "errors.configuration.stale", {"current_revision": 4}),
        ("invalid", 422, "invalid_configuration", "errors.configuration.invalid",
         {"problems": ["landscape must be true or false"]}),
        ("upstream", 502, "upstream_failure", "errors.member_system.unavailable", {}),
        ("missing", 404, "http_404", "errors.http", {"description": "gone"}),
    ])
    def test_each_failure_has_its_status_and_code(self, api_client, kind, status, code, message_key, details):
        response = api_client.get(f"/api/v1/clubs?kind={kind}")

        assert response.status_code == status
        assert response.get_json() == {"error": {"code": code, "message_key": message_key, "details": details}}

    @pytest.mark.parametrize("path", ["/not-found", "/api/v1/not-found"])
    def test_a_path_outside_jgps_endpoints_keeps_flasks_html_error(self, api_client, path):
        response = api_client.get(path)

        assert response.status_code == 404
        assert response.mimetype == "text/html"


class TestCataloguesAndAssets:

    def test_the_catalogues_have_identical_keys(self):
        catalogues = load_catalogues()

        assert set(catalogues) == {"sv", "en"}
        assert set(catalogues["sv"]) == set(catalogues["en"])

    def test_every_key_a_template_or_route_names_is_in_the_catalogue(self):
        catalogue = load_catalogues()["en"]
        sources = [*WEB_ROOT.glob("*.py"), *(WEB_ROOT / "templates" / "jgp").glob("*.jinja2")]
        keys = set()
        for source in sources:
            text = source.read_text(encoding="utf-8")
            keys |= set(re.findall(r'\bt\(\s*["\']([a-z_.]+[a-z_])["\']', text))
            keys |= set(re.findall(r'"((?:errors|generation|configuration|klass_naming|available_files)\.[a-z_.]+)"', text))
        # Include dynamically constructed locale and group-name error keys.
        keys |= {f"locale.{locale}" for locale in load_catalogues()}
        keys |= {f"errors.klass_naming.{reason}" for reason in KlassNameReason}
        keys.discard("generation.lease")  # the generation lease's file name

        assert keys - set(catalogue) == set()

    def test_bootstrap_lit_and_the_application_assets_are_served_locally(self, make_client):
        client = make_client()
        body = _body(client, "/")

        assert 'href="/static/css/bootstrap.min.css"' in body
        assert 'src="/static/js/bootstrap.bundle.min.js"' in body
        for path in ("/static/css/bootstrap.min.css", "/static/css/app.css", "/static/js/bootstrap.bundle.min.js",
                     "/static/js/lit-html.js", "/static/js/locale.js", "/static/js/configuration-tab-navigation.js"):
            asset = client.get(path)
            try:
                assert asset.status_code == 200, path
                assert len(asset.data) > 20, path
            finally:
                asset.close()

    def test_the_web_package_has_no_portal_auth_flash_or_cdn_path(self):
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in [*WEB_ROOT.glob("*.py"), *(WEB_ROOT / "templates" / "jgp").glob("*.jinja2")]
        )

        for forbidden in ("get_flashed_messages", "flash(", "flask_login", "REQUIRE_AUTH"):
            assert forbidden not in source
        assert "cdn." not in source.lower()


class TestOffline:

    def test_the_committed_configuration_renders_from_the_fixtures_without_network(self, state_directory):
        app = create_app({
            "TESTING": True,
            "STATE_DIRECTORY": state_directory,
            "CLOCK": lambda: OCTOBER_2026,
        })

        body = _body(app.test_client(), "/configuration?lang=en")

        assert "6544, 6540, 6534, 6685, 6826, 6853" in body
        assert "data-tournament-selection-problem" not in body
        assert "data-calendar-alert" not in body
        assert "disabled" not in re.search(r"<button[^>]*data-generation-open[^>]*>", body).group()


class TestTheEntryPoint:

    def test_it_binds_to_loopback_by_default(self):
        application = Mock()
        with patch("jgp_standings.web.__main__.create_app", return_value=application) as factory:
            main([])

        application.run.assert_called_once_with(host="127.0.0.1", port=8001, debug=False)
        assert factory.call_args.args[0] == {
            "STATE_DIRECTORY": Path("state"), "OUTPUT_DIRECTORY": Path("output"),
        }

    def test_debug_reloads_the_locale_catalogues(self):
        application = Mock()
        with patch("jgp_standings.web.__main__.create_app", return_value=application):
            main(["--debug"])

        run_options = application.run.call_args.kwargs
        assert run_options["debug"] is True
        assert {path.name for path in run_options["extra_files"]} == {"en.json", "sv.json"}

    def test_the_jgp_web_command_is_declared(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())

        assert project["project"]["scripts"]["jgp-web"] == "jgp_standings.web.__main__:main"
