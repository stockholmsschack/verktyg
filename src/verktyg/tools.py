"""Mount tools with portal access controls, auditing and file serving."""

import json
import logging
import shutil
from pathlib import Path

from flask import Response, abort, g, redirect, request, send_file, session, url_for
from flickornas_standings import artifact_publication as flickornas_publication
from flickornas_standings import web as flickornas_web
from flickornas_standings.member_system_client import MemberSystemClient
from flickornas_standings.web.mercure import MercurePublisher as FlickornasPublisher
from jgp_standings import artifact_publication as jgp_publication
from jgp_standings import web as jgp_web
from jgp_standings.upstream_client import UpstreamClient
from jgp_standings.web.mercure import MercurePublisher as JgpPublisher
from senior_gp import web as senior_web

from .auth import can_use_tool, valid_csrf
from .i18n import current_locale

CHECKOUT = Path(__file__).resolve().parents[2]


def mount_jgp(app) -> dict:
    settings = dict(app.config.get("JGP_SETTINGS", {}))
    settings.setdefault("MEMBER_SYSTEM_FETCH", UpstreamClient().get_json)
    set_mercure_publisher(app, settings, JgpPublisher)
    return mount_tool(app, jgp_web, jgp_publication, settings)


def mount_flickornas(app) -> dict:
    settings = dict(app.config.get("FLICKORNAS_SETTINGS", {}))
    settings.setdefault("MEMBER_SYSTEM_CLIENT", MemberSystemClient())
    set_mercure_publisher(app, settings, FlickornasPublisher)
    return mount_tool(app, flickornas_web, flickornas_publication, settings)


def mount_senior(app) -> dict:
    settings = dict(app.config.get("SENIOR_SETTINGS", {}))
    # Senior has no public standings, so it mounts without a snapshot route.
    return mount_tool(app, senior_web, None, settings)


def set_mercure_publisher(app, settings, publisher) -> None:
    """Push generation progress through the Mercure hub when one is configured.

    Without a hub, the browser polls the operation's status instead.
    """
    secret = app.config.get("MERCURE_JWT_SECRET")
    if not secret:
        return
    hub = app.config.get("MERCURE_HUB_URL")
    if not hub:
        raise ValueError("Set MERCURE_HUB_URL together with MERCURE_JWT_SECRET")
    settings.setdefault("MERCURE_PUBLISHER", publisher(hub, secret))


def mount_tool(app, web, publication, settings) -> dict:
    """Register a tool's `web` adapter behind the portal's access boundary.

    A tool with a `publication` module also gets `/api/v1/standings/<tool>`.
    """
    app.logger.setLevel(logging.INFO)
    tool, prefix = web.MANIFEST["id"], web.MANIFEST["url_prefix"]
    data = app.config.get("DATA_DIRECTORY")
    for key, name in (("STATE_DIRECTORY", "state"), ("OUTPUT_DIRECTORY", "output")):
        if not settings.get(key) and data:
            settings[key] = Path(data) / tool / name
        if not settings.get(key):
            raise ValueError(f"Set DATA_DIRECTORY or {tool.upper()}_SETTINGS {key}")
        settings[key] = Path(settings[key]).resolve()
    if app.debug:
        # Start a fresh debug checkout from the tool's sample configuration.
        active = settings["STATE_DIRECTORY"] / "active-configuration.toml"
        sample = CHECKOUT / "tools" / tool / "state/active-configuration.toml"
        if not active.exists() and sample.exists():
            active.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(sample, active)
    # Each tool owns its state and output; no directory may contain another.
    mounted = app.extensions.setdefault("verktyg_directories", [])
    owned = [settings["STATE_DIRECTORY"], settings["OUTPUT_DIRECTORY"]]
    for directory in owned:
        for other in mounted + [path for path in owned if path is not directory]:
            if directory.is_relative_to(other) or other.is_relative_to(directory):
                raise ValueError(
                    f"{tool} state and output must use separate, non-nested "
                    f"directories: {directory} overlaps {other}"
                )
    mounted.extend(owned)
    files_prefix = f"/files/{tool}"
    settings.update(
        BASE_TEMPLATE="base.html",
        FILES_PUBLIC_ORIGIN=files_prefix,
        MUTATION_PROTECTION=valid_csrf,
        GENERATION_TOURNAMENT_SELECTION_AUTHORIZER=lambda: can_use_tool(tool),
        ACTOR=lambda: {key: session["identity"].get(key) for key in ("email", "name")},
        LOCALE=current_locale,
    )

    def guard():
        if not any(
            request.path == path or request.path.startswith(path + "/")
            for path in (prefix, files_prefix)
        ):
            return None
        g.portal_tool = tool
        if "identity" not in session:
            if request.method == "GET" and request.path in (
                prefix,
                prefix + "/",
                prefix + "/configuration",
            ):
                return redirect(
                    url_for("auth.login", next=request.full_path.rstrip("?"))
                )
            abort(401)
        if not can_use_tool(tool):
            abort(403)
        return None

    def protect_and_audit(response):
        if g.get("portal_tool") == tool:
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                app.logger.info(
                    "tool_mutation %s",
                    json.dumps(
                        {
                            "tool": tool,
                            "actor": session.get("identity"),
                            "method": request.method,
                            "endpoint": request.endpoint,
                            "status": response.status_code,
                        }
                    ),
                )
        return response

    def serve_file(category, filename):
        if category not in ("downloads", "reports"):
            abort(404)
        root = settings["OUTPUT_DIRECTORY"] / "runs"
        candidate = root / filename
        if root.is_symlink() or not candidate.resolve().is_relative_to(root.resolve()):
            abort(404)
        for path in (candidate, *candidate.parents):
            if path == root:
                break
            if path.is_symlink():
                abort(404)
        if not candidate.is_file():
            abort(404)
        return send_file(candidate, as_attachment=True)

    def public_standings():
        snapshot = publication.read_public_snapshot(settings["OUTPUT_DIRECTORY"])
        if snapshot is None:
            abort(404)
        return Response(
            snapshot, mimetype="application/json", headers={"Cache-Control": "no-cache"}
        )

    app.before_request(guard)
    app.after_request(protect_and_audit)
    web.register(app, settings)
    app.add_url_rule(
        files_prefix + "/<category>/<path:filename>", f"{tool}_file", serve_file
    )
    if publication is not None:
        app.add_url_rule(
            f"/api/v1/standings/{tool}", f"public_{tool}_standings", public_standings
        )
    return web.MANIFEST
