#!/bin/bash
# Install or update Verktyg on the VPS from a git clone; see deploy/SETUP.md.
# Safe to rerun: it never overwrites .env or tool state, and restarts the service.
set -euo pipefail

APP_NAME="verktyg"
APP_DIR="/opt/${APP_NAME}"
DATA_DIR="/var/lib/${APP_NAME}"
ENV_FILE="${APP_DIR}/.env"
# Shared with Playtime, as created by its production_setup.sh.
UV_CACHE="/var/cache/uv"
UV_PYTHON_DIR="/opt/uv-python"
UV_PYTHON_VERSION="3.13"
UV_GROUP="uv-users"
# The repository root: this script lives in its deploy/ directory.
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Setting up ${APP_NAME} from ${SOURCE_DIR}..."

# --- Prerequisites ---
for command in uv rsync; do
    if ! command -v "${command}" &>/dev/null; then
        echo "Install ${command} first." >&2
        exit 1
    fi
done
# The service's PATH is /usr/local/bin:/usr/bin:/bin; see SETUP.md.
if ! PATH=/usr/local/bin:/usr/bin:/bin command -v typst &>/dev/null; then
    echo "Install typst in /usr/local/bin first; JGP and Flickornas need it." >&2
    exit 1
fi

# --- Shared uv cache and Python (idempotent, safe for multiple apps) ---
if ! getent group "${UV_GROUP}" &>/dev/null; then
    sudo groupadd --system "${UV_GROUP}"
fi
sudo install -d -o root -g "${UV_GROUP}" -m 2775 "${UV_CACHE}"
# World-traversable so the venv's python symlink resolves for the app user.
sudo install -d -o root -g root -m 755 "${UV_PYTHON_DIR}"

# --- App group and user ---
if ! getent group "${APP_NAME}" &>/dev/null; then
    sudo groupadd --system "${APP_NAME}"
fi
if ! id "${APP_NAME}" &>/dev/null; then
    sudo useradd --system \
        --shell /bin/false \
        --home-dir "${APP_DIR}" \
        --no-create-home \
        --gid "${APP_NAME}" \
        "${APP_NAME}"
fi

# --- Directory structure ---
# Root owns the code and venv; the verktyg group can read and execute them.
sudo install -d -o root -g "${APP_NAME}" -m 750 "${APP_DIR}"
# verktyg owns only its data: each tool's state and output.
sudo install -d -o "${APP_NAME}" -g "${APP_NAME}" -m 750 "${DATA_DIR}"

# JGP and Flickornas need an active configuration. Seed the checked-in
# samples once; never overwrite a configuration operators have edited.
for tool in jgp flickornas; do
    state="${DATA_DIR}/${tool}/state"
    sudo install -d -o "${APP_NAME}" -g "${APP_NAME}" -m 750 \
        "${DATA_DIR}/${tool}" "${state}"
    if ! sudo test -f "${state}/active-configuration.toml"; then
        echo "Seeding ${tool} sample configuration..."
        sudo install -o "${APP_NAME}" -g "${APP_NAME}" -m 640 \
            "${SOURCE_DIR}/tools/${tool}/state/active-configuration.toml" "${state}/"
    fi
done

# --- Copy application files ---
# Tools stay editable workspace members: their Typst assets and sample
# configuration are read from this tree at runtime.
echo "Copying application files..."
sudo rsync -a \
    --chown=root:"${APP_NAME}" \
    --chmod=D750,F640 \
    --exclude='.git' \
    --exclude='.env' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.egg-info' \
    --exclude='.pytest_cache' \
    --exclude='.ruff_cache' \
    --exclude='build/' \
    --exclude='dist/' \
    --exclude='instance/' \
    --exclude='tests/' \
    "${SOURCE_DIR}/" "${APP_DIR}/"

# --- Virtual environment and dependencies ---
# The venv is the project's own .venv in ${APP_DIR}. The UV_* variables matter
# only while installing: Python lands in the shared, world-readable directory.
echo "Installing dependencies..."
sudo UV_CACHE_DIR="${UV_CACHE}" \
    UV_PYTHON_INSTALL_DIR="${UV_PYTHON_DIR}" \
    uv sync --frozen --no-dev --extra production \
    --python "${UV_PYTHON_VERSION}" \
    --project "${APP_DIR}"

# uv writes as root:root; give the verktyg group read and execute.
sudo chgrp -R "${APP_NAME}" "${APP_DIR}/.venv"
sudo chmod -R g+rX,g-w "${APP_DIR}/.venv"
sudo chmod -R a+rX "${UV_PYTHON_DIR}"

# --- Production .env ---
# Created once with generated secrets; fill in the rest by hand (SETUP.md).
if ! sudo test -f "${ENV_FILE}"; then
    echo "Creating ${ENV_FILE}..."
    client_id="$(grep '^GOOGLE_OAUTH_CLIENT_ID=' "${SOURCE_DIR}/env.example")"
    secret_key="$(head -c 32 /dev/urandom | base64 | tr -d '=+/\n')"
    sudo install -o root -g "${APP_NAME}" -m 640 /dev/null "${ENV_FILE}"
    sudo tee "${ENV_FILE}" >/dev/null <<EOF
${client_id}
# The same secret as GOOGLE_OAUTH_CLIENT_SECRET in Playtime's production .env.
GOOGLE_OAUTH_CLIENT_SECRET=

VERKTYG_SECRET_KEY=${secret_key}
VERKTYG_DATA_DIRECTORY=${DATA_DIR}

# Verktyg's internal Mercure address in Caddy; the secret is publisher_jwt in /etc/caddy/Caddyfile.
VERKTYG_MERCURE_HUB_URL=http://localhost:6002/.well-known/mercure
VERKTYG_MERCURE_JWT_SECRET=
EOF
fi

# --- Systemd service ---
echo "Installing systemd service..."
sudo install -o root -g root -m 644 "${SOURCE_DIR}/deploy/${APP_NAME}.service" \
    "/etc/systemd/system/${APP_NAME}.service"
sudo systemctl daemon-reload
sudo systemctl enable "${APP_NAME}.service"

# --- Start, once .env is complete ---
missing=()
for name in GOOGLE_OAUTH_CLIENT_ID GOOGLE_OAUTH_CLIENT_SECRET VERKTYG_SECRET_KEY \
    VERKTYG_DATA_DIRECTORY VERKTYG_MERCURE_HUB_URL VERKTYG_MERCURE_JWT_SECRET; do
    if ! sudo grep -Eq "^${name}=.+" "${ENV_FILE}"; then
        missing+=("${name}")
    fi
done
if (( ${#missing[@]} )); then
    echo ""
    echo "Set ${missing[*]} in ${ENV_FILE} (sudoedit ${ENV_FILE}),"
    echo "then rerun this script to start ${APP_NAME}."
    exit 1
fi

sudo systemctl restart "${APP_NAME}"
echo ""
echo "Setup complete!"
echo ""
sudo systemctl status "${APP_NAME}" --no-pager || true
echo ""
echo "Follow logs with:  sudo journalctl -u ${APP_NAME} -f"
