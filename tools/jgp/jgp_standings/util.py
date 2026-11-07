from .upstream_client import Fetch
from .tournament_exceptions import policy_for


def fetch_tournament_data(fetch: Fetch, tournament_id: str):
    """Fetch tournament data from API."""
    api_url = f'https://member.schack.se/public/api/v1/tournament/tournament/id/{tournament_id}'
    data = fetch(api_url, f"fetching tournament {tournament_id}")
    if not data:
        raise ValueError(f"No data found for tournament {tournament_id}")

    return data


typst_doc = """    
#import "lib.typ": stylish_table, standings_heading
#set document(title: [JGP Ställning],
author: "Stockholms Schackförbunds UngdomsUtskott"
)
#set page(
margin: 0.8cm,
footer: context[
  #set text(8pt, font: "Roboto")
  #set align(center)
  #counter(page).display(
  "1 / 1", both: true)],
)
#set text(12pt, font: "Roboto", weight: "regular")
#show link: set text(fill: rgb("#0072B2"))
#standings_heading([JGP Ställning])
"""


def standings_to_typst(key, df, counter, underline=None):
    if underline is None:
        underline = []
    heading_key = key.replace('onward', '-')
    lines = [f"""
#standings_heading([JGP Ställning], group: [
  #link(<gototop>)[GP Ställning klass {heading_key}] #label("{counter}")
])
#stylish_table()[
      #table(
"""]

    # The name column takes the rest of the page, so every standings table spans the full width.
    columns_list = ["auto"] * len(df.columns)
    columns_list[df.columns.index('Namn')] = "1fr"
    lines.append(f"columns: ({', '.join(columns_list)}),")
    align_list = (["right + horizon"] + ["left + horizon"] + ["left + horizon"] + ["left + horizon"]
                  + ["right + horizon"] * (len(df.columns) - 4))
    lines.append(f"align: ({', '.join(align_list)}),")
    lines.append("rows: 1.8em,")
    header_cells = ", ".join(f"[*{col}*]" for col in df.columns)
    lines.append(f"table.header({header_cells}),")
    rows_data = df.to_dicts()
    for counter, row in enumerate(rows_data):
        row_cells = []
        for col in df.columns:
            val = row[col]
            if val is None:
                cell_val = ''
            elif val == 0:
                cell_val = '#text(red)[0]'
            elif isinstance(val, float) and val == int(val):
                cell_val = str(int(val))
            elif isinstance(val, float):
                cell_val = str(val).replace('.', ',')
            else:
                cell_val = str(val)
            row_cells.append(f"[{cell_val}]")

        lines.append(f"  {', '.join(row_cells)},")
        if counter + 1 in underline:
            lines.append("table.hline(stroke: 1pt + black),")

    lines.append(")]")
    return "\n".join(lines)


def clubs_df_to_typst(df, total=0):
    lines = ["#table(columns: (10%, 45%, 20%),", """align: (x, y) => if y == 0 { center } else { 
  if x == 0 {center}
  else if x == 1 { left }
  else {right}
  },""", f"table.header[*\#*][*Tävling*][*Antal starter*],"]
    rows_data = df.to_dicts()
    for row in rows_data:
        row_cells = ", ".join(f"[{val}]" for val in row.values())
        lines.append(f"  {row_cells},")

    lines.append(f"table.cell(stroke: none)[],table.cell(stroke: none)[],[*{total}*]")
    lines.append(")")
    return "\n".join(lines)


def breakdown_df_to_typst(df):
    lines = ["#table(columns: (auto, 30%), align: center,",
             f'table.header[*Klass*#label("gototop")][*Unika deltagare*],']
    rows_data = df.to_dicts()
    for counter, row in enumerate(rows_data):
        row_values = list(row.values())
        row_cells = []
        for col_idx, val in enumerate(row_values):
            if col_idx == 0 and counter < len(rows_data) - 1:
                cell = f"[#link(<{counter}>)[{val}]]"
            elif counter == len(rows_data) - 1:
                cell = f"[*{val}*]"
            else:
                cell = f"[{val}]"
            row_cells.append(cell)
        lines.append(f"  {', '.join(row_cells)},")

    lines.append(")")
    return "\n".join(lines)


def format_label(start, end):
    """Format a birth-year range; -1 marks an open boundary."""
    if start == -1:
        return f"up to {end}"
    elif end == -1:
        return f"{start} onward"
    elif start == end:
        return f"{start}"
    else:
        return f"{start}-{end}"


