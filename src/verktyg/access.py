"""Which Google groups may use each tool, edited on the portal's access page."""

import json
import os
import re
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .auth import can_manage_access, valid_csrf

# Used until the first save.
DEFAULT_GROUPS = {
    "jgp": ["uu@stockholmsschack.se"],
    "flickornas": ["uu@stockholmsschack.se"],
    "senior": ["tu@stockholmsschack.se"],
}
EMAIL = re.compile(r"[^@\s,]+@[^@\s,]+\.[^@\s,]+")

access = Blueprint("access", __name__)


class AccessGroups:
    """The saved groups per tool, read at startup and again after any save."""

    def __init__(self, path: Path):
        self.path = path
        self.load()

    def _stamp(self):
        try:
            return self.path.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def load(self) -> None:
        self.stamp = self._stamp()
        saved = json.loads(self.path.read_text()) if self.stamp is not None else {}
        self.groups = DEFAULT_GROUPS | saved

    def current(self) -> dict[str, list[str]]:
        # Another worker may have saved since this one last read the file.
        if self._stamp() != self.stamp:
            self.load()
        return self.groups

    def save(self, groups: dict[str, list[str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        draft = self.path.with_suffix(".tmp")
        draft.write_text(json.dumps(groups, indent=2) + "\n")
        os.replace(draft, self.path)
        self.load()


def init_access(app) -> None:
    path = app.config.get("ACCESS_FILE")
    if not path and app.config.get("DATA_DIRECTORY"):
        path = Path(app.config["DATA_DIRECTORY"]) / "access.json"
    if not path:
        raise ValueError("Set DATA_DIRECTORY or ACCESS_FILE")
    app.extensions["access_groups"] = AccessGroups(Path(path))
    app.register_blueprint(access)


def parse_groups(text: str) -> tuple[list[str], list[str]]:
    """Split comma-separated addresses into (valid, invalid), lower-cased and unique."""
    addresses = dict.fromkeys(
        address.strip().lower() for address in text.split(",") if address.strip()
    )
    valid = [address for address in addresses if EMAIL.fullmatch(address)]
    return valid, [address for address in addresses if address not in valid]


def tool_ids() -> list[str]:
    """Mounted tools first, then tools that have groups but are not mounted yet."""
    mounted = list(current_app.extensions["verktyg_tools"])
    stored = current_app.extensions["access_groups"].current()
    return mounted + [tool for tool in stored if tool not in mounted]


@access.before_request
def require_manager():
    if "identity" not in session:
        if request.method == "GET":
            return redirect(url_for("auth.login", next=request.path))
        abort(401)
    if not can_manage_access():
        abort(403)
    return None


@access.get("/access")
def page():
    groups = current_app.extensions["access_groups"].current()
    values = {tool: ", ".join(groups.get(tool, [])) for tool in tool_ids()}
    return render_template(
        "access.html", values=values, errors={}, saved="saved" in request.args
    )


@access.post("/access")
def save():
    if not valid_csrf():
        abort(403)
    tools = tool_ids()
    values = {tool: request.form.get(f"groups-{tool}", "") for tool in tools}
    groups, errors = {}, {}
    for tool in tools:
        groups[tool], invalid = parse_groups(values[tool])
        if invalid:
            errors[tool] = ", ".join(invalid)
    if errors:
        # Nothing is saved; the form keeps what was typed.
        page = render_template("access.html", values=values, errors=errors, saved=False)
        return page, 400
    current_app.extensions["access_groups"].save(groups)
    current_app.logger.info(
        "access_change %s",
        json.dumps({"actor": session["identity"].get("email"), "groups": groups}),
    )
    return redirect(url_for("access.page", saved=1))
