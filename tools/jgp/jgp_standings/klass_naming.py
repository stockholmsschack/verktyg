"""Parse group names using the formats in `KLASS_NAMING.md`.

`read_klass` raises `KlassNameRefused` for invalid names. `read_group_klasses`
collects refusals with tournament and group details so generation can report
all naming problems before calculation. Organisers must rename invalid groups
at source. Stable reason codes support translated web messages; the CLI uses
`detail`.
"""
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

JDM_NAME_MARKERS = ('Junior-DM', 'JuniorDM', 'JDM')

# Klasses that take no part in ordinary JGP standings; at a JDM event E and above do compete.
EXCLUDED_JGP_KLASSES = frozenset({'e', 'f', 'g', 'h'})

# The single-tournament check includes E and F, so it excludes only G and H.
EXCLUDED_VALIDATION_KLASSES = frozenset({'g', 'h'})

# Reject other keyword casing so a misspelling cannot silently exclude a group.
KLASS_KEYWORDS = ('Klass', 'klass', 'Grupp', 'grupp')

# The only merged klass: an organiser combining the A and B groups.
MERGED_KLASS = 'AB'

_LETTER_OR_DIGIT = r'[^\W_]'
_LETTER = r'[^\W\d_]'

# The keyword as a word of its own, in any casing; "Klassisk" and "Nybörjargrupp" are not keywords.
_KEYWORD = re.compile(rf'(?<!{_LETTER})(?:klass|grupp)(?!{_LETTER})', re.IGNORECASE)

# Match one or two capital letters A–H after the keyword. Pairs may be adjacent
# or joined by / - + & , or a space. The final lookahead rejects a third letter.
_SEPARATOR = r'\s*[/\-+&,]?\s*'
_KLASS_AFTER_KEYWORD = re.compile(
    rf'\s+(?P<klass>[A-H]{{2}}|[A-H](?:{_SEPARATOR}[A-H])?)'
    rf'(?!{_LETTER_OR_DIGIT})'
    rf'(?!{_SEPARATOR}[A-H](?!{_LETTER_OR_DIGIT}))'
)


class KlassNameReason(StrEnum):
    """Why a group name was refused. The values are stable codes for adapters to map to messages."""

    NO_KLASS = 'no_klass'
    KEYWORD_SPELLING = 'keyword_spelling'
    SEVERAL_KLASSES = 'several_klasses'
    UNREADABLE_KLASS = 'unreadable_klass'
    UNKNOWN_MERGE = 'unknown_merge'


class KlassNameRefused(ValueError):
    """A group name that does not follow `KLASS_NAMING.md`."""

    def __init__(self, group_name: str, reason: KlassNameReason, detail: str) -> None:
        self.group_name = group_name
        self.reason = reason
        self.detail = detail
        super().__init__(f"group name {group_name!r}: {detail}")


@dataclass(frozen=True)
class KlassNamingProblem:
    """One group, in one tournament, whose name has to be changed at source."""

    tournament_id: int
    tournament_name: str
    group_id: int
    group_name: str
    reason: KlassNameReason
    detail: str

    def __str__(self) -> str:
        return (f"{self.tournament_name} ({self.tournament_id}), group {self.group_name!r} "
                f"(id {self.group_id}): {self.detail}")


def read_klass(group_name: str) -> str:
    """Return the declared klass (`a` through `h`, or `ab`).

    Raise `KlassNameRefused` for formats outside `KLASS_NAMING.md`.
    """
    keywords = list(_KEYWORD.finditer(group_name))
    if not keywords:
        raise KlassNameRefused(group_name, KlassNameReason.NO_KLASS,
                               "it has no 'Klass' or 'Grupp' followed by a klass letter")
    if len(keywords) > 1:
        raise KlassNameRefused(group_name, KlassNameReason.SEVERAL_KLASSES,
                               f"it names {len(keywords)} klasses; a group name declares exactly one")

    keyword = keywords[0]
    if keyword.group() not in KLASS_KEYWORDS:
        raise KlassNameRefused(group_name, KlassNameReason.KEYWORD_SPELLING,
                               f"{keyword.group()!r} is not an accepted spelling; write 'Klass'")

    klass = _KLASS_AFTER_KEYWORD.match(group_name, keyword.end())
    if klass is None:
        raise KlassNameRefused(group_name, KlassNameReason.UNREADABLE_KLASS,
                               f"'{keyword.group()}' must be followed by a space and one capital "
                               f"klass letter, A to H, standing on its own")

    letters = re.sub(r'[^A-H]', '', klass['klass'])
    if len(letters) == 2 and letters != MERGED_KLASS:
        raise KlassNameRefused(group_name, KlassNameReason.UNKNOWN_MERGE,
                               f"{klass['klass']!r} is not a klass; the only merged klass is A and B, "
                               f"written in that order")
    return letters.lower()


def read_group_klasses(
    tournament_id: int, tournament_name: str, groups: Iterable[Mapping]
) -> tuple[list[tuple[Mapping, str]], list[KlassNamingProblem]]:
    """Return parsed `(group, klass)` pairs and all naming problems for a tournament."""
    readings = []
    problems = []
    for group in groups:
        try:
            readings.append((group, read_klass(group['name'])))
        except KlassNameRefused as refusal:
            problems.append(KlassNamingProblem(
                tournament_id=tournament_id,
                tournament_name=tournament_name,
                group_id=group['id'],
                group_name=group['name'],
                reason=refusal.reason,
                detail=refusal.detail,
            ))
    return readings, problems


def is_jdm_tournament_name(name: str) -> bool:
    """
    True when a tournament or group name marks it as a Junior-DM event.

    Matched as a case-sensitive substring against ``JDM_NAME_MARKERS``: ``Junior-DM``,
    ``JuniorDM``, ``JDM``. A JDM event uses a different set of klass bands and does not exclude
    klass E and above.
    """
    return any(marker in name for marker in JDM_NAME_MARKERS)


def is_excluded_jgp_klass(klass: str, *, is_jdm: bool) -> bool:
    """Exclude E through H from ordinary JGP standings.

    JDM tournaments keep all klasses. The tournament name selects both this
    exception and the JDM birth-year bands.
    """
    return not is_jdm and klass in EXCLUDED_JGP_KLASSES


def is_excluded_validation_klass(klass: str, *, is_jdm: bool) -> bool:
    """Exclude G and H from single-tournament checks, except at JDM events."""
    return not is_jdm and klass in EXCLUDED_VALIDATION_KLASSES
