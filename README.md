# Verktyg

Verktyg is StSF's umbrella Flask application. It provides a Swedish portal
landing page, public bookmarklets, and Google-protected JGP, Flickornas and
Senior GP standings tools. The shared shell bundles Bootstrap 5.3.8 CSS and
JavaScript. Public pages need no external services.

## Run locally

Requires Python 3.13 or newer and uv.

```sh
uv sync
uv run flask --app verktyg run --host 127.0.0.1 --port 6001 --debug
~/.local/bin/caddy_mercure run --config deploy/Caddyfile.local  # second terminal
```

Open `http://localhost:5000`. As in Playtime, Caddy serves the browser on port
5000 and proxies to Flask, here on 6001 as in production. The local Caddyfile
uses plain HTTP with automatic HTTPS off, so Caddy never requests a
certificate, and serves the Mercure hub at `/.well-known/mercure`, so
generation progress updates live as on the VPS. Stop Playtime's Caddy first:
both use port 5000.

`uv.lock` records the resolved dependencies, including the production and
development packages. `uv sync` installs the locked versions.

### Local development

`--debug` uses a fixed session key and plain-HTTP cookies and allows OAuth over
plain HTTP. CSRF protection stays on. Tool state and output go to the ignored
`instance/` directory; the portal copies JGP's and Flickornas's sample
`active-configuration.toml` there on first start and never overwrites it.
Senior GP has no sample and starts with no tournaments. The tools fetch live
member-system data.

Google sign-in stays on in debug runs. Verktyg shares Playtime's OAuth client,
whose only local callback is `http://localhost:5000/callback`, so sign in
through Caddy at `http://localhost:5000`, not `127.0.0.1` or port 6001. Before
the first run:

```sh
cp env.example .env  # then set GOOGLE_OAUTH_CLIENT_SECRET from Playtime's src/.env
```

`env.example` already points the portal at the local Mercure hub.

Like Playtime, the portal loads the git-ignored `.env` from the checkout at
startup; variables already set in the environment take precedence.

