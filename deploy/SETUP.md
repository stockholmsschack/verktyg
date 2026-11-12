# VPS setup

Verktyg runs on the same VPS as Playtime, behind the same Caddy, and follows
Playtime's layout. One difference: the virtual environment is the project's own
`.venv` inside the application directory, not a separate `-env` directory.

| What | Where |
| --- | --- |
| Service account | `verktyg` (system user, no login shell) |
| Application code | `/opt/verktyg` (root-owned, group `verktyg` reads) |
| Virtual environment | `/opt/verktyg/.venv` |
| Configuration | `/opt/verktyg/.env` (root-owned, mode 640) |
| Tool state and output | `/var/lib/verktyg` (the only persistent writable directory tree) |
| Service | `verktyg.service`, Gunicorn on `127.0.0.1:6001` |
| Logs | the journal: `sudo journalctl -u verktyg -f` |
| Python and uv cache | `/opt/uv-python` and `/var/cache/uv`, shared with Playtime |

All three tools run as the `verktyg` account and can create files and
subdirectories inside `/var/lib/verktyg`. Setup sets
`VERKTYG_DATA_DIRECTORY=/var/lib/verktyg`; the portal then assigns each tool
separate state and output directories:

```text
/var/lib/verktyg/
├── access.json
├── jgp/
│   ├── state/
│   └── output/
├── flickornas/
│   ├── state/
│   └── output/
└── senior/
    ├── state/
    └── output/
```

Each tool writes generated files beneath its own `output/` directory, and
the portal serves downloads from there. Code and templates remain read-only
under `/opt/verktyg`. The service also has private temporary storage through
`PrivateTmp=true`; persistent state and results belong in `/var/lib/verktyg`.

## Before you start

- Playtime is installed with its `production_setup.sh`, so uv, the shared uv
  directories and Caddy with the Mercure module are in place.
- Check `sudo uv --version` as well as `uv --version`: setup runs uv through
  sudo, whose PATH may not include a user-local installation. Install `rsync`
  and confirm ports 6001 and 6002 are free before the first deployment.
- DNS for `verktyg.stockholmsschack.se` points at the VPS.
- `https://verktyg.stockholmsschack.se/callback` is an authorized redirect URI
  of the Google OAuth client that Verktyg shares with Playtime.
- Typst is in `/usr/local/bin`; JGP and Flickornas render PDFs with it. The
  tools are tested with Typst 0.15.1. On an x86-64 VPS:

  ```sh
  curl -fsSLO https://github.com/typst/typst/releases/download/v0.15.1/typst-x86_64-unknown-linux-musl.tar.xz
  tar -xf typst-x86_64-unknown-linux-musl.tar.xz
  sudo install -m 755 typst-x86_64-unknown-linux-musl/typst /usr/local/bin/typst
  typst --version
  ```

## Install

1. Clone the repository into your home directory:

   ```sh
   git clone git@github.com:bganesh/verktyg.git ~/verktyg
   ```

2. Run the setup script:

   ```sh
   ~/verktyg/deploy/production_setup.sh
   ```

   It creates the account and directories, copies the code to `/opt/verktyg`,
   installs the locked production dependencies, seeds JGP's and Flickornas's
   sample `active-configuration.toml` into `/var/lib/verktyg`, and installs and
   enables the service. On the first run it also creates `/opt/verktyg/.env`
   with a generated `VERKTYG_SECRET_KEY`. It then stops and lists the settings
   still missing.

3. Fill in the two secrets:

   ```sh
   sudoedit /opt/verktyg/.env
   ```

   - `GOOGLE_OAUTH_CLIENT_SECRET`: the same value as in Playtime's production
     `.env`.
   - `VERKTYG_MERCURE_JWT_SECRET`: the `publisher_jwt` key in
     `/etc/caddy/Caddyfile`.

4. Run the script again. With `.env` complete, it starts the service.

### The `verktyg` account

The setup script creates the account if it is missing. To create it by hand,
for example before the first run, use the same commands:

```sh
sudo groupadd --system verktyg
sudo useradd --system --shell /bin/false --home-dir /opt/verktyg \
    --no-create-home --gid verktyg verktyg
```

It is a system account with no login shell and no home directory of its own.
It needs no other groups: root installs the code and runs `uv sync`, so
`verktyg` reads `/opt/verktyg` through its group and writes persistent state
and output beneath `/var/lib/verktyg`. Check it with:

