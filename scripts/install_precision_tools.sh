#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

if ! command -v git >/dev/null 2>&1 || ! command -v ffmpeg >/dev/null 2>&1; then
  apt-get update
  apt-get install -y git ffmpeg
fi

TOOLS_DIR="${H3_PRECISION_TOOLS_DIR:-/root/autodl-tmp/h3-precision-tools}"
SAM2_DIR="$TOOLS_DIR/sam2"
SAM2_ENV="$TOOLS_DIR/sam2-env"
FACEFUSION_DIR="$TOOLS_DIR/facefusion"
FACEFUSION_ENV="$TOOLS_DIR/facefusion-env"
FACEFUSION_VERSION="${H3_FACEFUSION_VERSION:-3.9.0}"
SAM2_CHECKPOINT="${H3_SAM2_CHECKPOINT:-$TOOLS_DIR/models/sam2.1_hiera_small.pt}"

mkdir -p "$TOOLS_DIR"

COMFY_DIR="${H3_COMFYUI_DIR:-/root/ComfyUI}"
if [[ -n "${H3_COMFYUI_PYTHON:-}" && -x "${H3_COMFYUI_PYTHON}" ]]; then
  BASE_PYTHON="$H3_COMFYUI_PYTHON"
elif [ -x "$COMFY_DIR/.venv/bin/python" ]; then
  BASE_PYTHON="$COMFY_DIR/.venv/bin/python"
elif [ -x "$COMFY_DIR/venv/bin/python" ]; then
  BASE_PYTHON="$COMFY_DIR/venv/bin/python"
elif [ -x /root/miniconda3/envs/comfyui/bin/python ]; then
  BASE_PYTHON=/root/miniconda3/envs/comfyui/bin/python
else
  BASE_PYTHON=python3
fi

"$BASE_PYTHON" -c "import torch; print('复用 PyTorch', torch.__version__, 'CUDA', torch.version.cuda)"

if [ ! -d "$SAM2_DIR/.git" ]; then
  git clone --depth 1 https://github.com/facebookresearch/sam2.git "$SAM2_DIR"
else
  git -C "$SAM2_DIR" pull --ff-only
fi

if [ ! -x "$SAM2_ENV/bin/python" ]; then
  "$BASE_PYTHON" -m venv --system-site-packages "$SAM2_ENV"
fi
"$SAM2_ENV/bin/python" -m pip install --upgrade pip
"$SAM2_ENV/bin/python" -m pip install -e "$SAM2_DIR" opencv-python-headless huggingface_hub
if [ -f "$SAM2_CHECKPOINT" ]; then
  echo "复用 SAM2 权重：$SAM2_CHECKPOINT"
else
  mkdir -p "$(dirname "$SAM2_CHECKPOINT")"
  SAM2_TARGET_DIR="$(dirname "$SAM2_CHECKPOINT")"
  SAM2_LOCAL_DIR="$SAM2_TARGET_DIR" SAM2_TARGET_PATH="$SAM2_CHECKPOINT" "$SAM2_ENV/bin/python" -c "import os, shutil; from pathlib import Path; from huggingface_hub import hf_hub_download; downloaded=Path(hf_hub_download('facebook/sam2.1-hiera-small', 'sam2.1_hiera_small.pt', local_dir=os.environ['SAM2_LOCAL_DIR'])); target=Path(os.environ['SAM2_TARGET_PATH']); shutil.copy2(downloaded, target) if downloaded.resolve() != target.resolve() else None"
fi

if [ ! -d "$FACEFUSION_DIR/.git" ]; then
  git clone --depth 1 https://github.com/facefusion/facefusion.git "$FACEFUSION_DIR"
else
  echo "复用 FaceFusion 源码：$FACEFUSION_DIR"
fi
git -C "$FACEFUSION_DIR" fetch --tags --force
git -C "$FACEFUSION_DIR" checkout --force "$FACEFUSION_VERSION"

CONDA_BIN=/root/miniconda3/bin/conda
if [ ! -x "$CONDA_BIN" ]; then
  echo "未找到 /root/miniconda3/bin/conda，无法安装 FaceFusion。" >&2
  exit 1
fi
if [ ! -x "$FACEFUSION_ENV/bin/python" ]; then
  "$CONDA_BIN" create -y -p "$FACEFUSION_ENV" python=3.12 pip=25.0
fi
(
  cd "$FACEFUSION_DIR"
  "$CONDA_BIN" run --no-capture-output \
    -p "$FACEFUSION_ENV" \
    python install.py cuda@12
)

PREFETCH_ARGUMENTS=(
  --facefusion-dir "$FACEFUSION_DIR"
)
if [[ -n "${H3_FACEFUSION_MODELS_DIR:-}" ]]; then
  PREFETCH_ARGUMENTS+=(--public-models-dir "$H3_FACEFUSION_MODELS_DIR")
fi
"$FACEFUSION_ENV/bin/python" "$PROJECT_DIR/scripts/prefetch_facefusion_models.py" "${PREFETCH_ARGUMENTS[@]}"

echo "SAM2 和 FaceFusion 已安装到 $TOOLS_DIR"
