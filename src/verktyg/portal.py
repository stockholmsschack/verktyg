from flask import Blueprint, Flask, current_app, render_template

from .bookmarklets import DIRECTORY, load_directory
from .i18n import current_locale

portal = Blueprint("portal", __name__)


def init_bookmarklets(app: Flask) -> None:
    """Parse the bookmarklet files once; invalid ones are logged and skipped."""
    bookmarklets, problems = load_directory(
        app.config.get("BOOKMARKLETS_DIRECTORY", DIRECTORY)
    )
    for problem in problems:
        app.logger.error("%s", problem)
    app.extensions["verktyg_bookmarklets"] = bookmarklets


@portal.get("/")
def index() -> str:
    return render_template("index.html")


@portal.get("/bookmarklets")
def bookmarklets() -> str:
    bookmarklets = current_app.extensions["verktyg_bookmarklets"]
    urls = {
        bookmarklet.slug: bookmarklet.install_url(current_locale())
        for bookmarklet in bookmarklets
    }
    return render_template("bookmarklets.html", bookmarklets=bookmarklets, urls=urls)


@portal.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
