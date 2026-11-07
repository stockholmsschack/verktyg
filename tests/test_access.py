import json

import pytest
from test_auth import GROUPS, TESTER, callback, google, google_config  # noqa: F401
from test_jgp import jgp_config

from verktyg import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(google_config(tmp_path))


def signed_in(app, email, tools=()):
    client = app.test_client()
    with client.session_transaction() as session:
        session["identity"] = {"email": email}
        session["tools"] = list(tools)
        session["csrf_token"] = "test-token"
    return client


def submit(client, **groups):
    form = {"csrf_token": "test-token"}
    form.update({f"groups-{tool}": value for tool, value in groups.items()})
    return client.post("/access?lang=en", data=form)


def stored(app):
    return json.loads(app.config["ACCESS_FILE"].read_text())


def test_only_override_accounts_reach_the_access_page(app):
    anonymous = app.test_client()
    assert anonymous.get("/access").location == "/login?next=/access"
    assert anonymous.post("/access").status_code == 401
    member = signed_in(app, "member@example.com", ["jgp"])
    assert member.get("/access").status_code == 403
    assert submit(member, jgp="evil@example.com").status_code == 403
    assert stored(app) == GROUPS
    assert 'href="/access"' not in member.get("/").text
    manager = signed_in(app, TESTER)
    assert manager.get("/access").status_code == 200
    assert 'href="/access"' in manager.get("/").text


def test_page_lists_each_tool_with_its_groups_and_hides_the_overrides(app):
    page = signed_in(app, TESTER).get("/access?lang=en").text
    for tool, groups in GROUPS.items():
        assert f'name="groups-{tool}" value="{", ".join(groups)}"' in page
    assert "JGP standings" in page and "Senior GP" in page
    assert "not installed in the portal yet" not in page
    assert "kansli@" not in page and 'value="tester@' not in page
    assert 'type="email" multiple' in page


def test_defaults_apply_until_the_first_save(tmp_path):
    app = create_app(
        jgp_config(tmp_path)
        | {"DEBUG": True, "REQUIRE_AUTH": False, "ACCESS_FILE": tmp_path / "a.json"}
    )
    page = app.test_client().get("/access").text
    assert 'name="groups-jgp" value="uu@stockholmsschack.se"' in page
    assert 'name="groups-flickornas" value="uu@stockholmsschack.se"' in page
    assert 'name="groups-senior" value="tu@stockholmsschack.se"' in page
    assert not (tmp_path / "a.json").exists()


def test_save_normalizes_and_persists_the_groups(app, caplog):
    client = signed_in(app, TESTER)
    response = submit(
        client,
        jgp=" UU@Example.com, new@example.com,,uu@example.com ",
        flickornas="",
        senior="tu@example.com",
    )
    assert response.status_code == 302 and response.location == "/access?saved=1"
    assert stored(app) == {
        "jgp": ["uu@example.com", "new@example.com"],
        "flickornas": [],
        "senior": ["tu@example.com"],
    }
    page = client.get(response.location).text
    assert 'value="uu@example.com, new@example.com"' in page
    assert "Access saved" in page
    assert '"actor": "tester@stockholmsschack.se"' in caplog.text


def test_invalid_addresses_save_nothing_and_keep_the_input(app):
    client = signed_in(app, TESTER)
    response = submit(
        client, jgp="uu@example.com, not an address", flickornas="x@y", senior=""
    )
    assert response.status_code == 400
    assert stored(app) == GROUPS
    page = response.text
    assert "Nothing was saved" in page
    assert 'value="uu@example.com, not an address"' in page
    assert "Not valid email addresses: not an address" in page
    assert "Not valid email addresses: x@y" in page
    assert page.count("is-invalid") == 2


def test_save_requires_the_csrf_token(app):
    client = signed_in(app, TESTER)
    response = client.post("/access", data={"groups-jgp": "evil@example.com"})
    assert response.status_code == 403
    assert stored(app) == GROUPS


def test_signed_in_users_keep_their_access_until_they_sign_in_again(app, google):  # noqa: F811
    member = signed_in(app, "member@example.com", ["jgp"])
    submit(signed_in(app, TESTER), jgp="", flickornas="uu@example.com", senior="")
    assert member.get("/tools/jgp/").status_code == 200
    google("member@example.com", groups={"uu@example.com"})
    client, _ = callback(app)
    assert client.get("/tools/jgp/").status_code == 403
    assert client.get("/tools/flickornas/").status_code == 200


def test_override_accounts_keep_every_tool_when_no_group_has_access(app, google):  # noqa: F811
    submit(signed_in(app, TESTER), jgp="", flickornas="", senior="")
    google(TESTER)
    client, _ = callback(app)
    assert client.get("/tools/jgp/").status_code == 200
    assert client.get("/tools/flickornas/").status_code == 200


def test_every_worker_sees_a_save(tmp_path):
    config = google_config(tmp_path)
    first, second = create_app(config), create_app(config)
    submit(signed_in(first, TESTER), jgp="new@example.com", flickornas="", senior="")
    page = signed_in(second, TESTER).get("/access").text
    assert 'name="groups-jgp" value="new@example.com"' in page


def test_startup_reads_the_saved_groups(tmp_path):
    config = google_config(tmp_path)
    config["ACCESS_FILE"].write_text(json.dumps({"jgp": ["saved@example.com"]}))
    page = signed_in(create_app(config), TESTER).get("/access").text
    assert 'name="groups-jgp" value="saved@example.com"' in page
    # Tools without a saved entry keep their defaults.
    assert 'name="groups-senior" value="tu@stockholmsschack.se"' in page


def test_development_identity_manages_access(tmp_path):
    app = create_app(jgp_config(tmp_path) | {"DEBUG": True, "REQUIRE_AUTH": False})
    client = app.test_client()
    assert 'href="/access"' in client.get("/").text
    assert client.get("/access").status_code == 200
