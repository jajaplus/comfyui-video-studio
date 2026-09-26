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

if curl -fsS --max-time 2 "${H3_COMFYUI_URL:-http://127.0.0.1:6008}/system_stats" >/dev/null 2>&1; then
  echo "ComfyUI 已在 ${H3_COMFYUI_URL:-http://127.0.0.1:6008} 运行。"
elif [[ -f logs/comfyui.pid ]] && kill -0 "$(cat logs/comfyui.pid)" 2>/dev/null; then
  echo "ComfyUI 进程正在启动。"
else
  nohup scripts/start_comfyui.sh > logs/comfyui.log 2>&1 &
  echo $! > logs/comfyui.pid
  echo "正在等待 ComfyUI 启动，日志：logs/comfyui.log"
  COMFY_READY=0
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 2 "${H3_COMFYUI_URL:-http://127.0.0.1:6008}/system_stats" >/dev/null 2>&1; then
      COMFY_READY=1
      break
    fi
    if ! kill -0 "$(cat logs/comfyui.pid)" 2>/dev/null; then
      echo "ComfyUI 启动失败，最近日志如下：" >&2
      tail -n 30 logs/comfyui.log >&2 || true
      exit 1
    fi
    sleep 1
  done
  if [[ "$COMFY_READY" = "1" ]]; then
    echo "ComfyUI 已启动。"
  else
    echo "ComfyUI 进程仍在加载；可运行 tail -f logs/comfyui.log 查看进度。"
  fi
fi

if [[ -f logs/app.pid ]] && kill -0 "$(cat logs/app.pid)" 2>/dev/null; then
  echo "网页服务已在运行。"
else
  nohup scripts/start_app.sh > logs/app.log 2>&1 &
  echo $! > logs/app.pid
  echo "网页服务已启动，日志：logs/app.log"
fi

echo "客户端监听 6006 端口，ComfyUI 监听 6008 端口。"
