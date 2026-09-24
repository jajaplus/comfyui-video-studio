#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip uv
.venv/bin/uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/uv pip install --python .venv/bin/python --prerelease=allow "sglang[diffusion]" comfy-kitchen

mkdir -p logs
echo "安装完成。下一步复制 .env.example 为 .env，修改令牌，然后运行 scripts/start_all.sh。"

