"""Collate standings and build artifacts in memory.

`collate_standings` returns a `CollatedStandings` value. `produce_artifacts`
builds the workbooks, Typst source, public JSON and diagnostic reports from
that value. `build_generation_result` combines both steps. File and network
access belong to the caller.
"""
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from io import BytesIO
from types import MappingProxyType
from typing import Any

import polars as pl
import polars.selectors as cs
import xlsxwriter

from .points import assign_standings_points
from .tournament_exceptions import policy_for
from .run_record import RunInput
from .util import (
    breakdown_df_to_typst,
    categorize_year,
    clubs_df_to_typst,
    generate_year_ranges,
    standings_to_typst,
    typst_doc,
)

PUBLIC_STANDINGS_FIELDS = frozenset(
    {
        "placement",
        "member_id",
        "name",
        "birth_year",
        "club",
        "tournaments",
        "total_points",
    }
)
PLAYER_KEY_COLUMNS = ['contenderId', 'playerInfo.firstName', 'playerInfo.lastName',
                      'playerInfo.birthdate', 'playerInfo.sex']
EXCEL_NUMBER_FORMATS = {
    pl.Int64: 'General',
    pl.Int32: 'General',
    pl.Float64: 'General',
    pl.Float32: 'General'
}


@dataclass(frozen=True)
class TournamentEntries:
    """
    One tournament's surviving entries: local, klass-validated (penalties applied) and
    de-duplicated. `starters` counts the entries before the locality filter, after no-shows and
    players with no games were removed; it is what the statistics report as JGP starts.
    """
    tournament_id: int
    name: str
    date: str  # the start date as YYYYMMDD
    starters: int
    entries: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class GenerationDiagnostics:
    rejected_players: tuple[str, ...]
    walkovers: Mapping[str, list[str]]
    no_matches_played: Mapping[str, list[str]]
    penalized_players: Mapping[int, list[dict]]


@dataclass(frozen=True)
class CollatedStandings:
    """
    `tournaments` keeps the run's tournament order, which is the order the statistics list them in.
    `bands` maps each age-band label to its ranked validation rows, in standings order.
    """
    run_input: RunInput
    tournaments: tuple[TournamentEntries, ...]
    bands: Mapping[str, pl.DataFrame]
    diagnostics: GenerationDiagnostics
    award_breakdown: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class GeneratedArtifacts:
    validation_workbook: bytes
    standings_workbook: bytes
    stats_workbook: bytes
    typst_source: str
    standings_json: str
    diagnostic_reports: Mapping[str, str]  # file name -> text; empty reports are omitted


@dataclass(frozen=True)
class GenerationResult:
    standings: CollatedStandings
    artifacts: GeneratedArtifacts


def build_generation_result(
    standings: CollatedStandings,
    *,
    contributor: Mapping[str, str],
    landscape: bool,
    club_display_names: Mapping[str, str] = MappingProxyType({}),
    finals_separators: Mapping[str, Sequence[int]] = MappingProxyType({}),
) -> GenerationResult:
    return GenerationResult(standings, produce_artifacts(
        standings, contributor=contributor, landscape=landscape, club_display_names=club_display_names,
        finals_separators=finals_separators,
    ))


def collate_standings(
    *,
    run_input: RunInput,
    tournaments: tuple[TournamentEntries, ...],
    player_list: Mapping[str, Mapping[str, Any]],
    dispensation_dict: Mapping[int, int],
    diagnostics: GenerationDiagnostics,
) -> CollatedStandings:
    """Allocate standings points per tournament and age band, and rank each band."""
    year_ranges = generate_year_ranges(run_input.effective_year)

    award_breakdown = []
    prepared_tournaments = []
    for tournament in tournaments:
        policy = policy_for(tournament.tournament_id)
        if policy:
            policy.validate_season(run_input.effective_year)
            awards, audit = policy.allocate(tournament.entries, dispensation_dict)
            award_breakdown.extend(audit)
            tournament = replace(tournament, entries=tuple(
                dict(entry, supplied_gp=awards[entry['contenderId']]) for entry in tournament.entries
            ))
        prepared_tournaments.append(tournament)
    df = create_merged_df(tuple(prepared_tournaments))
    df = adjust_columns(df, dispensation_dict, year_ranges)
    df = df.drop(['playerInfo.firstName', 'playerInfo.lastName', 'play_year', 'playerInfo.sex'])

    klubb_values = [
        player_list.get(str(contender_id), {}).get('klubb', 'Unknown')
        for contender_id in df['contenderId'].to_list()
    ]
    df = df.with_columns([
        pl.Series(name='Klubb', values=klubb_values)
    ])

    bands = {
        name: rank_band(df.filter(pl.col('year_group') == name), len(tournaments))
        for name in df['year_group'].unique().sort().to_list()
    }
    return CollatedStandings(run_input, tournaments, bands, diagnostics, tuple(award_breakdown))


