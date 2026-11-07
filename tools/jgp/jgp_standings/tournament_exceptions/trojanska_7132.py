"""Trojanska 2026: event eligibility and two ladders in annual class 2010–2012."""
from collections import Counter, defaultdict

import polars as pl

from ..points import assign_standings_points

VERSION = "trojanska-7132-v1"
SEASON = 2026
# Stable source IDs; these labels are local to the policy, not global klass rules.
GROUPS = {
    19203: ("Gymnasiet och äldre 2006-2010", "a", 2006, 2010, 7, -1),
    19204: ("Högstadiet 2011-2013", "b", 2011, 2013, 7, -1),
    19205: ("Mellanstadiet Elit 2014-2016", "c", 2014, 2016, 8, -1),
    19206: ("Lågstadiet Elit 2017-2019", "d", 2017, 2019, 7, -1),
    19207: ("Mellanstadiet Allmän 2014-2016", "y", 2014, 2016, 6, -1),
    19208: ("Lågstadiet Nybörjare 2017-2019 (utan klocka)", "z", 2017, 2019, 5, 1),
}
INCLUDED = frozenset({19203, 19204, 19205, 19206})
ELIGIBILITY_EXCEPTIONS = frozenset({570261, 577097})


def validate_season(year: int) -> None:
    if year != SEASON:
        raise ValueError(f"Tournament 7132 policy requires season 2026, received {year}")


def select_groups(data: dict) -> list[tuple[dict, str]]:
    """Refuse changed source structure rather than silently changing the calculation."""
    for field in ('start', 'end'):
        value = data.get(field)
        if not isinstance(value, str) or len(value) < 4 or not value[:4].isdigit():
            raise ValueError(f"Tournament 7132: missing or invalid {field} date; verify upstream metadata")
        validate_season(int(value[:4]))
    roots = data.get('rootClasses', [])
    groups = roots[0].get('groups', []) if len(roots) == 1 else []
    if data.get('id') != 7132 or Counter(g.get('id') for g in groups) != Counter(GROUPS.keys()):
        raise ValueError("Tournament 7132: expected one root class with groups 19203–19208; verify upstream metadata")
    for group in groups:
        name, _, _, _, rounds, points = GROUPS[group['id']]
        if (group.get('name'), group.get('nrofrounds'), group.get('pointSystem')) != (name, rounds, points):
            raise ValueError(f"Tournament 7132: unexpected metadata for group {group['id']}; verify name, rounds and point system")
    return [(g, GROUPS[g['id']][1]) for g in groups if g['id'] in INCLUDED]


def eligibility(player: dict, dispensations) -> tuple[bool, str]:
    gid = player['source_group_id']
    _, klass, first, last, _, _ = GROUPS[gid]
    cid = player['contenderId']
    year = dispensations.get(cid, int(player['playerInfo']['birthdate']))
    exception = gid == 19204 and cid in ELIGIBILITY_EXCEPTIONS and year == 2010
    return first <= year <= last or exception, f"{first}-{last} ({klass.upper()})"


def validate_players(players, dispensations, penalties) -> list[dict]:
    invalid = []
    for player in players:
        valid, allowed = eligibility(player, dispensations)
        if not valid:
            info = player['playerInfo']
            invalid.append({
                'contenderId': player['contenderId'],
                'name': f"{info['firstName']} {info['lastName']}",
                'birthYear': int(info['birthdate']), 'klass': player['klass'].upper(),
                'allowedYears': allowed,
            })
        reasons = []
        if not valid:
            reasons.append('wrong_group')
        if player['contenderId'] in penalties:
            reasons.append('explicit_penalty')
        player['penalty_reason'] = ', '.join(reasons)
    return invalid


def played_ids(rounds: list[dict]) -> set[int]:
    """Only completed games against real opponents establish participation."""
    return {
        cid for round_ in rounds for game in round_['games']
        if game['result'] in (-1, 0, 1) and game['whiteId'] > 0 and game['blackId'] > 0
        for cid in (game['whiteId'], game['blackId'])
    }


def reject_duplicates(players) -> None:
    duplicates = sorted(cid for cid, count in Counter(p['contenderId'] for p in players).items() if count > 1)
    if duplicates:
        raise ValueError(f"Tournament 7132: duplicate player IDs across included results: {duplicates}; correct upstream entries")


def allocate(entries, dispensations) -> tuple[dict[int, float], list[dict]]:
    from ..util import categorize_year, generate_year_ranges

    reject_duplicates(entries)
    partitions = defaultdict(list)
    audit = []
    for entry in entries:
        cid = entry['contenderId']
        year = dispensations.get(cid, int(entry['playerInfo']['birthdate']))
        band = categorize_year(year, generate_year_ranges(SEASON))
        partition = entry['source_group_id'] if 2010 <= year <= 2012 else None
        row = {
            'contenderId': cid, 'source_group_id': entry['source_group_id'],
            'annual_band': band, 'points': entry['points'], 'secPoints': entry['secPoints'],
            'place': entry['place'], 'reason': entry.get('penalty_reason', ''),
        }
        audit.append(row)
        if not row['reason']:
            partitions[band, partition].append(row)
    awards = {row['contenderId']: 0.0 for row in audit}
    for rows in partitions.values():
        ranked = assign_standings_points(pl.DataFrame(rows), 'place', 'points', 'secPoints', 'award', 'contenderId')
        awards.update({r['contenderId']: r['award'] for r in ranked.to_dicts()})
    for row in audit:
        row['awarded_points'] = awards[row['contenderId']]
    return awards, audit
