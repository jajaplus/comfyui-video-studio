#!/usr/bin/env python3
"""Link public FaceFusion assets when available and download only required files."""

from __future__ import annotations

import argparse
import os
import shutil
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


def download(url: str, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "H3-Studio/3.0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as target:
            shutil.copyfileobj(response, target, length=1024 * 1024)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--facefusion-dir", required=True, type=Path)
    parser.add_argument("--public-models-dir", type=Path)
    args = parser.parse_args()
    destination_dir = args.facefusion_dir / ".assets" / "models"
    destination_dir.mkdir(parents=True, exist_ok=True)

    linked = 0
    downloaded = 0
    for release, model_names in REQUIRED_MODELS.items():
        for model_name in model_names:
            for suffix in (".onnx", ".hash"):
                filename = model_name + suffix
                destination = destination_dir / filename
                if destination.is_file() and destination.stat().st_size > 0:
                    continue
                destination.unlink(missing_ok=True)
                public_file = find_public_file(args.public_models_dir, filename)
                if public_file:
                    os.symlink(public_file, destination)
                    linked += 1
                    print(f"复用公共模型：{filename} -> {public_file}")
                    continue
                url = (
                    "https://github.com/facefusion/facefusion-assets/releases/download/"
                    f"{release}/{filename}"
                )
                print(f"下载缺少文件：{filename}")
                download(url, destination)
                downloaded += 1
    print(f"FaceFusion 模型准备完成：公共模型链接 {linked} 个，下载 {downloaded} 个文件")


if __name__ == "__main__":
    main()
