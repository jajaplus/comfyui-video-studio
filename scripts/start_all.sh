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

if [[ -f logs/h3.pid ]] && kill -0 "$(cat logs/h3.pid)" 2>/dev/null; then
  echo "H3 服务已在运行。"
else
  nohup scripts/start_h3.sh > logs/h3.log 2>&1 &
  echo $! > logs/h3.pid
  echo "H3 服务已启动，日志：logs/h3.log"
fi

if [[ -f logs/app.pid ]] && kill -0 "$(cat logs/app.pid)" 2>/dev/null; then
  echo "网页服务已在运行。"
else
  nohup scripts/start_app.sh > logs/app.log 2>&1 &
  echo $! > logs/app.pid
  echo "网页服务已启动，日志：logs/app.log"
fi

echo "网页监听 6006 端口。H3 首次下载模型并加载可能需要较长时间。"

