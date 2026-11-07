"""
The single-tournament klass check behind `jgp check-tournament <tournament id> [--print-names]`.

It fetches one tournament, reads its group names with the standings' own parser (a refused name
raises `KlassNamingError`, listing every refused group), and reports each player registered in a
group whose birth year, or dispensation year, falls outside that klass's band. It also writes
`<tournament id>_playerinfo.json` to the working directory, as this check's own output.

`--print-names` adds the player lists of the D, E and F groups, sorted by birth year (ascending)
then alphabetically.
"""

import json
from typing import Dict, List, Any

from .upstream_client import Fetch
from .tournament_exceptions import policy_for
from .generation_orchestrator import KlassNamingError
from .klass_naming import is_excluded_validation_klass, is_jdm_tournament_name, read_group_klasses


class TournamentCheckError(RuntimeError):
    """The tournament cannot be checked: it is missing upstream, or it is not a JGP tournament."""


def klass_years_extended(tournament_year: int, tournament_month: int = 1) -> Dict[str, List[int]]:
    """
    Generate birth year ranges for JGP tournament competition klasses.

    Rules (birth year = tournament_year - age):
      A: born [year-20 .. year-17]  e.g. 2006–2009 for 2026
      B: born [year-16 .. year-14]  e.g. 2010–2012 for 2026
      C: born [year-13 .. year-11]  e.g. 2013–2015 for 2026
      D: born [year-10 .. year-7]   e.g. 2016–2019 for 2026, open-ended downward
                                    (younger players may play up, so year-10 and later accepted)
      AB (combined): [year-20 .. year-14]  used when organiser merges A and B groups

    E and higher are excluded from standings computations entirely.
    tournament_month is accepted for call-site compatibility but not used.
    """
    y = tournament_year
    return {
        "a": list(range(y - 20, y - 17 + 1)),  # [year-20 .. year-17]
        "b": list(range(y - 16, y - 14 + 1)),  # [year-16 .. year-14]
        "ab": list(range(y - 20, y - 14 + 1)),  # [year-20 .. year-14]
        "c": list(range(y - 13, y - 11 + 1)),  # [year-13 .. year-11]
        "d": list(range(y - 10, y + 1)),  # [year-10 .. year] open-ended: younger ok
    }


def fetch_tournament_data(fetch: Fetch, tournament_id: str) -> Dict[str, Any]:
    """Fetch tournament data from API."""
    print(f"Fetching tournament {tournament_id}...")

    api_url = f'https://member.schack.se/public/api/v1/tournament/tournament/id/{tournament_id}'
    data = fetch(api_url, f"fetching tournament {tournament_id}")
    if not data:
        raise TournamentCheckError(f"No data found for tournament {tournament_id}")

    return data


