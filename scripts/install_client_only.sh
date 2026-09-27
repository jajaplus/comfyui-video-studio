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
  echo "找不到 $COMFY_DIR/main.py。请复制 .env.autodl-comfyui.example 为 .env。" >&2
  exit 1
fi

if ! command -v ffprobe >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ffmpeg
fi

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip uv
.venv/bin/uv pip install --python .venv/bin/python -r requirements.txt

mkdir -p logs

if grep -Rqs --exclude-dir=.git --exclude-dir=.venv \
  "WanVaceToVideo" "$COMFY_DIR/comfy" "$COMFY_DIR/comfy_extras"; then
  echo "已检测到 WanVaceToVideo 原生节点。"
else
  echo "警告：当前 ComfyUI 未检测到 WanVaceToVideo。请按 README 的 ComfyUI 准备步骤更新或安装支持 VACE 的版本。" >&2
fi

echo "客户端依赖安装完成；已复用 $COMFY_DIR。"