def create_merged_df(tournaments: tuple[TournamentEntries, ...]) -> pl.DataFrame:
    """Join tournament entries with columns numbered `p_N`, `kvp_N` and `place_N`.

    Number tournaments by start date, then by tournament id compared as text.
    """
    all_df = None
    ordered = _standings_tournaments(tournaments)

    for count, tournament in enumerate(ordered, start=1):
        flattened_data = [
            {
                'contenderId': record['contenderId'],
                'playerInfo.firstName': record['playerInfo']['firstName'],
                'playerInfo.lastName': record['playerInfo']['lastName'],
                'playerInfo.birthdate': record['playerInfo']['birthdate'],
                'playerInfo.sex': record['playerInfo']['sex'],
                f'p_{count}': record['points'],
                f'kvp_{count}': record['secPoints'],
                f'place_{count}': record['place'],
                **({f'award_{count}': record['supplied_gp']} if 'supplied_gp' in record else {}),
            }
            for record in tournament.entries
        ]
        if flattened_data:
            temp_df = pl.DataFrame(flattened_data)
        else:
            schema = dict(zip(PLAYER_KEY_COLUMNS, (pl.Int64, pl.String, pl.String, pl.String, pl.Int64)))
            schema.update({f'p_{count}': pl.Float64, f'kvp_{count}': pl.Float64, f'place_{count}': pl.Int64})
            temp_df = pl.DataFrame(schema=schema)
        if policy_for(tournament.tournament_id) and f'award_{count}' not in temp_df.columns:
            temp_df = temp_df.with_columns(pl.lit(None, dtype=pl.Float64).alias(f'award_{count}'))

        if all_df is None:
            all_df = temp_df
        else:
            # A full outer join that merges the key columns instead of duplicating them
            all_df = all_df.join(temp_df, on=PLAYER_KEY_COLUMNS, how='full', coalesce=True)

    return all_df


def adjust_columns(df, dispensation_dict, year_ranges):
    df = df.with_columns([
        pl.col('playerInfo.birthdate').cast(pl.Int64)
    ])
    play_year_values = []
    for contender_id in df['contenderId'].to_list():
        play_year_values.append(
            dispensation_dict.get(contender_id, None)
        )

    df = df.with_columns([
        pl.Series(name='play_year_temp', values=play_year_values)
    ])

    df = df.with_columns([
        pl.when(pl.col('play_year_temp').is_not_null())
        .then(pl.col('play_year_temp'))
        .otherwise(pl.col('playerInfo.birthdate'))
        .alias('play_year')
    ]).drop('play_year_temp')
    df = df.with_columns([
        (pl.col('playerInfo.firstName') + ' ' + pl.col('playerInfo.lastName')).alias('Namn')
    ])
    year_group_values = []
    for year in df['play_year'].to_list():
        year_group_values.append(categorize_year(year, year_ranges))

    df = df.with_columns([
        pl.Series(name='year_group', values=year_group_values)
    ])

    return df


