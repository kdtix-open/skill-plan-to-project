#!/usr/bin/env bash
# use-app-token.sh — FR #49: source this to export GH_TOKEN + COPILOT_GITHUB_TOKEN.
#
# Usage:
#   source scripts/use-app-token.sh <org>
#   # e.g. source scripts/use-app-token.sh kdtix-open
#
# Reads config from SDLCA_APP_ID + SDLCA_APP_PRIVATE_KEY_PATH (env or
# ~/.sdlca/app.conf), mints a fresh 1-hour installation token via
# `python3 -m scripts.mint_app_token`, and exports both env var names so
# consumers of either conventional name (plan-to-project skill uses
# GH_TOKEN; some Copilot workflows expect COPILOT_GITHUB_TOKEN) pick it up.
#
# This script is idempotent — re-sourcing refreshes the token.

set -uo pipefail

# shellcheck disable=SC2164
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [ "$#" -lt 1 ]; then
    echo "[use-app-token] Usage: source use-app-token.sh <org>" >&2
    echo "[use-app-token]   e.g. source use-app-token.sh kdtix-open" >&2
    return 1 2>/dev/null || exit 1
fi

ORG="$1"

# Resolve a Python runner — prefer the project's pyproject-bound tool if
# available, otherwise fall back to plain python3.  The mint helper has no
# third-party deps beyond PyJWT + cryptography which are usually already
# installed (the skill's setup docs call them out).
PYTHON="${PYTHON:-python3}"

cd "${PROJECT_DIR}"

ENV_OUTPUT="$(${PYTHON} -m scripts.mint_app_token "${ORG}" --format env)"
MINT_STATUS=$?

if [ "${MINT_STATUS}" -ne 0 ] || [ -z "${ENV_OUTPUT}" ]; then
    echo "[use-app-token] mint failed (exit ${MINT_STATUS}); env not exported" >&2
    return 1 2>/dev/null || exit 1
fi

# The mint tool's --format=env output is:
#   export GH_TOKEN=...
#   export COPILOT_GITHUB_TOKEN=...
#   # expires at <iso>
# Source it into the current shell (operator's shell when `source`d).
# shellcheck disable=SC1090
eval "${ENV_OUTPUT}"

EXPIRY_LINE="$(echo "${ENV_OUTPUT}" | grep -E '^# expires at' | head -1)"
echo "[use-app-token] exported GH_TOKEN + COPILOT_GITHUB_TOKEN for ${ORG}"
if [ -n "${EXPIRY_LINE}" ]; then
    echo "[use-app-token] ${EXPIRY_LINE#\# }"
fi