def extract_groups(tournament_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract relevant groups from tournament data. A group name that does not follow
    `KLASS_NAMING.md` is refused exactly as the standings refuse it: every such group is
    raised together as a `KlassNamingError`.
    """
    policy = policy_for(tournament_data['id'])
    try:
        exceptional_readings = policy.select_groups(tournament_data) if policy else None
    except ValueError as error:
        raise TournamentCheckError(str(error)) from error
    groups = tournament_data['rootClasses'][0]['groups']
    tournament_date = tournament_data['start'].replace('-', '')
    tournament_year = int(tournament_date[:4])
    tournament_month = int(tournament_date[4:6])

    if policy:
        readings, problems = exceptional_readings, []
        print(f"Exceptional policy {policy.VERSION}: event age ranges; separate 2010–2012 ladders; Hugo/Rishi eligibility exceptions")
    else:
        readings, problems = read_group_klasses(tournament_data['id'], tournament_data['name'], groups)
    if problems:
        raise KlassNamingError(problems)

    is_jdm = is_jdm_tournament_name(tournament_data['name'])
    interesting_groups = []

    for group, klass in readings:
        name = group['name']

        # This utility reports on klass E and F, so it skips only G and H.
        if is_excluded_validation_klass(klass, is_jdm=is_jdm):
            print(f"Skipping klass {klass.upper()} group: {name}")
            continue

        interesting_groups.append({
            'group_id': group['id'],
            'group_name': name,
            'klass': klass,
            'tournament_year': tournament_year,
            'tournament_month': tournament_month
        })
        print(f"Found klass {klass.upper()} group: {name}")

    return interesting_groups


def fetch_players_for_group(fetch: Fetch, group_id: int) -> List[Dict[str, Any]]:
    """Fetch player registration data for a group."""
    # Tournament tables include registered players before results are available.
    api_url = f'https://member.schack.se/public/api/v1/tournamentresults/table/id/{group_id}'
    return fetch(api_url, f"fetching the table for group {group_id}")


def validate_players_in_group(players: List[Dict[str, Any]], klass: str,
                              tournament_year: int, tournament_month: int,
                              dispensation_dict: Dict[int, int]) -> List[Dict[str, Any]]:
    """Validate players in a specific group."""
    ranges = klass_years_extended(tournament_year, tournament_month)

    if klass not in ranges:
        print(f"Warning: No validation rules for klass '{klass}'")
        return []

    allowed_years = ranges[klass]
    invalid_players = []

    for player in players:
        contender_id = player["contenderId"]
        player_info = player["playerInfo"]
        first_name = player_info["firstName"]
        last_name = player_info["lastName"]
        birth_year = int(player_info["birthdate"])
        if contender_id in dispensation_dict:
            effective_year = dispensation_dict[contender_id]
            if effective_year in allowed_years:
                print(
                    f"  ℹ️  Player has dispensation: {first_name} {last_name} (born {birth_year}, dispensation allows {effective_year}) ✅")
                continue
            else:
                print(
                    f"  ℹ️  Player has dispensation: {first_name} {last_name} (born {birth_year}, dispensation allows {effective_year}) ⚠️  dispensation year {effective_year} not valid for klass {klass.upper()}")
                invalid_players.append({
                    "contenderId": contender_id,
                    "name": f"{first_name} {last_name}",
                    "birthYear": birth_year,
                    "effectiveYear": effective_year,
                    "klass": klass.upper(),
                    "allowedYears": f"{min(allowed_years)}-{max(allowed_years)}" if len(allowed_years) > 1 else str(
                        allowed_years[0])
                })
                continue

        if birth_year not in allowed_years:
            invalid_players.append({
                "contenderId": contender_id,
                "name": f"{first_name} {last_name}",
                "birthYear": birth_year,
                "klass": klass.upper(),
                "allowedYears": f"{min(allowed_years)}-{max(allowed_years)}" if len(allowed_years) > 1 else str(
                    allowed_years[0])
            })

    return invalid_players


def print_names_for_ef(ef_groups: Dict[str, List[Dict[str, Any]]]) -> None:
    """
    Print player name lists for Klass E and F groups.

    Players are sorted by birth year ascending; within each birth year
    they are sorted alphabetically by full name.  A separator is printed
    between the E and F sections.
    """
    klasses_to_print = [k for k in ("d", "e", "f") if k in ef_groups]

    if not klasses_to_print:
        print("\nNo Klass E or F groups found.")
        return

    for idx, klass in enumerate(klasses_to_print):
        if idx > 0:
            print()
            print("-" * 40)

        players = ef_groups[klass]
        players_sorted = sorted(players, key=lambda p: (p["birthYear"], p["name"]))

        print()
        print(f"Klass {klass.upper()} — {len(players_sorted)} players")
        print("=" * 40)

        current_year = None
        for player in players_sorted:
            if player["birthYear"] != current_year:
                current_year = player["birthYear"]
                print(f"\n  Born {current_year}:")
            print(f"    {player['name']}")


def check_tournament(
    tournament_id: int, dispensation_dict: Dict[int, int], *, fetch: Fetch, print_names: bool = False,
) -> None:
    """
    Print the check's report for one tournament. A refused group name raises `KlassNamingError`; a
    tournament that cannot be checked raises `TournamentCheckError`, and an upstream failure
    `UpstreamError`. Players in the wrong klass are reported, not raised.
    """
    print(f"Loaded {len(dispensation_dict)} dispensations")
    tournament_data = fetch_tournament_data(fetch, str(tournament_id))
    tournament_name = tournament_data["name"]
    print(f"Tournament: {tournament_name}")
    policy = policy_for(tournament_id)
    if "JGP" not in tournament_name and not policy:
        raise TournamentCheckError(f"Tournament: {tournament_name} is not a JGP tournament. Halting.")
    groups = extract_groups(tournament_data)
    if not groups:
        print("No relevant groups found in tournament")
        return

    print(f"Found {len(groups)} groups to validate")
    all_player_info = {}
    all_invalid_players = []
    ef_groups: Dict[str, List[Dict[str, Any]]] = {}  # klass -> list of player dicts
    for group in groups:
        print(f"\nProcessing {group['group_name']} (Klass {group['klass'].upper()})...")
        players = fetch_players_for_group(fetch, group['group_id'])
        if not players:
            continue

        print(f"  Found {len(players)} registered players")
        for player in players:
            player_info = player['playerInfo']
            all_player_info[str(player['contenderId'])] = {
                "name": f"{player_info['firstName']} {player_info['lastName']}",
                "club": player_info.get('club', ''),
                "birthYear": int(player_info['birthdate']),
                "gender": player_info.get('sex', '')
            }
        if print_names and group['klass'] in ('d','e','f'):
            klass_key = group['klass']
            if klass_key not in ef_groups:
                ef_groups[klass_key] = []
            for player in players:
                pi = player['playerInfo']
                ef_groups[klass_key].append({
                    "name": f"{pi['firstName']} {pi['lastName']}",
                    "birthYear": int(pi['birthdate']),
                })
        if policy:
            entries = [dict(p, source_group_id=group['group_id'], klass=group['klass']) for p in players]
            invalid = policy.validate_players(entries, dispensation_dict, [])
        else:
            invalid = validate_players_in_group(
                players, group['klass'],
                group['tournament_year'], group['tournament_month'],
                dispensation_dict
            )

        if invalid:
            print(f"  ⚠️  {len(invalid)} players in wrong klass:")
            for player in invalid:
                print(f"    - {player['name']} (born {player['birthYear']}, should be {player['allowedYears']})")
            all_invalid_players.extend(invalid)
        else:
            print("  ✅ All players in correct klass")
    player_info_file = f"{tournament_id}_playerinfo.json"
    with open(player_info_file, 'w', encoding='utf-8') as f:
        json.dump(all_player_info, f, indent=2, ensure_ascii=False)
    print(f"\nPlayer info saved to {player_info_file}")
    print(f"\n{'=' * 50}")
    print("VALIDATION SUMMARY")
    print(f"{'=' * 50}")

    if all_invalid_players:
        print(f"❌ Found {len(all_invalid_players)} players in incorrect klasses:")
        print()
        by_klass = {}
        for player in all_invalid_players:
            klass = player['klass']
            if klass not in by_klass:
                by_klass[klass] = []
            by_klass[klass].append(player)

        for klass in sorted(by_klass.keys()):
            sample_group = groups[0]
            ranges = klass_years_extended(sample_group['tournament_year'], sample_group['tournament_month'])
            allowed_years = ranges.get(klass.lower(), [])
            year_range = f"{min(allowed_years)}-{max(allowed_years)}" if len(allowed_years) > 1 else str(
                allowed_years[0]) if allowed_years else "Unknown"

            if policy:
                year_range = by_klass[klass][0]['allowedYears']
            print(f"Class {klass} (allowed years: {year_range}):")
            for player in by_klass[klass]:
                print(f"  • {player['name']} (ID: {player['contenderId']}, born {player['birthYear']})")
            print()
    else:
        print("✅ All players are in the correct classes!")
    if print_names:
        print_names_for_ef(ef_groups)
