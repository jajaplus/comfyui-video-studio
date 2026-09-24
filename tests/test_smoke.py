from __future__ import annotations

import asyncio
import importlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook, load_workbook
from starlette.datastructures import UploadFile


class StudioSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        os.environ["H3_STUDIO_DATA_DIR"] = cls.temp.name
        os.environ["H3_COMFYUI_INPUT_DIR"] = str(Path(cls.temp.name) / "comfyui" / "input")
        os.environ["H3_COMFYUI_OUTPUT_DIR"] = str(Path(cls.temp.name) / "comfyui" / "output")
        import app.main
        cls.main = importlib.reload(app.main)
        cls.main.init_db()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_01_queue_prompt_completion_and_delete(self) -> None:
        folder = Path(self.temp.name) / "fixtures"
        folder.mkdir(exist_ok=True)
        source = folder / "source.mp4"
        person = folder / "person.jpg"
        product = folder / "product.png"
        source.write_bytes(b"video")
        person.write_bytes(b"image")
        product.write_bytes(b"image")

        task_id = self.main.insert_task(
            prompt="Keep the original background.", source_video=source,
            reference_images=[person, product], duration=5.25,
            aspect_ratio="16:9", seed=42, display_name="smoke.mp4",
        )
        row = self.main.claim_next_task()
        self.assertEqual(row["id"], task_id)
        prompt = self.main.build_h3_prompt(row)
        self.assertIn("<Video 1>", prompt)
        self.assertIn("<Picture 1>", prompt)
        self.assertIn("<Picture 2>", prompt)

        def fake_http(method, url, payload=None, timeout=60):
            if url.endswith("/prompt"):
                workflow = payload["prompt"]
                sampler = workflow["136"]["inputs"]
                self.assertIn("ref_videos.ref_video_0", sampler)
                self.assertIn("ref_images.ref_image_0", sampler)
                self.assertIn("ref_images.ref_image_1", sampler)
                return {"prompt_id": "comfy-1"}
            if "/history/comfy-1" in url:
                return {"comfy-1": {
                    "status": {"status_str": "success"},
                    "outputs": {"92": {"images": [{
                        "filename": "result.mp4", "subfolder": "h3_studio", "type": "output"
                    }]}},
                }}
            return {}

        def fake_download(_, destination):
            destination.write_bytes(b"fake-mp4")

        with patch.object(self.main, "http_json", side_effect=fake_http), patch.object(
            self.main, "download_comfy_output", side_effect=fake_download
        ):
            self.main.process_task(row)

        with self.main.connect_db() as conn:
            completed = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        self.assertEqual(completed["status"], "completed")
        self.assertTrue(Path(completed["output_path"]).is_file())
        result = self.main.delete_task(task_id)
        self.assertTrue(result["deleted"])
        self.assertFalse(Path(completed["output_path"]).exists())

    def test_02_excel_import_and_template(self) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "任务"
        sheet.append(["upload_video_url", "reference_image_urls", "prompt", "aspect_ratio", "seed"])
        sheet.append([
            "https://media.example.com/bulk.mp4",
            "https://media.example.com/face.jpg",
            "自然光", "9:16", 99,
        ])
        excel_data = io.BytesIO()
        workbook.save(excel_data)
        excel_data.seek(0)

        excel = UploadFile(filename="tasks.xlsx", file=excel_data)
        with patch.object(self.main, "probe_video_duration", return_value=4.5):
            result = asyncio.run(self.main.import_excel_tasks(excel=excel))
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["errors"], [])

        response = self.main.excel_template()

        async def consume() -> bytes:
            parts = []
            async for part in response.body_iterator:
                parts.append(part)
            return b"".join(parts)

        template_bytes = asyncio.run(consume())
        template = load_workbook(io.BytesIO(template_bytes))
        self.assertEqual(template.sheetnames, ["任务", "字段说明"])
        self.assertEqual(template["任务"]["A1"].value, "upload_video_url")
        self.assertEqual(template["任务"]["B1"].value, "reference_image_urls")
        self.assertGreater(len(template["任务"].data_validations.dataValidation), 0)

    def test_03_manual_multiple_videos(self) -> None:
        videos = [
            UploadFile(filename="one.mp4", file=io.BytesIO(b"video-one")),
            UploadFile(filename="two.mov", file=io.BytesIO(b"video-two")),
        ]
        references = [
            UploadFile(filename="person.jpg", file=io.BytesIO(b"person")),
            UploadFile(filename="product.png", file=io.BytesIO(b"product")),
        ]
        result = asyncio.run(self.main.create_manual_task(
            upload_videos=videos,
            reference_images=references,
            prompt="Picture 1 is the person. Picture 2 is the product.",
            aspect_ratio="16:9",
            seed=None,
            video_durations="[5.2, 8.4]",
        ))
        self.assertEqual(result["created"], 2)
        with self.main.connect_db() as conn:
            rows = conn.execute(
                "SELECT name,duration,reference_images FROM tasks WHERE id IN (?,?) ORDER BY name",
                result["task_ids"],
            ).fetchall()
        self.assertEqual([row["name"] for row in rows], ["one", "two"])
        self.assertEqual([row["duration"] for row in rows], [5.2, 8.4])
        self.assertTrue(all(len(json.loads(row["reference_images"])) == 2 for row in rows))


if __name__ == "__main__":
    unittest.main()
