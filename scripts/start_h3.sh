#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -x .venv/bin/sglang ]]; then
  echo "未找到 SGLang，请先运行 scripts/install_autodl.sh" >&2
  exit 1
fi

GPU_COUNT="${H3_GPU_COUNT:-$(nvidia-smi --query-gpu=name --format=csv,noheader | wc -l | tr -d ' ')}"
MIN_VRAM_MIB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | sort -n | head -1 | tr -d ' ')"
COMMON=(
  --model-path MiniMaxAI/MiniMax-H3
  --model-variant ref2va
  --encoder-parallel auto
  --enable-torch-compile false
  --port 30011
  --host 127.0.0.1
)

case "$GPU_COUNT" in
  1)
    echo "使用单卡低显存方案：INT8 + CPU 分层卸载。首次启动会下载模型。"
    exec .venv/bin/sglang serve "${COMMON[@]}" \
      --num-gpus 1 \
      --quantization kitchen_int8 \
      --attention-backend fa \
      --performance-mode memory \
      --layerwise-offload-components dit,text_encoder \
      --dit-offload-prefetch-size 1 \
      --dit-layerwise-resident-layers 0
    ;;
  2)
    RESIDENT=0
    if [[ "$MIN_VRAM_MIB" -ge 30000 ]]; then RESIDENT=20; fi
    echo "使用双卡 TP2 分层卸载方案，常驻 DiT 层数：$RESIDENT"
    exec .venv/bin/sglang serve "${COMMON[@]}" \
      --num-gpus 2 \
      --tp-size 2 \
      --ulysses-degree 1 \
      --performance-mode memory \
      --layerwise-offload-components dit,text_encoder,vae \
      --dit-offload-prefetch-size 1 \
      --dit-layerwise-resident-layers "$RESIDENT"
    ;;
  4)
    echo "使用四卡 TP2 + Ulysses2 常驻权重方案。"
    exec .venv/bin/sglang serve "${COMMON[@]}" \
      --num-gpus 4 \
      --tp-size 2 \
      --ulysses-degree 2 \
      --performance-mode speed
    ;;
  *)
    echo "当前脚本支持 1、2 或 4 张 GPU，检测到 $GPU_COUNT 张。可设置 H3_GPU_COUNT 或自行修改拓扑。" >&2
    exit 1
    ;;
esac

