"""Resolve dispensation entries against the member system.

Each input line has the form `<first name> <last name ...> <birth year>
<dispensation year>`. Verification requires exactly one matching player with
the supplied birth year, born after the season's oldest band. Refusals identify
the line and reason. Rollover applies the same age limit to stored identities
without querying the member system again.

Callers supply a `PlayerSearch`. The CLI uses `MemberSystemClient.player_name_search`
and prints verified TOML entries with player-name comments.
"""
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace

from .active_configuration import ActiveConfiguration
from .upstream_client import PlayerInfo, UpstreamError
from .util import generate_year_ranges

PlayerSearch = Callable[[str, str], list[PlayerInfo]]


@dataclass(frozen=True)
class DispensationRequest:
    first_name: str
    last_name: str
    birth_year: int
    dispensation_year: int


@dataclass(frozen=True)
class VerifiedDispensation:
    player_id: int
    first_name: str
    last_name: str
    birth_year: int
    club: str
    dispensation_year: int

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def as_configuration_lines(self) -> str:
        return (f"# {self.name}, born {self.birth_year}, {self.club}\n"
                f'"{self.player_id}" = {self.dispensation_year}')


class DispensationRefused(ValueError):
    """
    One entry cannot be verified. The message is the reason in English; `code` and `parameters`
    name it for a translated message.
    """

    def __init__(self, message: str, code: str, **parameters: object) -> None:
        self.code = code
        self.parameters = parameters
        super().__init__(message)


@dataclass(frozen=True)
class RefusedLine:
    line_number: int
    line: str
    reason: str
    code: str
    parameters: dict[str, object] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"line {self.line_number}: {self.line!r}: {self.reason}"


def parse_line(line: str) -> DispensationRequest:
    parts = line.split()
    first_year = next((index for index, token in enumerate(parts) if _is_number(token)), None)
    if first_year is None:
        raise DispensationRefused("no birth year and dispensation year found", "no_years")
    if first_year < 2:
        raise DispensationRefused("a first name and a last name are needed before the years", "names_needed")
    years = parts[first_year:]
    if len(years) != 2 or not all(map(_is_number, years)):
        found = " ".join(years)
        raise DispensationRefused(
            f"expected a birth year and a dispensation year after the name, got {found!r}",
            "years_expected", found=found,
        )
    for year in years:
        if len(year) != 4:
            raise DispensationRefused(f"{year!r} is not a four-digit year", "not_four_digits", year=year)
    return DispensationRequest(parts[0], " ".join(parts[1:first_year]), int(years[0]), int(years[1]))


def oldest_band_end(season_year: int) -> int:
    """The last birth year of the season's oldest band; nobody born then or earlier has a dispensation."""
    return generate_year_ranges(season_year)[0]["end"]


def refuse_oldest_band(birth_year: int, season_year: int) -> None:
    last_year = oldest_band_end(season_year)
    if birth_year <= last_year:
        raise DispensationRefused(
            f"born {birth_year}, in or before the oldest band, which ends {last_year}: no dispensation applies",
            "oldest_band", birth_year=birth_year, last_year=last_year,
        )


def carry_dispensations_forward(configuration: ActiveConfiguration, season_year: int) -> ActiveConfiguration:
    """
    The rollover's dispensations for `season_year`: each year advanced by one with its verified
    player kept, and every player born in or before the season's oldest band dropped.
    """
    last_year = oldest_band_end(season_year)
    carried = {
        player_id: player
        for player_id, player in configuration.dispensation_players.items()
        if player.birth_year > last_year
    }
    return replace(
        configuration,
        dispensation_dict={
            player_id: configuration.dispensation_dict[player_id] + 1 for player_id in carried
        },
        dispensation_players=carried,
    )


def verify(request: DispensationRequest, search: PlayerSearch, season_year: int) -> VerifiedDispensation:
    """The one upstream player the request names, or `DispensationRefused` saying why there is none."""
    try:
        players = search(request.first_name, request.last_name)
    except UpstreamError as error:
        raise DispensationRefused(str(error), "upstream_failure") from error

    if len(players) != 1:
        raise DispensationRefused(
            f"expected exactly 1 player of that name upstream, found {len(players)}",
            "match_count", count=len(players),
        )
    (player,) = players
    if player.birth_year != request.birth_year:
        raise DispensationRefused(
            f"birth year mismatch: entered {request.birth_year}, upstream {player.birth_year}",
            "birth_year_mismatch", entered=request.birth_year, upstream=player.birth_year,
        )
    refuse_oldest_band(player.birth_year, season_year)

    return VerifiedDispensation(
        player_id=player.player_id,
        first_name=player.first_name,
        last_name=player.last_name,
        birth_year=player.birth_year,
        club=player.current_club_name or "",
        dispensation_year=request.dispensation_year,
    )


def resolve_lines(
    lines: Iterable[str], season_year: int, search: PlayerSearch,
) -> tuple[list[VerifiedDispensation], list[RefusedLine]]:
    """
    Resolve every line, skipping blank lines and `#` comments. A player resolved by an earlier line
    is refused on a later one, since `dispensation_dict` holds one year per player.
    """
    verified: list[VerifiedDispensation] = []
    refused: list[RefusedLine] = []
    first_line_of: dict[int, int] = {}

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            dispensation = verify(parse_line(line), search, season_year)
            if dispensation.player_id in first_line_of:
                earlier_line = first_line_of[dispensation.player_id]
                raise DispensationRefused(
                    f"player {dispensation.player_id} was already resolved on line {earlier_line}",
                    "already_resolved", player_id=dispensation.player_id, line=earlier_line,
                )
        except DispensationRefused as refusal:
            refused.append(RefusedLine(line_number, line, str(refusal), refusal.code, refusal.parameters))
            continue
        first_line_of[dispensation.player_id] = line_number
        verified.append(dispensation)

    return verified, refused


def _is_number(token: str) -> bool:
    return token.isascii() and token.isdigit()
