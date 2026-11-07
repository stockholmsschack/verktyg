"""The portal's language choice, which the tools and bookmarklets inherit."""

import json
from pathlib import Path

from flask import request, session

LOCALES = ("sv", "en")
CATALOGUES = {
    locale: json.loads(
        (Path(__file__).parent / f"locales/{locale}.json").read_text(encoding="utf-8")
    )
    for locale in LOCALES
}


def current_locale() -> str:
    return session.get("locale", LOCALES[0])


def remember_requested_locale() -> None:
    """Keep a `?lang=` choice from any page for the rest of the session."""
    if request.args.get("lang") in LOCALES:
        session["locale"] = request.args["lang"]


def localization_context() -> dict:
    locale = current_locale()
    other = next(choice for choice in LOCALES if choice != locale)

    def translate(key: str, **parameters: object) -> str:
        message = CATALOGUES[locale].get(key, key)
        return message.format_map(parameters) if parameters else message

    return {
        "locale": locale,
        "other_locale": other,
        "other_locale_name": CATALOGUES[other]["locale.name"],
        "_": translate,
    }