After signing in, users see the tools their Google groups allow (see
[Tool access](#tool-access)).

To skip sign-in, set the explicit flag, on the command line or in `.env`:

```sh
VERKTYG_REQUIRE_AUTH=false uv run flask --app verktyg run --host 127.0.0.1 --port 6001 --debug
```

Without sign-in, Caddy is optional; `http://127.0.0.1:6001` works too. The
portal then injects `DEV_USER_EMAIL` and `DEV_USER_NAME` as the identity,
with full tool access, and refuses requests that do not come from this computer.
Startup refuses `REQUIRE_AUTH = False` unless `DEBUG` is true, so production
always requires sign-in.

To populate uv's cache for offline work, run this outside the agent sandbox
with network access:

```sh
./scripts/setup-cache.sh
```

The script installs all dependency groups and production extras, builds the
packages, and verifies installation and packaging offline. It uses uv's normal
cache location, or `UV_CACHE_DIR` if you have set it.

## Routes

| Route | Purpose |
| --- | --- |
| `/` | Public portal landing page and future tool status |
| `/bookmarklets` | Public bookmarklet installation instructions |
| `/health` | Local readiness response: `{"status":"ok"}` |
| `/tools/jgp/`, `/tools/flickornas/`, `/tools/senior/` | Operator tools |
| `/files/<tool>/downloads/…`, `/files/<tool>/reports/…` | Protected generated files |
| `/api/v1/standings/jgp`, `/api/v1/standings/flickornas` | Public JSON snapshot, or 404 when unpublished |
| `/login`, `/callback` | Google sign-in, as in Playtime |
| `/logout` | Sign-out (POST with the CSRF token) |

Each bookmarklet is one Markdown file, `NN-<slug>.md`, in
`src/verktyg/bookmarklets/`: `10-avprickning.md` marks paid players present,
and `20-klubb-betalning.md` marks a chosen club's players as paid. A file holds
the tab and button texts in front matter, a `# sv` and an `# en` description,
and the source in a `# Code` block. `NN` sets the tab order; files without it
are ignored. `AUTHORING.md` there describes the format and how to add one;
start from `template.md`. Check files with the validator, which exits non-zero
on any problem:

```sh
uv run python -m verktyg.bookmarklets            # the directory
uv run python -m verktyg.bookmarklets FILE ...   # drafts
```

The portal reads the files at startup, so restart it after a change. It skips
an invalid file and logs why. The page builds a bookmark link from each file. Users drag it to the bookmarks
bar, or copy the code and paste it as a new bookmark's address. If copying
fails, the page shows the code selected for manual copying. Clicking the link
on the portal shows instructions; the saved bookmark runs on the avprickning
page.

## Checks and packaging

```sh
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
uv build
(cd tools/jgp && ../../.venv/bin/python -m pytest -q)
(cd tools/flickornas && ../../.venv/bin/python -m pytest -q)
(cd tools/senior && ../../.venv/bin/python -m pytest -q)
```

The last three commands run each tool's suite in the portal's environment. JGP and
Flickornas need Typst on `PATH`. All three need Node, which runs the tools'
browser-component checks.
Flickornas skips three Caddy/Mercure checks unless
`RUN_CADDY_MERCURE_INTEGRATION=1` is set with a local Caddy/Mercure running.

Templates, Bootstrap CSS and JavaScript, its license, and bookmarklet files are included in
the wheel. The Flask application factory is `verktyg.create_app`; the public
pages are registered through the `portal` Blueprint.

## VPS setup

[deploy/SETUP.md](deploy/SETUP.md) installs the portal on Playtime's VPS from a
git clone with `deploy/production_setup.sh`. Gunicorn serves it on
`127.0.0.1:6001` under systemd (`deploy/verktyg.service`), logging to the
journal. `Caddyfile.vps` is the VPS's shared Caddyfile with the
`verktyg.stockholmsschack.se` site, which proxies to Gunicorn and serves the
Mercure hub.

The setup guide also covers [safe updates and deployment gaps](deploy/SETUP.md#deploy-an-update)
and [switching the Git upstream for tool subtrees](deploy/SETUP.md#change-the-git-remote-for-tools).

## Tool integration status

JGP, Flickornas and Senior GP are uv workspace members. JGP mounts at
`/tools/jgp`, Flickornas at `/tools/flickornas` and Senior GP at
`/tools/senior`. `src/verktyg/tools.py` gives all three the same shared
shell, access guard, CSRF check, locale, actor, audit log and file serving.
JGP and Flickornas also get a public snapshot endpoint. Senior GP has no public
standings, only files that operators download. Startup refuses tool
directories that overlap.

JGP and Flickornas passed joint local validation in phases 1–4 of the
[implementation plan](_plans/portal-tool-integration.md). Phase 5 replaced the
interim sign-in with Playtime's Google sign-in and a Cloud Identity group check;
see [Authentication](_docs/authentication.md). Production tool activation waits
for the remaining Phase 5 checks. Phase 15 of Senior GP's
[own plan](tools/senior/_plans/senior-gp.md) records its portal acceptance.

All configuration comes from `.env` or the environment; [env.example](env.example)
lists the settings. Production requires `VERKTYG_SECRET_KEY`,
`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` and
`VERKTYG_DATA_DIRECTORY`.
Each tool keeps `<tool>/state` and `<tool>/output` in `DATA_DIRECTORY`.
Any setting can be given as a `VERKTYG_` variable. Lists and dictionaries use
JSON syntax, and `__` sets one key of a dictionary, such as
`VERKTYG_JGP_SETTINGS__STATE_DIRECTORY` to override a tool's directory. Keep a
persistent `SECRET_KEY` shared by all workers.

JGP and Flickornas need Typst on `PATH` and initialized active configuration.
Senior GP writes Excel workbooks, needs no Typst and starts each season with
no tournaments. Every tool needs separate writable state/output directories
outside the source tree. The systemd template
creates `/var/lib/verktyg` using `StateDirectory=verktyg`. Review fixture
configurations before using them with live data; preserve existing configuration
when starting the portal.

Caddy proxies generated-file requests to Flask, which serves each tool's
`output/runs` under `/files/<tool>/downloads/` and `/files/<tool>/reports/` with
access checks. `/api/v1/standings/jgp` and `/api/v1/standings/flickornas`
each serve only that tool's published JSON snapshot, or 404 when it is absent.
Partial generations preserve each snapshot; clearing downloads removes it.

Mutation requests require a session-bound CSRF token. The shared shell attaches
it to same-origin fetch requests. The portal logs each tool mutation's tool,
actor, endpoint, method and status; generation records also retain the actor and completion outcome.
Each tool runs one generation at a time across all workers, using a lease in
its state directory. A busy tool does not block the others. A new worker fails
the operation of a lease whose owner exited, then frees the lease.
Polling works without Mercure. With `MERCURE_JWT_SECRET` and
`MERCURE_HUB_URL` set, JGP and Flickornas also publish generation progress to
that hub, and the browser subscribes at `/.well-known/mercure` on the portal's
origin. Senior GP always polls.

The portal pins Polars to 1.38.1 following JGP ordering failures with 1.44.2.
All three tool suites pass with the shared resolution.
The portal generation tests use real Typst when installed and skip otherwise;
record skips explicitly when validating. Historical validation results are not
proof that the current checkout passes.

## Tool access

The tester and kansli accounts may use every tool and open the **Access** page
(`/access`) in the header. The page lists each tool with a field for the Google
groups, comma-separated, whose direct or nested members may use it. An empty
field closes the tool to everyone except those two accounts, which the page
does not show and cannot change.

Until the first save, JGP and Flickornas allow `uu@stockholmsschack.se` and
Senior GP allows `tu@stockholmsschack.se`. A save writes
`access.json` in `DATA_DIRECTORY` (or `ACCESS_FILE`), and the change is logged
with the editor's email. Each worker reads the file at startup and again
whenever it has been saved. The access decision is made at sign-in and lasts for
the 8-hour session, so a change applies to each user from their next sign-in.

## Other tools and subtree updates

Flickornas, JGP and Senior GP are included as git subtrees with full history.
To update a tool from its upstream branch:

```sh
scripts/update-tool.sh <jgp|flickornas|senior> <branch>
```

The [JGP](_docs/jgp-integration.md),
[Flickornas](_docs/flickornas-integration.md) and
[Senior GP](_docs/senior-integration.md) notes describe the subtree workflow.
Each tool remains independently runnable. Future tools must namespace their
Python modules before being installed in the shared process.

## References

- [Flask application factories](https://flask.palletsprojects.com/en/stable/patterns/appfactories/)
- [Gunicorn with Flask](https://flask.palletsprojects.com/en/stable/deploying/gunicorn/)
- [Bootstrap](https://getbootstrap.com/docs/5.3/getting-started/introduction/)
- [Caddy imports](https://caddyserver.com/docs/caddyfile/directives/import)
