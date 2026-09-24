#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

export H3_STUDIO_DATA_DIR="${H3_STUDIO_DATA_DIR:-/root/autodl-tmp/h3-studio-data}"
export H3_COMFYUI_URL="${H3_COMFYUI_URL:-http://127.0.0.1:8188}"
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 6006 --workers 1
