"""Capture complete inputs and explain every source entry in exceptional runs."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from . import trojanska_7132 as policy

BASE = 'https://member.schack.se/public/api/v1'


class RecordingFetch:
    def __init__(self, fetch):
        self.fetch = fetch
        self.responses = {}

    def __call__(self, url, stage):
        if url not in self.responses:
            self.responses[url] = deepcopy(self.fetch(url, stage))
        return deepcopy(self.responses[url])


def reports(standings, configuration, recorded: RecordingFetch) -> dict[str, str]:
    awards = {r['contenderId']: r for r in standings.award_breakdown}
    clubs = {r['id'] for r in recorded.responses[f'{BASE}/organisation/district/clubs/5821']}
    rating_url = f'{BASE}/ratinglist/district/5821/date/{standings.run_input.rating_list_date.isoformat()}/ratingtype/1/category/0'
    district_players = {r['id'] for r in recorded.responses[rating_url]}
    rows = []
    from ..util import categorize_year, generate_year_ranges
    for gid in policy.GROUPS:
        table = recorded(f'{BASE}/tournamentresults/table/id/{gid}', f'capturing excluded group {gid}')
        rounds = recorded(f'{BASE}/tournamentresults/roundresults/id/{gid}', f'capturing rounds for {gid}')
        played = policy.played_ids(rounds)
        for player in table:
            cid = player['contenderId']
            info = player['playerInfo']
            year = configuration.dispensation_dict.get(cid, int(info['birthdate']))
            row = {
                'contenderId': cid, 'source_group_id': gid,
                'annual_band': categorize_year(year, generate_year_ranges(policy.SEASON)),
                'points': player['points'], 'secPoints': player['secPoints'], 'place': player['place'],
                'awarded_points': None,
            }
            if gid not in policy.INCLUDED:
                row['reason'] = 'excluded_group'
            elif cid not in played or not any(player[k] for k in ('wonGames', 'drawGames', 'lostGames')):
                row['reason'] = 'no_played_games'
            elif info.get('clubId') not in clubs and cid not in district_players:
                row['reason'] = 'outside_district'
            elif cid in awards:
                row.update(awards[cid])
            else:
                row['reason'] = 'no_show'
            rows.append(row)
    root = Path(__file__).resolve().parents[1]
    source_hashes = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob('*.py'))
    }
    manifest = {
        'policy_version': policy.VERSION,
        'source_sha256': source_hashes,
        'run_input': asdict(standings.run_input),
        'configuration': asdict(configuration),
    }
    return {
        'trojanska-7132-awards.json': json.dumps({'policy_version': policy.VERSION, 'entries': rows}, ensure_ascii=False, indent=2),
        'calculation-inputs.json': json.dumps({'manifest': manifest, 'responses': recorded.responses}, ensure_ascii=False, default=str),
    }