def categorize_year(year, ranges):
    """Return the label of the first matching range, or "Unknown".

    A start or end of -1 leaves that boundary open.
    """
    for year_range in ranges:
        start = year_range.get('start')
        end = year_range.get('end')
        if start == -1 and year <= end:
            return format_label(start, end)
        elif end == -1 and year >= start:
            return format_label(start, end)
        elif start <= year <= end:
            return format_label(start, end)

    return "Unknown"


def generate_year_ranges(tournament_year):
    """Return standings birth-year ranges for the season, oldest first.

    Each range has `start` and `end` keys; an end of -1 includes all younger players.
    """
    OFFSETS = [
        (-20, -17),
        (-16, -14),
        (-13, -13),
        (-12, -12),
        (-11, -11),
        (-10, -10),
        (-9, -9),
        (-8, -1)
    ]

    year_ranges = [
        {'start': tournament_year + start_offset,
         'end': -1 if end_offset == -1 else tournament_year + end_offset}
        for start_offset, end_offset in OFFSETS
    ]

    return year_ranges


def band_labels(tournament_year):
    """Every age-band label of the year, oldest first, as the standings key their bands."""
    return [format_label(year_range['start'], year_range['end'])
            for year_range in generate_year_ranges(tournament_year)]


def klass_years(tournament_year, tournament_month=1):
    """Return JGP birth-year bands for A, B, AB, C and D.

    D includes younger players who play up. `tournament_month` is retained for
    call compatibility; bands depend only on the year.
    """
    y = tournament_year
    return {
        "A": list(range(y - 20, y - 17 + 1)),  # [year-20 .. year-17]
        "B": list(range(y - 16, y - 14 + 1)),  # [year-16 .. year-14]
        "AB": list(range(y - 20, y - 14 + 1)),  # [year-20 .. year-14]
        "C": list(range(y - 13, y - 11 + 1)),  # [year-13 .. year-11]
        "D": list(range(y - 10, y + 1)),  # [year-10 .. year] open-ended: younger ok
    }


def jdm_klass_years(tournament_year, tournament_month=1, tournament_name=""):
    """Return JDM birth-year bands for A through E.

    E includes younger players who play up. `tournament_month` and
    `tournament_name` are retained for call compatibility.
    """
    y = tournament_year
    return {
        "A": list(range(y - 20, y - 17 + 1)),  # [year-20 .. year-17]
        "B": list(range(y - 16, y - 14 + 1)),  # [year-16 .. year-14]
        "C": list(range(y - 13, y - 12 + 1)),  # [year-13 .. year-12]
        "D": list(range(y - 11, y - 10 + 1)),  # [year-11 .. year-10]
        "E": list(range(y - 9, y + 1)),  # [year-9  .. year]   open-ended younger
    }


def validate_players(players_list, tournament_year, tournament_month=1,
                     dispensation_dict=None, penalties=None, is_jdm=False, tournament_name=""):
    """Negate points for penalties and invalid klass entries; return invalid players.

    Dispensations map contender ids to effective birth years. Explicit penalties
    apply before klass checks. Invalid-player reports retain the real birth year.
    `is_jdm` selects the JDM bands.
    """
    if penalties is None:
        penalties = []
    if is_jdm:
        ranges = jdm_klass_years(tournament_year, tournament_month, tournament_name)
    else:
        ranges = klass_years(tournament_year, tournament_month)

    invalid_players = []

    for player in players_list:
        contender_id = player["contenderId"]
        first_name = player["playerInfo"]["firstName"]
        last_name = player["playerInfo"]["lastName"]
        birth_year = int(player["playerInfo"]["birthdate"])
        klass = player["klass"].upper()

        # Apply explicit penalties once, before checking dispensations or klass bands.
        if contender_id in penalties:
            player['points'] *= -1
            player['secPoints'] *= -1
            continue

        # Use dispensation year as effective birth year if present, then validate
        if contender_id in dispensation_dict:
            effective_year = dispensation_dict[contender_id]
            if klass in ranges and effective_year in ranges[klass]:
                continue
            # An invalid dispensation incurs the same penalty as an invalid birth year.
            player['points'] *= -1
            player['secPoints'] *= -1
            invalid_players.append({
                "contenderId": contender_id,
                "name": f"{first_name} {last_name}",
                "birthYear": birth_year,
                "klass": klass
            })
            continue
        if not (klass in ranges and birth_year in ranges[klass]):
            player['points'] *= -1
            player['secPoints'] *= -1
            invalid_players.append({
                "contenderId": contender_id,
                "name": f"{first_name} {last_name}",
                "birthYear": birth_year,
                "klass": klass
            })

    return invalid_players


