from __future__ import annotations

import io
import json
import os
import secrets
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation


ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
DATA_DIR = Path(os.getenv("H3_STUDIO_DATA_DIR", ROOT / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"
DB_PATH = DATA_DIR / "studio.db"
ENGINE_URL = os.getenv("H3_ENGINE_URL", "http://127.0.0.1:30011").rstrip("/")
API_TOKEN = os.getenv("H3_STUDIO_TOKEN", "").strip()
POLL_SECONDS = max(1.0, float(os.getenv("H3_POLL_SECONDS", "3")))
MAX_UPLOAD_GB = max(1, int(os.getenv("H3_MAX_UPLOAD_GB", "20")))
MAX_UPLOAD_BYTES = MAX_UPLOAD_GB * 1024**3
SERVER_ASSET_ROOT = os.getenv("H3_SERVER_ASSET_ROOT", "").strip()

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
EXCEL_EXTENSIONS = {".xlsx", ".xlsm"}
worker_stop = threading.Event()
worker_thread: threading.Thread | None = None


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
    for folder in (DATA_DIR, UPLOAD_DIR, OUTPUT_DIR):
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
                duration INTEGER NOT NULL,
                aspect_ratio TEXT NOT NULL,
                source_start REAL NOT NULL DEFAULT 0,
                seed INTEGER NOT NULL,
                engine_job_id TEXT,
                output_path TEXT,
                error TEXT,
                progress INTEGER NOT NULL DEFAULT 0,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_queue
                ON tasks(status, deleted_at, created_at);
            """
        )
        conn.execute(
            "UPDATE tasks SET status='queued', progress=0, started_at=NULL, "
            "updated_at=? WHERE status IN ('starting','running') AND deleted_at IS NULL",
            (utcnow(),),
        )


def is_authorized(authorization: str | None, x_api_key: str | None) -> bool:
    if not API_TOKEN:
        return True
    bearer = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    supplied = bearer or (x_api_key or "")
    return secrets.compare_digest(supplied, API_TOKEN)


def validate_extension(filename: str, allowed: set[str], label: str) -> None:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in allowed:
        expected = "、".join(sorted(allowed))
        raise HTTPException(400, f"{label}格式不支持，请使用 {expected}")


def validate_task_values(duration: int, aspect_ratio: str, source_start: float) -> None:
    if duration < 4 or duration > 15:
        raise HTTPException(400, "时长必须为 4–15 秒")
    if aspect_ratio not in {"auto", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"}:
        raise HTTPException(400, "不支持的画面比例")
    if source_start < 0:
        raise HTTPException(400, "源视频起始秒数不能小于 0")


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
            result[field] = Path(result[field]).name
    result["cancel_requested"] = bool(result["cancel_requested"])
    result["queue_position"] = queue_position
    result["download_url"] = f"/api/tasks/{row['id']}/output" if row["status"] == "completed" else None
    return result


def insert_task(
    *, name: str, prompt: str, source_video: Path, character_image: Path | None,
    product_image: Path | None, duration: int, aspect_ratio: str,
    source_start: float, seed: int | None,
) -> str:
    validate_task_values(duration, aspect_ratio, source_start)
    if not source_video.is_file() or source_video.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("source_video 必须是已上传的受支持视频文件")
    for image, label in ((character_image, "character_image"), (product_image, "product_image")):
        if image and (not image.is_file() or image.suffix.lower() not in IMAGE_EXTENSIONS):
            raise ValueError(f"{label} 必须是已上传的受支持图片文件")
    task_id = uuid.uuid4().hex
    now = utcnow()
    actual_seed = seed if seed is not None else secrets.randbelow(2_147_483_647)
    with connect_db() as conn:
        conn.execute(
            """INSERT INTO tasks (
                id,name,status,prompt,source_video,character_image,product_image,
                duration,aspect_ratio,source_start,seed,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id, name.strip() or f"任务-{task_id[:6]}", "queued", prompt.strip(),
                str(source_video), str(character_image) if character_image else None,
                str(product_image) if product_image else None, duration, aspect_ratio,
                source_start, actual_seed, now, now,
            ),
        )
    return task_id


def build_h3_prompt(row: sqlite3.Row | dict[str, Any]) -> str:
    definitions = [
        "<Video 1> is the source video. Preserve its shot order, camera motion, subject motion, "
        "timing, composition, lighting, background, and synchronized soundtrack unless explicitly changed below."
    ]
    instructions: list[str] = []
    picture_index = 0
    if row["character_image"]:
        picture_index += 1
        definitions.append(
            f"<Picture {picture_index}> defines the replacement person's identity, face, hair, and appearance."
        )
        instructions.append(
            f"Replace the principal person in <Video 1> with the person from <Picture {picture_index}>. "
            "Transfer the original person's pose, motion, gaze, interaction, scale, placement, and timing to the replacement."
        )
    if row["product_image"]:
        picture_index += 1
        definitions.append(
            f"<Picture {picture_index}> defines the replacement product's exact category, shape, colors, branding, and packaging."
        )
        instructions.append(
            f"Replace the principal handled or displayed product in <Video 1> with the product from <Picture {picture_index}>. "
            "Keep realistic size, perspective, occlusion, hand contact, reflections, shadows, and temporal consistency."
        )
    if not instructions:
        instructions.append("Apply the requested edit to <Video 1> while preserving all unspecified content.")

    user_request = row["prompt"].strip() or "Keep the result natural, photorealistic, and temporally consistent."
    return (
        "subject_definitions:\n" + "\n".join(definitions) +
        "\n\nsummary:\n[video editing + identity/product reference + audio reuse] "
        "The target is a faithful edited version of <Video 1>. " + " ".join(instructions) +
        " Preserve the original environment and synchronized audio. Do not add extra people, products, text, logos, cuts, or camera moves."
        "\n\nretention_analysis:\n<Video 1>: fully_preserved except for the explicitly requested replacements. "
        "Motion, timing, framing, background, lighting, and audio remain consistent."
        "\n\ndetailed_description:\n" + user_request
    )


def build_engine_payload(row: sqlite3.Row) -> dict[str, Any]:
    conditions: list[dict[str, Any]] = [
        {
            "type": "video",
            "uri": Path(row["source_video"]).resolve().as_uri(),
            "role": "reference",
            "start_time_seconds": float(row["source_start"]),
        }
    ]
    for path in (row["character_image"], row["product_image"]):
        if path:
            conditions.append({"type": "image", "uri": Path(path).resolve().as_uri(), "role": "reference"})
    return {
        "model": "MiniMaxAI/MiniMax-H3",
        "prompt": build_h3_prompt(row),
        "seconds": int(row["duration"]),
        "task": "ref2va",
        "conditions": conditions,
        "target": {
            "short_edge": 768,
            "aspect_ratio": row["aspect_ratio"],
            "duration_seconds": float(row["duration"]),
        },
        "num_outputs_per_prompt": 1,
        "num_inference_steps": 50,
        "flow_shift": 12.0,
        "audio_flow_shift": 3.0,
        "seed": int(row["seed"]),
    }


def http_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: int = 60) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"H3 服务返回 HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 H3 服务 {ENGINE_URL}: {exc.reason}") from exc


