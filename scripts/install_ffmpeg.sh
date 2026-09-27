#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

TOOLS_DIR="${H3_PRECISION_TOOLS_DIR:-/root/autodl-tmp/h3-precision-tools}"
FFMPEG_ENV="${H3_FFMPEG_ENV:-$TOOLS_DIR/ffmpeg-env}"
CONDA_BIN="${H3_CONDA_BIN:-/root/miniconda3/bin/conda}"
mkdir -p "$(dirname "$FFMPEG_ENV")"
if [[ ! -x "$CONDA_BIN" ]]; then
  echo "未找到 Conda：$CONDA_BIN；无法安装新版 FFmpeg。" >&2
  exit 1
fi

FFMPEG_HELP=""
FFMPEG_ENCODERS=""
if [[ -x "$FFMPEG_ENV/bin/ffmpeg" ]]; then
  FFMPEG_HELP="$("$FFMPEG_ENV/bin/ffmpeg" -hide_banner -h full 2>&1 || true)"
  FFMPEG_ENCODERS="$("$FFMPEG_ENV/bin/ffmpeg" -hide_banner -encoders 2>&1 || true)"
fi
if [[ "$FFMPEG_HELP" != *-fps_mode* || "$FFMPEG_ENCODERS" != *libx264* ]]; then
  if [[ -f "$FFMPEG_ENV/conda-meta/history" ]]; then
    "$CONDA_BIN" install -y -p "$FFMPEG_ENV" --override-channels -c conda-forge 'ffmpeg=7.*=gpl*'
  else
    "$CONDA_BIN" create -y -p "$FFMPEG_ENV" --override-channels -c conda-forge 'ffmpeg=7.*=gpl*'
  fi
fi

FFMPEG_HELP="$("$FFMPEG_ENV/bin/ffmpeg" -hide_banner -h full 2>&1)"
if [[ "$FFMPEG_HELP" != *-fps_mode* ]]; then
  echo "新版 FFmpeg 安装后仍不支持 -fps_mode：$FFMPEG_ENV/bin/ffmpeg" >&2
  exit 1
fi
if [[ ! -x "$FFMPEG_ENV/bin/ffprobe" ]]; then
  echo "新版 FFmpeg 缺少 ffprobe：$FFMPEG_ENV/bin/ffprobe" >&2
  exit 1
fi
FFMPEG_ENCODERS="$("$FFMPEG_ENV/bin/ffmpeg" -hide_banner -encoders 2>&1)"
if [[ "$FFMPEG_ENCODERS" != *libx264* ]]; then
  echo "新版 FFmpeg 缺少 libx264 编码器" >&2
  exit 1
fi

"$FFMPEG_ENV/bin/ffmpeg" -version | sed -n '1p'
echo "新版 FFmpeg 已就绪：$FFMPEG_ENV/bin/ffmpeg"
