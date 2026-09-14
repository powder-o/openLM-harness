#!/usr/bin/env bash
# Start the Study App: builds the frontend once, then serves API + UI on http://127.0.0.1:${STUDY_PORT:-8765}
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$ROOT/frontend/dist/index.html" ] || [ "${REBUILD_FRONTEND:-0}" = "1" ]; then
  echo "Building frontend..."
  (cd "$ROOT/frontend" && npm install && npm run build)
fi

cd "$ROOT/backend"
uv sync
echo "Study App on http://127.0.0.1:${STUDY_PORT:-8765}"
exec uv run uvicorn study.app:app --host 127.0.0.1 --port "${STUDY_PORT:-8765}"