def rank_band(group: pl.DataFrame, tournament_count: int) -> pl.DataFrame:
    """Allocate each tournament's standings points within one age band and rank the band."""
    s_group = group.clone()

    for idx in range(tournament_count):
        award_column = f'award_{idx + 1}'
        if award_column in s_group.columns:
            s_group = s_group.with_columns(
                pl.col(award_column).alias(f'STD {idx + 1}')
            ).drop(award_column)
        else:
            s_group = assign_standings_points(s_group, f'place_{idx + 1}', f'p_{idx + 1}',
                                              f'kvp_{idx + 1}', f'STD {idx + 1}', 'contenderId')

    std_cols = [col for col in s_group.columns if col.startswith('STD')]
    s_group = s_group.with_columns([
        pl.sum_horizontal([pl.col(col) for col in std_cols]).alias('Σ')
    ])
    kvp_cols = [col for col in s_group.columns if col.startswith('kvp_')]
    s_group = s_group.with_columns([
        pl.sum_horizontal([pl.col(col).is_not_null().cast(pl.Int32) for col in kvp_cols]).alias('played')
    ])

    # Full joins return rows in an unspecified order. Break ranking ties by descending
    # contenderId so repeated runs produce the same standings.
    s_group = s_group.sort(
        ["Σ", "played", "playerInfo.birthdate",
         pl.max_horizontal([
             pl.when(pl.col(c).is_not_null()).then(int(c.split(" ")[1])).otherwise(0)
             for c in std_cols
         ]),
         "contenderId"
         ],
        descending=[True, False, True, True, True]
    )

    s_group = s_group.with_columns([
        pl.Series(name='Placering', values=list(range(1, s_group.height + 1)), dtype=pl.Int64)
    ])
    s_group = s_group.select(['Placering'] + [col for col in s_group.columns if col != 'Placering'])

    return s_group.drop('year_group')


def prepare_for_export(columns_list, dataframe):
    dataframe = dataframe.drop(columns_list)
    dataframe = dataframe.rename({'playerInfo.birthdate': 'Född'})
    rename_dict = {}
    for col in dataframe.columns:
        if col.startswith('STD'):
            new_name = col.split(' ')[1]
            rename_dict[col] = new_name

    if rename_dict:
        dataframe = dataframe.rename(rename_dict)

    # Swap the second and third columns for export.
    cols = dataframe.columns
    if len(cols) >= 3:
        new_order = [cols[0], cols[2], cols[1]] + cols[3:]
        dataframe = dataframe.select(new_order)

    return dataframe


def produce_artifacts(
    standings: CollatedStandings,
    *,
    contributor: Mapping[str, str],
    landscape: bool,
    club_display_names: Mapping[str, str] = MappingProxyType({}),
    finals_separators: Mapping[str, Sequence[int]] = MappingProxyType({}),
) -> GeneratedArtifacts:
    """Build every artifact directly from the one collated result."""
    breakdown = _breakdown_frame(standings)
    tournament_frame = _tournament_frame(standings)
    published = _published_bands(standings)
    return GeneratedArtifacts(
        validation_workbook=_bands_workbook(standings.bands, '{}!'),
        standings_workbook=_bands_workbook(published, 'GP Ställning klass {}'),
        stats_workbook=_stats_workbook(breakdown, tournament_frame),
        typst_source=standings_typst(
            breakdown, tournament_frame, published, contributor, landscape, club_display_names, finals_separators
        ),
        standings_json=serialize_standings_json(
            _public_standings_rows(standings, published),
            tournament_ids=[t.tournament_id for t in _standings_tournaments(standings.tournaments)],
        ),
        diagnostic_reports=diagnostic_reports(standings.diagnostics),
    )


def standings_typst(
    breakdown, tournament_frame, published, contributor, landscape, club_display_names, finals_separators,
) -> str:
    """
    The document's source. A club's shortened name replaces its canonical name in the standings tables
    only, and each band's table draws a bold line after the rows `finals_separators` lists for its label.
    """
    source = typst_doc + breakdown_df_to_typst(breakdown)
    total_players_in_all = tournament_frame['JGP-Starter (*)'].sum()
    source += clubs_df_to_typst(tournament_frame, total=total_players_in_all)
    source += f"""#linebreak()\n
    #show link: set text(blue)\n \\ \\
    Synpunkter / Comments: #link("mailto:{contributor['email']}")[{contributor['name']}]
    """
    source += "\n" + '#include "details.typ"\n'

    for counter, (key, df) in enumerate(published.items()):
        df = df.rename({'Placering': '\\#'})
        df = df.drop(cs.starts_with("place_"))
        df = df.with_columns(pl.col('Klubb').replace(dict(club_display_names)))

        parts = [
            "#pagebreak()",
            "#set page(margin: 0.8cm)",
            "#page(flipped: true)[" if landscape else "",
            standings_to_typst(key, df, counter, finals_separators.get(key)),
            "]" if landscape else ""
        ]

        source += "\n".join(parts)

    return source


