import json
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from types import SimpleNamespace
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

import pytest
from flask.testing import FlaskClient
from googleapiclient.errors import HttpError
from oauthlib.oauth2.rfc6749.errors import InvalidGrantError
from test_jgp import jgp_config

from verktyg import auth, create_app

CREDENTIALS = SimpleNamespace(token="access-secret", refresh_token="refresh-secret")


class FakeFlow:
    """Stands in for google_auth_oauthlib's Flow at the callback."""

    created: ClassVar[list] = []
    failure = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.credentials = CREDENTIALS

    @classmethod
    def from_client_config(cls, **kwargs):
        flow = cls(**kwargs)
        cls.created.append(flow)
        return flow

    def fetch_token(self, authorization_response):
        self.authorization_response = authorization_response
        if self.failure:
            raise self.failure


class LocalClient(FlaskClient):
    """A browser on the registered local origin."""

    host = "localhost:5000"

    def open(self, *args, **kwargs):
        kwargs.setdefault("base_url", f"http://{self.host}")
        return super().open(*args, **kwargs)


def local_client(app, host="localhost:5000"):
    client = LocalClient(app, app.response_class, use_cookies=True)
    client.host = host
    return client


def fake_build(user_info):
    def build(service, version, credentials):
        assert (service, version, credentials) == ("oauth2", "v2", CREDENTIALS)
        request = SimpleNamespace(execute=lambda: user_info)
        return SimpleNamespace(userinfo=lambda: SimpleNamespace(get=lambda: request))

    return build


GROUPS = {
    "jgp": ["uu@example.com"],
    "flickornas": ["girls@example.com", "uu@example.com"],
    "senior": ["tu@example.com"],
}
TESTER = "tester@stockholmsschack.se"


def google_config(tmp_path):
    access = tmp_path / "access.json"
    access.write_text(json.dumps(GROUPS))
    return jgp_config(tmp_path) | {
        "GOOGLE_OAUTH_CLIENT_ID": "client-id",
        "GOOGLE_OAUTH_CLIENT_SECRET": "client-secret",
        "ACCESS_FILE": access,
    }


@pytest.fixture
def app(tmp_path):
    return create_app(google_config(tmp_path))


@pytest.fixture
def google(monkeypatch):
    """Fake Google: the user is in the `groups` given; returns the checks made."""
    FakeFlow.created, FakeFlow.failure = [], None
    checks = []

    def sign_in(email, groups=(), verified=True, **user_info):
        monkeypatch.setattr(auth, "Flow", FakeFlow)
        monkeypatch.setattr(
            auth,
            "build",
            fake_build({"email": email, "verified_email": verified, **user_info}),
        )

        def check(credentials, user_email, group_email):
            assert credentials is CREDENTIALS
            checks.append((user_email, group_email))
            return group_email in groups

        monkeypatch.setattr(auth, "check_user_in_group", check)
        return checks

    return sign_in


def callback(app, client=None, next_path="/tools/jgp/configuration"):
    client = client or local_client(app)
    with client.session_transaction() as session:
        session["oauth_state"] = "state-1"
        session["oauth_code_verifier"] = "verifier-1"
        session["next"] = next_path
        session["locale"] = "en"
    return client, client.get("/callback?state=state-1&code=code-1")


def test_login_redirects_to_google_with_the_registered_callback(app):
    client = local_client(app)
    response = client.get("/login?next=/tools/jgp/configuration")
    assert response.status_code == 302
    location = urlsplit(response.location)
    assert location.netloc == "accounts.google.com"
    query = parse_qs(location.query)
    assert query["redirect_uri"] == ["http://localhost:5000/callback"]
    assert query["client_id"] == ["client-id"]
    assert query["access_type"] == ["offline"]
    assert "cloud-identity.groups.readonly" in query["scope"][0]
    assert query["code_challenge_method"] == ["S256"]
    with client.session_transaction() as session:
        assert session["oauth_state"] == query["state"][0]
        assert session["oauth_code_verifier"]
        assert session["next"] == "/tools/jgp/configuration"


@pytest.mark.parametrize(
    "target", ["//evil.example/path", "https://evil.example/", "\\\\evil", "x"]
)
def test_login_refuses_external_return_targets(app, target):
    client = local_client(app)
    client.get("/login", query_string={"next": target})
    with client.session_transaction() as session:
        assert session["next"] == "/"


def test_login_behind_caddy_uses_the_public_https_callback(app):
    response = app.test_client().get(
        "/login",
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "verktyg.stockholmsschack.se",
        },
    )
    query = parse_qs(urlsplit(response.location).query)
    assert query["redirect_uri"] == ["https://verktyg.stockholmsschack.se/callback"]


def test_login_explains_an_unregistered_callback(app):
    response = local_client(app, "127.0.0.1:6001").get("/login")
    assert response.status_code == 500
    assert "http://localhost:5000/callback" in response.text


def test_group_member_gets_only_the_tools_of_their_groups(app, google):
    checks = google("member@example.com", groups={"girls@example.com"})
    client, response = callback(app, next_path="/tools/flickornas/")
    assert response.location == "/tools/flickornas/"
    # Each distinct group is checked once, though UU guards two tools.
    assert sorted(group for _, group in checks) == [
        "girls@example.com",
        "tu@example.com",
        "uu@example.com",
    ]
    assert client.get("/tools/flickornas/").status_code == 200
    assert client.get("/tools/jgp/").status_code == 403
    page = client.get("/").text
    assert 'href="/tools/flickornas/"' in page and 'href="/tools/jgp/"' not in page


