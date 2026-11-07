from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

import pytest

from verktyg import create_app
from verktyg.bookmarklets import load_directory


class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in {"href", "src"}:
                self.urls.append(value)


@pytest.fixture
def client():
    return create_app(
        {"TESTING": True, "DEBUG": True, "REQUIRE_AUTH": False}
    ).test_client()


@pytest.mark.parametrize("path", ["/", "/bookmarklets"])
def test_public_pages_and_their_local_assets_are_available(client, path):
    response = client.get(path)
    assert response.status_code == 200
    page = PageLinks()
    page.feed(response.get_data(as_text=True))
    for url in page.urls:
        if url.startswith("/"):
            assert client.get(url).status_code == 200, url


@pytest.mark.parametrize("locale", ["sv", "en"])
def test_installation_links_contain_the_bookmarklets_in_the_chosen_language(
    client, locale
):
    page = PageLinks()
    page.feed(client.get(f"/bookmarklets?lang={locale}").get_data(as_text=True))
    bookmarklets = [url for url in page.urls if url.startswith("javascript:")]
    sources = [bookmarklet.source for bookmarklet in load_directory()[0]]
    assert sources
    assert [unquote(url) for url in bookmarklets] == [
        source.replace("const locale = 'sv';", f"const locale = '{locale}';")
        for source in sources
    ]


def test_bookmarklet_sources_are_not_served_as_static_files(client):
    assert client.get("/static/bookmarklets/avprickning.js").status_code == 404


def test_language_switch_shows_the_other_language(client):
    page = client.get("/").text
    assert '<html lang="sv">' in page and "Verktyg för StSF" in page
    assert 'lang="en" title="English">' in page and "flags/gb.svg" in page
    assert '<input type="hidden" name="lang" value="en">' in page
    page = client.get("/bookmarklets?lang=en").text
    assert '<html lang="en">' in page and "Check in paid players" in page
    assert 'lang="sv" title="Svenska">' in page and "flags/se.svg" in page
    # The choice lasts for the session.
    assert "Tools for StSF" in client.get("/").text


def test_portal_catalogues_have_the_same_keys():
    from verktyg.i18n import CATALOGUES

    assert set(CATALOGUES["sv"]) == set(CATALOGUES["en"])


def test_health_is_public(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json == {"status": "ok"}


@pytest.mark.parametrize(
    ("caddyfile", "address"),
    [
        ("Caddyfile.vps", "verktyg.stockholmsschack.se"),
        ("deploy/Caddyfile.local", "http://localhost:5000"),
    ],
)
def test_caddy_proxies_generated_files_to_flask(caddyfile, address):
    text = (Path(__file__).resolve().parents[1] / caddyfile).read_text()
    site = text[text.index(f"\n{address} {{") :]
    site = site[: site.index("\n}\n")]
    assert "reverse_proxy 127.0.0.1:6001" in site
    assert "file_server" not in site