def _standings_tournaments(tournaments: Sequence[TournamentEntries]) -> list[TournamentEntries]:
    """Match the table columns: start date, then tournament id compared as text."""
    return sorted(tournaments, key=lambda t: (t.date, str(t.tournament_id)))


def serialize_standings_json(
    bands: Mapping[str, Iterable[Mapping[str, Any]]], *, tournament_ids: Sequence[int],
) -> str:
    """
    Serialize ordered tournament ids and age-band standings with member ids.
    Reject unknown fields, missing fields and wrongly typed values.
    """
    validated_bands = {}
    for label, rows in bands.items():
        if not isinstance(label, str) or not label:
            raise ValueError("age-band labels must be text")
        validated_bands[label] = [_validated_public_row(label, index, row) for index, row in enumerate(rows)]
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in tournament_ids):
        raise ValueError("tournament_ids must contain positive integers")
    if len(set(tournament_ids)) != len(tournament_ids):
        raise ValueError("tournament_ids must be unique")
    return json.dumps(
        {
            "last_updated": datetime.now(UTC).isoformat(timespec="seconds"),
            "tournament_ids": list(tournament_ids),
            "standings": validated_bands,
        },
        ensure_ascii=False, indent=2,
    ) + "\n"


def _validated_public_row(label: str, index: int, row: Mapping[str, Any]) -> dict[str, Any]:
    where = f"standings row {index} in {label!r}"
    fields = set(row)
    unknown = fields - PUBLIC_STANDINGS_FIELDS
    missing = PUBLIC_STANDINGS_FIELDS - fields
    if unknown:
        raise ValueError(f"{where} contains non-allowlisted fields: " + ", ".join(sorted(unknown)))
    if missing:
        raise ValueError(f"{where} is missing public fields: " + ", ".join(sorted(missing)))
    if isinstance(row["placement"], bool) or not isinstance(row["placement"], int):
        raise ValueError(f"{where} placement must be an integer")
    if isinstance(row["member_id"], bool) or not isinstance(row["member_id"], int) or row["member_id"] <= 0:
        raise ValueError(f"{where} member_id must be a positive integer")
    if (
        isinstance(row["birth_year"], bool)
        or not isinstance(row["birth_year"], int)
        or not 1000 <= row["birth_year"] <= 9999
    ):
        raise ValueError(f"{where} birth_year must be a four-digit year")
    for field_name in ("name", "club"):
        if not isinstance(row[field_name], str):
            raise ValueError(f"{where} {field_name} must be text")
    if isinstance(row["total_points"], bool) or not isinstance(row["total_points"], (int, float)):
        raise ValueError(f"{where} total_points must be numeric")
    tournaments = row["tournaments"]
    if not isinstance(tournaments, Mapping):
        raise ValueError(f"{where} tournaments must be an object")
    for tournament_id, standings_points in tournaments.items():
        if not isinstance(tournament_id, str) or not tournament_id:
            raise ValueError(f"{where} tournament identifiers must be text")
        if isinstance(standings_points, bool) or not isinstance(standings_points, (int, float)):
            raise ValueError(f"{where} tournament {tournament_id} must contain only standings points")
    return dict(row)


