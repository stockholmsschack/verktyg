"""Capture upstream responses for offline replay of the partial 2026 season.

Run `uv run python capture_fixtures.py` with network access. Tournament ids come
from `state/active-configuration.toml`. The generation's fetch functions request
club and rating lists, tournament metadata, group tables and round results.

`fixtures/2026/responses.json` maps requested URLs to parsed JSON payloads.
`fixtures/2026/manifest.json` records the source, capture date, purpose,
tournament ids and rating-list date. Recording wraps the live client's
`get_json` without changing requests.
"""
import json
from copy import deepcopy
from datetime import date
from functools import partial
from pathlib import Path

from jgp_standings.active_configuration import load_configuration
from jgp_standings.generation_orchestrator import (
    fetch_club_ids,
    fetch_rating_list_ids,
    fetch_tournament_info,
)
from jgp_standings.run_record import resolve_run_input
from jgp_standings.upstream_client import UpstreamClient
from jgp_standings.util import fetch_group_details, fetch_tournament_data

FIXTURES_DIR = Path("fixtures/2026")
STATE_DIRECTORY = Path("state")
SOURCE = "https://member.schack.se/public/api/v1"
PURPOSE = "Frozen partial-2026-season upstream inputs for the JGP standings rewrite (Phase 3)."


def main():
    recorded = {}
    live_get_json = UpstreamClient().get_json

    def fetch(url, stage):
        data = live_get_json(url, stage)
        # A copy, because callers mutate what they are given (fetch_group_details sets 'klass').
        recorded[url] = deepcopy(data)
        return data

    tournament_ids = list(load_configuration(STATE_DIRECTORY).tournament_ids)
    today = date.today()

    club_ids = fetch_club_ids(fetch)
    run_input = resolve_run_input(tournament_ids, partial(fetch_tournament_data, fetch), today)
    rating_list_ids = fetch_rating_list_ids(fetch, run_input.rating_list_date)
    groups_by_tournament, _ = fetch_tournament_info(fetch, tournament_ids)

    for tournament in tournament_ids:
        groups = groups_by_tournament.get(tournament, [])
        if not groups:
            continue
        fetch_group_details(
            fetch, groups, groups[0]['tournament_name'],
            club_ids, rating_list_ids, {}, {}, {}
        )

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    (FIXTURES_DIR / "responses.json").write_text(
        json.dumps(recorded, indent=2, ensure_ascii=False)
    )
    manifest = {
        "source": SOURCE,
        "captured_at": today.isoformat(),
        "purpose": PURPOSE,
        "tournament_ids": tournament_ids,
        "rating_list_date": run_input.rating_list_date.isoformat(),
    }
    (FIXTURES_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"Captured {len(recorded)} responses to {FIXTURES_DIR}")


if __name__ == '__main__':
    main()
