# Junior Grand Prix Standings Calculation System

## Overview

The Junior Grand Prix (JGP) is a series of chess tournaments for young players. This document explains how tournament results are processed and standings are calculated to determine the overall rankings across multiple tournaments.

## Prerequisites

The system requires access to tournament information through a chess federation API. Key data collected includes:
- Tournament details (name, date, groups)
- Player information (name, birth year, club)
- Match results

## Process Flow

### Step 1: Data Collection and Filtering

1. **Club Information**: A list of all clubs within the Stockholm Chess Federation is collected to identify local players.

2. **Tournament Data**: For each tournament in the series, the following information is retrieved:
   - Tournament name and date
   - Group details (class/age divisions)
   - Player information and results

3. **Player Filtering**:
   - Players who registered but didn't show up (had only walkover losses) are removed
   - Players who registered but played no matches are removed
   - Players who are not members of a club within the Stockholm Chess Federation are removed at this
     point, before any points are calculated. They take no part in the standings points allocation
     and do not occupy a place in it.

4. **Player Validation**:
   - Players are checked to ensure they are competing in the correct age class based on their birth year
     - A player who did not play in their correct age group has both their match points and their
       quality points negated for that tournament, which becomes 0 standings points in the allocation
   - A dispensation replaces a player's birth year for this check. It does not widen the class: a
     dispensation year that falls outside the class the player entered leaves the entry invalid
   - Penalties may be "manually" applied to players who compete in incorrect groups
     - This is used when dispensations are provided later than date of play
     - A penalty negates the player's points for that tournament regardless of any dispensation they
       hold, and the player is not additionally reported as being in the wrong class

5. **Duplicate Resolution**:
   - A player entered twice in the same tournament — for example in two groups — is resolved to a
     single entry: the one with the **lowest** quality points. Entries level on quality points are
     resolved to the first one seen.

### Step 2: Points Calculation

1. **Tournament Points**: In each tournament, players earn points based on their performance:
   - Match points (1 point for a win, 0.5 for a draw, 0 for a loss)
   - Quality points (used as tiebreakers)

2. **Standings Points**:
   - Players are sorted by their match points and quality points within each birth year group
   - Special points (called "standings points") are assigned to each player based on their ranking
   - The standings points system ensures fair comparison across different tournaments

