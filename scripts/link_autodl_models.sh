#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

if [[ "$#" -ne 0 && "$#" -ne 3 ]]; then
  echo "用法：$0 [VACE模型路径 UMT5文本编码器路径 WAN-VAE路径]" >&2
  echo "不传参数时使用 README 中已确认的 AutoDL 公共模型路径。" >&2
  exit 2
fi

COMFY_DIR="${H3_COMFYUI_DIR:-/root/autodl-tmp/ComfyUI}"
UNET_SOURCE="${1:-${VACE_UNET_PUBLIC_PATH:-/.autodl/e8/9f/11/e89f11d81110d363b9fde6e921e802b7}}"
CLIP_SOURCE="${2:-${VACE_CLIP_PUBLIC_PATH:-/.autodl/4b/ad/50/4bad5031eb889beddcfb6e1b9b12cccc}}"
VAE_SOURCE="${3:-${VACE_VAE_PUBLIC_PATH:-/.autodl/0d/d0/84/0dd084bcf4728fe874a9e3b18110af74}}"

for source in "$UNET_SOURCE" "$CLIP_SOURCE" "$VAE_SOURCE"; do
  if [[ ! -e "$source" ]]; then
    echo "公共模型路径不存在：$source" >&2
    exit 1
  fi
done

mkdir -p "$COMFY_DIR/models/diffusion_models"
mkdir -p "$COMFY_DIR/models/text_encoders"
mkdir -p "$COMFY_DIR/models/vae"

ln -sfn "$UNET_SOURCE" "$COMFY_DIR/models/diffusion_models/wan2.1_vace_14B_fp16.safetensors"
ln -sfn "$CLIP_SOURCE" "$COMFY_DIR/models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"
ln -sfn "$VAE_SOURCE" "$COMFY_DIR/models/vae/wan_2.1_vae.safetensors"

echo "Wan VACE 模型已挂载到 $COMFY_DIR/models。"
