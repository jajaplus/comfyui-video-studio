#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if ! command -v ffprobe >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ffmpeg
fi

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip uv
.venv/bin/uv pip install --python .venv/bin/python -r requirements.txt

mkdir -p logs
echo "网页依赖安装完成。下一步挂载 AutoDL 公共模型、复制 .env.example 为 .env，然后运行 scripts/start_all.sh。"