def try_cancel_engine(engine_job_id: str | None) -> None:
    if not engine_job_id:
        return
    try:
        http_json("DELETE", f"{ENGINE_URL}/v1/videos/{engine_job_id}", timeout=10)
    except Exception:
        pass


def download_output(engine_job_id: str, destination: Path) -> None:
    req = urllib.request.Request(f"{ENGINE_URL}/v1/videos/{engine_job_id}/content")
    try:
        with urllib.request.urlopen(req, timeout=900) as response, destination.open("wb") as target:
            shutil.copyfileobj(response, target, length=1024 * 1024)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


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
            "UPDATE tasks SET status='starting',progress=5,started_at=?,updated_at=? WHERE id=?",
            (now, now, row["id"]),
        )
        return conn.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone()


def get_control_state(task_id: str) -> sqlite3.Row | None:
    with connect_db() as conn:
        return conn.execute(
            "SELECT cancel_requested,deleted_at FROM tasks WHERE id=?", (task_id,)
        ).fetchone()


def mark_task(task_id: str, **values: Any) -> None:
    if not values:
        return
    values["updated_at"] = utcnow()
    columns = ",".join(f"{key}=?" for key in values)
    with connect_db() as conn:
        conn.execute(f"UPDATE tasks SET {columns} WHERE id=?", (*values.values(), task_id))


