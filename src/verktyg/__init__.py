import os
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .access import init_access
from .auth import can_use_tool, init_auth
from .i18n import localization_context, remember_requested_locale
from .portal import init_bookmarklets, portal
from .tools import CHECKOUT, mount_flickornas, mount_jgp, mount_senior

# Like Playtime, read the checkout's git-ignored .env; set variables win.
load_dotenv(CHECKOUT / ".env")

TOOL_PAGES = (
    ("landing", "nav.home"),
    ("configuration", "nav.configuration"),
    ("available_files", "nav.available_files"),
    ("reports", "nav.reports"),
)


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    # Behind Caddy, url_for(..., _external=True) builds the public https callback.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    app.config.from_mapping(
        # Playtime's names and environment variables, so both share one OAuth client.
        GOOGLE_OAUTH_CLIENT_ID=os.environ.get("GOOGLE_OAUTH_CLIENT_ID"),
        GOOGLE_OAUTH_CLIENT_SECRET=os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"),
        GOOGLE_OAUTH_REDIRECT_URIS=[
            "https://verktyg.stockholmsschack.se/callback",
            "http://localhost:5000/callback",
        ],
        REQUIRE_AUTH=True,
        DEV_USER_EMAIL="dev@example.com",
        DEV_USER_NAME="Dev User",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    app.config.from_prefixed_env(prefix="VERKTYG")
    if config is not None:
        app.config.update(config)
    if app.debug:
        # Debug runs from a checkout need no further configuration: plain
        # HTTP, a fixed key and tool data under instance/. Sign-in stays on
        # unless REQUIRE_AUTH=False is set explicitly.
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
        app.config.setdefault("DATA_DIRECTORY", CHECKOUT / "instance")
        app.config["SECRET_KEY"] = app.config["SECRET_KEY"] or "dev"
        app.config["SESSION_COOKIE_SECURE"] = False
    if not app.config["REQUIRE_AUTH"] and not app.debug:
        raise ValueError("REQUIRE_AUTH=False is allowed only with DEBUG")
    init_bookmarklets(app)
    app.register_blueprint(portal)
    # Before the tools' guards, so a tool page can switch language too.
    app.before_request(remember_requested_locale)
    app.context_processor(localization_context)
    init_auth(app)
    # Manifests of the tools, keyed by id, for navigation.
    tools = {
        manifest["id"]: manifest
        for manifest in (mount_jgp(app), mount_flickornas(app), mount_senior(app))
    }
    app.extensions["verktyg_tools"] = tools
    # Each tool's navigation lists the pages it has. Senior has no
    # configuration page. Its overview holds the tournament picker.
    tool_pages = {
        tool: [
            (page, label)
            for page, label in TOOL_PAGES
            if f"{tool}.{page}" in app.view_functions
        ]
        for tool in tools
    }
    init_access(app)
    app.context_processor(
        lambda: {
            "tools": tools,
            "tool_pages": tool_pages,
            "visible_tools": [tool for tool in tools if can_use_tool(tool)],
        }
    )
    return app
