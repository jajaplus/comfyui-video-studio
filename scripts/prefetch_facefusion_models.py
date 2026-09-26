#!/usr/bin/env python3
"""Link public FaceFusion assets when available and download only required files."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path


REQUIRED_MODELS = {
    "models-3.3.0": ["nsfw_1", "nsfw_2", "nsfw_3"],
    "models-3.1.0": ["xseg_1"],
    "models-3.0.0": [
        "fairface", "yoloface_8n", "2dfan4", "fan_68_5",
        "bisenet_resnet_34", "arcface_w600k_r50", "kim_vocal_2",
        "inswapper_128_fp16",
    ],
}


def find_public_file(root: Path | None, filename: str) -> Path | None:
    if root is None or not root.is_dir():
        return None
    direct = root / filename
    if direct.is_file():
        return direct.resolve()
    return next((path.resolve() for path in root.rglob(filename) if path.is_file()), None)


def download(url: str, destination: Path, retries: int) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(1, retries + 1):
        downloaded = temporary.stat().st_size if temporary.is_file() else 0
        headers = {"User-Agent": "H3-Studio/3.0"}
        if downloaded:
            headers["Range"] = f"bytes={downloaded}-"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                append = downloaded > 0 and getattr(response, "status", 200) == 206
                mode = "ab" if append else "wb"
                if downloaded and not append:
                    downloaded = 0
                total_header = response.headers.get("Content-Length")
                total = downloaded + int(total_header) if total_header else None
                with temporary.open(mode) as target:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        target.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            print(
                                f"\r  {destination.name}: {downloaded / 1024 / 1024:.1f} / "
                                f"{total / 1024 / 1024:.1f} MB",
                                end="", flush=True,
                            )
            print()
            temporary.replace(destination)
            return
        except (
            http.client.HTTPException,
            TimeoutError,
            urllib.error.URLError,
            OSError,
        ) as exc:
            if attempt >= retries:
                raise RuntimeError(
                    f"下载 {destination.name} 失败，已重试 {retries} 次；"
                    "可在 .env 设置 H3_GITHUB_PROXY 后继续运行安装脚本"
                ) from exc
            delay = min(30, 2 ** attempt)
            current = temporary.stat().st_size if temporary.is_file() else 0
            print(
                f"\n连接中断，第 {attempt}/{retries} 次失败；"
                f"已保留 {current / 1024 / 1024:.1f} MB，{delay} 秒后断点续传。"
            )
            time.sleep(delay)


def expected_sha256(hash_path: Path) -> str | None:
    if not hash_path.is_file():
        return None
    match = re.search(r"\b[0-9a-fA-F]{64}\b", hash_path.read_text(errors="ignore"))
    return match.group(0).lower() if match else None


def valid_model(model_path: Path, hash_path: Path) -> bool:
    if not model_path.is_file() or model_path.stat().st_size == 0:
        return False
    expected = expected_sha256(hash_path)
    if not expected:
        return True
    digest = hashlib.sha256()
    with model_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected


def remote_url(release: str, filename: str, proxy: str) -> str:
    original = (
        "https://github.com/facefusion/facefusion-assets/releases/download/"
        f"{release}/{filename}"
    )
    return f"{proxy.rstrip('/')}/{original}" if proxy else original


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--facefusion-dir", required=True, type=Path)
    parser.add_argument("--public-models-dir", type=Path)
    parser.add_argument("--github-proxy", default=os.getenv("H3_GITHUB_PROXY", "").strip())
    parser.add_argument(
        "--retries", type=int,
        default=max(1, int(os.getenv("H3_FACEFUSION_DOWNLOAD_RETRIES", "8"))),
    )
    args = parser.parse_args()
    destination_dir = args.facefusion_dir / ".assets" / "models"
    destination_dir.mkdir(parents=True, exist_ok=True)

    linked = 0
    downloaded = 0
    for release, model_names in REQUIRED_MODELS.items():
        for model_name in model_names:
            for suffix in (".hash", ".onnx"):
                filename = model_name + suffix
                destination = destination_dir / filename
                hash_path = destination_dir / f"{model_name}.hash"
                if suffix == ".onnx" and valid_model(destination, hash_path):
                    continue
                if suffix == ".hash" and destination.is_file() and destination.stat().st_size > 0:
                    continue
                destination.unlink(missing_ok=True)
                public_file = find_public_file(args.public_models_dir, filename)
                if public_file:
                    os.symlink(public_file, destination)
                    if suffix == ".onnx" and not valid_model(destination, hash_path):
                        destination.unlink(missing_ok=True)
                        raise RuntimeError(f"公共模型 {filename} 的 SHA-256 校验失败")
                    linked += 1
                    print(f"复用公共模型：{filename} -> {public_file}")
                    continue
                url = remote_url(release, filename, args.github_proxy)
                print(f"下载缺少文件：{filename}")
                download(url, destination, args.retries)
                if suffix == ".onnx" and not valid_model(destination, hash_path):
                    destination.unlink(missing_ok=True)
                    raise RuntimeError(f"{filename} 下载完成但 SHA-256 校验失败，请重新运行安装脚本")
                downloaded += 1
    print(f"FaceFusion 模型准备完成：公共模型链接 {linked} 个，下载 {downloaded} 个文件")


if __name__ == "__main__":
    main()
