#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

COMFY_DIR="${H3_COMFYUI_DIR:-/root/autodl-tmp/ComfyUI}"
BASE_PYTHON="${H3_BASE_PYTHON:-python3}"
export UV_CACHE_DIR="${H3_UV_CACHE_DIR:-/root/autodl-tmp/.cache/uv}"
mkdir -p "$UV_CACHE_DIR"

if ! command -v git >/dev/null 2>&1; then
  echo "正在安装 git..."
  apt-get update
  apt-get install -y git
fi

if ! command -v "$BASE_PYTHON" >/dev/null 2>&1; then
  echo "找不到 Python：$BASE_PYTHON" >&2
  exit 1
fi

echo "正在检查基础镜像中的 PyTorch 和 CUDA 构建..."
"$BASE_PYTHON" - <<'PY'
import sys

try:
    import torch
    import torchvision
except Exception as exc:
    raise SystemExit(
        "基础镜像没有完整安装 PyTorch 和 torchvision，请重新选择 AutoDL 的 PyTorch 基础镜像。"
    ) from exc

parts = torch.__version__.split("+", 1)[0].split(".")
version = tuple(int("".join(ch for ch in part if ch.isdigit()) or 0) for part in parts[:2])
if version < (2, 7):
    raise SystemExit(
        f"当前 PyTorch 为 {torch.__version__}，ComfyUI 官方最低支持 2.7。"
        "请重新选择带 PyTorch 2.7 或更高版本的基础镜像。"
    )
if torch.version.cuda is None:
    raise SystemExit(
        "当前安装的是 CPU 版 PyTorch。请使用带 CUDA 版 PyTorch 的 AutoDL 基础镜像。"
    )

print(f"Python: {sys.version.split()[0]}")
print(f"PyTorch: {torch.__version__}")
print(f"torchvision: {torchvision.__version__}")
print(f"PyTorch CUDA 构建: {torch.version.cuda}")
if torch.cuda.is_available():
    print(f"当前 GPU: {torch.cuda.get_device_name(0)}")
else:
    print("当前为无 GPU 开机模式，继续安装依赖；切换到 GPU 模式后再启动服务。")
PY

if [[ -e "$COMFY_DIR" && ! -d "$COMFY_DIR/.git" ]]; then
  echo "$COMFY_DIR 已存在，但不是 ComfyUI Git 目录。请在 .env 中换一个 H3_COMFYUI_DIR。" >&2
  exit 1
fi

if [[ ! -d "$COMFY_DIR/.git" ]]; then
  echo "正在安装最新版 ComfyUI 到 $COMFY_DIR ..."
  git clone --depth 1 https://github.com/Comfy-Org/ComfyUI.git "$COMFY_DIR"
else
  echo "已找到 ComfyUI：$COMFY_DIR，保留当前版本。"
fi

if [[ ! -x "$COMFY_DIR/.venv/bin/python" ]]; then
  "$BASE_PYTHON" -m venv --system-site-packages "$COMFY_DIR/.venv"
fi

COMFY_PYTHON="$COMFY_DIR/.venv/bin/python"
"$COMFY_PYTHON" -m pip install --upgrade pip uv
FAST_REQUIREMENTS="$COMFY_DIR/.requirements-without-base-torch.txt"
"$BASE_PYTHON" - "$COMFY_DIR/requirements.txt" "$FAST_REQUIREMENTS" <<'PY'
import re
import sys
from pathlib import Path

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
skipped = {"torch", "torchvision", "torchaudio"}
lines = []
for line in source.read_text().splitlines():
    requirement = line.split("#", 1)[0].strip()
    match = re.match(r"^([A-Za-z0-9_.-]+)", requirement)
    if match and match.group(1).lower().replace("_", "-") in skipped:
        continue
    lines.append(line)
destination.write_text("\n".join(lines) + "\n")
PY

echo "正在快速安装 ComfyUI 依赖；复用基础镜像的 torch/torchvision，不重复下载 CUDA 大包。"
"$COMFY_DIR/.venv/bin/uv" pip install \
  --python "$COMFY_PYTHON" \
  -r "$FAST_REQUIREMENTS"

"$COMFY_PYTHON" - <<'PY'
import torch

if torch.version.cuda is None:
    raise SystemExit("ComfyUI 虚拟环境加载到了 CPU 版 PyTorch，安装已停止。")
print(f"ComfyUI 将使用 PyTorch {torch.__version__}，CUDA 构建 {torch.version.cuda}")
if torch.cuda.is_available():
    print(f"当前 GPU：{torch.cuda.get_device_name(0)}")
else:
    print("当前未挂载 GPU，依赖安装不受影响。")
PY

if ! grep -Rqs --exclude-dir=.git --exclude-dir=.venv \
  "MiniMaxH3ReferenceToVideo" "$COMFY_DIR/comfy" "$COMFY_DIR/comfy_extras"; then
  echo "当前 ComfyUI 中未找到 MiniMax-H3 原生节点。请更新 ComfyUI 后重新运行本脚本。" >&2
  exit 1
fi

mkdir -p "$COMFY_DIR/input" "$COMFY_DIR/output" "$COMFY_DIR/models"
echo "ComfyUI 安装完成：$COMFY_DIR"
echo "如果当前使用无 GPU 模式，请先关闭实例并切换到 GPU 模式，再运行 scripts/start_all.sh。"
