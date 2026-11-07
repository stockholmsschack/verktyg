"""Render a downloadable dispensation list without publishing an artifact."""

from datetime import date
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

from .active_configuration import ActiveConfiguration
from .artifact_publication import DEFAULT_TYPST_ASSETS, TypstRenderer


def render_dispensations(configuration: ActiveConfiguration, exported_on: date) -> bytes:
    players = sorted(
        (
            {"name": player.name, "birth_year": player.birth_year, "plays_as": year}
            for player_id, year in configuration.dispensation_dict.items()
            for player in [configuration.dispensation_players[player_id]]
        ),
        key=lambda player: (player["birth_year"], player["name"].casefold()),
    )
    with TemporaryDirectory(prefix="jgp-dispensations-") as directory:
        root = Path(directory)
        for filename in ("dispensations.typ", "stsf.png"):
            shutil.copyfile(DEFAULT_TYPST_ASSETS / filename, root / filename)
        (root / "dispensations.json").write_text(json.dumps({
            "year": exported_on.year, "date": exported_on.isoformat(), "players": players,
        }, ensure_ascii=False), encoding="utf-8")
        destination = root / "dispensations.pdf"
        TypstRenderer().render(
            root / "dispensations.typ", destination,
            root=root, font_directory=DEFAULT_TYPST_ASSETS / "fonts",
        )
        return destination.read_bytes()
