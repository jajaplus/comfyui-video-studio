#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

COMFY_DIR="${H3_COMFYUI_DIR:-/root/ComfyUI}"
PYTHON_BIN="${H3_SAM2_PYTHON:-$PROJECT_DIR/.venv/bin/python}"
HF_ENDPOINT_URL="${H3_HF_ENDPOINT:-https://hf-mirror.com}"
UNET_PUBLIC="${VACE_UNET_PUBLIC_PATH:-/.autodl/e8/9f/11/e89f11d81110d363b9fde6e921e802b7}"
CLIP_PUBLIC="${VACE_CLIP_PUBLIC_PATH:-/.autodl/4b/ad/50/4bad5031eb889beddcfb6e1b9b12cccc}"
VAE_PUBLIC="${VACE_VAE_PUBLIC_PATH:-/.autodl/0d/d0/84/0dd084bcf4728fe874a9e3b18110af74}"
ALLOW_DOWNLOAD=0

if [[ "$#" -gt 1 || ( "$#" -eq 1 && "$1" != "--download" ) ]]; then
  echo "用法：$0 [--download]" >&2
  echo "默认挂载 AutoDL 公共模型；只有显式传入 --download 才会联网下载。" >&2
  exit 2
fi
if [[ "${1:-}" == "--download" ]]; then
  ALLOW_DOWNLOAD=1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN=python3
fi
if [[ ! -f "$COMFY_DIR/main.py" ]]; then
  echo "找不到 $COMFY_DIR/main.py，请先按 README 准备 ComfyUI。" >&2
  exit 1
fi

if [[ "$ALLOW_DOWNLOAD" = "0" ]]; then
  missing=0
  for source in "$UNET_PUBLIC" "$CLIP_PUBLIC" "$VAE_PUBLIC"; do
    if [[ ! -e "$source" ]]; then
      echo "公共模型路径不存在：$source" >&2
      missing=1
    fi
  done
  if [[ "$missing" = "1" ]]; then
    echo "未联网下载。请先在 AutoDL 模型广场加载上述公共模型。" >&2
    echo "确认需要占用本地磁盘时，才执行：$0 --download" >&2
    exit 1
  fi
  echo "检测到 AutoDL 公共模型，直接建立软链接，不下载文件。"
  exec "$PROJECT_DIR/scripts/link_autodl_models.sh" \
    "$UNET_PUBLIC" "$CLIP_PUBLIC" "$VAE_PUBLIC"
fi

if ! "$PYTHON_BIN" -c 'import huggingface_hub' >/dev/null 2>&1; then
  "$PYTHON_BIN" -m pip install huggingface_hub
fi

mkdir -p "$COMFY_DIR/models/diffusion_models" "$COMFY_DIR/models/text_encoders" "$COMFY_DIR/models/vae"

export HF_ENDPOINT="$HF_ENDPOINT_URL"
export VACE_COMFY_DIR="$COMFY_DIR"
echo "已显式启用联网下载模式；大约需要 40GB 以上可用磁盘空间。"
"$PYTHON_BIN" - <<'PY'
import os
from pathlib import Path
from huggingface_hub import hf_hub_download

root = Path(os.environ["VACE_COMFY_DIR"]) / "models"
items = [
    ("Comfy-Org/Wan_2.1_ComfyUI_repackaged", "split_files/diffusion_models/wan2.1_vace_14B_fp16.safetensors", root / "diffusion_models"),
    ("Comfy-Org/Wan_2.1_ComfyUI_repackaged", "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", root / "text_encoders"),
    ("Comfy-Org/Wan_2.1_ComfyUI_repackaged", "split_files/vae/wan_2.1_vae.safetensors", root / "vae"),
]
for repo_id, filename, destination in items:
    target = destination / Path(filename).name
    if target.is_file() and target.stat().st_size > 1024 * 1024:
        print(f"复用：{target}")
        continue
    destination.mkdir(parents=True, exist_ok=True)
    print(f"下载：{filename}")
    downloaded = Path(hf_hub_download(repo_id=repo_id, filename=filename, local_dir=destination))
    if downloaded.resolve() != target.resolve():
        target.unlink(missing_ok=True)
        target.symlink_to(downloaded)
print("Wan VACE 模型准备完成。")
PY