def test_group_member_is_returned_to_the_tool(app, google):
    google("member@example.com", groups={"uu@example.com"})
    client, response = callback(app)
    assert response.location == "/tools/jgp/configuration"
    flow = FakeFlow.created[0]
    assert flow.kwargs["state"] == "state-1"
    assert flow.kwargs["code_verifier"] == "verifier-1"
    assert flow.authorization_response.startswith("http://localhost:5000/callback?")
    assert client.get("/tools/jgp/configuration").status_code == 200


@pytest.mark.parametrize("email", [TESTER, "Kansli@Stockholmsschack.se"])
def test_override_accounts_get_every_tool_without_a_group_check(app, google, email):
    checks = google(email, name="Tester")
    client, response = callback(app)
    assert response.location == "/tools/jgp/configuration"
    assert checks == []
    assert client.get("/tools/flickornas/").status_code == 200
    with client.session_transaction() as session:
        assert session["tools"] == ["jgp", "flickornas", "senior"]


def test_outsider_stays_signed_in_without_tools(app, google):
    google("outsider@example.com", name="Test Visitor")
    client, response = callback(app)
    assert response.location == "/"
    page = client.get("/").text
    assert "Test Visitor" in page and "Sign out" in page
    assert "outsider@example.com" not in page
    assert "/tools/jgp/" not in page and "More tools" not in page
    assert client.get("/tools/jgp/").status_code == 403
    assert client.get("/files/jgp/downloads/run/a.pdf").status_code == 403


def test_session_keeps_identity_and_decision_but_no_credentials(app, google):
    google("Member@Example.com", groups={"uu@example.com"}, name="M", given_name="M")
    client, _ = callback(app)
    with client.session_transaction() as session:
        assert session["identity"] == {
            "email": "member@example.com",
            "name": "M",
            "given_name": "M",
            "family_name": None,
        }
        assert session["tools"] == ["jgp", "flickornas"]
        assert session["locale"] == "en"
        assert set(session) == {"_permanent", "identity", "tools", "locale"}
        for secret in ("access-secret", "refresh-secret", "client-secret", "verifier"):
            assert secret not in str(dict(session))


def test_session_expires_after_eight_hours(app, google):
    google(TESTER)
    _, response = callback(app)
    cookie = response.headers["Set-Cookie"]
    expires = parsedate_to_datetime(cookie.split("Expires=")[1].split(";")[0])
    lifetime = expires - datetime.now(UTC)
    assert timedelta(hours=7, minutes=59) < lifetime <= timedelta(hours=8)
    assert "HttpOnly" in cookie


@pytest.mark.parametrize(
    "failure",
    [
        InvalidGrantError(),
        Warning("Scope has changed"),
        HttpError(SimpleNamespace(status=401, reason="no"), b""),
    ],
)
def test_failed_google_exchange_signs_nobody_in(app, google, failure):
    google(TESTER)
    FakeFlow.failure = failure
    client, response = callback(app)
    assert response.status_code == 401
    with client.session_transaction() as session:
        assert "identity" not in session


def test_unverified_email_is_refused(app, google):
    google(TESTER, verified=False)
    client, response = callback(app)
    assert response.status_code == 403
    with client.session_transaction() as session:
        assert "identity" not in session


def test_callback_without_a_login_is_refused(app):
    assert local_client(app).get("/callback?code=x").status_code == 400


def test_public_routes_need_no_session(app):
    client = app.test_client()
    for path in (
        "/",
        "/bookmarklets",
        "/health",
        "/static/js/bookmarklets.js",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
    assert client.get("/api/v1/standings/jgp").status_code == 404
    assert client.get("/tools/jgp/").location.startswith("/login?next=/tools/jgp/")
    assert client.post("/tools/jgp/api/v1/configuration/rules").status_code == 401


@pytest.mark.parametrize(
    "response, expected",
    [({"hasMembership": True}, True), ({"hasMembership": False}, False), ({}, False)],
)
def test_check_user_in_group_checks_transitive_membership(
    app, monkeypatch, response, expected
):
    calls = {}

    class Groups:
        def lookup(self, groupKey_id):
            calls["lookup"] = groupKey_id
            return SimpleNamespace(execute=lambda: {"name": "groups/uu"})

        def memberships(self):
            return self

        def checkTransitiveMembership(self, **kwargs):
            calls["check"] = kwargs
            return SimpleNamespace(execute=lambda: response)

    def build(service, version, credentials):
        assert (service, version, credentials) == ("cloudidentity", "v1", CREDENTIALS)
        return SimpleNamespace(groups=Groups)

    monkeypatch.setattr(auth, "build", build)
    with app.app_context():
        assert (
            auth.check_user_in_group(CREDENTIALS, "a@example.com", "uu@example.com")
            is expected
        )
    assert calls["lookup"] == "uu@example.com"
    assert calls["check"] == {
        "parent": "groups/uu",
        "query": 'member_key_id == "a@example.com"',
    }


def test_production_requires_the_google_client():
    with pytest.raises(ValueError, match="GOOGLE_OAUTH_CLIENT_ID"):
        create_app({"SECRET_KEY": "test-only"})
