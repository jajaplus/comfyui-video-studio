#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

if [[ "$#" -ne 4 ]]; then
  echo "用法：$0 REF2VA模型路径 文本编码器路径 视频VAE路径 音频VAE路径" >&2
  echo "四个源路径请从 AutoDL 公共模型页面复制，通常以 /.autodl/ 开头。" >&2
  exit 2
fi

COMFY_DIR="${H3_COMFYUI_DIR:-/root/autodl-tmp/ComfyUI}"
UNET_SOURCE="$1"
CLIP_SOURCE="$2"
VIDEO_VAE_SOURCE="$3"
AUDIO_VAE_SOURCE="$4"

for source in "$UNET_SOURCE" "$CLIP_SOURCE" "$VIDEO_VAE_SOURCE" "$AUDIO_VAE_SOURCE"; do
  if [[ ! -e "$source" ]]; then
    echo "公共模型路径不存在：$source" >&2
    exit 1
  fi
done

mkdir -p "$COMFY_DIR/models/diffusion_models"
mkdir -p "$COMFY_DIR/models/text_encoders"
mkdir -p "$COMFY_DIR/models/vae"

ln -sfn "$UNET_SOURCE" "$COMFY_DIR/models/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors"
ln -sfn "$CLIP_SOURCE" "$COMFY_DIR/models/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
ln -sfn "$VIDEO_VAE_SOURCE" "$COMFY_DIR/models/vae/minimax_h3_video_vae_int8_convrot.safetensors"
ln -sfn "$AUDIO_VAE_SOURCE" "$COMFY_DIR/models/vae/minimax_h3_audio_vae_fp32.safetensors"

echo "MiniMax-H3 Ref2VA 公共模型已挂载到 $COMFY_DIR/models。"
