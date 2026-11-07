"""Google sign-in, Cloud Identity group access, development identity and CSRF.

The sign-in follows Playtime's `web.auth` module; see _docs/authentication.md.
"""

import ipaddress
import secrets
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, redirect, request, session, url_for
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from oauthlib.oauth2.rfc6749.errors import OAuth2Error

auth = Blueprint("auth", __name__)

_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cloud-identity.groups.readonly",
]


# These accounts may use every tool and manage access; they cannot be removed.
OVERRIDE_EMAILS = frozenset(
    {"tester@stockholmsschack.se", "kansli@stockholmsschack.se"}
)


def can_use_tool(tool: str) -> bool:
    """One decision for a tool, its files and its tournament selection."""
    if not current_app.config["REQUIRE_AUTH"]:
        return True
    return tool in session.get("tools", ())


def can_manage_access() -> bool:
    if not current_app.config["REQUIRE_AUTH"]:
        return True
    return session.get("identity", {}).get("email") in OVERRIDE_EMAILS


def csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def valid_csrf() -> bool:
    expected = session.get("csrf_token")
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    return bool(
        expected
        and supplied
        and secrets.compare_digest(expected.encode(), supplied.encode())
    )


def inject_development_identity() -> None:
    """Sign in a local request as the development user, like Playtime's `_inject_dev_user`."""
    if not ipaddress.ip_address(request.remote_addr or "0.0.0.0").is_loopback:
        abort(403, "REQUIRE_AUTH=False serves only requests from this computer.")
    if "identity" not in session:
        session["identity"] = {
            "email": current_app.config["DEV_USER_EMAIL"].lower(),
            "name": current_app.config["DEV_USER_NAME"],
        }


def init_auth(app) -> None:
    if not app.config.get("SECRET_KEY"):
        raise ValueError("Tools require a persistent SECRET_KEY")
    if app.config["REQUIRE_AUTH"]:
        init_google_login(app)
    else:
        app.before_request(inject_development_identity)
    app.context_processor(
        lambda: {"csrf_token": csrf_token, "can_manage_access": can_manage_access()}
    )


def init_google_login(app) -> None:
    for key in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"):
        if not app.config.get(key):
            hint = " or set VERKTYG_REQUIRE_AUTH=false to work locally without sign-in"
            raise ValueError(
                f"Google sign-in requires {key}{hint if app.debug else ''}"
            )
    if not isinstance(app.config["GOOGLE_OAUTH_REDIRECT_URIS"], (list, tuple, set)):
        raise TypeError("GOOGLE_OAUTH_REDIRECT_URIS must be a list")
    app.register_blueprint(auth)


def _get_client_config() -> dict:
    return {
        "web": {
            "client_id": current_app.config["GOOGLE_OAUTH_CLIENT_ID"],
            "client_secret": current_app.config["GOOGLE_OAUTH_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": list(current_app.config["GOOGLE_OAUTH_REDIRECT_URIS"]),
        }
    }


def _callback_uri() -> str:
    uri = url_for("auth.callback", _external=True)
    allowed = current_app.config["GOOGLE_OAUTH_REDIRECT_URIS"]
    if uri not in allowed:
        # Google would refuse it with redirect_uri_mismatch; say why here instead.
        abort(
            500,
            f"Sign-in callback {uri} is not registered; use one of {', '.join(allowed)}.",
        )
    return uri


def _local_target(target: str | None) -> str:
    target = target or "/"
    parsed = urlsplit(target)
    if (
        parsed.scheme
        or parsed.netloc
        or not target.startswith("/")
        or target.startswith("//")
        or "\\" in target
    ):
        return "/"
    return target


@auth.get("/login")
def login():
    flow = Flow.from_client_config(client_config=_get_client_config(), scopes=_SCOPES)
    flow.redirect_uri = _callback_uri()
    authorization_url, state = flow.authorization_url(access_type="offline")
    session["oauth_state"] = state
    # The callback's flow must present the PKCE verifier this flow generated.
    session["oauth_code_verifier"] = flow.code_verifier
    session["next"] = _local_target(request.args.get("next"))
    return redirect(authorization_url)


@auth.get("/callback")
def callback():
    if "oauth_state" not in session:
        abort(400, "Sign-in expired. Please try again.")
    flow = Flow.from_client_config(
        client_config=_get_client_config(),
        scopes=_SCOPES,
        state=session.pop("oauth_state"),
        code_verifier=session.pop("oauth_code_verifier", None),
    )
    flow.redirect_uri = _callback_uri()
    # oauthlib raises a Warning when the user declined one of the scopes.
    try:
        flow.fetch_token(authorization_response=request.url)
        user_info = (
            build("oauth2", "v2", credentials=flow.credentials)
            .userinfo()
            .get()
            .execute()
        )
    except (OAuth2Error, HttpError, Warning) as error:
        current_app.logger.warning("Google sign-in failed: %s", error)
        abort(401, "Google sign-in failed. Please try again.")
    email = (user_info.get("email") or "").lower()
    if not email or user_info.get("verified_email") is False:
        abort(403, "Google did not provide a verified email address.")
    # Decide now, while the credentials are in memory; they never enter the session.
    # The decision lasts for the session, so a later access change waits for
    # the next sign-in.
    groups = current_app.extensions["access_groups"].current()
    if email in OVERRIDE_EMAILS:
        tools = list(groups)
    else:
        distinct = {group for allowed in groups.values() for group in allowed}
        member_of = {
            group
            for group in distinct
            if check_user_in_group(flow.credentials, email, group)
        }
        tools = [
            tool for tool, allowed in groups.items() if member_of.intersection(allowed)
        ]
    target = session.pop("next", "/") if tools else "/"
    locale = session.get("locale")
    session.clear()
    if locale:
        session["locale"] = locale
    session.permanent = True
    session["identity"] = {
        "email": email,
        "name": user_info.get("name"),
        "given_name": user_info.get("given_name"),
        "family_name": user_info.get("family_name"),
    }
    session["tools"] = tools
    return redirect(target)


@auth.post("/logout")
def logout():
    if not valid_csrf():
        abort(403)
    session.clear()
    return redirect(url_for("portal.index"))


def check_user_in_group(credentials, user_email, group_email):
    """Return True if user_email is a (direct or transitive) member of group_email.

    Copied from Playtime; it runs with the signed-in user's own credentials.
    """
    try:
        svc = build("cloudidentity", "v1", credentials=credentials)
        group_lookup = svc.groups().lookup(groupKey_id=group_email).execute()
        group_name = group_lookup.get("name")
        if not group_name:
            return False
        response = (
            svc.groups()
            .memberships()
            .searchTransitiveGroups(
                parent="groups/-",
                query=f"member_key_id == '{user_email}' && group == '{group_name}'",
                pageSize=1,
            )
            .execute()
        )
        return len(response.get("memberships", [])) > 0
    except Exception as e:  # noqa: BLE001 - Playtime treats any failure as no membership
        current_app.logger.error("check_user_in_group error: %s", e)
        return False
