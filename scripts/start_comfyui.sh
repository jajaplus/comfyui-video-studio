#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

COMFY_DIR="${H3_COMFYUI_DIR:-/root/ComfyUI}"
if [[ ! -f "$COMFY_DIR/main.py" ]]; then
  echo "找不到 $COMFY_DIR/main.py。请使用 AutoDL 的 ComfyUI 应用镜像，或在 .env 设置 H3_COMFYUI_DIR。" >&2
  exit 1
fi

if [[ -n "${H3_COMFYUI_PYTHON:-}" ]]; then
  COMFY_PYTHON="$H3_COMFYUI_PYTHON"
elif [[ -x "$COMFY_DIR/venv/bin/python" ]]; then
  COMFY_PYTHON="$COMFY_DIR/venv/bin/python"
elif [[ -x /root/miniconda3/envs/comfyui/bin/python ]]; then
  COMFY_PYTHON=/root/miniconda3/envs/comfyui/bin/python
else
  COMFY_PYTHON=python3
fi

exec "$COMFY_PYTHON" "$COMFY_DIR/main.py" \
  --listen 127.0.0.1 \
  --port 8188 \
  --disable-auto-launch
