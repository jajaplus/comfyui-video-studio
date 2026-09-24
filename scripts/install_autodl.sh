#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if ! command -v ffprobe >/dev/null 2>&1 || ! command -v git >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ffmpeg git
fi

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip uv
.venv/bin/uv pip install --python .venv/bin/python -r requirements.txt

scripts/install_comfyui.sh

mkdir -p logs
echo "客户端和 ComfyUI 已安装完成。下一步挂载 AutoDL 公共模型，然后运行 scripts/start_all.sh。"