```sh
id verktyg
getent passwd verktyg
```

## Caddy

`Caddyfile.vps` is a checked-in snapshot of the shared VPS configuration with
two Verktyg blocks. Compare it with the live configuration before installing;
the repository cannot establish whether other sites have changed on the VPS.

- `verktyg.stockholmsschack.se` proxies to Gunicorn and serves the Mercure hub
  at `/.well-known/mercure`, where browsers subscribe to generation progress.
- `http://localhost:6002` is where the portal publishes. Only Verktyg uses it.

Site blocks with the same Mercure settings share one hub, so updates published
on one reach subscribers on the other. Verktyg does not publish through the
existing `http://localhost` block, which Playtime may use: its `handle_path`
strips `/.well-known/mercure` before Mercure sees the request, so Caddy answers
publishes there with an empty 200 and drops them.

Install it, after checking that port 6002 is free and comparing the file with
the live one. The diff should show only the two Verktyg blocks:

```sh
ss -ltn | grep ':6002 '
sudo diff /etc/caddy/Caddyfile ~/verktyg/Caddyfile.vps
sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak
sudo install -m 644 ~/verktyg/Caddyfile.vps /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

If validation fails, restore `Caddyfile.bak` before reloading.
Use the same Mercure-enabled Caddy executable that the running service uses;
the standard binary cannot validate the `mercure` directive. Confirm port
6002 is reachable only locally, using the listener addresses and VPS firewall.
The `localhost` site name alone is not evidence of a loopback-only listener.

Replace the example Mercure JWT key in both Verktyg blocks and set the matching
`VERKTYG_MERCURE_JWT_SECRET` in `/opt/verktyg/.env`. Keep the two blocks'
Mercure settings identical. Coordinate any changes to Playtime's key with its
configuration too.

## Check the deployment

```sh
sudo systemctl status verktyg --no-pager
sudo systemctl is-active --quiet verktyg
curl --fail --show-error --silent http://127.0.0.1:6001/health
curl --fail --show-error --silent https://verktyg.stockholmsschack.se/health
```

Then sign in at `https://verktyg.stockholmsschack.se` with the kansli or tester
account. On `/access`, set the Google groups for each tool. Review the seeded
JGP and Flickornas configurations before generating with live data, then run a
generation: its progress should update without reloading the page.
Also sign in with a regular group member: the kansli and tester accounts bypass
group checks and cannot verify Cloud Identity access. Check PDF generation in
JGP and Flickornas and workbook generation in Senior GP, then download the
results. `/health` confirms the app responds; it does not test Google, upstream
data services, Typst, or Mercure. Polling can hide a broken Mercure connection,
so inspect the browser's event stream and journal when checking live updates.

## Manage the service

```sh
sudo systemctl restart verktyg
sudo systemctl stop verktyg
sudo journalctl -u verktyg -f
```

## Deploy an update

Use a clean checkout containing the reviewed portal commit and tool subtree
changes. Record the currently deployed commit when deploying each release.
Wait for active generations to finish and arrange a maintenance window.
The setup script overwrites code and syncs the live virtual environment before
restarting Gunicorn, so stop the service yourself before running it:

```sh
git -C ~/verktyg pull --ff-only
git -C ~/verktyg status --short
git -C ~/verktyg rev-parse HEAD
sudo systemctl stop verktyg
# Use a new backup directory for each deployment; this fails if it already exists.
sudo install -d -m 700 /var/backups/verktyg
sudo mkdir -m 700 /var/backups/verktyg/pre-update
sudo cp -a /opt/verktyg/.env /var/backups/verktyg/pre-update/env
sudo cp -a /var/lib/verktyg /var/backups/verktyg/pre-update/data
~/verktyg/deploy/production_setup.sh
```

Run the deployment checks above afterward. The script preserves `.env` and
tool state, but does not back them up. If installation fails, leave the service
stopped until code and dependencies are consistent. There is no automatic
rollback. To revert, use the recorded previous commit in a clean checkout and
rerun setup; review state compatibility before restoring data from backup.

## Change the Git remote for tools

`tools/` is part of the Verktyg Git repository. Its `jgp`, `flickornas`, and
`senior` directories are separate Git subtrees, not submodules or nested clones.
They have no individual `origin`. Deploying Verktyg copies the versions already
committed in those directories and installs them as uv workspace members; the
VPS does not fetch their upstream repositories.