def process_task(row: sqlite3.Row) -> None:
    task_id = row["id"]
    engine_job_id: str | None = None
    try:
        submitted = http_json("POST", f"{ENGINE_URL}/v1/videos", build_engine_payload(row), timeout=120)
        engine_job_id = str(submitted.get("id") or "")
        if not engine_job_id:
            raise RuntimeError(f"H3 服务未返回任务 ID: {submitted}")
        mark_task(task_id, status="running", progress=15, engine_job_id=engine_job_id)

        started = time.monotonic()
        while not worker_stop.is_set():
            control = get_control_state(task_id)
            if control is None or control["cancel_requested"] or control["deleted_at"]:
                try_cancel_engine(engine_job_id)
                if control is not None and not control["deleted_at"]:
                    mark_task(task_id, status="cancelled", progress=0, finished_at=utcnow())
                return
            remote = http_json("GET", f"{ENGINE_URL}/v1/videos/{engine_job_id}", timeout=30)
            status = str(remote.get("status", "")).lower()
            if status in {"completed", "succeeded"}:
                break
            if status in {"failed", "error", "cancelled", "canceled"}:
                message = remote.get("error") or remote.get("message") or json.dumps(remote, ensure_ascii=False)
                raise RuntimeError(f"H3 生成失败: {message}")
            elapsed = time.monotonic() - started
            progress = min(92, 18 + int(elapsed / 15))
            mark_task(task_id, progress=progress)
            worker_stop.wait(POLL_SECONDS)
        if worker_stop.is_set():
            return

        destination = OUTPUT_DIR / f"{task_id}.mp4"
        mark_task(task_id, progress=95)
        download_output(engine_job_id, destination)
        mark_task(
            task_id, status="completed", progress=100, output_path=str(destination),
            finished_at=utcnow(), error=None,
        )
    except Exception as exc:
        mark_task(task_id, status="failed", progress=0, error=str(exc)[:4000], finished_at=utcnow())


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