3. **Points Allocation Rules**:

   Allocation runs once per tournament within each age group, over the players who are left after
   non-local players, no-shows and duplicates have been removed.

   - A pre-defined list of 18 standings points is used:
     [25, 22, 20, 18, 16, 14, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
   - Players are ranked by match points, then quality points, both descending. The place recorded by
     the tournament system decides only whether a player took part in that tournament; it does not
     decide their order here
   - Standings points are handed out sequentially down that ranking, one list entry per player
   - **Ties are not averaged.** Players level on both match points and quality points consume
     successive entries from the list, so two tied players take 25 and 22 rather than 23.5 each.
     The player with the higher player id takes the higher entry, the same final tie-break the
     standings ranking uses, so the result never depends on the order the data arrived in
   - A **penalised** player — one whose quality points are negative, whether from a wrong-class entry
     or a manual penalty — scores 0 and **consumes no entry** from the list. The player below them
     receives the points they would otherwise have taken
   - A player with **no place** in a tournament did not take part in it and likewise consumes no entry
   - Once the list is exhausted, every remaining ranked player scores **1 point** each
   - Finally, any player who is still unscored but does carry quality points for the tournament scores
     **1 point**
   - A player with neither a place nor quality points scores nothing for that tournament; the cell is
     left empty rather than set to zero

### Step 3: Collation and Ranking

1. **Age Grouping**:
   - Players are grouped by birth year according to predefined ranges — see *Age Classification*
     below for the ranges themselves. They are finer than the competition classes: the two oldest
     groups span several years, the younger ones are a single birth year each
   - The grouping sticks to the calendar year; that is, players stay in the group they were in in
     January even if they moved to a higher grade in August
   - A player who holds a dispensation is grouped by their dispensation year rather than their birth
     year

2. **Standings Calculation**:
   - For each player, standings points from all tournaments are summed
   - Players are sorted by total standings points in descending order
   - In case of a tie in total standings points, these tie-breaks apply in order:
     - The player who took part in fewer tournaments is ranked higher
     - Then the younger player — the later birth year — is ranked higher
     - Then the player whose most recent scoring tournament is later is ranked higher

### Step 4: Output Generation

1. **Validation File**:
   - A detailed Excel file is created with all player data, including tournament points and quality points
   - Non-local players are highlighted for verification purposes

2. **Final Standings File**:
   - A clean Excel file with final standings by age group
   - Only includes players from Stockholm Chess Federation clubs
   - Shows placement, name, club, birth year, and standings points from each tournament

3. **Statistics Reports**:
   - Player count by age group
   - Tournament participation statistics
   - Lists of walkover matches and no-shows

## Additional Features

### Age Classification

Two separate groupings are in play, and they are not the same thing.

**Competition classes (klass)** are what a player enters at a tournament, and what validation checks.
They are defined as birth-year offsets from the tournament year, so they move with the year. Written
out for 2026:

| Klass | Ordinary JGP | Junior-DM (JDM) |
| --- | --- | --- |
| A | year-20 … year-17 (2006–2009) | year-20 … year-17 (2006–2009) |
| B | year-16 … year-14 (2010–2012) | year-16 … year-14 (2010–2012) |
| C | year-13 … year-11 (2013–2015) | year-13 … year-12 (2013–2014) |
| D | year-10 and later (2016–) | year-11 … year-10 (2015–2016) |
| E | — | year-9 and later (2017–) |
| AB | year-20 … year-14 (2006–2012) | — |

- A and B are the same in both. C and D are narrower at a JDM event, and E competes there.
- The lowest class in each table is open-ended downward: a younger player may play up.
- `AB` is used when an organiser merges the A and B groups into one. There is no combined class at a
  JDM event.
- Ordinary JGP events have no class E or above; those groups are excluded from the standings entirely
  (see *Klass identification* below). A player entered in a class with no band at all — class F, or
  AB, at a JDM event — is treated as being in the wrong class and has their points negated. A group
  name that does not declare a class is refused, not scored.
- The month of the tournament does not move the bands. A player stays in the class they had in
  January even after the school year turns in August.

**Standings age groups** are how the published tables are broken up, and they are finer than the
competition classes. Relative to the tournament year they are: year-20…year-17, year-16…year-14, then
one group per year for year-13, year-12, year-11, year-10 and year-9, then year-8 and younger as one
open-ended group. For 2026 that is 2006–2009, 2010–2012, 2013, 2014, 2015, 2016, 2017, and
"2018 onward". A player who holds a dispensation is grouped by their dispensation year, not their
birth year.

### Klass identification

The class a group belongs to is read out of the group's name as it comes from the federation API.
[`KLASS_NAMING.md`](KLASS_NAMING.md) lists the accepted formats, simplest first, and the refused
ones. In short: the name holds exactly one `Klass` (or `klass`, `Grupp`, `grupp`) followed by a space
and a capital klass letter A–H standing on its own, or A and B merged as `Klass A/B`.

A name outside those formats is never guessed at. The generation stops before anything is calculated,
lists every refused group in every tournament of the run, and exits with status 1; the group has to
be renamed in the member system. `jgp_standings/klass_naming.py` implements the rules, and
`tests/test_klass_identification.py` reads the document's example tables as test data, so the two
cannot drift apart. `jgp check-tournament` applies the same rules to a single tournament.

**Which classes reach the standings.** At an ordinary JGP event, groups of class E, F, G and H are
read and then dropped. A tournament whose name carries a JDM marker — `Junior-DM`, `JuniorDM` or
`JDM`, matched case-sensitively — drops nothing; the same decision selects the JDM birth-year bands.
`jgp check-tournament` reports on classes E and F, so at an ordinary JGP it drops only G and H.

### Special Cases

1. **Dispensations**: Some players may receive permission to play in a different age group than their birth year would normally allow.

2. **Penalties**: Players competing in incorrect groups may have their points negated for that tournament.

3. **Non-Local Players**: Non-local players are excluded before standings points are calculated, so
   their results have no effect on the points awarded to anyone else. This differs from the
   Flickornas standings, where non-local players are scored and only removed at the end; the two
   series intentionally treat this differently.

   Some players have multiple club memberships with their main club outside the Stockholm district,
   and the API lists only the main club. Such a player is treated as non-local; there is no
   override.

## Report Generation

The system produces several outputs:
- Excel files with detailed standings for each age group
- Statistical summaries of tournament participation
- Text reports of players who received penalties, walkovers, or were rejected

These outputs provide tournament organizers with comprehensive information for running the Junior Grand Prix series and determining qualified players for finals.

## Running a generation

`uv sync` installs the tool from the checkout, with the `jgp` command:

```
uv run jgp validate
uv run jgp generate
```

The configuration is `state/active-configuration.toml`: the tournament ids, the orientation, the
contributor, the club names, the separators, the dispensations and the penalties. The tool writes it, carrying a
`revision` that every save advances, and a save rewrites it whole, so comments do not survive.
Penalties are the one setting maintained by hand, under `[penalty_dict]` as
`"<tournament id>" = [<player id>, ...]`. `[club_display_names]` maps a district club's name to the
shortened name the PDF's standings tables show; the workbooks and the standings JSON keep the club's
own name. `[finals_separators]` maps an age-band label to the rows of that band's standings table a
bold line is drawn after, for example `"2010-2012" = [10, 20]`; a label naming no band of the run
refuses the generation. Each `[dispensation_dict]` entry has a `[dispensation_players."<id>"]` table
beside it with the name, birth year and club the member system returned when the entry was verified.

`validate` checks the configuration and lists every problem in it, fetching and writing nothing.
`generate` fetches, calculates, renders and writes every output of one run into a new directory,
`output/runs/<run id>/`: the validation, standings and statistics workbooks
(`jgp-validations-<time>.xlsx`, `jgp-standings-<time>.xlsx`, `Stats-<time>.xlsx`), the rendered
`standings-<time>.pdf`, the public standings `standings-<time>.json`, the text reports under
`diagnostics/`, and the Typst source `standings.typ`. `<time>` is the run's UTC time as
`YYYYMMDDTHHMMSSZ`, the same for every file of one run; the run id is that time plus a short random
suffix. It reports each stage on standard error as it goes; on success it prints the PDF's path,
and on failure it names the stage that failed and exits with status 1.

The PDF is compiled with the `typst` command, which has to be on the `PATH`, from copies of
`typst/lib.typ`, `details.typ`, `rules.typ`, `stsf.png` and `typst/fonts/` placed beside the source in
the run directory; system fonts are ignored, so the document looks the same on every machine.

The standings JSON contains `last_updated`, the JSON generation time in UTC ISO 8601 format
(for example, `2026-10-29T12:34:56+00:00`). This timestamp stays fixed when the API serves the snapshot.
It also contains `tournament_ids`, an array of integer IDs in table-column order
(tournament start date, then tournament ID compared as text), and `standings`, an object keyed by
age-band label in standings order. Each band's rows appear in placement order, with `placement`
restarting at 1. Each row carries exactly `placement`, `member_id` (the player's integer member
system ID), `name`, `birth_year`, `club`, `tournaments` (tournament ID to standings points, omitting
tournaments not played) and `total_points`. Consumers of the earlier payload must now read age
bands under `standings`. `output/public-standings.json` is a symlink to the latest successful run's
JSON: the public snapshot.

The rating-list date, effective year and tournament ids the run used are recorded in
`state/run_input.json` and replayed by later runs over the same tournaments; a run over a different
list resolves them afresh and replaces the record. The run directory, the run record and the snapshot
are committed together: a failure at any stage, rendering included, leaves the earlier runs, the
earlier run record and the earlier snapshot exactly as they were. Nothing else is written, and
earlier runs are never cleared.

`jgp generate --year 2025 --tournaments 5429,5475` runs an earlier or later season's tournaments
instead of the configured ones; the two options are only accepted together. Such a run is partial:
its files carry `-partial` before the time (`standings-partial-<time>.pdf`, `standings-partial.typ`),
and it neither reads nor writes `state/run_input.json` and leaves the public snapshot alone. `--state-dir` and
`--output-dir`, given before the command, replace `state/` and `output/` in the working directory; the
configuration is read from the state directory.

## The web application

```
uv run jgp-web
```

serves the operator pages on `http://127.0.0.1:8001/`, in Swedish or English: an overview, the
configuration, and the available files and reports. It binds to the loopback interface and asks for
no sign-in, since the local operator is already authorized; `--host` and `--port` change that, and
`--state-directory` and `--output-directory` replace `state/` and `output/`. `--debug` restarts the
server when the code or a catalogue changes. The configuration page reads the same
`state/active-configuration.toml` as the `jgp` command and looks the configured tournaments up in the
member system, warning when they belong to an earlier, mixed or later year. When they all belong to
an earlier year it offers **Start {year} season**: the configured tournaments are set aside, nothing is
stored, and **Cancel new season** brings them back. Saving a selection of this year's tournaments
completes the new season. That save clears the separators and carries the dispensations forward, each
dispensation year advanced by one, dropping every player born in the new season's oldest band or
earlier; the contributor, club names, orientation and penalties are kept. Its tabs edit the
tournaments, the dispensations, the contributor, the club names and the separators, each saved on its
own. The **PDF rules page** tab downloads the `rules.typ` the PDF ends with, uploads a replacement and
restores the original; an upload is kept as `state/typst/rules.typ` and every later run, from the web
or the `jgp` command, renders with it. The default orientation is not edited from the web; the
generation dialog opens on it and can choose another for each run. **Generate standings** opens the generation
dialog: the year, the page layout for this run, and, on a loopback address, **Choose tournaments for
this run**, which makes the run partial as `--tournaments` does and also unlocks the year. The
generation runs in the background, one at a time, with its progress and log in the dialog; a second
request is refused with a link to the one running, and a reload reopens a dialog whose generation is
still running. A finished run offers its PDF and the list of files. The run's records are kept in
`state/operations/`. The Separators tab holds one group of row numbers per age band of
the current year, each with its own Clear all, and a Clear all groups; nothing is stored until Save
separators. The Dispensations tab adds one player at a time through the player
search; a removal names the player, birth year and club. With `JGP_FIXTURES_DIR` set, it answers from a captured fixture set instead of the member
system.

Caddy fronts it as `http://jgp.localhost:5000`, following Flickornas. The workspace's shared
`../Caddyfile` already routes that address to port 8001; the project-local [Caddyfile](Caddyfile) does
the same for a checkout run on its own:

```
uv run jgp-web --debug
caddy_mercure run --config Caddyfile
```

The standalone Flask app serves the latest public standings directly on port 8001:

```sh
curl -fsS http://127.0.0.1:8001/api/v1/standings/jgp | jq .
```

Run `uv run jgp generate` to publish a fresh payload. The endpoint returns `404` until a full
generation has published a snapshot. Caddy is optional for this endpoint.

Caddy serves generated files itself, below `/downloads/` and `/reports/`, from `./output/runs`, or
from `JGP_DOWNLOAD_ROOT` when that is set; Flask only renders the two indexes, newest run first.
Available files lists each run's PDF and its standings and validation workbooks; Reports lists the
statistics workbook, the standings JSON and the diagnostics. A partial run's files are marked as
such, and the Typst source and rendering support are listed on neither. **Clear downloads** on
Available files, once confirmed, deletes every run under `output/runs/` and the
`public-standings.json` snapshot, leaving `state/` (the configuration, the run record and the
operation records) as it was; it is refused while a generation runs. The Caddyfile also
hosts the Mercure hub at `/.well-known/mercure`. With `MERCURE_JWT_SECRET` set to the hub's publisher
secret, the server publishes each step of a generation there and the dialog updates as it happens;
without it, or when the hub is down, the dialog asks the server every two seconds instead.
`JGP_FILES_PUBLIC_ORIGIN` (default `http://jgp.localhost:5000`) is the origin the dialog's PDF link
and the two indexes' file links use.

## Mounting in a host

A host, such as the Verktyg portal, mounts JGP in its own Flask application with
`jgp_standings.web.register(app, settings)`. `MANIFEST` gives the tool's `id` (`jgp`), `name` and default
`url_prefix` (`/tools/jgp`). Every page, API, static, help and status link follows the prefix.
`tests/test_web_hosting.py` is a minimal working host.

Required settings (registration raises `KeyError` without them):

- `STATE_DIRECTORY` and `OUTPUT_DIRECTORY`: the writable directories JGP owns (`state/`, holding the
  configuration, `typst/rules.typ` and `operations/`, and `output/`).
- `MEMBER_SYSTEM_FETCH`: the member-system fetch, for example `FixtureReplay(path)`.
- `FILES_PUBLIC_ORIGIN`: the origin the host's file server serves `output/runs/` from, at both
  `/downloads/` and `/reports/`. It may carry a path.

Optional settings:

- `BASE_TEMPLATE`: the host's shell; JGP fills its `title` and `content` blocks and appends to its
  `head` and `scripts` blocks.
- `LOCALE` (a callable returning `sv` or `en`) and `DEFAULT_LOCALE` (`sv`).
- `ACTOR`: a callable returning who acts; each operation record keeps it.
- `MUTATION_PROTECTION`: a callable; when it returns false, every JGP POST, PUT and DELETE gets `403`.
- `GENERATION_TOURNAMENT_SELECTION_AUTHORIZER`: whether the user may choose a run's tournaments.
  Without it, nobody may. Standalone grants it to a loopback `Host` header.
- `MERCURE_PUBLISHER`, with `MERCURE_PUBLIC_URL` for the browser. Topics are `https://stockholmsschack.se/jgp/operations/<id>`.
  Without a publisher, or when it fails, the dialog polls the operation's status.
- `MEMBER_SYSTEM_CLIENT` and `CLOCK`.

The host guards access to the whole prefix, CSRF and auditing; JGP has no login. The host's file
server serves the generated files itself, applying its own access check. Those files are for
operators. The public standings are the snapshot `read_public_snapshot(OUTPUT_DIRECTORY)` returns,
the JSON containing ordered `tournament_ids` and age-band `standings`, or `None` when nothing is
published (a `404`). A partial
run, one given its own tournaments, never replaces it, and Clear downloads removes it.

One generation runs at a time across processes. A file lock holds the lease, so a worker that dies
releases it; its unfinished operation is marked failed (`interrupted`) when JGP next starts or the
next generation is requested. Generation needs the
`typst` command on the `PATH`, as above.

Before accepting a JGP revision, the host runs, from the JGP tree, `uv run pytest -q`, which also
runs the Node checks in `tests/browser/`. `tests/test_web_hosting.py` and
`tests/test_fixture_equivalence.py` cover the mounted contract.

## Checking a tournament and entering dispensations

```
uv run jgp check-tournament 6544 [--print-names]
uv run jgp dispensation Anna Svensson 2010 2009
uv run jgp dispensation --file dispens.txt
```

`check-tournament` reads one tournament's groups with the standings' own klass parser and reports
every registered player whose birth year, or dispensation year from the configuration, falls outside
their klass. It writes `<tournament id>_playerinfo.json` to the working directory, and
`--print-names` adds the D, E and F player lists by birth year. A group name the standings would
refuse refuses the check, with every refused group listed, so run it as soon as an organiser
publishes the groups. A tournament without `JGP` in its name, or an upstream failure, exits with
status 1.

`dispensation` takes one entry, first name, last name, birth year and dispensation year, or a file of
such lines (`--file -` reads a pasted list from standard input, ending with Ctrl-D). Every line goes
through the same lookup: it is verified only when exactly one player of that name exists in the
member system, their birth year agrees and they were born after this year's oldest band (no
dispensation applies to a player in the oldest band), and is otherwise refused with its line number and the
reason. Verified entries are printed as `dispensation_dict` lines, each under a comment naming the
matched player and club; refusals go to standard
error, and any refusal makes the exit status 1. It reads no configuration and writes nothing; the
configuration page's Dispensations tab is where entries are stored.

## Tests

```
uv run pytest
```

The suite runs offline and pins the rules this document describes, so a change in behaviour shows up
as a failing test rather than as a quietly different standings table:

- `tests/test_klass_identification.py` — every accepted and refused example in `KLASS_NAMING.md`,
  that a refused name stops the run with every problem listed, and which classes reach the
  standings
- `tests/test_klass_bands.py` — the JGP and JDM birth-year bands and the validation that applies
  them, including dispensations and penalties
- `tests/test_points_allocation.py` — the standings points list, how it is consumed, and that the
  allocation does not depend on input row order
- `tests/test_eligibility_and_duplicates.py` — duplicate resolution, and that non-local players are
  removed before points are allocated

Where this document and the code disagreed, the code was taken as the specification and the document
corrected to match.