def get_no_show_walkover_players(fetch: Fetch, group_id):
    url = f'https://member.schack.se/public/api/v1/tournamentresults/roundresults/id/{group_id}'
    data = fetch(url, f"fetching round results for group {group_id}")
    player_status = {}

    for round_data in data:
        for game in round_data['games']:
            if game['whiteId'] == -100 or game['blackId'] == -100:
                continue

            result = game['result']
            white_id = game['whiteId']
            black_id = game['blackId']
            for player_id in (white_id, black_id):
                if player_id not in player_status:
                    player_status[player_id] = set()

            if result == 2:
                player_status[black_id].add('walkover_loss')
            elif result == -2:
                player_status[white_id].add('walkover_loss')
            else:
                player_status[white_id].add('played')
                player_status[black_id].add('played')

    # Players with only walkover losses count as no-shows.
    no_shows = [player_id for player_id, results in player_status.items()
                if results == {'walkover_loss'}]

    return no_shows


def fetch_group_details(fetch: Fetch, interesting_groups: list, tournament_name, ssf_club_ids_set, stsf_player_ids, player_list, walkovers,
                        no_matches_played):
    tournament_results = []

    for group in interesting_groups:
        api_url = f'https://member.schack.se/public/api/v1/tournamentresults/table/id/{group["group_id"]}'
        data = fetch(api_url, f"fetching the table for group {group['group_id']}")
        for player in data:
            player_info = player['playerInfo']
            elo = player_info.get('elo') or {}
            rapid = elo.get('rapidRating', 0)
            club_id = player_info.get('clubId')
            stsf_player_id = player['contenderId']

            player_list[str(player['contenderId'])] = {
                "klubb": player_info.get('club'),
                "klubbId": player_info.get('clubId'),
                "local": (club_id in ssf_club_ids_set) or (stsf_player_id in stsf_player_ids),
                "name": f"{player_info.get('firstName')} {player_info.get('lastName')}",
                "firstname": player_info.get('firstName'),
                "lastname": player_info.get('lastName'),
                "gender": player_info.get('sex'),
                "rating": rapid,
                "playdate": player_info.get('birthdate'),
            }
            player['klass'] = group['klass']
            player['source_group_id'] = group['group_id']
        no_shows = get_no_show_walkover_players(fetch, group["group_id"])
        if no_shows:
            wo_players = [f'{player_list[str(no_show)]["name"]} ({no_show})' for no_show in no_shows]
            if tournament_name in walkovers:
                walkovers[tournament_name].extend(wo_players)
            else:
                walkovers[tournament_name] = wo_players
            filtered_data = [item for item in data if item['contenderId'] not in no_shows]
        else:
            filtered_data = data

        policy = policy_for(group.get('tournament_id', 0))
        if policy:
            rounds = fetch(
                f"https://member.schack.se/public/api/v1/tournamentresults/roundresults/id/{group['group_id']}",
                f"checking participation for group {group['group_id']}",
            )
            played = policy.played_ids(rounds)
            unplayed = [p for p in filtered_data if p['contenderId'] not in played]
            if unplayed:
                no_matches_played.setdefault(tournament_name, []).extend(
                    f"{p['playerInfo']['firstName']} {p['playerInfo']['lastName']}" for p in unplayed
                )
            filtered_data = [p for p in filtered_data if p['contenderId'] in played]
        tournament_results.extend(filtered_data)
        zero_matches = [result for result in tournament_results if (
                result['wonGames'] == 0 and
                result['drawGames'] == 0 and
                result['lostGames'] == 0)]
        tournament_results = [result for result in tournament_results if not (
                result['wonGames'] == 0 and
                result['drawGames'] == 0 and
                result['lostGames'] == 0)]
        if len(zero_matches):
            zero_match_players = [f"{player['playerInfo']['firstName']} {player['playerInfo']['lastName']}" for player
                                  in zero_matches]
            if tournament_name in no_matches_played:
                no_matches_played[tournament_name].extend(zero_match_players)
            else:
                no_matches_played[tournament_name] = zero_match_players

    return tournament_results


def filter_non_local_players(all_players, tournament_name, player_list, rejected_players):
    output = []
    for player in all_players:
        if not str(player['contenderId']) in player_list:
            continue  # the dummy W.O. player, or a player missing from every group table
        if player_list[str(player['contenderId'])]['local']:
            output.append(player)
        else:
            rejected_players.append(
                f'{tournament_name}: {player_list[str(player["contenderId"])]["name"]} ({player["contenderId"]})')
    return output

