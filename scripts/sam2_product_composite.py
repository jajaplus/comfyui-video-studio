#!/usr/bin/env python3
"""Automatically locate or manually box a product, track it, and composite that region."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np
import torch
from sam2.sam2_video_predictor import SAM2VideoPredictor
from sam2.build_sam import build_sam2_video_predictor


PRODUCT_HINTS = (
    (r"(?:t恤|tee\s*shirt|t-?shirt)", "t-shirt"),
    (r"(?:衬衫|shirt|blouse)", "shirt"),
    (r"(?:上衣|衣服|服装|衣物|top|clothing|garment)", "upper-body clothing"),
    (r"(?:外套|夹克|大衣|jacket|coat)", "jacket"),
    (r"(?:连衣裙|裙子|dress|skirt)", "dress"),
    (r"(?:裤子|长裤|短裤|pants|trousers|shorts)", "pants"),
    (r"(?:鞋子|鞋|运动鞋|靴子|shoes|sneakers|boots)", "shoes"),
    (r"(?:手提包|背包|包包|包|handbag|backpack|bag)", "bag"),
    (r"(?:帽子|帽|hat|cap)", "hat"),
    (r"(?:眼镜|墨镜|glasses|sunglasses)", "glasses"),
    (r"(?:手表|腕表|watch)", "watch"),
    (r"(?:项链|necklace)", "necklace"),
    (r"(?:耳环|耳饰|earrings?)", "earrings"),
    (r"(?:手机|电话|phone|smartphone)", "phone"),
    (r"(?:杯子|水杯|mug|cup|bottle)", "cup"),
)
GENERIC_REFERENCE_LABELS = {
    "person", "people", "man", "woman", "boy", "girl", "human", "face",
    "model", "background", "image", "photo", "object",
}


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


def target_from_text(text: str) -> str | None:
    lowered = text.lower()
    for pattern, target in PRODUCT_HINTS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            return target
    return None


def florence_inference(model, processor, image, task: str, text_input: str = ""):
    prompt = task + text_input
    inputs = processor(text=prompt, images=image, return_tensors="pt")
    prepared = {}
    for key, value in inputs.items():
        if torch.is_floating_point(value):
            prepared[key] = value.to(model.device, dtype=model.dtype)
        else:
            prepared[key] = value.to(model.device)
    with torch.inference_mode():
        generated_ids = model.generate(
            **prepared, max_new_tokens=512, do_sample=False, num_beams=3,
        )
    generated_text = processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
    parsed = processor.post_process_generation(
        generated_text, task=task, image_size=(image.width, image.height),
    )
    return parsed.get(task, parsed) if isinstance(parsed, dict) else parsed


def flatten_points(value) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
            points.append((float(value[0]), float(value[1])))
        else:
            for item in value:
                points.extend(flatten_points(item))
    return points


def boxes_and_labels(payload) -> tuple[list[list[float]], list[str]]:
    if not isinstance(payload, dict):
        return [], []
    boxes = [list(map(float, box[:4])) for box in payload.get("bboxes", []) if len(box) >= 4]
    labels = [str(label).strip() for label in payload.get("labels", [])]
    if not boxes:
        for polygon in payload.get("polygons", []):
            points = flatten_points(polygon)
            if points:
                xs = [point[0] for point in points]
                ys = [point[1] for point in points]
                boxes.append([min(xs), min(ys), max(xs), max(ys)])
    if len(labels) < len(boxes):
        labels.extend([""] * (len(boxes) - len(labels)))
    return boxes, labels


def load_florence(model_source: str, device: str):
    from transformers import AutoModelForCausalLM, AutoProcessor

    source_path = Path(model_source)
    local_only = source_path.exists()
    dtype = torch.float16 if device == "cuda" else torch.float32
    processor = AutoProcessor.from_pretrained(
        model_source, trust_remote_code=True, local_files_only=local_only,
    )
    model = AutoModelForCausalLM.from_pretrained(
        model_source, torch_dtype=dtype, trust_remote_code=True,
        local_files_only=local_only,
    ).to(device)
    model.eval()
    return model, processor


def infer_reference_target(model, processor, reference, description: str) -> str:
    prompt_target = target_from_text(description)
    if prompt_target:
        return prompt_target
    payload = florence_inference(model, processor, reference, "<OD>")
    boxes, labels = boxes_and_labels(payload)
    candidates: list[tuple[float, str]] = []
    for box, label in zip(boxes, labels):
        normalized = label.lower().strip(" .")
        if not normalized or normalized in GENERIC_REFERENCE_LABELS:
            continue
        area = max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
        product_bonus = 2.0 if target_from_text(normalized) else 1.0
        candidates.append((area * product_bonus, target_from_text(normalized) or normalized))
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    caption = florence_inference(model, processor, reference, "<MORE_DETAILED_CAPTION>")
    caption_text = str(caption)
    caption_target = target_from_text(caption_text)
    if caption_target:
        return caption_target
    raise RuntimeError("AI 无法判断商品参考图的类型，请在提示词中写明要替换的物品，例如上衣、包或鞋")


def choose_box(boxes: list[list[float]], width: int, height: int) -> list[float] | None:
    valid: list[tuple[float, list[float]]] = []
    for raw in boxes:
        x1, y1, x2, y2 = raw
        x1, x2 = sorted((max(0.0, x1), min(float(width), x2)))
        y1, y2 = sorted((max(0.0, y1), min(float(height), y2)))
        if x2 - x1 < 8 or y2 - y1 < 8:
            continue
        area_ratio = ((x2 - x1) * (y2 - y1)) / max(1.0, width * height)
        center_x = (x1 + x2) / (2 * width)
        center_y = (y1 + y2) / (2 * height)
        center_distance = ((center_x - 0.5) ** 2 + (center_y - 0.5) ** 2) ** 0.5
        score = area_ratio * max(0.35, 1.0 - center_distance * 0.55)
        if area_ratio <= 0.82:
            valid.append((score, [x1, y1, x2, y2]))
    return max(valid, key=lambda item: item[0])[1] if valid else None


def auto_product_box(
    first_frame: Path, product_reference: Path, description: str,
    florence_model: str,
) -> tuple[list[float], str]:
    from PIL import Image

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, processor = load_florence(florence_model, device)
    reference = Image.open(product_reference).convert("RGB")
    source = Image.open(first_frame).convert("RGB")
    target = infer_reference_target(model, processor, reference, description)
    payload = florence_inference(
        model, processor, source, "<OPEN_VOCABULARY_DETECTION>", target,
    )
    boxes, _ = boxes_and_labels(payload)
    selected = choose_box(boxes, source.width, source.height)
    if selected is None:
        payload = florence_inference(
            model, processor, source, "<CAPTION_TO_PHRASE_GROUNDING>", target,
        )
        boxes, _ = boxes_and_labels(payload)
        selected = choose_box(boxes, source.width, source.height)
    if selected is None:
        raise RuntimeError(
            f"AI 没有在视频首帧找到“{target}”，请使用客户端的“修正商品区域”手动框选"
        )
    margin_x = max(2.0, (selected[2] - selected[0]) * 0.03)
    margin_y = max(2.0, (selected[3] - selected[1]) * 0.03)
    expanded = [
        max(0.0, selected[0] - margin_x) / source.width,
        max(0.0, selected[1] - margin_y) / source.height,
        min(float(source.width), selected[2] + margin_x) / source.width,
        min(float(source.height), selected[3] + margin_y) / source.height,
    ]
    return expanded, target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--box")
    parser.add_argument("--product-reference", type=Path)
    parser.add_argument("--target-description", default="")
    parser.add_argument("--florence-model", default="microsoft/Florence-2-base-ft")
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--model-config", default="configs/sam2.1/sam2.1_hiera_s.yaml")
    parser.add_argument("--work-dir", required=True, type=Path)
    args = parser.parse_args()

    box = [float(value) for value in json.loads(args.box)] if args.box else None
    if box is not None and (
        len(box) != 4 or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)
    ):
        raise ValueError("商品框必须是 [x1,y1,x2,y2] 的 0–1 坐标")
    if box is None and (not args.product_reference or not args.product_reference.is_file()):
        raise ValueError("自动识别商品区域需要商品参考图")

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
    if box is None:
        box, target = auto_product_box(
            source_frames[0], args.product_reference, args.target_description,
            args.florence_model,
        )
        print(json.dumps({"auto_product_box": box, "target": target}, ensure_ascii=False), flush=True)
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
