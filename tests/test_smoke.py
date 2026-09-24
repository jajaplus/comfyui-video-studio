from __future__ import annotations

import asyncio
import importlib
import io
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
            name="smoke", prompt="Keep the original background.", source_video=source,
            character_image=person, product_image=product, duration=5,
            aspect_ratio="16:9", source_start=0, seed=42,
        )
        row = self.main.claim_next_task()
        self.assertEqual(row["id"], task_id)
        prompt = self.main.build_h3_prompt(row)
        self.assertIn("<Video 1>", prompt)
        self.assertIn("<Picture 1>", prompt)
        self.assertIn("<Picture 2>", prompt)

        def fake_http(method, url, payload=None, timeout=60):
            if method == "POST":
                self.assertEqual(payload["task"], "ref2va")
                self.assertEqual(len(payload["conditions"]), 3)
                return {"id": "engine-1"}
            return {"status": "completed"}

        def fake_download(_, destination):
            destination.write_bytes(b"fake-mp4")

        with patch.object(self.main, "http_json", side_effect=fake_http), patch.object(
            self.main, "download_output", side_effect=fake_download
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
        sheet.append([
            "task_name", "source_video", "character_image", "product_image", "prompt",
            "duration", "aspect_ratio", "source_start", "seed",
        ])
        sheet.append(["bulk", "bulk.mp4", "face.jpg", "", "自然光", 4, "9:16", 1.5, 99])
        excel_data = io.BytesIO()
        workbook.save(excel_data)
        excel_data.seek(0)

        excel = UploadFile(filename="tasks.xlsx", file=excel_data)
        media = [
            UploadFile(filename="bulk.mp4", file=io.BytesIO(b"video")),
            UploadFile(filename="face.jpg", file=io.BytesIO(b"image")),
        ]
        result = asyncio.run(self.main.import_excel_tasks(excel=excel, media=media))
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
        self.assertEqual(template["任务"]["B1"].value, "source_video")
        self.assertGreater(len(template["任务"].data_validations.dataValidation), 0)


if __name__ == "__main__":
    unittest.main()
