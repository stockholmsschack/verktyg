import pytest


@pytest.fixture(autouse=True)
def data_directory(tmp_path_factory, monkeypatch):
    """Keep tools that a test does not configure out of the checkout."""
    monkeypatch.setenv("VERKTYG_DATA_DIRECTORY", str(tmp_path_factory.mktemp("data")))
    # Tests configure sign-in themselves, never from a developer's shell or .env.
    for name in (
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "VERKTYG_REQUIRE_AUTH",
        "VERKTYG_MERCURE_HUB_URL",
        "VERKTYG_MERCURE_JWT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
