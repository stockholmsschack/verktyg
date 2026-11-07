# Edge-case coverage of the 2026 baseline

What the six captured tournaments in `fixtures/2026` actually exercise. Measured against the capture,
not assumed. Gaps are recorded rather than filled with synthetic fixtures — a baseline that covers
five of six cases and says so is worth more than one that covers six and hides which are invented.

| Edge case | Covered | Evidence in the capture |
| --- | --- | --- |
| Dispensation moving a player into a different age band | yes | 15 dispensations configured, 13 apply to players present in the capture, and 8 of those move the player out of the band their birth year alone would give — e.g. `Bruce Sun Åslund` (2014) plays 2010-2012, `Melvin Ral Lustig` (2011) plays 2006-2009 |
| Per-tournament penalty on a player who also holds a dispensation | yes | 10 penalties (negative `secPoints`) across the capture; 4 fall on players who also hold a dispensation, including `664867` penalised in three separate tournaments |
| Non-local player removed before allocation | yes | 24 rejections recorded in `reports/rejected_players.txt`, across all six tournaments |
| Player past the end of the points list scoring 1 | yes | 23 class/tournament combinations have more than 20 scorers, so the 20-entry standings-points list is exhausted and the tail falls through to 1 point |
| At least one JDM event alongside ordinary JGP events | yes | `Junior-DM i Snabbschack 2026` (6853), alongside five ordinary JGP events |
| Duplicate entry resolved to the lowest quality points | **no** | `print_duplicate_players` reports no repeated `contenderId` anywhere in the capture, so `remove_duplicates_by_secpoints` never has a duplicate to resolve. The path is unexercised by this baseline and covered instead by the unit tests in `tests/test_eligibility_and_duplicates.py` |

## The gap

Duplicate-entry resolution has no coverage here and cannot get any from these six tournaments. It
is covered by focused unit tests over `remove_duplicates_by_secpoints` in
`tests/test_eligibility_and_duplicates.py` rather than by a fixture, beside the allocation rules and
apart from the baseline. Do not add a fabricated duplicate to `fixtures/2026` to close this row —
the fixture set's value is that it is exactly what the upstream API returned on the capture date.

## Also observed

The capture exercises two diagnostic paths that are not on the list above: walkover removal (20
entries in `reports/walkovers.txt`) and players who played no matches (`reports/no_matches_played.txt`,
covering `Wasa JGP 2026` and `Junior-DM i Snabbschack 2026`).
