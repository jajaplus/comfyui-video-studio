from __future__ import annotations

import asyncio
import importlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook, load_workbook
from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse
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

    def fixture(self, name: str, content: bytes) -> Path:
        folder = Path(self.temp.name) / "fixtures"
        folder.mkdir(exist_ok=True)
        path = folder / name
        path.write_bytes(content)
        return path

    def test_01_queue_prompt_completion_and_delete(self) -> None:
        source = self.fixture("source.mp4", b"video")
        product = self.fixture("product.png", b"product")
        task_id = self.main.insert_task(
            prompt="把上衣换成参考商品", source_video=source,
            reference_images=[product], reference_roles=["product"], duration=5.0,
            aspect_ratio="auto", seed=42, display_name="smoke.mp4",
            scheduler="karras", sampler="euler", steps=38, denoise=0.85,
        )
        row = self.main.claim_next_task()
        self.assertEqual(row["id"], task_id)
        prompt = self.main.build_vace_prompt(row)
        self.assertIn("把上衣换成参考商品", prompt)
        self.assertIn("只修改蒙版", prompt)
        self.assertIn("蒙版外像素必须保持", prompt)

        output = Path(self.temp.name) / "outputs" / f"{task_id}.mp4"
        output.write_bytes(b"done")
        self.main.mark_task(task_id, status="completed", output_path=str(output), stage="精准替换完成")
        result = self.main.delete_task(task_id)
        self.assertTrue(result["deleted"])
        self.assertFalse(output.exists())

    def test_02_vace_api_workflow(self) -> None:
        self.main.COMFYUI_INPUT_DIR.mkdir(parents=True, exist_ok=True)
        (self.main.COMFYUI_INPUT_DIR / "source.mp4").write_bytes(b"video")
        row = {
            "id": "workflow-test", "duration": 5.0, "aspect_ratio": "16:9",
            "quality": "low", "seed": 123, "sampler": "uni_pc",
            "scheduler": "simple", "steps": 50, "denoise": 1.0,
            "prompt": "替换上衣",
        }
        workflow = self.main.build_comfy_workflow(row, "source.mp4", "mask.mp4", "product.png")
        self.assertEqual(workflow["1"]["inputs"]["file"], "source.mp4")
        self.assertEqual(workflow["3"]["inputs"]["file"], "mask.mp4")
        self.assertEqual(workflow["14"]["inputs"]["image"], "product.png")
        self.assertEqual(workflow["21"]["inputs"]["length"], 81)
        self.assertEqual(workflow["21"]["inputs"]["batch_size"], 1)
        self.assertEqual(workflow["21"]["inputs"]["strength"], 1.0)
        self.assertEqual((workflow["21"]["inputs"]["width"], workflow["21"]["inputs"]["height"]), (832, 480))
        self.assertEqual(workflow["12"]["class_type"], "EmptyImage")
        self.assertEqual(workflow["12"]["inputs"]["color"], 0x808080)
        self.assertEqual(workflow["12"]["inputs"]["batch_size"], 81)
        self.assertEqual(workflow["16"]["inputs"]["shift"], 16.0)
        self.assertEqual(workflow["22"]["inputs"]["sampler_name"], "uni_pc")
        self.assertEqual(workflow["22"]["inputs"]["steps"], 50)
        self.assertEqual(workflow["15"]["inputs"]["weight_dtype"], "default")

    def test_03_excel_import_and_template(self) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "任务"
        sheet.append(["upload_video_url", "reference_image_urls", "reference_roles", "product_box", "prompt", "aspect_ratio", "quality", "seed"])
        sheet.append(["https://media.example.com/bulk.mp4", "https://media.example.com/product.jpg", "product", "", "替换上衣", "auto", "low", 99])
        data = io.BytesIO()
        workbook.save(data)
        data.seek(0)
        with patch.object(self.main, "probe_video_duration", return_value=4.5):
            result = asyncio.run(self.main.import_excel_tasks(excel=UploadFile(filename="tasks.xlsx", file=data)))
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["errors"], [])

        response = self.main.excel_template()

        async def consume() -> bytes:
            return b"".join([part async for part in response.body_iterator])

        template = load_workbook(io.BytesIO(asyncio.run(consume())))
        self.assertEqual(template.sheetnames, ["任务", "字段说明"])
        self.assertEqual(template["任务"]["J2"].value, "uni_pc")
        self.assertEqual(template["任务"]["K2"].value, 50)
        self.assertGreater(len(template["任务"].data_validations.dataValidation), 0)

    def test_04_manual_multiple_videos(self) -> None:
        videos = [UploadFile(filename="one.mp4", file=io.BytesIO(b"one")), UploadFile(filename="two.mov", file=io.BytesIO(b"two"))]
        references = [UploadFile(filename="product.png", file=io.BytesIO(b"product"))]
        result = asyncio.run(self.main.create_manual_task(
            upload_videos=videos, reference_images=references, reference_roles='["product"]',
            prompt="替换上衣", aspect_ratio="auto", seed=None, video_durations="[5.2, 8.4]",
        ))
        self.assertEqual(result["created"], 2)
        with self.main.connect_db() as conn:
            rows = conn.execute("SELECT name,quality,sampler FROM tasks WHERE id IN (?,?) ORDER BY name", result["task_ids"]).fetchall()
        self.assertEqual([row["name"] for row in rows], ["one", "two"])
        self.assertTrue(all(row["quality"] == "low" and row["sampler"] == "uni_pc" for row in rows))

    def test_05_gpu_status_parser(self) -> None:
        parsed = self.main.parse_nvidia_smi("0, NVIDIA L40S, 87, 36120, 46068, 68, 287.4, 350.0\n")
        self.assertEqual(parsed[0]["name"], "NVIDIA L40S")
        self.assertAlmostEqual(parsed[0]["memory_percent"], 78.4)

    def test_06_comfy_sampling_progress(self) -> None:
        workflow = {"22": {"class_type": "KSampler", "_meta": {"title": "局部重绘"}}}
        with patch.object(self.main, "mark_task") as mark:
            finished = self.main.apply_comfy_event("task-1", "prompt-1", workflow, {"type": "progress", "data": {"prompt_id": "prompt-1", "node": "22", "value": 10, "max": 20}})
        self.assertFalse(finished)
        self.assertIn("VACE", mark.call_args.kwargs["stage"])

    def test_07_vace_segment_plan(self) -> None:
        self.assertEqual(self.main.segment_durations(5), [5.0])
        self.assertEqual(self.main.segment_durations(6), [3.0, 3.0])
        parts = self.main.segment_durations(31)
        self.assertEqual(len(parts), 7)
        self.assertAlmostEqual(sum(parts), 31.0, places=3)
        self.assertTrue(all(1 <= value <= 5 for value in parts))
        self.assertEqual(self.main.vace_frame_count(5), 81)
        self.assertEqual(self.main.vace_frame_count(3), 49)

    def test_08_precision_pipeline_orders_mask_vace_face_and_audio(self) -> None:
        folder = Path(self.temp.name) / "precision"
        folder.mkdir(exist_ok=True)
        source, face, product = folder / "source.mp4", folder / "face.jpg", folder / "product.png"
        source.write_bytes(b"video")
        face.write_bytes(b"face")
        product.write_bytes(b"product")
        task_id = self.main.insert_task(
            prompt="只替换商品和脸", source_video=source, reference_images=[face, product],
            reference_roles=["face", "product"], duration=6.0, aspect_ratio="auto", seed=12,
        )
        with self.main.connect_db() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        calls: list[str] = []

        def fake_mask(_source, box, reference, description, destination, _work):
            self.assertIsNone(box)
            self.assertEqual(reference.read_bytes(), b"product")
            self.assertEqual(description, "只替换商品和脸")
            destination.write_bytes(b"mask")
            calls.append("sam2-mask")

        def fake_split(_source, durations, work_folder):
            work_folder.mkdir(parents=True, exist_ok=True)
            results = []
            for index, _duration in enumerate(durations, 1):
                part = work_folder / f"part-{index}.mp4"
                part.write_bytes(b"part")
                results.append(part)
            return results

        def fake_vace(_parent, segment_row, _index, _count, destination, mask, reference):
            self.assertTrue(mask.is_file())
            self.assertEqual(reference.read_bytes(), b"product")
            self.assertLessEqual(segment_row["duration"], 5)
            destination.write_bytes(b"vace")
            calls.append("vace")
            return True

        def fake_concat(parts, destination, _work):
            self.assertEqual(len(parts), 2)
            destination.write_bytes(b"merged")
            calls.append("merge")

        def fake_composite(_source, _generated, _mask, destination):
            destination.write_bytes(b"composited")
            calls.append("composite")

        def fake_facefusion(refs, _target, destination, _work):
            self.assertEqual(len(refs), 1)
            destination.write_bytes(b"face")
            calls.append("facefusion")

        def fake_audio(_processed, _source, destination):
            destination.write_bytes(b"final")
            calls.append("audio")

        with patch.object(self.main, "probe_video_dimensions", return_value=(1920, 1080)), patch.object(self.main, "run_product_mask_tracking", side_effect=fake_mask), patch.object(self.main, "split_source_video", side_effect=fake_split), patch.object(self.main, "split_mask_video", side_effect=fake_split), patch.object(self.main, "run_comfy_segment", side_effect=fake_vace), patch.object(self.main, "composite_product_region", side_effect=fake_composite), patch.object(self.main, "concat_generated_videos", side_effect=fake_concat), patch.object(self.main, "ensure_visible_video"), patch.object(self.main, "facefusion_ffmpeg_environment"), patch.object(self.main, "run_facefusion", side_effect=fake_facefusion), patch.object(self.main, "restore_original_audio", side_effect=fake_audio):
            self.main.process_task(row)
        self.assertEqual(calls, ["sam2-mask", "vace", "composite", "vace", "composite", "merge", "facefusion", "audio"])
        with self.main.connect_db() as conn:
            completed = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["stage"], "精准替换完成")

    def test_09_virtualenv_python_symlink_is_not_resolved(self) -> None:
        folder = Path(self.temp.name) / "python-paths"
        target, interpreter = folder / "base" / "python", folder / "sam2-env" / "bin" / "python"
        target.parent.mkdir(parents=True, exist_ok=True)
        interpreter.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("python")
        interpreter.symlink_to(target)
        self.assertEqual(self.main.absolute_path_without_resolving(interpreter), interpreter.absolute())

    def test_10_black_frame_signal_detection(self) -> None:
        black = "\n".join(["lavfi.signalstats.YAVG=16", "lavfi.signalstats.YMAX=16", "lavfi.signalstats.YAVG=16.2", "lavfi.signalstats.YMAX=17"])
        visible = "\n".join(["lavfi.signalstats.YAVG=16", "lavfi.signalstats.YMAX=16", "lavfi.signalstats.YAVG=42", "lavfi.signalstats.YMAX=201"])
        self.assertTrue(self.main.signalstats_indicates_black(black))
        self.assertFalse(self.main.signalstats_indicates_black(visible))

    def test_11_facefusion_command_forces_cuda_and_sensitive_detection(self) -> None:
        command = self.main.build_facefusion_command(
            [Path("face.jpg")], Path("target.mp4"), Path("output.mp4"),
            Path("temp"), video_output=True,
        )
        joined = " ".join(str(value) for value in command)
        self.assertIn("--execution-providers cuda", joined)
        self.assertIn("--execution-device-ids 0", joined)
        self.assertIn("--face-detector-model yolo_face", joined)
        self.assertIn("--face-detector-angles 0 90 180 270", joined)
        self.assertIn("--face-detector-score 0.25", joined)
        self.assertIn("--face-swapper-weight 0.85", joined)
        self.assertIn("--face-mask-types box occlusion", joined)
        self.assertIn("--workflow-strategy disk", joined)
        self.assertIn("--output-audio-volume 0", joined)
        self.assertNotIn(" region", joined)
        self.assertNotIn("--halt-on-error", command)

    def test_12_product_composite_uses_binary_mask_and_original_frame(self) -> None:
        with patch.object(self.main, "probe_video_dimensions", return_value=(1280, 720)), patch.object(self.main, "run_ffmpeg") as ffmpeg, patch.object(self.main, "ensure_visible_video"):
            self.main.composite_product_region(
                Path("source.mp4"), Path("generated.mp4"),
                Path("mask.mp4"), Path("composited.mp4"),
            )
        command = ffmpeg.call_args.args[0]
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("[0:v]fps=16", graph)
        self.assertIn("[1:v]fps=16", graph)
        self.assertIn("[2:v]fps=16", graph)
        self.assertIn("if(gte(val,128),255,0)", graph)
        self.assertIn("[base][edit][region]maskedmerge[out]", graph)

    def test_13_facefusion_retries_with_retinaface(self) -> None:
        folder = Path(self.temp.name) / "face-retry"
        folder.mkdir(exist_ok=True)
        entrypoint = folder / "facefusion.py"
        interpreter = folder / "python"
        entrypoint.write_text("")
        interpreter.write_text("")
        commands: list[list[str]] = []

        def fake_extract(_arguments, _purpose):
            Path(_arguments[-1]).write_bytes(b"frame")

        def fake_facefusion(command, _purpose, **_kwargs):
            commands.append(command)
            Path(command[command.index("--output-path") + 1]).write_bytes(b"output")
            _kwargs["log_path"].write_text("loading model inswapper_128_fp16 succeeded")

        def fake_hash(path):
            return "changed" if "retinaface" in path.name else "same"

        with patch.object(self.main, "FACEFUSION_DIR", folder), patch.object(self.main, "FACEFUSION_PYTHON", interpreter), patch.object(self.main, "facefusion_ffmpeg_environment"), patch.object(self.main, "facefusion_execution_providers"), patch.object(self.main, "probe_video_duration", return_value=5.0), patch.object(self.main, "run_ffmpeg", side_effect=fake_extract), patch.object(self.main, "run_external_command", side_effect=fake_facefusion), patch.object(self.main, "decoded_frame_hash", side_effect=fake_hash), patch.object(self.main, "ensure_visible_video"), patch.object(self.main, "persist_facefusion_log"):
            self.main.run_facefusion([Path("face.jpg")], Path("target.mp4"), folder / "final.mp4", folder)
        models = [command[command.index("--face-detector-model") + 1] for command in commands]
        self.assertEqual(models, ["yolo_face", "yolo_face", "yolo_face", "retinaface", "retinaface"])
        self.assertTrue(all(command[command.index("--face-detector-size") + 1] == "640x640" for command in commands))

    def test_13b_facefusion_rejects_black_probe_output(self) -> None:
        folder = Path(self.temp.name) / "black-face-probe"
        folder.mkdir(exist_ok=True)
        (folder / "facefusion.py").write_text("")
        (folder / "python").write_text("")

        def fake_extract(arguments, _purpose):
            Path(arguments[-1]).write_bytes(b"visible frame")

        def fake_facefusion(command, _purpose, **_kwargs):
            Path(command[command.index("--output-path") + 1]).write_bytes(b"black frame")

        def fake_black(path):
            return "probe_output" in path.name

        with patch.object(self.main, "FACEFUSION_DIR", folder), patch.object(self.main, "FACEFUSION_PYTHON", folder / "python"), patch.object(self.main, "facefusion_ffmpeg_environment"), patch.object(self.main, "facefusion_execution_providers"), patch.object(self.main, "probe_video_duration", return_value=5.0), patch.object(self.main, "run_ffmpeg", side_effect=fake_extract), patch.object(self.main, "run_external_command", side_effect=fake_facefusion) as facefusion, patch.object(self.main, "video_appears_black", side_effect=fake_black), patch.object(self.main, "persist_facefusion_log"):
            with self.assertRaisesRegex(self.main.FaceSwapError, "预检.*输出黑屏"):
                self.main.run_facefusion([Path("face.jpg")], Path("target.mp4"), folder / "final.mp4", folder)
        self.assertEqual(facefusion.call_count, 1)

    def test_14_task_details_include_previewable_media(self) -> None:
        source = self.fixture("detail-source.mp4", b"video")
        reference = self.fixture("detail-face.jpg", b"face")
        task_id = self.main.insert_task(
            prompt="替换人脸", source_video=source,
            reference_images=[reference], reference_roles=["face"],
            duration=3.0, aspect_ratio="auto", seed=7,
        )
        with self.main.connect_db() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        detail = self.main.task_to_dict(row)
        self.assertEqual(detail["prompt"], "替换人脸")
        self.assertEqual(detail["reference_images"], [reference.name])
        self.assertEqual(detail["reference_roles"], ["face"])
        self.assertEqual(detail["source_media_url"], f"/api/tasks/{task_id}/media/source/0")
        self.assertEqual(detail["reference_media_urls"], [f"/api/tasks/{task_id}/media/reference/0"])
        self.assertIsInstance(self.main.task_media(task_id, "source", 0), FileResponse)
        self.assertIsInstance(self.main.task_media(task_id, "reference", 0), FileResponse)
        with self.assertRaises(HTTPException):
            self.main.task_media(task_id, "reference", 1)

    def test_15_remote_task_media_redirects(self) -> None:
        task_id = self.main.insert_task(
            prompt="替换商品", source_video="https://media.example.com/source.mp4",
            reference_images=["https://media.example.com/product.jpg"],
            reference_roles=["product"], duration=3.0, aspect_ratio="auto", seed=7,
        )
        self.assertIsInstance(self.main.task_media(task_id, "source", 0), RedirectResponse)

    def test_16_facefusion_uses_upgraded_ffmpeg_and_rejects_old_version(self) -> None:
        folder = Path(self.temp.name) / "ffmpeg-env"
        binary_dir = folder / "bin"
        binary_dir.mkdir(parents=True, exist_ok=True)
        (binary_dir / "ffmpeg").write_text("ffmpeg")
        (binary_dir / "ffprobe").write_text("ffprobe")
        (binary_dir / "ffmpeg").chmod(0o755)
        (binary_dir / "ffprobe").chmod(0o755)
        self.main.facefusion_ffmpeg_environment.cache_clear()
        with patch.object(self.main, "FFMPEG_ENV", folder), patch.object(
            self.main.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 0, "-fps_mode passthrough", ""),
        ):
            environment = self.main.facefusion_ffmpeg_environment()
        self.assertTrue(environment["PATH"].startswith(f"{binary_dir}{os.pathsep}"))
        self.main.facefusion_ffmpeg_environment.cache_clear()
        with patch.object(self.main, "FFMPEG_ENV", folder), patch.object(
            self.main.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 0, "old ffmpeg", ""),
        ):
            with self.assertRaisesRegex(self.main.FaceSwapError, "install_ffmpeg.sh"):
                self.main.facefusion_ffmpeg_environment()
        self.main.facefusion_ffmpeg_environment.cache_clear()

    def test_17_external_error_keeps_ffmpeg_reason(self) -> None:
        progress = "\r".join(f"extracting: {index}%|progress" for index in range(101))
        stderr = f"{progress}\n[FACEFUSION.FFMPEG] Unrecognized option 'fps_mode'.\n"
        with patch.object(
            self.main.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 1, "", stderr),
        ):
            with self.assertRaisesRegex(RuntimeError, "Unrecognized option 'fps_mode'") as raised:
                self.main.run_external_command(["facefusion"], "FaceFusion 人脸替换")
        self.assertNotIn("extracting:", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
