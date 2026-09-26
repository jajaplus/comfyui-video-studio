from __future__ import annotations

import io
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode, urlparse

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation


ROOT = Path(__file__).resolve().parent.parent


def absolute_path_without_resolving(value: str | os.PathLike[str]) -> Path:
    """Make a path absolute while preserving virtualenv interpreter symlinks."""
    return Path(os.path.abspath(Path(value).expanduser()))


STATIC_DIR = ROOT / "static"
DATA_DIR = Path(os.getenv("H3_STUDIO_DATA_DIR", ROOT / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"
WORK_DIR = DATA_DIR / "work"
DB_PATH = DATA_DIR / "studio.db"
COMFYUI_URL = os.getenv("H3_COMFYUI_URL", "http://127.0.0.1:6008").rstrip("/")
COMFYUI_DIR = Path(os.getenv("H3_COMFYUI_DIR", "/root/autodl-tmp/ComfyUI")).resolve()
COMFYUI_INPUT_DIR = Path(os.getenv("H3_COMFYUI_INPUT_DIR", COMFYUI_DIR / "input")).resolve()
COMFYUI_OUTPUT_DIR = Path(os.getenv("H3_COMFYUI_OUTPUT_DIR", COMFYUI_DIR / "output")).resolve()
COMFYUI_WORKFLOW = Path(
    os.getenv("H3_COMFYUI_WORKFLOW", ROOT / "workflows" / "minimax_h3_ref2va_api.json")
).resolve()
COMFYUI_TIMEOUT = max(300, int(os.getenv("H3_COMFYUI_TIMEOUT", "10800")))
PRECISION_TOOLS_DIR = Path(
    os.getenv("H3_PRECISION_TOOLS_DIR", "/root/autodl-tmp/h3-precision-tools")
).resolve()
SAM2_PYTHON = Path(
    os.getenv("H3_SAM2_PYTHON", PRECISION_TOOLS_DIR / "sam2-env" / "bin" / "python")
)
SAM2_PYTHON = absolute_path_without_resolving(SAM2_PYTHON)
SAM2_SCRIPT = Path(
    os.getenv("H3_SAM2_SCRIPT", ROOT / "scripts" / "sam2_product_composite.py")
).resolve()
SAM2_MODEL_ID = os.getenv("H3_SAM2_MODEL_ID", "facebook/sam2.1-hiera-small")
SAM2_CHECKPOINT = Path(
    os.getenv(
        "H3_SAM2_CHECKPOINT",
        PRECISION_TOOLS_DIR / "models" / "sam2.1_hiera_small.pt",
    )
).resolve()
SAM2_CONFIG = os.getenv("H3_SAM2_CONFIG", "configs/sam2.1/sam2.1_hiera_s.yaml")
FLORENCE2_MODEL = os.getenv(
    "H3_FLORENCE2_MODEL",
    str(PRECISION_TOOLS_DIR / "models" / "Florence-2-base-ft"),
)
FACEFUSION_DIR = Path(
    os.getenv("H3_FACEFUSION_DIR", PRECISION_TOOLS_DIR / "facefusion")
).resolve()
FACEFUSION_PYTHON = Path(
    os.getenv("H3_FACEFUSION_PYTHON", PRECISION_TOOLS_DIR / "facefusion-env" / "bin" / "python")
)
FACEFUSION_PYTHON = absolute_path_without_resolving(FACEFUSION_PYTHON)
COMFYUI_UNET = os.getenv(
    "H3_COMFYUI_UNET", "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
)
COMFYUI_CLIP = os.getenv(
    "H3_COMFYUI_CLIP", "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
)
COMFYUI_VIDEO_VAE = os.getenv(
    "H3_COMFYUI_VIDEO_VAE", "minimax_h3_video_vae_int8_convrot.safetensors"
)
COMFYUI_AUDIO_VAE = os.getenv("H3_COMFYUI_AUDIO_VAE", "minimax_h3_audio_vae_fp32.safetensors")
POLL_SECONDS = max(1.0, float(os.getenv("H3_POLL_SECONDS", "3")))
MAX_UPLOAD_GB = max(1, int(os.getenv("H3_MAX_UPLOAD_GB", "20")))
MAX_UPLOAD_BYTES = MAX_UPLOAD_GB * 1024**3
MAX_SEGMENT_SECONDS = 15.0
MIN_VIDEO_SECONDS = 4.0
QUALITY_LEVELS = {"low", "standard", "high"}
SAMPLER_NAMES = {
    "res_multistep", "euler", "euler_ancestral", "heun",
    "dpmpp_2m", "dpmpp_2m_sde",
}
SCHEDULER_NAMES = {"simple", "normal", "karras", "exponential", "sgm_uniform"}
DEFAULT_SAMPLER = "res_multistep"
DEFAULT_SCHEDULER = "simple"
DEFAULT_STEPS = 20
DEFAULT_DENOISE = 1.0


class BlackVideoError(RuntimeError):
    pass

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
EXCEL_EXTENSIONS = {".xlsx", ".xlsm"}
worker_stop = threading.Event()
worker_thread: threading.Thread | None = None
gpu_cache_lock = threading.Lock()
gpu_cache_at = 0.0
gpu_cache_value: dict[str, Any] = {"available": False, "gpus": []}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    for folder in (DATA_DIR, UPLOAD_DIR, OUTPUT_DIR, WORK_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    with connect_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                prompt TEXT NOT NULL,
                source_video TEXT NOT NULL,
                character_image TEXT,
                product_image TEXT,
                reference_images TEXT,
                duration REAL NOT NULL,
                aspect_ratio TEXT NOT NULL,
                source_start REAL NOT NULL DEFAULT 0,
                seed INTEGER NOT NULL,
                engine_job_id TEXT,
                output_path TEXT,
                error TEXT,
                progress INTEGER NOT NULL DEFAULT 0,
                stage TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                segment_count INTEGER NOT NULL DEFAULT 1,
                current_segment INTEGER NOT NULL DEFAULT 0,
                quality TEXT NOT NULL DEFAULT 'low',
                output_width INTEGER,
                output_height INTEGER,
                reference_roles TEXT,
                product_box TEXT,
                precision_mode INTEGER NOT NULL DEFAULT 1,
                sampler TEXT NOT NULL DEFAULT 'res_multistep',
                scheduler TEXT NOT NULL DEFAULT 'simple',
                steps INTEGER NOT NULL DEFAULT 20,
                denoise REAL NOT NULL DEFAULT 1.0
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_queue
                ON tasks(status, deleted_at, created_at);
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        if "reference_images" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN reference_images TEXT")
        if "stage" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN stage TEXT")
        if "segment_count" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN segment_count INTEGER NOT NULL DEFAULT 1")
        if "current_segment" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN current_segment INTEGER NOT NULL DEFAULT 0")
        if "quality" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN quality TEXT NOT NULL DEFAULT 'low'")
            conn.execute("UPDATE tasks SET quality='high'")
        if "output_width" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN output_width INTEGER")
        if "output_height" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN output_height INTEGER")
        if "reference_roles" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN reference_roles TEXT")
        if "product_box" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN product_box TEXT")
        if "precision_mode" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN precision_mode INTEGER NOT NULL DEFAULT 0")
        if "sampler" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN sampler TEXT NOT NULL DEFAULT 'res_multistep'")
        if "scheduler" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN scheduler TEXT NOT NULL DEFAULT 'simple'")
        if "steps" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN steps INTEGER NOT NULL DEFAULT 20")
        if "denoise" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN denoise REAL NOT NULL DEFAULT 1.0")
        conn.execute(
            "UPDATE tasks SET status='queued', progress=0, stage='等待队列', started_at=NULL, "
            "updated_at=? WHERE status IN ('starting','running') AND deleted_at IS NULL",
            (utcnow(),),
        )


def validate_extension(filename: str, allowed: set[str], label: str) -> None:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in allowed:
        expected = "、".join(sorted(allowed))
        raise HTTPException(400, f"{label}格式不支持，请使用 {expected}")


def segment_durations(duration: float) -> list[float]:
    """Split a source duration into nearly equal H3-compatible 4–15 second parts."""
    if not math.isfinite(duration) or duration < MIN_VIDEO_SECONDS:
        raise ValueError(f"上传视频时长为 {duration:.2f} 秒；视频不能短于 {MIN_VIDEO_SECONDS:g} 秒")
    count = max(1, math.ceil(duration / MAX_SEGMENT_SECONDS))
    segment = duration / count
    if segment < MIN_VIDEO_SECONDS:
        raise ValueError("无法把视频切分为符合 MiniMax-H3 要求的片段")
    parts = [round(segment, 3) for _ in range(count)]
    parts[-1] = round(duration - sum(parts[:-1]), 3)
    return parts


def validate_task_values(
    duration: float, aspect_ratio: str, quality: str = "low",
    sampler: str = DEFAULT_SAMPLER, scheduler: str = DEFAULT_SCHEDULER,
    steps: int = DEFAULT_STEPS, denoise: float = DEFAULT_DENOISE,
) -> None:
    segment_durations(duration)
    if aspect_ratio not in {"auto", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"}:
        raise ValueError("不支持的画面比例")
    if quality not in QUALITY_LEVELS:
        raise ValueError("不支持的视频清晰度")
    if sampler not in SAMPLER_NAMES:
        raise ValueError("不支持的 Sampler")
    if scheduler not in SCHEDULER_NAMES:
        raise ValueError("不支持的 Scheduler")
    if not 1 <= int(steps) <= 100:
        raise ValueError("Steps 必须在 1–100 之间")
    if not math.isfinite(float(denoise)) or not 0.01 <= float(denoise) <= 1:
        raise ValueError("Denoise 必须在 0.01–1.00 之间")


def is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_public_media_url(value: str, label: str) -> str:
    value = value.strip()
    if not is_http_url(value):
        raise ValueError(f"{label}必须是完整的 http:// 或 https:// URL")
    if urlparse(value).username or urlparse(value).password:
        raise ValueError(f"{label}不能在 URL 中包含用户名或密码")
    return value


def media_display_name(value: str | Path) -> str:
    text = str(value)
    if is_http_url(text):
        return Path(urlparse(text).path).name or urlparse(text).netloc
    return Path(text).name


def probe_video_duration(source: str | Path, hint: float | None = None) -> float:
    duration: float | None = None
    try:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(source),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        duration = float(completed.stdout.strip())
    except (FileNotFoundError, subprocess.SubprocessError, ValueError):
        if hint is not None and hint > 0:
            duration = float(hint)
    if duration is None:
        raise ValueError(f"无法读取视频“{media_display_name(source)}”的时长，请确认 URL 可公开访问或视频可正常播放")
    duration = round(duration, 3)
    validate_task_values(duration, "16:9")
    return duration


async def save_upload(upload: UploadFile, destination: Path, allowed: set[str], label: str) -> Path:
    validate_extension(upload.filename or "", allowed, label)
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with destination.open("wb") as target:
            while chunk := await upload.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, f"单个文件不能超过 {MAX_UPLOAD_GB} GB")
                target.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return destination.resolve()


def safe_name(filename: str) -> str:
    raw = Path(filename or "file").name
    cleaned = "".join(c if c.isalnum() or c in "._-" else "_" for c in raw)
    return cleaned[:160] or "file"


def task_to_dict(row: sqlite3.Row, queue_position: int | None = None) -> dict[str, Any]:
    result = {key: row[key] for key in row.keys()}
    for field in ("source_video", "character_image", "product_image", "output_path"):
        if result.get(field):
            result[field] = media_display_name(result[field])
    result["reference_images"] = [media_display_name(path) for path in row_reference_paths(row)]
    result["reference_roles"] = row_reference_roles(row)
    if result.get("product_box"):
        try:
            result["product_box"] = json.loads(result["product_box"])
        except (TypeError, json.JSONDecodeError):
            result["product_box"] = None
    result["cancel_requested"] = bool(result["cancel_requested"])
    result["precision_mode"] = bool(result.get("precision_mode"))
    result["queue_position"] = queue_position
    result["download_url"] = f"/api/tasks/{row['id']}/output" if row["status"] == "completed" else None
    return result


def insert_task(
    *, prompt: str, source_video: str | Path, reference_images: list[str | Path], duration: float,
    aspect_ratio: str, seed: int | None, quality: str = "low", display_name: str | None = None,
    reference_roles: list[str] | None = None, product_box: list[float] | None = None,
    precision_mode: bool = True, sampler: str = DEFAULT_SAMPLER,
    scheduler: str = DEFAULT_SCHEDULER, steps: int = DEFAULT_STEPS,
    denoise: float = DEFAULT_DENOISE,
) -> str:
    steps = int(steps)
    denoise = float(denoise)
    validate_task_values(duration, aspect_ratio, quality, sampler, scheduler, steps, denoise)
    source_text = str(source_video)
    if is_http_url(source_text):
        validate_public_media_url(source_text, "upload_video_url")
    else:
        source_path = Path(source_text)
        if not source_path.is_file() or source_path.suffix.lower() not in VIDEO_EXTENSIONS:
            raise ValueError("upload_video 必须是已上传的受支持视频文件")
    if len(reference_images) > 9:
        raise ValueError("参考图片最多上传 9 张")
    roles = list(reference_roles or [])
    if roles and len(roles) != len(reference_images):
        raise ValueError("参考图片角色数量与图片数量不一致")
    if not roles:
        roles = ["auto"] * len(reference_images)
    if any(role not in {"face", "product", "auto"} for role in roles):
        raise ValueError("参考图片角色必须是 face、product 或 auto")
    if product_box is not None:
        if len(product_box) != 4 or any(not math.isfinite(float(value)) for value in product_box):
            raise ValueError("商品框格式不正确")
        product_box = [float(value) for value in product_box]
        x1, y1, x2, y2 = product_box
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError("商品框必须使用 0–1 的归一化坐标")
    for image in reference_images:
        image_text = str(image)
        if is_http_url(image_text):
            validate_public_media_url(image_text, "reference_image_url")
        else:
            image_path = Path(image_text)
            if not image_path.is_file() or image_path.suffix.lower() not in IMAGE_EXTENSIONS:
                raise ValueError("reference_images 必须是已上传的受支持图片文件")
    task_id = uuid.uuid4().hex
    now = utcnow()
    actual_seed = seed if seed is not None else secrets.randbelow(2_147_483_647)
    name = Path(display_name or media_display_name(source_video)).stem
    part_count = len(segment_durations(duration))
    with connect_db() as conn:
        conn.execute(
            """INSERT INTO tasks (
                id,name,status,prompt,source_video,reference_images,
                duration,aspect_ratio,source_start,seed,stage,segment_count,current_segment,quality,
                reference_roles,product_box,precision_mode,sampler,scheduler,steps,denoise,
                created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id, name or f"视频-{task_id[:6]}", "queued", prompt.strip(),
                source_text, json.dumps([str(path) for path in reference_images], ensure_ascii=False),
                duration, aspect_ratio, 0, actual_seed, "等待队列", part_count, 0, quality,
                json.dumps(roles, ensure_ascii=False),
                json.dumps(product_box) if product_box is not None else None,
                1 if precision_mode else 0,
                sampler, scheduler, steps, denoise,
                now, now,
            ),
        )
    return task_id


def row_reference_roles(row: sqlite3.Row | dict[str, Any]) -> list[str]:
    keys = set(row.keys())
    raw = row["reference_roles"] if "reference_roles" in keys else None
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(role) for role in parsed]
        except (TypeError, json.JSONDecodeError):
            pass
    return []


def row_reference_paths(
    row: sqlite3.Row | dict[str, Any], role: str | None = None
) -> list[str]:
    keys = set(row.keys())
    raw = row["reference_images"] if "reference_images" in keys else None
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                paths = [str(path) for path in parsed if path]
                if role is None:
                    return paths
                roles = row_reference_roles(row)
                if len(roles) != len(paths):
                    return []
                return [path for path, item_role in zip(paths, roles) if item_role == role]
        except (TypeError, json.JSONDecodeError):
            pass
    legacy = []
    for field in ("character_image", "product_image"):
        if field in keys and row[field]:
            legacy.append(str(row[field]))
    return legacy


def build_h3_prompt(row: sqlite3.Row | dict[str, Any]) -> str:
    definitions = [
        "<Video 1> is the authoritative source video and the ground truth for every frame. "
        "Keep its exact shot order, people, body motion, timing, framing, camera motion, lighting, background, and soundtrack."
    ]
    reference_paths = row_reference_paths(row)
    for picture_index, _ in enumerate(reference_paths, start=1):
        definitions.append(
            f"<Picture {picture_index}> is an appearance reference only. Use the user's mapping to decide whether it supplies "
            "a face identity or a product appearance. Never copy its pose, body, clothing, background, camera, or composition."
        )
    user_request = row["prompt"].strip() or (
        "Infer which supplied pictures show a face/person and which show a product. "
        "Use face/person pictures only for facial identity, and product pictures only for the existing product."
    )
    return (
        "subject_definitions:\n" + "\n".join(definitions) +
        "\n\nedit_scope:\n"
        "Perform a localized face-and-product edit of <Video 1>, not a person replacement and not a scene recreation. "
        "For a face reference, change only the visible facial identity and facial appearance inside the original face region. "
        "Keep the original person's hair, head position, body, skin outside the face, clothing, pose, gestures, gaze direction, "
        "scale, location, motion, and visibility. For a product reference, replace only the pixels belonging to the original product. "
        "Keep the product in the exact original location and preserve its size, orientation, motion, perspective, hand contact, "
        "occlusion, reflections, and shadows. When the original face or product is occluded or outside the frame, keep it occluded or absent."
        "\n\nhard_constraints:\n"
        "Every person visible in <Video 1> must remain visible in exactly the same frames. The main model must never disappear, "
        "be replaced by an empty background, become a different full body, or change clothes. Never add or remove a person, limb, "
        "product, shot, cut, logo, text, or camera move. Preserve the background, lighting, timing, motion, framing, and synchronized audio. "
        "If any replacement is uncertain, preserve the original source pixels and subject presence instead of inventing content."
        "\n\nuser_mapping_and_request:\n" + user_request +
        "\n\nfinal_priority:\nSource-video structure and subject presence have higher priority than reference-image appearance. "
        "The only permitted visual changes are the requested face identity and existing product appearance."
    )


def http_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: int = 60) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content = response.read()
            return json.loads(content.decode("utf-8")) if content else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"ComfyUI 返回 HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 ComfyUI {COMFYUI_URL}: {exc.reason}") from exc


def parse_nvidia_smi(output: str) -> list[dict[str, Any]]:
    gpus: list[dict[str, Any]] = []
    for line in output.splitlines():
        values = [value.strip() for value in line.split(",")]
        if len(values) != 8:
            continue
        try:
            index, name, utilization, memory_used, memory_total, temperature, power_draw, power_limit = values
            used = float(memory_used)
            total = float(memory_total)
            gpus.append({
                "index": int(index),
                "name": name,
                "utilization": float(utilization),
                "memory_used_mb": used,
                "memory_total_mb": total,
                "memory_percent": round(used / total * 100, 1) if total else 0,
                "temperature_c": float(temperature),
                "power_draw_w": float(power_draw),
                "power_limit_w": float(power_limit),
            })
        except ValueError:
            continue
    return gpus


def gpu_status() -> dict[str, Any]:
    global gpu_cache_at, gpu_cache_value
    now = time.monotonic()
    with gpu_cache_lock:
        if now - gpu_cache_at < 2:
            return gpu_cache_value
        try:
            completed = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,"
                    "temperature.gpu,power.draw,power.limit",
                    "--format=csv,noheader,nounits",
                ],
                check=True, capture_output=True, text=True, timeout=3,
            )
            gpus = parse_nvidia_smi(completed.stdout)
            gpu_cache_value = {"available": bool(gpus), "gpus": gpus, "error": None}
        except (FileNotFoundError, subprocess.SubprocessError) as exc:
            gpu_cache_value = {"available": False, "gpus": [], "error": type(exc).__name__}
        gpu_cache_at = now
        return gpu_cache_value


def comfy_websocket_url(client_id: str) -> str:
    parsed = urlparse(COMFYUI_URL)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc}/ws?{urlencode({'clientId': client_id})}"


def open_comfy_websocket(client_id: str):
    try:
        from websockets.sync.client import connect
        return connect(comfy_websocket_url(client_id), open_timeout=3, close_timeout=1)
    except Exception:
        return None


def comfy_node_stage(workflow: dict[str, Any], node_id: Any) -> tuple[str, int]:
    node = workflow.get(str(node_id), {})
    class_type = str(node.get("class_type") or "")
    title = str((node.get("_meta") or {}).get("title") or class_type)
    if class_type in {"LoadVideo", "GetVideoComponents", "LoadImage"}:
        return "读取参考素材", 18
    if class_type in {"UNETLoader", "CLIPLoader", "VAELoader"}:
        return f"加载模型：{title}", 22
    if class_type == "MiniMaxH3ReferenceToVideo":
        return "编码视频、图片与提示词", 30
    if class_type in {"BasicScheduler", "BasicGuider", "KSamplerSelect", "RandomNoise"}:
        return "准备采样参数", 34
    if class_type == "SamplerCustomAdvanced":
        return "采样生成视频", 36
    if class_type == "VAEDecode":
        return "解码视频画面", 90
    if class_type == "VAEDecodeAudio":
        return "解码音频", 92
    if class_type == "CreateVideo":
        return "合成视频与音频", 95
    if class_type == "SaveVideo":
        return "保存生成视频", 97
    return (f"执行节点：{title}" if title else "ComfyUI 生成中"), 20


def segment_stage(stage: str, segment_index: int, segment_count: int) -> str:
    if segment_count <= 1:
        return stage
    return f"第 {segment_index}/{segment_count} 段 · {stage}"


def segment_progress(local_progress: int | float, segment_index: int, segment_count: int) -> int:
    local = min(100.0, max(0.0, float(local_progress)))
    count = max(1, segment_count)
    overall = 8 + ((segment_index - 1 + local / 100) / count) * 86
    return min(94, max(8, round(overall)))


def mark_segment_task(
    task_id: str, segment_index: int, segment_count: int, stage: str, local_progress: int | float,
    **values: Any,
) -> None:
    mark_task(
        task_id,
        current_segment=segment_index,
        segment_count=segment_count,
        stage=segment_stage(stage, segment_index, segment_count),
        progress=segment_progress(local_progress, segment_index, segment_count),
        **values,
    )


def apply_comfy_event(
    task_id: str, prompt_id: str, workflow: dict[str, Any], message: dict[str, Any],
    segment_index: int = 1, segment_count: int = 1,
) -> bool:
    event_type = message.get("type")
    data = message.get("data") or {}
    if data.get("prompt_id") not in {None, prompt_id}:
        return False
    if event_type == "execution_start":
        mark_segment_task(task_id, segment_index, segment_count, "ComfyUI 开始执行", 17)
    elif event_type == "executing":
        node_id = data.get("node")
        if node_id is None and data.get("prompt_id") == prompt_id:
            mark_segment_task(task_id, segment_index, segment_count, "ComfyUI 执行完成", 94)
            return True
        stage, progress = comfy_node_stage(workflow, node_id)
        mark_segment_task(task_id, segment_index, segment_count, stage, progress)
    elif event_type == "progress":
        value = float(data.get("value") or 0)
        maximum = float(data.get("max") or 0)
        stage, base = comfy_node_stage(workflow, data.get("node"))
        if maximum > 0:
            percent = min(1.0, max(0.0, value / maximum))
            progress = max(base, min(89, 36 + round(percent * 53)))
            stage = f"{stage}（{value:g}/{maximum:g}）"
            mark_segment_task(task_id, segment_index, segment_count, stage, progress)
    elif event_type == "progress_state":
        nodes = data.get("nodes") or {}
        running = next((item for item in nodes.values() if item.get("state") == "running"), None)
        if running:
            display_id = running.get("display_node_id") or running.get("real_node_id") or running.get("node_id")
            synthetic = {"type": "progress", "data": {
                "prompt_id": prompt_id, "node": display_id,
                "value": running.get("value"), "max": running.get("max"),
            }}
            apply_comfy_event(
                task_id, prompt_id, workflow, synthetic, segment_index, segment_count
            )
    return False


def copy_media_to_comfy(source: str | Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_text = str(source)
    written = 0
    try:
        if is_http_url(source_text):
            request = urllib.request.Request(source_text, headers={"User-Agent": "H3-Studio/1.0"})
            with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as target:
                while chunk := response.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_UPLOAD_BYTES:
                        raise RuntimeError(f"远程素材不能超过 {MAX_UPLOAD_GB} GB")
                    target.write(chunk)
        else:
            shutil.copy2(Path(source_text), destination)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def prepare_comfy_inputs(row: sqlite3.Row) -> tuple[str, list[str], Path]:
    task_folder = COMFYUI_INPUT_DIR / "h3_studio" / row["id"]
    task_folder.mkdir(parents=True, exist_ok=True)
    try:
        source_suffix = Path(urlparse(str(row["source_video"])).path).suffix.lower() or ".mp4"
        if source_suffix not in VIDEO_EXTENSIONS:
            source_suffix = ".mp4"
        source_path = task_folder / f"source{source_suffix}"
        copy_media_to_comfy(row["source_video"], source_path)

        reference_names: list[str] = []
        for index, source in enumerate(row_reference_paths(row), start=1):
            suffix = Path(urlparse(str(source)).path).suffix.lower() or ".jpg"
            if suffix not in IMAGE_EXTENSIONS:
                suffix = ".jpg"
            destination = task_folder / f"reference_{index:02d}{suffix}"
            copy_media_to_comfy(source, destination)
            reference_names.append(destination.relative_to(COMFYUI_INPUT_DIR).as_posix())
        return source_path.relative_to(COMFYUI_INPUT_DIR).as_posix(), reference_names, task_folder
    except Exception:
        shutil.rmtree(task_folder, ignore_errors=True)
        raise


QUALITY_DIMENSIONS = {
    "low": {
        "16:9": (672, 384),
        "9:16": (384, 672),
        "1:1": (512, 512),
        "4:3": (576, 448),
        "3:4": (448, 576),
        "21:9": (768, 320),
    },
    "standard": {
        "16:9": (832, 480),
        "9:16": (480, 832),
        "1:1": (640, 640),
        "4:3": (736, 544),
        "3:4": (544, 736),
        "21:9": (960, 416),
    },
    "high": {
        "16:9": (1344, 768),
        "9:16": (768, 1344),
        "1:1": (1024, 1024),
        "4:3": (1024, 768),
        "3:4": (768, 1024),
        "21:9": (1568, 672),
    },
}


def source_aspect_ratio(source: str | Path) -> str:
    try:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(source),
            ],
            check=True, capture_output=True, text=True, timeout=30,
        )
        width, height = (int(value) for value in completed.stdout.strip().split("x", 1))
        ratio = width / height
        dimensions = QUALITY_DIMENSIONS["high"]
        return min(dimensions, key=lambda name: abs(ratio - dimensions[name][0] / dimensions[name][1]))
    except (FileNotFoundError, subprocess.SubprocessError, ValueError, ZeroDivisionError):
        return "16:9"


def probe_video_dimensions(source: str | Path) -> tuple[int, int] | None:
    try:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(source),
            ],
            check=True, capture_output=True, text=True, timeout=30,
        )
        width, height = (int(value) for value in completed.stdout.strip().split("x", 1))
        return (width, height) if width > 0 and height > 0 else None
    except (FileNotFoundError, subprocess.SubprocessError, ValueError):
        return None


def signalstats_indicates_black(output: str) -> bool:
    averages = [float(value) for value in re.findall(r"lavfi\.signalstats\.YAVG=([0-9.]+)", output)]
    maximums = [float(value) for value in re.findall(r"lavfi\.signalstats\.YMAX=([0-9.]+)", output)]
    return bool(averages and maximums) and max(averages) <= 18.0 and max(maximums) <= 32.0


def video_appears_black(source: Path) -> bool:
    try:
        sample_rate = min(1.0, 8.0 / probe_video_duration(source))
    except (TypeError, ValueError):
        sample_rate = 1.0
    try:
        completed = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "info", "-i", str(source),
                "-vf", f"fps={sample_rate:.8f},scale=64:-2,signalstats,metadata=print",
                "-frames:v", "8", "-an", "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=120,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and signalstats_indicates_black(
        f"{completed.stdout}\n{completed.stderr}"
    )


def ensure_visible_video(source: Path, stage: str) -> None:
    if not source.is_file() or source.stat().st_size == 0:
        raise RuntimeError(f"{stage}没有生成有效视频")
    if video_appears_black(source):
        raise BlackVideoError(f"{stage}检测为全黑画面")


def next_workflow_node_id(workflow: dict[str, Any]) -> str:
    return str(max(int(node_id) for node_id in workflow) + 1)


def build_comfy_workflow(row: sqlite3.Row, source_upload: str, reference_uploads: list[str]) -> dict[str, Any]:
    if not COMFYUI_WORKFLOW.is_file():
        raise RuntimeError(f"找不到 ComfyUI 工作流：{COMFYUI_WORKFLOW}")
    workflow = json.loads(COMFYUI_WORKFLOW.read_text(encoding="utf-8"))
    sampler = workflow["136"]["inputs"]
    local_source = COMFYUI_INPUT_DIR / source_upload
    ratio = row["aspect_ratio"] if row["aspect_ratio"] != "auto" else source_aspect_ratio(local_source)
    quality = str(row.get("quality") or "low") if isinstance(row, dict) else str(row["quality"] or "low")
    if quality not in QUALITY_LEVELS:
        quality = "low"
    width, height = QUALITY_DIMENSIONS[quality][ratio]
    sampler["width"] = width
    sampler["height"] = height
    workflow["132"]["inputs"]["value"] = float(row["duration"])
    workflow["129"]["inputs"]["noise_seed"] = int(row["seed"])
    workflow["123"]["inputs"]["sampler_name"] = str(row["sampler"] or DEFAULT_SAMPLER)
    workflow["124"]["inputs"]["scheduler"] = str(row["scheduler"] or DEFAULT_SCHEDULER)
    workflow["124"]["inputs"]["steps"] = int(row["steps"] or DEFAULT_STEPS)
    workflow["124"]["inputs"]["denoise"] = float(
        row["denoise"] if row["denoise"] is not None else DEFAULT_DENOISE
    )
    workflow["138"]["inputs"]["value"] = build_h3_prompt(row)
    workflow["92"]["inputs"]["filename_prefix"] = f"h3_studio/{row['id']}"
    workflow["127"]["inputs"]["unet_name"] = COMFYUI_UNET
    workflow["128"]["inputs"]["clip_name"] = COMFYUI_CLIP
    workflow["119"]["inputs"]["vae_name"] = COMFYUI_VIDEO_VAE
    workflow["120"]["inputs"]["vae_name"] = COMFYUI_AUDIO_VAE

    for key in list(sampler):
        if key.startswith(("ref_images.ref_image_", "ref_videos.ref_video_", "ref_video_audios.ref_video_audio_")):
            del sampler[key]
    for node_id in list(workflow):
        if workflow[node_id]["class_type"] in {"LoadImage", "LoadVideo", "GetVideoComponents"}:
            del workflow[node_id]

    load_video_id = next_workflow_node_id(workflow)
    workflow[load_video_id] = {
        "class_type": "LoadVideo", "inputs": {"file": source_upload},
        "_meta": {"title": "上传视频"},
    }
    components_id = next_workflow_node_id(workflow)
    workflow[components_id] = {
        "class_type": "GetVideoComponents", "inputs": {"video": [load_video_id, 0]},
        "_meta": {"title": "读取视频画面与声音"},
    }
    sampler["ref_videos.ref_video_0"] = [components_id, 0]
    sampler["ref_video_audios.ref_video_audio_0"] = [components_id, 1]

    for index, uploaded_name in enumerate(reference_uploads[:9]):
        node_id = next_workflow_node_id(workflow)
        workflow[node_id] = {
            "class_type": "LoadImage", "inputs": {"image": uploaded_name},
            "_meta": {"title": f"参考图片 {index + 1}"},
        }
        sampler[f"ref_images.ref_image_{index}"] = [node_id, 0]
    return workflow


def try_cancel_comfy(prompt_id: str | None) -> None:
    if not prompt_id:
        return
    try:
        http_json("POST", f"{COMFYUI_URL}/queue", {"delete": [prompt_id]}, timeout=10)
    except Exception:
        pass
    try:
        http_json("POST", f"{COMFYUI_URL}/interrupt", {}, timeout=10)
    except Exception:
        pass


def comfy_history_output(record: dict[str, Any]) -> dict[str, str]:
    status = record.get("status") or {}
    if status.get("status_str") == "error":
        messages = [item[1] for item in status.get("messages", []) if item and item[0] == "execution_error"]
        raise RuntimeError(f"ComfyUI 生成失败：{messages[-1] if messages else status}")
    node_output = (record.get("outputs") or {}).get("92") or {}
    for field in ("images", "videos", "gifs"):
        entries = node_output.get(field) or []
        if entries:
            return entries[0]
    raise RuntimeError("ComfyUI 已结束，但没有返回 SaveVideo 输出")


def download_comfy_output(output: dict[str, str], destination: Path) -> None:
    filename = Path(output.get("filename", "")).name
    subfolder = Path(output.get("subfolder", ""))
    source = (COMFYUI_OUTPUT_DIR / subfolder / filename).resolve()
    output_root = COMFYUI_OUTPUT_DIR.resolve()
    if source.is_file() and (source == output_root or output_root in source.parents):
        shutil.copy2(source, destination)
        source.unlink(missing_ok=True)
        return
    query = urlencode({
        "filename": filename, "subfolder": output.get("subfolder", ""),
        "type": output.get("type", "output"),
    })
    req = urllib.request.Request(f"{COMFYUI_URL}/view?{query}")
    try:
        with urllib.request.urlopen(req, timeout=900) as response, destination.open("wb") as target:
            shutil.copyfileobj(response, target, length=1024 * 1024)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def run_ffmpeg(arguments: list[str], purpose: str) -> None:
    try:
        completed = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *arguments],
            capture_output=True,
            text=True,
            timeout=COMFYUI_TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("找不到 ffmpeg，请重新运行安装脚本") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{purpose}超时") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "未知错误").strip()[-2000:]
        raise RuntimeError(f"{purpose}失败：{detail}")


def run_external_command(
    command: list[str], purpose: str, cwd: Path | None = None,
    log_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=COMFYUI_TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"{purpose}所需程序不存在：{command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{purpose}超时") from exc
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"STDOUT\n{completed.stdout}\n\nSTDERR\n{completed.stderr}",
            encoding="utf-8",
        )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "未知错误").strip()[-4000:]
        raise RuntimeError(f"{purpose}失败：{detail}")
    return completed


def parse_product_box(row: sqlite3.Row | dict[str, Any]) -> list[float] | None:
    keys = set(row.keys())
    raw = row["product_box"] if "product_box" in keys else None
    if not raw:
        return None
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(parsed, list) and len(parsed) == 4:
            return [float(value) for value in parsed]
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return None


def unload_comfy_models() -> None:
    try:
        http_json(
            "POST", f"{COMFYUI_URL}/free",
            {"unload_models": True, "free_memory": True}, timeout=30,
        )
    except Exception:
        pass


def run_product_mask_composite(
    source: Path, candidate: Path, product_box: list[float] | None,
    product_reference: Path, target_description: str, destination: Path,
    work_folder: Path,
) -> None:
    if not SAM2_PYTHON.is_file() or not SAM2_SCRIPT.is_file():
        raise RuntimeError("SAM2 精准商品工具未安装，请运行 scripts/install_precision_tools.sh")
    unload_comfy_models()
    command = [
            str(SAM2_PYTHON), str(SAM2_SCRIPT),
            "--source", str(source),
            "--candidate", str(candidate),
            "--output", str(destination),
            "--product-reference", str(product_reference),
            "--target-description", target_description,
            "--florence-model", FLORENCE2_MODEL,
            "--model-id", SAM2_MODEL_ID,
            "--work-dir", str(work_folder / "sam2"),
        ]
    if product_box is not None:
        command.extend(["--box", json.dumps(product_box)])
    if SAM2_CHECKPOINT.is_file():
        command.extend(["--checkpoint", str(SAM2_CHECKPOINT), "--model-config", SAM2_CONFIG])
    run_external_command(
        command,
        "SAM2 商品跟踪与蒙版合成",
    )
    if not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError("SAM2 商品合成没有生成视频")


def materialize_reference_images(
    sources: list[str], work_folder: Path, prefix: str,
) -> list[Path]:
    results: list[Path] = []
    for index, source in enumerate(sources, start=1):
        suffix = Path(urlparse(source).path).suffix.lower() or ".jpg"
        if suffix not in IMAGE_EXTENSIONS:
            suffix = ".jpg"
        destination = work_folder / f"{prefix}_{index:02d}{suffix}"
        copy_media_to_comfy(source, destination)
        results.append(destination)
    return results


def facefusion_execution_providers() -> list[str]:
    try:
        completed = subprocess.run(
            [
                str(FACEFUSION_PYTHON), "-c",
                "import json, onnxruntime as ort; print(json.dumps(ort.get_available_providers()))",
            ],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise RuntimeError("无法检查 FaceFusion ONNX Runtime") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "未知错误").strip()[-2000:]
        raise RuntimeError(f"检查 FaceFusion ONNX Runtime 失败：{detail}")
    try:
        providers = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError("FaceFusion ONNX Runtime 没有返回执行设备") from exc
    if "CUDAExecutionProvider" not in providers:
        raise RuntimeError(
            "FaceFusion CUDA 未启用，当前执行设备为 "
            f"{', '.join(providers) or '无'}；请重新运行 scripts/install_precision_tools.sh"
        )
    return [str(provider) for provider in providers]


def run_facefusion(
    face_references: list[Path], target: Path, destination: Path, work_folder: Path,
) -> None:
    entrypoint = FACEFUSION_DIR / "facefusion.py"
    if not FACEFUSION_PYTHON.is_file() or not entrypoint.is_file():
        raise RuntimeError("FaceFusion 换脸工具未安装，请运行 scripts/install_precision_tools.sh")
    unload_comfy_models()
    facefusion_execution_providers()
    run_external_command(
        [
            str(FACEFUSION_PYTHON), str(entrypoint), "headless-run",
            "--source-paths", *[str(path) for path in face_references],
            "--target-path", str(target),
            "--output-path", str(destination),
            "--processors", "face_swapper",
            "--face-selector-mode", "one",
            "--face-mask-types", "box", "occlusion", "region",
            "--face-mask-blur", "0.15",
            "--face-swapper-model", "inswapper_128_fp16",
            "--face-swapper-pixel-boost", "256x256",
            "--execution-providers", "cuda",
            "--output-video-encoder", "libx264",
            "--output-video-preset", "veryfast",
            "--output-video-quality", "90",
            "--temp-path", str(work_folder / "facefusion-temp"),
            "--log-level", "info",
        ],
        "FaceFusion 人脸替换",
        cwd=FACEFUSION_DIR,
        log_path=work_folder / "facefusion.log",
    )
    ensure_visible_video(destination, "FaceFusion 输出")


def restore_original_audio(processed: Path, source: Path, destination: Path) -> None:
    run_ffmpeg(
        [
            "-i", str(processed), "-i", str(source),
            "-map", "0:v:0", "-map", "1:a?", "-c:v", "copy", "-c:a", "aac",
            "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(destination),
        ],
        "恢复原视频声音",
    )


def split_source_video(source: Path, durations: list[float], work_folder: Path) -> list[Path]:
    work_folder.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    start = 0.0
    for index, duration in enumerate(durations, start=1):
        destination = work_folder / f"source_part_{index:03d}.mp4"
        run_ffmpeg(
            [
                "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
                "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", str(destination),
            ],
            f"切割第 {index}/{len(durations)} 段视频",
        )
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError(f"切割第 {index}/{len(durations)} 段视频后没有生成文件")
        parts.append(destination)
        start += duration
    return parts


def concat_generated_videos(parts: list[Path], destination: Path, work_folder: Path) -> None:
    if not parts:
        raise RuntimeError("没有可合并的生成片段")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if len(parts) == 1:
        shutil.move(str(parts[0]), destination)
        return
    concat_file = work_folder / "generated_parts.txt"
    concat_lines = []
    for path in parts:
        escaped_path = str(path.resolve()).replace("'", "'\\''")
        concat_lines.append(f"file '{escaped_path}'\n")
    concat_file.write_text("".join(concat_lines), encoding="utf-8")
    copy_error: Exception | None = None
    try:
        run_ffmpeg(
            ["-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", "-movflags", "+faststart", str(destination)],
            "无损合并生成片段",
        )
    except Exception as exc:
        copy_error = exc
        destination.unlink(missing_ok=True)
        run_ffmpeg(
            [
                "-f", "concat", "-safe", "0", "-i", str(concat_file),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(destination),
            ],
            "重新编码并合并生成片段",
        )
    if not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError(f"合并片段后没有生成文件；首次合并错误：{copy_error or '无'}")


def local_source_for_split(row: sqlite3.Row, work_folder: Path) -> Path:
    source = str(row["source_video"])
    if not is_http_url(source):
        return Path(source)
    suffix = Path(urlparse(source).path).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        suffix = ".mp4"
    destination = work_folder / f"downloaded_source{suffix}"
    copy_media_to_comfy(source, destination)
    return destination


def claim_next_task() -> sqlite3.Row | None:
    with connect_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM tasks WHERE status='queued' AND deleted_at IS NULL "
            "AND cancel_requested=0 ORDER BY created_at, id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        now = utcnow()
        conn.execute(
            "UPDATE tasks SET status='starting',progress=5,stage='准备任务素材',current_segment=0,started_at=?,updated_at=? WHERE id=?",
            (now, now, row["id"]),
        )
        return conn.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone()


def get_control_state(task_id: str) -> sqlite3.Row | None:
    with connect_db() as conn:
        return conn.execute(
            "SELECT cancel_requested,deleted_at FROM tasks WHERE id=?", (task_id,)
        ).fetchone()


def task_was_cancelled(task_id: str, destination: Path | None = None) -> bool:
    control = get_control_state(task_id)
    if control is not None and not control["cancel_requested"] and not control["deleted_at"]:
        return False
    if destination is not None:
        destination.unlink(missing_ok=True)
    if control is not None and not control["deleted_at"]:
        mark_task(
            task_id, status="cancelled", progress=0,
            stage="任务已取消", finished_at=utcnow(), engine_job_id=None,
        )
    return True


def mark_task(task_id: str, **values: Any) -> None:
    if not values:
        return
    values["updated_at"] = utcnow()
    columns = ",".join(f"{key}=?" for key in values)
    with connect_db() as conn:
        conn.execute(f"UPDATE tasks SET {columns} WHERE id=?", (*values.values(), task_id))


def run_comfy_segment(
    task_id: str, segment_row: dict[str, Any], segment_index: int, segment_count: int,
    destination: Path,
) -> bool:
    prompt_id: str | None = None
    comfy_input_folder: Path | None = None
    websocket = None
    try:
        control = get_control_state(task_id)
        if control is None or control["cancel_requested"] or control["deleted_at"]:
            if control is not None and not control["deleted_at"]:
                mark_task(
                    task_id, status="cancelled", progress=0,
                    stage="任务已取消", finished_at=utcnow(),
                )
            return False
        mark_segment_task(task_id, segment_index, segment_count, "复制视频和参考图片", 8)
        source_upload, reference_uploads, comfy_input_folder = prepare_comfy_inputs(segment_row)
        mark_segment_task(task_id, segment_index, segment_count, "构建 MiniMax-H3 工作流", 12)
        workflow = build_comfy_workflow(segment_row, source_upload, reference_uploads)
        workflow_size = workflow["136"]["inputs"]
        mark_task(
            task_id,
            output_width=int(workflow_size["width"]),
            output_height=int(workflow_size["height"]),
        )
        client_id = f"h3-studio-{task_id}-{segment_index}"
        websocket = open_comfy_websocket(client_id)
        mark_segment_task(task_id, segment_index, segment_count, "提交任务到 ComfyUI", 14)
        submitted = http_json(
            "POST", f"{COMFYUI_URL}/prompt",
            {"prompt": workflow, "client_id": client_id}, timeout=120,
        )
        prompt_id = str(submitted.get("prompt_id") or "")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI 未返回 prompt_id：{submitted}")
        mark_segment_task(
            task_id, segment_index, segment_count, "等待 ComfyUI 执行", 15,
            status="running", engine_job_id=prompt_id,
        )

        started = time.monotonic()
        history_record: dict[str, Any] | None = None
        while not worker_stop.is_set():
            control = get_control_state(task_id)
            if control is None or control["cancel_requested"] or control["deleted_at"]:
                try_cancel_comfy(prompt_id)
                if control is not None and not control["deleted_at"]:
                    mark_task(
                        task_id, status="cancelled", progress=0,
                        stage="任务已取消", finished_at=utcnow(),
                    )
                return False
            if websocket is not None:
                try:
                    raw_message = websocket.recv(timeout=POLL_SECONDS)
                    if isinstance(raw_message, str):
                        event_finished = apply_comfy_event(
                            task_id, prompt_id, workflow, json.loads(raw_message),
                            segment_index, segment_count,
                        )
                        if event_finished:
                            mark_segment_task(
                                task_id, segment_index, segment_count, "读取生成结果", 94
                            )
                except TimeoutError:
                    pass
                except Exception:
                    try:
                        websocket.close()
                    except Exception:
                        pass
                    websocket = None
            history = http_json("GET", f"{COMFYUI_URL}/history/{prompt_id}", timeout=30)
            history_record = history.get(prompt_id)
            if history_record is not None:
                break
            elapsed = time.monotonic() - started
            if elapsed > COMFYUI_TIMEOUT:
                try_cancel_comfy(prompt_id)
                raise TimeoutError(f"ComfyUI 生成超过 {COMFYUI_TIMEOUT} 秒")
            if websocket is None:
                local_progress = min(88, 18 + int(elapsed / 20))
                mark_segment_task(
                    task_id, segment_index, segment_count, "ComfyUI 生成中", local_progress
                )
                worker_stop.wait(POLL_SECONDS)
        if worker_stop.is_set():
            try_cancel_comfy(prompt_id)
            return False
        if history_record is None:
            raise RuntimeError("ComfyUI 未返回任务结果")

        mark_segment_task(task_id, segment_index, segment_count, "保存生成片段", 96)
        download_comfy_output(comfy_history_output(history_record), destination)
        try:
            http_json("POST", f"{COMFYUI_URL}/history", {"delete": [prompt_id]}, timeout=10)
        except Exception:
            pass
        return True
    finally:
        if websocket is not None:
            try:
                websocket.close()
            except Exception:
                pass
        if comfy_input_folder is not None:
            shutil.rmtree(comfy_input_folder, ignore_errors=True)


def process_task(row: sqlite3.Row) -> None:
    task_id = row["id"]
    work_folder = WORK_DIR / task_id
    destination = OUTPUT_DIR / f"{task_id}.mp4"
    keep_work_folder = False
    try:
        precision_mode = bool(row["precision_mode"]) if "precision_mode" in row.keys() else False
        face_sources = row_reference_paths(row, "face")
        product_sources = row_reference_paths(row, "product")
        explicit_roles = bool(face_sources or product_sources)
        precision_mode = precision_mode and explicit_roles
        durations = segment_durations(float(row["duration"]))
        segment_count = len(durations) if (not precision_mode or product_sources) else 1
        mark_task(task_id, segment_count=segment_count, current_segment=0)
        shutil.rmtree(work_folder, ignore_errors=True)
        work_folder.mkdir(parents=True, exist_ok=True)

        if precision_mode:
            mark_task(task_id, status="running", stage="读取原视频", progress=6)
            local_source = local_source_for_split(row, work_folder)
            dimensions = probe_video_dimensions(local_source)
            if dimensions:
                mark_task(task_id, output_width=dimensions[0], output_height=dimensions[1])

            working_video = local_source
            if product_sources:
                product_box = parse_product_box(row)
                mark_task(task_id, stage="准备商品参考图", progress=7)
                product_references = materialize_reference_images(
                    product_sources, work_folder, "product_reference"
                )
                if len(durations) > 1:
                    mark_task(task_id, stage=f"正在切割源视频（共 {len(durations)} 段）", progress=7)
                    source_parts: list[str | Path] = split_source_video(
                        local_source, durations, work_folder / "source-parts"
                    )
                else:
                    source_parts = [local_source]

                product_row = dict(row)
                product_row["reference_images"] = json.dumps(product_sources, ensure_ascii=False)
                product_row["reference_roles"] = json.dumps(
                    ["product"] * len(product_sources), ensure_ascii=False
                )
                original_roles = row_reference_roles(row)
                product_indices = [
                    index for index, role in enumerate(original_roles, start=1)
                    if role == "product"
                ]
                mapping = "；".join(
                    f"当前参考图{new_index}对应原任务参考图{original_index}"
                    for new_index, original_index in enumerate(product_indices, start=1)
                )
                original_prompt = str(row["prompt"] or "").strip()
                product_row["prompt"] = (
                    "这是商品专用生成步骤，当前提供的所有参考图都只用于商品外观。"
                    f"{mapping}。只执行原提示词中的商品或服装替换，忽略人物和脸部替换要求。"
                    f"原提示词：{original_prompt}"
                )
                generated_parts: list[Path] = []
                for index, (source_part, part_duration) in enumerate(
                    zip(source_parts, durations), start=1
                ):
                    segment_row = dict(product_row)
                    segment_row["id"] = f"{task_id}_product_{index:03d}"
                    segment_row["source_video"] = str(source_part)
                    segment_row["duration"] = part_duration
                    segment_destination = work_folder / f"product_candidate_{index:03d}.mp4"
                    if not run_comfy_segment(
                        task_id, segment_row, index, len(durations), segment_destination
                    ):
                        return
                    generated_parts.append(segment_destination)

                if task_was_cancelled(task_id, destination):
                    return
                candidate = work_folder / "product_candidate.mp4"
                mark_task(
                    task_id, progress=94,
                    stage="合并商品候选片段" if len(generated_parts) > 1 else "准备商品候选视频",
                )
                concat_generated_videos(generated_parts, candidate, work_folder)
                ensure_visible_video(candidate, "MiniMax-H3 商品候选视频")
                product_stage = (
                    "SAM2 跟踪修正后的商品区域"
                    if product_box is not None
                    else "AI 识别并跟踪商品区域"
                )
                mark_task(task_id, status="running", progress=95, stage=product_stage)
                product_composite = work_folder / "product_composite.mp4"
                run_product_mask_composite(
                    local_source, candidate, product_box, product_references[0],
                    str(row["prompt"] or ""), product_composite, work_folder,
                )
                ensure_visible_video(product_composite, "SAM2 商品合成视频")
                working_video = product_composite

            if task_was_cancelled(task_id, destination):
                return
            if face_sources:
                mark_task(task_id, status="running", progress=97, stage="准备人脸参考图")
                face_references = materialize_reference_images(
                    face_sources, work_folder, "face_reference"
                )
                face_output = work_folder / "face_swapped.mp4"
                mark_task(task_id, progress=98, stage="FaceFusion 替换脸部")
                run_facefusion(face_references, working_video, face_output, work_folder)
                working_video = face_output

            if task_was_cancelled(task_id, destination):
                return
            mark_task(task_id, progress=99, stage="恢复原视频声音")
            restore_original_audio(working_video, local_source, destination)
            ensure_visible_video(destination, "最终视频")
            if task_was_cancelled(task_id, destination):
                return
            mark_task(
                task_id, status="completed", progress=100, output_path=str(destination),
                stage="精准替换完成", current_segment=segment_count, engine_job_id=None,
                finished_at=utcnow(), error=None,
            )
            return

        if segment_count > 1:
            mark_task(task_id, stage=f"正在切割源视频（共 {segment_count} 段）", progress=6)
            local_source = local_source_for_split(row, work_folder)
            source_parts = split_source_video(local_source, durations, work_folder)
        else:
            source_parts = [row["source_video"]]

        generated_parts: list[Path] = []
        for index, (source_part, part_duration) in enumerate(zip(source_parts, durations), start=1):
            segment_row = dict(row)
            segment_row["id"] = f"{task_id}_part_{index:03d}"
            segment_row["source_video"] = str(source_part)
            segment_row["duration"] = part_duration
            segment_destination = work_folder / f"generated_part_{index:03d}.mp4"
            if not run_comfy_segment(
                task_id, segment_row, index, segment_count, segment_destination
            ):
                return
            generated_parts.append(segment_destination)

        if task_was_cancelled(task_id, destination):
            return
        mark_task(task_id, progress=96, stage="合并生成片段" if segment_count > 1 else "保存生成视频")
        concat_generated_videos(generated_parts, destination, work_folder)
        ensure_visible_video(destination, "最终视频")
        if task_was_cancelled(task_id, destination):
            return
        mark_task(
            task_id, status="completed", progress=100, output_path=str(destination),
            stage="生成完成", current_segment=segment_count, engine_job_id=None,
            finished_at=utcnow(), error=None,
        )
    except Exception as exc:
        keep_work_folder = isinstance(exc, BlackVideoError)
        destination.unlink(missing_ok=True)
        detail = str(exc)
        if keep_work_folder:
            detail = f"{detail}；中间文件已保留：{work_folder}"
        mark_task(
            task_id, status="failed", progress=0, stage="生成失败",
            error=detail[:4000], finished_at=utcnow(),
        )
    finally:
        if not keep_work_folder:
            shutil.rmtree(work_folder, ignore_errors=True)


def worker_loop() -> None:
    while not worker_stop.is_set():
        row = claim_next_task()
        if row is None:
            worker_stop.wait(1.0)
            continue
        process_task(row)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global worker_thread
    init_db()
    worker_stop.clear()
    worker_thread = threading.Thread(target=worker_loop, name="h3-task-worker", daemon=True)
    worker_thread.start()
    yield
    worker_stop.set()
    if worker_thread:
        worker_thread.join(timeout=5)


app = FastAPI(title="MiniMax H3 Video Studio", version="3.0.0", lifespan=lifespan)


@app.get("/api/config")
def config() -> dict[str, Any]:
    return {
        "max_upload_gb": MAX_UPLOAD_GB,
        "engine": "comfyui",
        "min_video_seconds": MIN_VIDEO_SECONDS,
        "max_segment_seconds": MAX_SEGMENT_SECONDS,
        "long_video_split": True,
        "default_quality": "low",
        "quality_dimensions": QUALITY_DIMENSIONS,
        "samplers": sorted(SAMPLER_NAMES),
        "schedulers": sorted(SCHEDULER_NAMES),
        "default_sampler": DEFAULT_SAMPLER,
        "default_scheduler": DEFAULT_SCHEDULER,
        "default_steps": DEFAULT_STEPS,
        "default_denoise": DEFAULT_DENOISE,
    }


@app.get("/api/system/status")
def system_status() -> dict[str, Any]:
    return {"gpu": gpu_status(), "updated_at": utcnow()}


@app.get("/health")
def health() -> dict[str, Any]:
    engine_ok = False
    try:
        req = urllib.request.Request(f"{COMFYUI_URL}/system_stats")
        with urllib.request.urlopen(req, timeout=2) as response:
            engine_ok = response.status < 500
    except Exception:
        pass
    return {
        "app": "ok", "engine": "ok" if engine_ok else "unavailable",
        "engine_name": "ComfyUI", "engine_url": COMFYUI_URL,
    }


@app.post("/api/tasks", status_code=201)
async def create_manual_task(
    upload_videos: Annotated[list[UploadFile], File(...)],
    reference_images: Annotated[list[UploadFile], File()] = [],
    prompt: Annotated[str, Form()] = "",
    aspect_ratio: Annotated[str, Form()] = "auto",
    quality: Annotated[str, Form()] = "low",
    seed: Annotated[int | None, Form()] = None,
    sampler: Annotated[str, Form()] = DEFAULT_SAMPLER,
    scheduler: Annotated[str, Form()] = DEFAULT_SCHEDULER,
    steps: Annotated[int, Form()] = DEFAULT_STEPS,
    denoise: Annotated[float, Form()] = DEFAULT_DENOISE,
    video_durations: Annotated[str, Form()] = "[]",
    reference_roles: Annotated[str, Form()] = "[]",
    product_boxes: Annotated[str, Form()] = "[]",
    precision_mode: Annotated[bool, Form()] = True,
) -> dict[str, Any]:
    if not upload_videos or not any(item.filename for item in upload_videos):
        raise HTTPException(400, "请至少上传一个视频")
    if len([item for item in upload_videos if item.filename]) > 3:
        raise HTTPException(400, "一次最多上传 3 个视频")
    if len([item for item in reference_images if item.filename]) > 9:
        raise HTTPException(400, "参考图片最多上传 9 张")
    try:
        hints_raw = json.loads(video_durations)
        duration_hints = [float(value) for value in hints_raw] if isinstance(hints_raw, list) else []
    except (json.JSONDecodeError, TypeError, ValueError):
        duration_hints = []
    try:
        roles_raw = json.loads(reference_roles)
        parsed_roles = [str(value) for value in roles_raw] if isinstance(roles_raw, list) else []
    except (json.JSONDecodeError, TypeError, ValueError):
        parsed_roles = []
    try:
        boxes_raw = json.loads(product_boxes)
        parsed_boxes = boxes_raw if isinstance(boxes_raw, list) else []
    except (json.JSONDecodeError, TypeError, ValueError):
        parsed_boxes = []
    task_folder = UPLOAD_DIR / uuid.uuid4().hex
    created: list[str] = []
    try:
        saved_references: list[Path] = []
        for index, image in enumerate(reference_images, start=1):
            if image.filename:
                saved_references.append(await save_upload(
                    image, task_folder / f"ref_{index:02d}_{safe_name(image.filename)}", IMAGE_EXTENSIONS, "参考图片"
                ))

        prepared: list[tuple[Path, str, float]] = []
        for index, video in enumerate(upload_videos, start=1):
            if not video.filename:
                continue
            original_name = Path(video.filename).name
            saved_video = await save_upload(
                video, task_folder / f"video_{index:03d}_{safe_name(original_name)}", VIDEO_EXTENSIONS, "上传视频"
            )
            hint = duration_hints[index - 1] if index - 1 < len(duration_hints) else None
            duration = probe_video_duration(saved_video, hint)
            prepared.append((saved_video, original_name, duration))

        if saved_references and not parsed_roles:
            parsed_roles = ["auto"] * len(saved_references)
            precision_mode = False
        elif saved_references and len(parsed_roles) != len(saved_references):
            raise ValueError("请为每张参考图片选择“脸部”或“商品”")
        validate_task_values(
            prepared[0][2], aspect_ratio, quality, sampler, scheduler, steps, denoise
        )
        for video_index, (saved_video, original_name, duration) in enumerate(prepared):
            product_box = parsed_boxes[video_index] if video_index < len(parsed_boxes) else None
            created.append(insert_task(
                prompt=prompt, source_video=saved_video, reference_images=saved_references,
                duration=duration, aspect_ratio=aspect_ratio, seed=seed, quality=quality,
                display_name=original_name, reference_roles=parsed_roles,
                product_box=product_box, precision_mode=precision_mode,
                sampler=sampler, scheduler=scheduler, steps=steps, denoise=denoise,
            ))
        return {"created": len(created), "task_ids": created, "status": "queued"}
    except HTTPException:
        if not created:
            shutil.rmtree(task_folder, ignore_errors=True)
        raise
    except Exception as exc:
        if not created:
            shutil.rmtree(task_folder, ignore_errors=True)
        raise HTTPException(400, str(exc)) from exc


def text_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def int_cell(value: Any, default: int | None = None) -> int | None:
    if value is None or str(value).strip() == "":
        return default
    return int(float(value))


def float_cell(value: Any, default: float) -> float:
    if value is None or str(value).strip() == "":
        return default
    return float(value)


@app.post("/api/tasks/import", status_code=201)
async def import_excel_tasks(
    excel: Annotated[UploadFile, File(...)],
) -> dict[str, Any]:
    validate_extension(excel.filename or "", EXCEL_EXTENSIONS, "Excel")
    created: list[str] = []
    errors: list[dict[str, Any]] = []
    try:
        excel_bytes = await excel.read()
        if len(excel_bytes) > 50 * 1024 * 1024:
            raise HTTPException(413, "Excel 文件不能超过 50 MB")
        await excel.close()

        workbook = load_workbook(io.BytesIO(excel_bytes), read_only=True, data_only=True)
        if "任务" not in workbook.sheetnames:
            raise HTTPException(400, "Excel 中缺少“任务”工作表，请使用页面下载的模板")
        sheet = workbook["任务"]
        rows = sheet.iter_rows(values_only=True)
        headers = [text_cell(value) for value in next(rows, [])]
        required = {"upload_video_url"}
        if not required.issubset(headers):
            raise HTTPException(400, "Excel 缺少必填列：upload_video_url")
        index = {name: position for position, name in enumerate(headers) if name}
        data_rows = [values for values in rows if any(value is not None and str(value).strip() for value in values)]
        if len(data_rows) > 3:
            raise HTTPException(400, "一次最多导入 3 条视频任务")

        for excel_row, values in enumerate(data_rows, start=2):
            def cell(column: str) -> Any:
                position = index.get(column)
                return values[position] if position is not None and position < len(values) else None
            try:
                source_url = validate_public_media_url(text_cell(cell("upload_video_url")), "upload_video_url")
                reference_urls = [
                    validate_public_media_url(item.strip(), "reference_image_url")
                    for item in re.split(r"[,，;；\n]+", text_cell(cell("reference_image_urls"))) if item.strip()
                ]
                if len(reference_urls) > 9:
                    raise ValueError("reference_image_urls 最多填写 9 个图片 URL")
                reference_roles = [
                    item.strip().lower()
                    for item in re.split(r"[,，;；\n]+", text_cell(cell("reference_roles"))) if item.strip()
                ]
                if reference_urls and len(reference_roles) != len(reference_urls):
                    raise ValueError("reference_roles 必须与参考图片逐行对应")
                if any(role not in {"face", "product"} for role in reference_roles):
                    raise ValueError("reference_roles 只能填写 face 或 product")
                product_box_text = text_cell(cell("product_box"))
                product_box = None
                if product_box_text:
                    values_box = [item.strip() for item in re.split(r"[,，]+", product_box_text)]
                    if len(values_box) != 4:
                        raise ValueError("product_box 必须填写 x1,y1,x2,y2 四个 0–1 坐标")
                    product_box = [float(value) for value in values_box]
                duration = probe_video_duration(source_url)
                aspect_ratio = text_cell(cell("aspect_ratio")) or "auto"
                quality = text_cell(cell("quality")) or "low"
                seed = int_cell(cell("seed"), None)
                scheduler = text_cell(cell("scheduler")) or DEFAULT_SCHEDULER
                sampler = text_cell(cell("sampler")) or DEFAULT_SAMPLER
                steps = int_cell(cell("steps"), DEFAULT_STEPS)
                denoise = float_cell(cell("denoise"), DEFAULT_DENOISE)
                task_id = insert_task(
                    prompt=text_cell(cell("prompt")), source_video=source_url,
                    reference_images=reference_urls, duration=duration, aspect_ratio=aspect_ratio,
                    seed=seed, quality=quality, display_name=media_display_name(source_url),
                    reference_roles=reference_roles, product_box=product_box, precision_mode=True,
                    scheduler=scheduler, sampler=sampler,
                    steps=steps if steps is not None else DEFAULT_STEPS,
                    denoise=denoise,
                )
                created.append(task_id)
            except Exception as exc:
                errors.append({"row": excel_row, "error": str(exc)})
        return {"created": len(created), "task_ids": created, "errors": errors}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Excel 导入失败：{exc}") from exc


@app.get("/api/tasks")
def list_tasks() -> dict[str, Any]:
    with connect_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE deleted_at IS NULL ORDER BY created_at DESC, id DESC"
        ).fetchall()
        queued = [row["id"] for row in reversed(rows) if row["status"] == "queued"]
    positions = {task_id: i + 1 for i, task_id in enumerate(queued)}
    return {"items": [task_to_dict(row, positions.get(row["id"])) for row in rows]}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str) -> dict[str, Any]:
    with connect_db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=? AND deleted_at IS NULL", (task_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "任务不存在")
        now = utcnow()
        conn.execute(
            "UPDATE tasks SET cancel_requested=1,deleted_at=?,updated_at=? WHERE id=?",
            (now, now, task_id),
        )
    if row["status"] in {"starting", "running"}:
        try_cancel_comfy(row["engine_job_id"])
    if row["output_path"]:
        Path(row["output_path"]).unlink(missing_ok=True)
    return {"deleted": True, "was_running": row["status"] in {"starting", "running"}}


@app.post("/api/tasks/{task_id}/retry")
def retry_task(task_id: str) -> dict[str, Any]:
    with connect_db() as conn:
        row = conn.execute("SELECT status FROM tasks WHERE id=? AND deleted_at IS NULL", (task_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "任务不存在")
        if row["status"] not in {"failed", "cancelled"}:
            raise HTTPException(409, "只有失败或已取消的任务可以重试")
        conn.execute(
            "UPDATE tasks SET status='queued',progress=0,stage='等待队列',error=NULL,cancel_requested=0,"
            "engine_job_id=NULL,current_segment=0,output_width=NULL,output_height=NULL,"
            "started_at=NULL,finished_at=NULL,updated_at=? WHERE id=?",
            (utcnow(), task_id),
        )
    return {"id": task_id, "status": "queued"}


@app.get("/api/tasks/{task_id}/output")
def task_output(task_id: str):
    with connect_db() as conn:
        row = conn.execute(
            "SELECT name,status,output_path FROM tasks WHERE id=? AND deleted_at IS NULL", (task_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "任务不存在")
    if row["status"] != "completed" or not row["output_path"] or not Path(row["output_path"]).is_file():
        raise HTTPException(409, "成片尚未生成")
    filename = safe_name(row["name"]) + ".mp4"
    return FileResponse(row["output_path"], media_type="video/mp4", filename=filename)


@app.get("/api/template")
def excel_template() -> StreamingResponse:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "任务"
    headers = [
        "upload_video_url", "reference_image_urls", "reference_roles", "product_box",
        "prompt", "aspect_ratio", "quality", "seed",
        "scheduler", "sampler", "steps", "denoise",
    ]
    sheet.append(headers)
    sheet.append([
        "https://your-domain.example/videos/source.mp4",
        "https://your-domain.example/images/person.jpg\nhttps://your-domain.example/images/product.png",
        "face\nproduct", "",
        "将视频中的人物脸部换成参考图1，人物身上的上衣换成参考图2，并保持手部遮挡自然。",
        "auto", "low", "", "simple", "res_multistep", 20, 1.0,
    ])
    header_fill = PatternFill("solid", fgColor="253449")
    for cell in sheet[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
    for cell in sheet[2]:
        cell.fill = PatternFill("solid", fgColor="FFF4CC")
        cell.alignment = Alignment(vertical="top", wrap_text=True)
    sheet.row_dimensions[2].height = 38
    widths = [52, 62, 24, 28, 62, 18, 18, 18, 20, 22, 14, 14]
    for i, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + i)].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = "A1:L2"
    sheet["A1"].comment = Comment(
        "必填；用户自行提供可公开访问的视频 URL。不能短于 4 秒；超过 15 秒会自动分段生成并合并。",
        "MiniMax H3 Studio",
    )
    sheet["B1"].comment = Comment("可选；用户自行提供人物或商品图片 URL，每行一个，最多 9 个。", "MiniMax H3 Studio")
    sheet["C1"].comment = Comment("有参考图时必填；与图片逐行对应，只能填 face 或 product。", "MiniMax H3 Studio")
    sheet["D1"].comment = Comment("可选；AI 默认根据提示词和商品参考图定位。识别不准时填写首帧商品框的 0–1 坐标 x1,y1,x2,y2。", "MiniMax H3 Studio")
    sheet["G1"].comment = Comment("可选；low、standard 或 high。默认 low，速度最快。", "MiniMax H3 Studio")
    sheet["I1"].comment = Comment("可选；噪声调度器，默认 simple。", "MiniMax H3 Studio")
    sheet["J1"].comment = Comment("可选；采样器，默认 res_multistep。", "MiniMax H3 Studio")
    sheet["K1"].comment = Comment("可选；采样步数 1–100，默认 20。", "MiniMax H3 Studio")
    sheet["L1"].comment = Comment("可选；生成变化强度 0.01–1.00，默认 1.0。", "MiniMax H3 Studio")
    ratio_validation = DataValidation(type="list", formula1='"auto,16:9,9:16,1:1,4:3,3:4,21:9"')
    sheet.add_data_validation(ratio_validation)
    ratio_validation.add("F2:F1000")
    quality_validation = DataValidation(type="list", formula1='"low,standard,high"')
    sheet.add_data_validation(quality_validation)
    quality_validation.add("G2:G1000")
    scheduler_validation = DataValidation(type="list", formula1='"simple,normal,karras,exponential,sgm_uniform"')
    sheet.add_data_validation(scheduler_validation)
    scheduler_validation.add("I2:I1000")
    sampler_validation = DataValidation(type="list", formula1='"res_multistep,euler,euler_ancestral,heun,dpmpp_2m,dpmpp_2m_sde"')
    sheet.add_data_validation(sampler_validation)
    sampler_validation.add("J2:J1000")
    steps_validation = DataValidation(type="whole", operator="between", formula1="1", formula2="100")
    sheet.add_data_validation(steps_validation)
    steps_validation.add("K2:K1000")
    denoise_validation = DataValidation(type="decimal", operator="between", formula1="0.01", formula2="1")
    sheet.add_data_validation(denoise_validation)
    denoise_validation.add("L2:L1000")

    guide = workbook.create_sheet("字段说明")
    guide.append(["字段", "是否必填", "说明"])
    guide_rows = [
        ("upload_video_url", "是", "用户自行提供可公开访问的视频 URL。不能短于 4 秒；超过 15 秒会自动分段生成并合并。"),
        ("reference_image_urls", "否", "用户自行提供人物和商品参考图片 URL；每行一个，最多 9 个。"),
        ("reference_roles", "有参考图时是", "与 reference_image_urls 逐行对应。脸部图填 face，商品图填 product。"),
        ("product_box", "否", "AI 默认自动定位。识别不准时填写首帧原商品框的 0–1 坐标 x1,y1,x2,y2，例如 0.42,0.46,0.72,0.88。"),
        ("prompt", "否", "写清商品目标会提高定位准确率，例如“人物身上的上衣换成参考图2”。"),
        ("aspect_ratio", "否", "默认 auto，跟随上传视频比例；也可指定固定比例。"),
        ("quality", "否", "清晰度：low（低清，默认且最快）、standard（标清）或 high（高清且最慢）。"),
        ("seed", "否", "固定随机种子便于复现；空白时自动生成。"),
        ("scheduler", "否", "噪声调度器：simple（默认）、normal、karras、exponential 或 sgm_uniform。"),
        ("sampler", "否", "采样器：res_multistep（默认）、euler、euler_ancestral、heun、dpmpp_2m 或 dpmpp_2m_sde。"),
        ("steps", "否", "采样步数 1–100，默认 20；通常越高越慢。"),
        ("denoise", "否", "生成变化强度 0.01–1.00，默认 1.0；越低越接近输入。"),
    ]
    for row in guide_rows:
        guide.append(row)
    for cell in guide[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = header_fill
    guide.column_dimensions["A"].width = 24
    guide.column_dimensions["B"].width = 14
    guide.column_dimensions["C"].width = 72
    guide.freeze_panes = "A2"

    content = io.BytesIO()
    workbook.save(content)
    content.seek(0)
    headers_out = {"Content-Disposition": 'attachment; filename="h3_tasks_template.xlsx"'}
    return StreamingResponse(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers_out,
    )


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
