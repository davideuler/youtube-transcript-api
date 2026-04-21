#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${YTA_API_HOST:-0.0.0.0}"
PORT="${YTA_API_PORT:-8080}"
WORKERS="${YTA_GUNICORN_WORKERS:-2}"
TIMEOUT="${YTA_GUNICORN_TIMEOUT:-120}"

cd "${ROOT_DIR}"
uv sync --locked --extra dev

exec uv run gunicorn \
  --bind "${HOST}:${PORT}" \
  --workers "${WORKERS}" \
  --timeout "${TIMEOUT}" \
  "ytt_flask_api.app:app"
