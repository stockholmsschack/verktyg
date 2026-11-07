import os
from pathlib import Path

import pytest
from flickornas_standings.web.mercure import MercurePublisher as FlickornasPublisher
from jgp_standings.web.mercure import MercurePublisher as JgpPublisher
from test_flickornas import TOKEN, dev_client, development, flickornas_config
from test_jgp import jgp_config
from test_senior import senior_config

from verktyg import create_app

TOOLS = ("jgp", "flickornas", "senior")


class PendingExecutor:
    """Accepts work and never runs it, so the generation lease stays held."""

    def submit(self, function, *args, **kwargs):
        return None


def shared_config(tmp_path):
    (tmp_path / "jgp").mkdir()
    return development(
        jgp_config(tmp_path / "jgp")
        | flickornas_config(tmp_path)
        | senior_config(tmp_path)
    )


def worker(config):
    """Create an app over shared directories, as one Gunicorn worker would."""
    config = config | {
        f"{tool.upper()}_SETTINGS": config[f"{tool.upper()}_SETTINGS"]
        | {"GENERATION_EXECUTOR": PendingExecutor()}
        for tool in TOOLS
    }
    return create_app(config)


def start(client, tool):
    body = (
        {"revision": 1}
        if tool == "senior"
        else {"year": None, "tournament_ids": None, "layout": "portrait"}
    )
    return client.post(
        f"/tools/{tool}/api/v1/generation/operations", json=body, headers=TOKEN
    )


def test_tools_keep_separate_routes_services_templates_and_assets(tmp_path):
    app = worker(shared_config(tmp_path))
    client = dev_client(app)
    services = [app.extensions[tool] for tool in TOOLS]
    assert services[0]["settings"] is not services[1]["settings"]
    assert {
        tool: app.extensions[tool]["settings"]["FILES_PUBLIC_ORIGIN"] for tool in TOOLS
    } == {tool: f"/files/{tool}" for tool in TOOLS}

    portal_templates = set(app.jinja_loader.list_templates())
    for tool in TOOLS:
        blueprint = app.blueprints[tool]
        rules = [r for r in app.url_map.iter_rules() if r.endpoint.startswith(tool)]
        assert {r.endpoint for r in rules} >= {f"{tool}_file", f"{tool}.static"}
        for rule in rules:
            assert rule.rule.startswith(
                (f"/tools/{tool}/", f"/files/{tool}/", f"/api/v1/standings/{tool}")
            ), rule.rule
        templates = blueprint.jinja_loader.list_templates()
        assert templates and all(name.startswith(f"{tool}/") for name in templates)
        assert not portal_templates & set(templates)

        static = Path(blueprint.static_folder)
        for path in static.rglob("*"):
            if path.is_file():
                url = f"/tools/{tool}/static/{path.relative_to(static).as_posix()}"
                response = client.get(url)
                assert response.data == path.read_bytes(), url
                response.close()


def test_one_generation_per_tool_across_workers_and_recovery(tmp_path):
    config = shared_config(tmp_path)
    first, second = (dev_client(worker(config)) for _ in range(2))
    assert start(first, "flickornas").status_code == 202
    # A busy Flickornas leaves JGP free, in this worker and in another.
    assert start(second, "flickornas").status_code == 409
    assert start(second, "jgp").status_code == 202
    assert start(first, "jgp").status_code == 409
    assert start(first, "senior").status_code == 202
    assert start(second, "senior").status_code == 409

    # Closing the first worker's lease descriptor stands in for its exit.
    manager = first.application.extensions["flickornas"]["generation_operations"]
    (operation_id,) = manager.store._held
    os.close(manager.store._held.pop(operation_id))

    restarted = dev_client(worker(config))
    status = restarted.get(
        f"/tools/flickornas/api/v1/generation/operations/{operation_id}"
    ).json["data"]
    assert (status["state"], status["failure_stage"]) == ("failed", "interrupted")
    assert start(restarted, "flickornas").status_code == 202
    # The second worker is alive, so its JGP lease survives the restart.
    assert start(restarted, "jgp").status_code == 409


def test_configured_mercure_hub_receives_jgp_and_flickornas_progress(tmp_path):
    hub = "http://localhost/.well-known/mercure"
    app = create_app(
        shared_config(tmp_path) | {"MERCURE_HUB_URL": hub, "MERCURE_JWT_SECRET": "key"}
    )
    for tool, publisher in (("jgp", JgpPublisher), ("flickornas", FlickornasPublisher)):
        installed = app.extensions[tool]["mercure_publisher"]
        assert isinstance(installed, publisher)
        assert (installed.hub_url, installed.jwt_secret) == (hub, "key")


def test_without_mercure_secret_progress_is_polled(tmp_path):
    app = create_app(shared_config(tmp_path) | {"MERCURE_HUB_URL": "http://hub"})
    for tool, publisher in (("jgp", JgpPublisher), ("flickornas", FlickornasPublisher)):
        assert not isinstance(app.extensions[tool]["mercure_publisher"], publisher)


def test_mercure_secret_requires_hub(tmp_path):
    with pytest.raises(ValueError, match="MERCURE_HUB_URL"):
        create_app(shared_config(tmp_path) | {"MERCURE_JWT_SECRET": "key"})
