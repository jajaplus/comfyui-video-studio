#!/usr/bin/env python3
"""Track a boxed product in the source video and composite only that area from a candidate."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np
import torch
from sam2.sam2_video_predictor import SAM2VideoPredictor
from sam2.build_sam import build_sam2_video_predictor


def run(command: list[str], label: str) -> None:
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "未知错误").strip()[-3000:]
        raise RuntimeError(f"{label}失败：{detail}")


def video_fps(path: Path) -> float:
    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=avg_frame_rate", "-of", "default=nk=1:nw=1", str(path),
        ],
        check=True, capture_output=True, text=True,
    )
    try:
        fps = float(Fraction(completed.stdout.strip()))
    except (ValueError, ZeroDivisionError):
        fps = 24.0
    return fps if 1 <= fps <= 240 else 24.0


def extract_frames(source: Path, destination: Path, extension: str, fps: float) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-vf", f"fps={fps:.8f}", "-start_number", "0", str(destination / f"%06d.{extension}"),
        ],
        f"读取 {source.name} 的视频帧",
    )


def load_resized(path: Path, width: int, height: int) -> np.ndarray:
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError(f"无法读取视频帧：{path.name}")
    if frame.shape[1] != width or frame.shape[0] != height:
        frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_LANCZOS4)
    return frame


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--box", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--model-config", default="configs/sam2.1/sam2.1_hiera_s.yaml")
    parser.add_argument("--work-dir", required=True, type=Path)
    args = parser.parse_args()

    box = [float(value) for value in json.loads(args.box)]
    if len(box) != 4 or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
        raise ValueError("商品框必须是 [x1,y1,x2,y2] 的 0–1 坐标")

    shutil.rmtree(args.work_dir, ignore_errors=True)
    source_dir = args.work_dir / "source"
    candidate_dir = args.work_dir / "candidate"
    output_dir = args.work_dir / "composite"
    output_dir.mkdir(parents=True, exist_ok=True)

    fps = video_fps(args.source)
    extract_frames(args.source, source_dir, "jpg", fps)
    extract_frames(args.candidate, candidate_dir, "png", fps)
    source_frames = sorted(source_dir.glob("*.jpg"))
    candidate_frames = sorted(candidate_dir.glob("*.png"))
    if not source_frames or not candidate_frames:
        raise RuntimeError("源视频或 H3 商品候选视频没有可读取的画面")

    first = cv2.imread(str(source_frames[0]), cv2.IMREAD_COLOR)
    if first is None:
        raise RuntimeError("无法读取源视频首帧")
    height, width = first.shape[:2]
    pixel_box = np.array(
        [box[0] * width, box[1] * height, box[2] * width, box[3] * height],
        dtype=np.float32,
    )

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if args.checkpoint and args.checkpoint.is_file():
        predictor = build_sam2_video_predictor(
            args.model_config, str(args.checkpoint), device=device,
        )
    else:
        predictor = SAM2VideoPredictor.from_pretrained(args.model_id, device=device)
    inference_state = predictor.init_state(video_path=str(source_dir))
    predictor.add_new_points_or_box(
        inference_state=inference_state, frame_idx=0, obj_id=1, box=pixel_box,
    )

    written: set[int] = set()
    dilation = max(3, round(min(width, height) * 0.008))
    if dilation % 2 == 0:
        dilation += 1
    feather = max(5, round(min(width, height) * 0.012))
    if feather % 2 == 0:
        feather += 1
    kernel = np.ones((dilation, dilation), np.uint8)

    with torch.inference_mode():
        for frame_index, object_ids, mask_logits in predictor.propagate_in_video(inference_state):
            index = int(frame_index)
            if index < 0 or index >= len(source_frames):
                continue
            source = load_resized(source_frames[index], width, height)
            candidate_index = min(index, len(candidate_frames) - 1)
            candidate = load_resized(candidate_frames[candidate_index], width, height)
            if len(object_ids) and mask_logits.shape[0]:
                mask = (mask_logits[0] > 0.0).detach().cpu().numpy().squeeze().astype(np.uint8) * 255
                if mask.shape != (height, width):
                    mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)
                mask = cv2.dilate(mask, kernel, iterations=1)
                alpha = cv2.GaussianBlur(mask, (feather, feather), 0).astype(np.float32) / 255.0
                alpha = alpha[..., None]
                composite = (candidate.astype(np.float32) * alpha + source.astype(np.float32) * (1 - alpha)).astype(np.uint8)
            else:
                composite = source
            cv2.imwrite(str(output_dir / f"{index:06d}.png"), composite)
            written.add(index)

    for index, source_path in enumerate(source_frames):
        if index not in written:
            shutil.copy2(source_path, output_dir / f"{index:06d}.png")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-framerate", f"{fps:.8f}", "-start_number", "0",
            "-i", str(output_dir / "%06d.png"), "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(args.output),
        ],
        "编码商品局部合成视频",
    )


if __name__ == "__main__":
    main()