app = FastAPI(title="MiniMax H3 Video Studio", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def api_auth(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.url.path not in {"/api/config"}:
        if not is_authorized(request.headers.get("authorization"), request.headers.get("x-api-key")):
            return JSONResponse({"detail": "访问令牌无效"}, status_code=401)
    return await call_next(request)


@app.get("/api/config")
def config() -> dict[str, Any]:
    return {"auth_required": bool(API_TOKEN), "max_upload_gb": MAX_UPLOAD_GB}


@app.get("/health")
def health() -> dict[str, Any]:
    engine_ok = False
    try:
        req = urllib.request.Request(f"{ENGINE_URL}/health")
        with urllib.request.urlopen(req, timeout=2) as response:
            engine_ok = response.status < 500
    except Exception:
        pass
    return {"app": "ok", "engine": "ok" if engine_ok else "unavailable", "engine_url": ENGINE_URL}


@app.post("/api/tasks", status_code=201)
async def create_manual_task(
    source_video: Annotated[UploadFile, File(...)],
    character_image: Annotated[UploadFile | None, File()] = None,
    product_image: Annotated[UploadFile | None, File()] = None,
    name: Annotated[str, Form()] = "",
    prompt: Annotated[str, Form()] = "",
    duration: Annotated[int, Form()] = 5,
    aspect_ratio: Annotated[str, Form()] = "16:9",
    source_start: Annotated[float, Form()] = 0,
    seed: Annotated[int | None, Form()] = None,
) -> dict[str, Any]:
    validate_task_values(duration, aspect_ratio, source_start)
    task_folder = UPLOAD_DIR / uuid.uuid4().hex
    try:
        source = await save_upload(
            source_video, task_folder / ("source_" + safe_name(source_video.filename or "source.mp4")), VIDEO_EXTENSIONS, "源视频"
        )
        character = None
        product = None
        if character_image and character_image.filename:
            character = await save_upload(
                character_image, task_folder / ("character_" + safe_name(character_image.filename)), IMAGE_EXTENSIONS, "人物参考图"
            )
        if product_image and product_image.filename:
            product = await save_upload(
                product_image, task_folder / ("product_" + safe_name(product_image.filename)), IMAGE_EXTENSIONS, "商品参考图"
            )
        task_id = insert_task(
            name=name, prompt=prompt, source_video=source, character_image=character,
            product_image=product, duration=duration, aspect_ratio=aspect_ratio,
            source_start=source_start, seed=seed,
        )
        return {"id": task_id, "status": "queued"}
    except Exception:
        shutil.rmtree(task_folder, ignore_errors=True)
        raise


def text_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def int_cell(value: Any, default: int | None = None) -> int | None:
    if value is None or str(value).strip() == "":
        return default
    return int(float(value))


def float_cell(value: Any, default: float = 0) -> float:
    if value is None or str(value).strip() == "":
        return default
    return float(value)


def resolve_excel_asset(value: str, uploaded: dict[str, Path]) -> Path | None:
    value = value.strip()
    if not value:
        return None
    basename = Path(value).name.lower()
    if basename in uploaded:
        return uploaded[basename]
    if SERVER_ASSET_ROOT:
        root = Path(SERVER_ASSET_ROOT).resolve()
        candidate = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
        if candidate.is_file() and (candidate == root or root in candidate.parents):
            return candidate
    raise ValueError(f"找不到素材“{value}”，请同时选择并上传该文件")


@app.post("/api/tasks/import", status_code=201)
async def import_excel_tasks(
    excel: Annotated[UploadFile, File(...)],
    media: Annotated[list[UploadFile], File()] = [],
) -> dict[str, Any]:
    validate_extension(excel.filename or "", EXCEL_EXTENSIONS, "Excel")
    batch_folder = UPLOAD_DIR / f"batch-{uuid.uuid4().hex}"
    batch_folder.mkdir(parents=True, exist_ok=True)
    uploaded: dict[str, Path] = {}
    created: list[str] = []
    errors: list[dict[str, Any]] = []
    try:
        excel_bytes = await excel.read()
        if len(excel_bytes) > 50 * 1024 * 1024:
            raise HTTPException(413, "Excel 文件不能超过 50 MB")
        await excel.close()
        for item in media:
            if not item.filename:
                continue
            key = Path(item.filename).name.lower()
            if key in uploaded:
                raise HTTPException(400, f"素材文件名重复：{item.filename}")
            allowed = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS
            uploaded[key] = await save_upload(item, batch_folder / safe_name(item.filename), allowed, "素材")

        workbook = load_workbook(io.BytesIO(excel_bytes), read_only=True, data_only=True)
        if "任务" not in workbook.sheetnames:
            raise HTTPException(400, "Excel 中缺少“任务”工作表，请使用页面下载的模板")
        sheet = workbook["任务"]
        rows = sheet.iter_rows(values_only=True)
        headers = [text_cell(value) for value in next(rows, [])]
        required = {"source_video"}
        if not required.issubset(headers):
            raise HTTPException(400, "Excel 缺少必填列：source_video")
        index = {name: position for position, name in enumerate(headers) if name}

        for excel_row, values in enumerate(rows, start=2):
            if not any(value is not None and str(value).strip() for value in values):
                continue
            def cell(column: str) -> Any:
                position = index.get(column)
                return values[position] if position is not None and position < len(values) else None
            try:
                source = resolve_excel_asset(text_cell(cell("source_video")), uploaded)
                if source is None:
                    raise ValueError("source_video 不能为空")
                character = resolve_excel_asset(text_cell(cell("character_image")), uploaded)
                product = resolve_excel_asset(text_cell(cell("product_image")), uploaded)
                duration = int_cell(cell("duration"), 5) or 5
                aspect_ratio = text_cell(cell("aspect_ratio")) or "16:9"
                source_start = float_cell(cell("source_start"), 0)
                seed = int_cell(cell("seed"), None)
                task_id = insert_task(
                    name=text_cell(cell("task_name")) or f"Excel 第 {excel_row} 行",
                    prompt=text_cell(cell("prompt")), source_video=source,
                    character_image=character, product_image=product, duration=duration,
                    aspect_ratio=aspect_ratio, source_start=source_start, seed=seed,
                )
                created.append(task_id)
            except Exception as exc:
                errors.append({"row": excel_row, "error": str(exc)})
        if not created and errors:
            shutil.rmtree(batch_folder, ignore_errors=True)
        return {"created": len(created), "task_ids": created, "errors": errors}
    except HTTPException:
        shutil.rmtree(batch_folder, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(batch_folder, ignore_errors=True)
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
        try_cancel_engine(row["engine_job_id"])
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
            "UPDATE tasks SET status='queued',progress=0,error=NULL,cancel_requested=0,"
            "engine_job_id=NULL,started_at=NULL,finished_at=NULL,updated_at=? WHERE id=?",
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
        "task_name", "source_video", "character_image", "product_image", "prompt",
        "duration", "aspect_ratio", "source_start", "seed",
    ]
    sheet.append(headers)
    sheet.append([
        "示例-人物和商品替换", "source.mp4", "person.jpg", "product.png",
        "保持原视频场景和运镜，人物自然拿着新商品，包装文字尽量清晰。", 5, "16:9", 0, "",
    ])
    header_fill = PatternFill("solid", fgColor="253449")
    for cell in sheet[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
    for cell in sheet[2]:
        cell.fill = PatternFill("solid", fgColor="FFF4CC")
    widths = [24, 24, 24, 24, 62, 12, 16, 16, 16]
    for i, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + i)].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = "A1:I2"
    sheet["B1"].comment = Comment("必填；填写同时上传的视频文件名。", "MiniMax H3 Studio")
    sheet["C1"].comment = Comment("可选；人物正面清晰参考图文件名。", "MiniMax H3 Studio")
    sheet["D1"].comment = Comment("可选；商品清晰参考图文件名。", "MiniMax H3 Studio")
    sheet["F1"].comment = Comment("4–15 秒。", "MiniMax H3 Studio")
    ratio_validation = DataValidation(type="list", formula1='"auto,16:9,9:16,1:1,4:3,3:4,21:9"')
    duration_validation = DataValidation(type="whole", operator="between", formula1="4", formula2="15")
    sheet.add_data_validation(ratio_validation)
    sheet.add_data_validation(duration_validation)
    ratio_validation.add("G2:G1000")
    duration_validation.add("F2:F1000")

    guide = workbook.create_sheet("字段说明")
    guide.append(["字段", "是否必填", "说明"])
    guide_rows = [
        ("task_name", "否", "任务名称；空白时自动生成。"),
        ("source_video", "是", "源视频文件名；需在导入时同时选择上传。"),
        ("character_image", "否", "替换人物的参考图片文件名。"),
        ("product_image", "否", "替换商品的参考图片文件名。"),
        ("prompt", "否", "补充要求，例如服装、环境、动作或保留项。"),
        ("duration", "否", "输出 4–15 秒，默认 5 秒。"),
        ("aspect_ratio", "否", "默认 16:9；可使用 auto。"),
        ("source_start", "否", "从源视频第几秒开始读取，默认 0。"),
        ("seed", "否", "固定随机种子便于复现；空白时自动生成。"),
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