Change upstreams in a development checkout, then commit and deploy the resulting
Verktyg revision. From the repository root, with a clean working tree, this
example switches JGP updates to a fork that retains the existing Git history.
Replace the URL and `main` with your repository and branch:

```sh
git remote add jgp-upstream git@github.com:YOUR-ORG/stsf_jgp_standings.git
git fetch jgp-upstream
git subtree pull --prefix=tools/jgp jgp-upstream main \
    -m "Update JGP from the new upstream"
```

If that remote name already exists, use
`git remote set-url jgp-upstream git@github.com:YOUR-ORG/stsf_jgp_standings.git`
before fetching. Repeat with `tools/flickornas` or `tools/senior` and a separate
remote name as needed. Preserve full history, matching the existing subtree
imports; do not add `--squash`. Resolve any merge conflicts and run the portal
and affected tool checks from [README.md](../README.md#checks-and-packaging).
If dependency declarations changed, run `uv lock` and commit `uv.lock` along
with the subtree update before deploying with `--frozen`.

**Future updates:** `scripts/update-tool.sh` hardcodes the
`git@github.com:bganesh/` URLs and does not consult these remotes. Continue using
the explicit `git subtree pull` command above, or edit the script's repository
mapping and URL construction and commit that change so all checkouts use the
new source. Git remote configuration is local to each clone; a fresh clone
needs the `git remote add` command again. Changing the portal's own `origin`
does not change the tool URLs used by the script.

Keep the existing `tools/<tool>` paths and Python package names when switching
upstreams: `pyproject.toml`, `uv.lock`, portal imports and sample configuration
seeding depend on them. A repository containing all of `tools/` is not a drop-in
upstream for an individual subtree, whose repository root must contain that
tool's files. An unrelated replacement repository needs a separate migration;
do not force it into the existing subtree history.

## Deployment gaps compared with Playtime

This comparison is based on the checked-in scripts in Verktyg and `../playtime`,
not an inspection of the live VPS. These limitations remain in the scripts:

| Gap or difference | Deployment consequence and action |
| --- | --- |
| Verktyg does not stop the service before copying code or syncing dependencies; Playtime's update scripts do. | Requests and background jobs can see mixed versions. Use the stopped-service update procedure above. |
| Verktyg ends with `systemctl status ... || true` and no HTTP readiness check. | Setup can report completion despite failed workers. Run both health checks and inspect the journal. |
| Both use rsync without deletion. | Removed modules, templates and bookmarklets can remain deployed. Review removed files between releases and remove stale deployed files deliberately; rollback also requires this review. Do not apply an unrestricted deletion to the app directory containing `.env` and `.venv`. |
| Neither workflow provides a complete backup and rollback procedure in its scripts. | Preserve production configuration and tool state before deployment and record the deployed revision. |
| Playtime's `setup/` scripts use their own directory as the application source; its guide's manual update also copies `~/playtime/setup/`. | Running those scripts in their checked-in location misses the application root. Verktyg correctly resolves the parent of `deploy/`; keep that layout. Playtime also has an older root-level update script with different environment and venv assumptions. |
| Playtime uses `/opt/playtime-env`, file logs, gevent and MariaDB; Verktyg uses `.venv`, the journal and filesystem state. | Use Verktyg's service unit. Its static and protected files go through Flask, so Caddy does not need membership in the `verktyg` group. |
| Setup checks only that required `.env` values have text. | Incorrect secrets, quoted empty values or inappropriate overrides can still pass. Verify actual sign-in, group access and generation; preserve production authentication settings. |
| `StateDirectory=verktyg` supplies the persistent writable directory. | Moving `VERKTYG_DATA_DIRECTORY`, `ACCESS_FILE` or tool paths outside `/var/lib/verktyg` also requires ownership and systemd write permissions. An environment change alone is insufficient. |
| The shared Caddyfile and Mercure key are examples requiring live comparison. | Blind replacement can disrupt other sites; an unchanged example key permits unauthorized publication. Merge and validate the intended blocks, then verify publish and subscribe behavior. |

The setup script requires Mercure settings even though the application supports
polling without Mercure. Supply the documented hub and key for this deployment
workflow. Production acceptance still requires the live sign-in and generation
checks above; successful dependency installation is not enough.
