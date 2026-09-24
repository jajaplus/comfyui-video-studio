#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
mkdir -p logs

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

if [[ -f logs/comfyui.pid ]] && kill -0 "$(cat logs/comfyui.pid)" 2>/dev/null; then
  echo "ComfyUI 已在运行。"
else
  nohup scripts/start_comfyui.sh > logs/comfyui.log 2>&1 &
  echo $! > logs/comfyui.pid
  echo "ComfyUI 已启动，日志：logs/comfyui.log"
fi

if [[ -f logs/app.pid ]] && kill -0 "$(cat logs/app.pid)" 2>/dev/null; then
  echo "网页服务已在运行。"
else
  nohup scripts/start_app.sh > logs/app.log 2>&1 &
  echo $! > logs/app.pid
  echo "网页服务已启动，日志：logs/app.log"
fi

echo "网页监听 6006 端口，ComfyUI 仅监听本机 8188 端口。"
