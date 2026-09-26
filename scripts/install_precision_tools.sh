#!/usr/bin/env bash
set -euo pipefail

FORCE_INSTALL=0
UPDATE_SOURCES="${H3_UPDATE_PRECISION_TOOLS:-0}"
for argument in "$@"; do
  case "$argument" in
    --force) FORCE_INSTALL=1 ;;
    --update-sources) UPDATE_SOURCES=1 ;;
    *) echo "未知参数：$argument（支持 --force、--update-sources）" >&2; exit 2 ;;
  esac
done

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
FLORENCE2_MODEL="${H3_FLORENCE2_MODEL:-$TOOLS_DIR/models/Florence-2-base-ft}"
FLORENCE2_MODEL_ID="${H3_FLORENCE2_MODEL_ID:-microsoft/Florence-2-base-ft}"
FLORENCE2_PUBLIC_PATH="${H3_FLORENCE2_PUBLIC_PATH:-}"

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
elif [ "$UPDATE_SOURCES" = "1" ]; then
  git -C "$SAM2_DIR" pull --ff-only
else
  echo "复用 SAM2 源码：$SAM2_DIR"
fi

if [ ! -x "$SAM2_ENV/bin/python" ]; then
  "$BASE_PYTHON" -m venv --system-site-packages "$SAM2_ENV"
fi
if [ "$FORCE_INSTALL" = "1" ] || ! "$SAM2_ENV/bin/python" -c \
  "import cv2, einops, huggingface_hub, sam2, timm, transformers; assert transformers.__version__ == '4.49.0'" \
  >/dev/null 2>&1; then
  echo "安装 SAM2 与 Florence-2 Python 依赖（仅首次安装或环境不完整时执行）"
  "$SAM2_ENV/bin/python" -m pip install --upgrade pip
  "$SAM2_ENV/bin/python" -m pip install \
    -e "$SAM2_DIR" \
    opencv-python-headless \
    huggingface_hub \
    'transformers==4.49.0' \
    timm \
    einops
else
  echo "复用 SAM2 与 Florence-2 Python 环境：$SAM2_ENV"
fi
if [ -f "$SAM2_CHECKPOINT" ]; then
  echo "复用 SAM2 权重：$SAM2_CHECKPOINT"
else
  mkdir -p "$(dirname "$SAM2_CHECKPOINT")"
  SAM2_TARGET_DIR="$(dirname "$SAM2_CHECKPOINT")"
  SAM2_LOCAL_DIR="$SAM2_TARGET_DIR" SAM2_TARGET_PATH="$SAM2_CHECKPOINT" "$SAM2_ENV/bin/python" -c "import os, shutil; from pathlib import Path; from huggingface_hub import hf_hub_download; downloaded=Path(hf_hub_download('facebook/sam2.1-hiera-small', 'sam2.1_hiera_small.pt', local_dir=os.environ['SAM2_LOCAL_DIR'])); target=Path(os.environ['SAM2_TARGET_PATH']); shutil.copy2(downloaded, target) if downloaded.resolve() != target.resolve() else None"
fi

if [[ -n "$FLORENCE2_PUBLIC_PATH" ]]; then
  if [ ! -e "$FLORENCE2_PUBLIC_PATH" ]; then
    echo "Florence-2 公共模型路径不存在：$FLORENCE2_PUBLIC_PATH" >&2
    exit 1
  fi
  mkdir -p "$FLORENCE2_MODEL"
  if [ -d "$FLORENCE2_PUBLIC_PATH" ]; then
    while IFS= read -r -d '' item; do
      ln -sfn "$item" "$FLORENCE2_MODEL/$(basename "$item")"
    done < <(find "$FLORENCE2_PUBLIC_PATH" -mindepth 1 -maxdepth 1 \( -type f -o -type l \) -print0)
  else
    ln -sfn "$FLORENCE2_PUBLIC_PATH" "$FLORENCE2_MODEL/model.safetensors"
  fi
  echo "复用 Florence-2 公共模型：$FLORENCE2_PUBLIC_PATH"
fi

if [ -f "$FLORENCE2_MODEL/config.json" ] && [ -f "$FLORENCE2_MODEL/model.safetensors" ]; then
  echo "复用 Florence-2 商品识别模型：$FLORENCE2_MODEL"
else
  mkdir -p "$FLORENCE2_MODEL"
  FLORENCE2_LOCAL_DIR="$FLORENCE2_MODEL" \
  FLORENCE2_REPO_ID="$FLORENCE2_MODEL_ID" \
  "$SAM2_ENV/bin/python" -c "import os; from pathlib import Path; from huggingface_hub import snapshot_download; target=Path(os.environ['FLORENCE2_LOCAL_DIR']); patterns=['*.json', '*.txt', '*.py', '*.model']; patterns += [] if (target / 'model.safetensors').is_file() else ['*.safetensors']; snapshot_download(repo_id=os.environ['FLORENCE2_REPO_ID'], local_dir=target, allow_patterns=patterns)"
fi

if [ ! -d "$FACEFUSION_DIR/.git" ]; then
  git clone --depth 1 --branch "$FACEFUSION_VERSION" \
    https://github.com/facefusion/facefusion.git "$FACEFUSION_DIR"
elif [ "$UPDATE_SOURCES" = "1" ]; then
  git -C "$FACEFUSION_DIR" fetch --tags --force
  git -C "$FACEFUSION_DIR" checkout --force "$FACEFUSION_VERSION"
else
  echo "复用 FaceFusion 源码：$FACEFUSION_DIR"
fi

CONDA_BIN=/root/miniconda3/bin/conda
if [ ! -x "$CONDA_BIN" ]; then
  echo "未找到 /root/miniconda3/bin/conda，无法安装 FaceFusion。" >&2
  exit 1
fi
if [ ! -x "$FACEFUSION_ENV/bin/python" ]; then
  "$CONDA_BIN" create -y -p "$FACEFUSION_ENV" python=3.12 pip=25.0
fi
if [ "$FORCE_INSTALL" = "1" ] || ! (
  cd "$FACEFUSION_DIR"
  "$FACEFUSION_ENV/bin/python" -c "import cv2, numpy, onnxruntime" >/dev/null 2>&1
  "$FACEFUSION_ENV/bin/python" facefusion.py --version >/dev/null 2>&1
); then
  echo "安装 FaceFusion Python 依赖（仅首次安装或环境不完整时执行）"
  (
    cd "$FACEFUSION_DIR"
    "$CONDA_BIN" run --no-capture-output \
      -p "$FACEFUSION_ENV" \
      python install.py cuda@12
  )
else
  echo "复用 FaceFusion Python 环境：$FACEFUSION_ENV"
fi

PREFETCH_ARGUMENTS=(
  --facefusion-dir "$FACEFUSION_DIR"
)
if [[ -n "${H3_FACEFUSION_MODELS_DIR:-}" ]]; then
  PREFETCH_ARGUMENTS+=(--public-models-dir "$H3_FACEFUSION_MODELS_DIR")
fi
"$FACEFUSION_ENV/bin/python" "$PROJECT_DIR/scripts/prefetch_facefusion_models.py" "${PREFETCH_ARGUMENTS[@]}"

echo "Florence-2、SAM2 和 FaceFusion 已准备完成：$TOOLS_DIR"
