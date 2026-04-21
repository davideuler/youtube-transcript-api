#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:8080}"
VIDEO_URL="${VIDEO_URL:-https://www.youtube.com/watch?v=GJLlxj_dtq8}"
LANGUAGES="${LANGUAGES:-en,zh}"

curl --fail-with-body --silent --show-error \
  "${BASE_URL}/healthz" | python3 -m json.tool

curl --fail-with-body --silent --show-error \
  -X POST "${BASE_URL}/api/v1/transcripts" \
  -H "Content-Type: application/json" \
  -d "$(printf '{"url":"%s","languages":"%s"}' "$VIDEO_URL" "$LANGUAGES")" \
  | python3 -m json.tool
