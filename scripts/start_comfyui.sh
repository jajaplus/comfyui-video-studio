#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

if [[ -n "${OMP_NUM_THREADS:-}" && ! "$OMP_NUM_THREADS" =~ ^[1-9][0-9]*$ ]]; then
  echo "忽略无效的 OMP_NUM_THREADS=$OMP_NUM_THREADS"
  unset OMP_NUM_THREADS
fi

COMFY_DIR="${H3_COMFYUI_DIR:-/root/ComfyUI}"
if [[ ! -f "$COMFY_DIR/main.py" ]]; then
  echo "找不到 $COMFY_DIR/main.py。请按 README 第 2 节选择一种 ComfyUI 准备方式，并检查 .env 中的 H3_COMFYUI_DIR。" >&2
  exit 1
fi

if [[ -n "${H3_COMFYUI_PYTHON:-}" ]]; then
  COMFY_PYTHON="$H3_COMFYUI_PYTHON"
elif [[ -x "$COMFY_DIR/.venv/bin/python" ]]; then
  COMFY_PYTHON="$COMFY_DIR/.venv/bin/python"
elif [[ -x "$COMFY_DIR/venv/bin/python" ]]; then
  COMFY_PYTHON="$COMFY_DIR/venv/bin/python"
elif [[ -x /root/miniconda3/envs/comfyui/bin/python ]]; then
  COMFY_PYTHON=/root/miniconda3/envs/comfyui/bin/python
else
  COMFY_PYTHON=python3
fi

if ! "$COMFY_PYTHON" - <<'PY'
import torch

if not torch.cuda.is_available():
    raise SystemExit(1)
print(f"GPU 检查通过：{torch.cuda.get_device_name(0)}；PyTorch {torch.__version__}；CUDA {torch.version.cuda}")
PY
then
  echo "当前没有可用 GPU。请先关闭 AutoDL 无卡实例，切换为 GPU 模式开机，再启动 ComfyUI。" >&2
  exit 1
fi

exec "$COMFY_PYTHON" "$COMFY_DIR/main.py" \
  --listen 0.0.0.0 \
  --port 6008 \
  --disable-auto-launch