def diagnostic_reports(diagnostics: GenerationDiagnostics) -> dict[str, str]:
    reports = {}
    if diagnostics.rejected_players:
        reports['rejected_players.txt'] = ''.join(f'{player}\r\n' for player in diagnostics.rejected_players)
    if diagnostics.walkovers:
        reports['walkovers.txt'] = _grouped_report(diagnostics.walkovers)
    if diagnostics.no_matches_played:
        reports['no_matches_played.txt'] = _grouped_report(diagnostics.no_matches_played)
    if diagnostics.penalized_players:
        reports['penalties.txt'] = json.dumps(diagnostics.penalized_players, ensure_ascii=False, indent=4)
    return reports


def _grouped_report(values: Mapping[str, list[str]]) -> str:
    return ''.join(
        f'{key}:\n' + ''.join(f'\t{value}\n' for value in items) + '\n'
        for key, items in values.items()
    )


def _published_bands(standings: CollatedStandings) -> dict[str, pl.DataFrame]:
    """The rows the standings workbook and document show: no points detail, no ids."""
    tournament_count = len(standings.tournaments)
    columns_list = [f"{prefix}_{i}" for i in range(1, tournament_count + 1) for prefix in ("p", "kvp")] + [
        'contenderId', 'played']
    return {key: prepare_for_export(columns_list, band) for key, band in standings.bands.items()}


def _public_standings_rows(
    standings: CollatedStandings, published: Mapping[str, pl.DataFrame]
) -> dict[str, list[dict[str, Any]]]:
    """
    Each band's published rows, in placement order, as public rows. `tournaments` maps the run's
    tournament id to the standings points scored there and omits the tournaments not played.
    """
    tournament_ids = [str(tournament.tournament_id) for tournament in _standings_tournaments(standings.tournaments)]
    return {
        label: [
            {
                "placement": row["Placering"],
                "member_id": row["contenderId"],
                "name": row["Namn"],
                "birth_year": row["Född"],
                "club": row["Klubb"],
                "tournaments": {
                    tournament_id: row[str(index)]
                    for index, tournament_id in enumerate(tournament_ids, start=1)
                    if row[str(index)] is not None
                },
                "total_points": row["Σ"],
            }
            for row in band.with_columns(standings.bands[label]["contenderId"]).to_dicts()
        ]
        for label, band in published.items()
    }


def _breakdown_frame(standings: CollatedStandings) -> pl.DataFrame:
    breakdown = pl.DataFrame([
        {"Klass": key, "Unika deltagare": band.height} for key, band in standings.bands.items()
    ])
    total_row = pl.DataFrame([{"Klass": "Totalt", "Unika deltagare": breakdown['Unika deltagare'].sum()}])
    return pl.concat([breakdown, total_row])


def _tournament_frame(standings: CollatedStandings) -> pl.DataFrame:
    return pl.DataFrame([
        {'#': index, 'Tävling': tournament.name, 'JGP-Starter (*)': tournament.starters}
        for index, tournament in enumerate(standings.tournaments, start=1)
    ])


def _bands_workbook(bands: Mapping[str, pl.DataFrame], title_template: str) -> bytes:
    """One worksheet per age band, headed by `title_template` filled with the band label."""
    buffer = BytesIO()
    with xlsxwriter.Workbook(buffer, {'in_memory': True}) as workbook:
        title_format = workbook.add_format({
            'font_name': 'Calibri',
            'font_size': 14,
            'bold': True,
            'font_color': '0072B2'
        })
        for key, band in bands.items():
            worksheet = workbook.add_worksheet(key)
            worksheet.write(0, 1, title_template.format(key), title_format)
            band.write_excel(
                workbook=workbook,
                worksheet=key,
                position=(1, 0),
                table_style="Table Style Light 9",
                autofit=True,
                float_precision=1,
                dtype_formats=EXCEL_NUMBER_FORMATS,
            )
    return buffer.getvalue()


def _stats_workbook(breakdown: pl.DataFrame, tournament_frame: pl.DataFrame) -> bytes:
    buffer = BytesIO()
    with xlsxwriter.Workbook(buffer, {'in_memory': True}) as workbook:
        breakdown.write_excel(workbook=workbook, worksheet='Antal', position=(1, 0), autofit=True)
        tournament_frame.write_excel(workbook=workbook, worksheet='tournaments', position=(0, 0), autofit=True)
    return buffer.getvalue()
