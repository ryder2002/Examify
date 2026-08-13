from __future__ import annotations

import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main


class _ProgressStore:
    def __init__(self) -> None:
        self.state = {
            "status": "queued",
            "exam_type": "listening",
            "audios": [{"id": "full.mp3", "part": "full"}],
        }
        self.events: list[dict[str, object]] = []

    def read(self, _job_id: str) -> dict[str, object]:
        return dict(self.state)

    def write_progress(self, _job_id: str, **changes: object) -> dict[str, object]:
        self.state.update(changes)
        self.events.append(dict(changes))
        return dict(self.state)

    def write(self, _job_id: str, state: dict[str, object]) -> None:
        self.state = dict(state)


class AudioOCRJobOrchestrationTests(unittest.TestCase):
    def test_pdf_without_audio_skips_audio_module_and_runs_ocr(self) -> None:
        fake_store = _ProgressStore()
        fake_store.state["audios"] = []

        with (
            patch.object(main, "store", fake_store),
            patch.object(
                main,
                "settings",
                SimpleNamespace(desktop=False, paddle_ocr_url="http://ocr/ocr"),
            ),
            patch.object(main, "_run_extraction") as run_ocr,
        ):
            main._run_extraction_job("job", Path("input.pdf"))

        run_ocr.assert_called_once()
        self.assertEqual(run_ocr.call_args.kwargs["overall_progress_start"], 0)

    def test_remote_ocr_starts_before_audio_finishes(self) -> None:
        fake_store = _ProgressStore()
        audio_started = threading.Event()
        ocr_started = threading.Event()

        def fake_audio(_store, _job_id, *, progress=None):
            self.assertIsNotNone(progress)
            progress(10, "Audio started")
            audio_started.set()
            self.assertTrue(ocr_started.wait(timeout=2))
            progress(100, "Audio complete")

        def fake_ocr(
            _job_id,
            _input_path,
            *,
            overall_progress_start=0,
            progress_callback=None,
            before_finalize=None,
            failure_callback=None,
        ):
            self.assertTrue(audio_started.wait(timeout=2))
            self.assertEqual(overall_progress_start, 0)
            self.assertIsNotNone(progress_callback)
            progress_callback(50, "OCR page 5/10")
            ocr_started.set()
            self.assertIsNotNone(before_finalize)
            before_finalize()

        with (
            patch.object(main, "store", fake_store),
            patch.object(
                main,
                "settings",
                SimpleNamespace(desktop=False, paddle_ocr_url="http://ocr/ocr"),
            ),
            patch.object(main, "prepare_web_audio", side_effect=fake_audio),
            patch.object(main, "_run_extraction", side_effect=fake_ocr),
        ):
            main._run_extraction_job("job", Path("input.pdf"))

        parallel = [
            item
            for item in fake_store.events
            if item.get("processing_phase") == "audio_ocr"
        ]
        self.assertTrue(parallel)
        self.assertTrue(any(item.get("ocr_progress") == 50 for item in parallel))
        self.assertTrue(any(item.get("audio_progress") == 100 for item in parallel))

    def test_local_ocr_also_starts_before_audio_finishes(self) -> None:
        """Desktop/local OCR must use the same two-branch orchestration."""
        fake_store = _ProgressStore()
        audio_started = threading.Event()
        ocr_started = threading.Event()

        def fake_audio(_store, _job_id, *, progress=None):
            self.assertIsNotNone(progress)
            progress(5, "Audio local started")
            audio_started.set()
            self.assertTrue(ocr_started.wait(timeout=2))
            progress(100, "Audio local complete")

        def fake_ocr(
            _job_id,
            _input_path,
            *,
            overall_progress_start=0,
            progress_callback=None,
            before_finalize=None,
            failure_callback=None,
        ):
            self.assertTrue(audio_started.wait(timeout=2))
            self.assertEqual(overall_progress_start, 0)
            self.assertIsNotNone(progress_callback)
            progress_callback(25, "OCR local page 1/4")
            ocr_started.set()
            self.assertIsNotNone(before_finalize)
            before_finalize()

        with (
            patch.object(main, "store", fake_store),
            patch.object(
                main,
                "settings",
                SimpleNamespace(desktop=True, paddle_ocr_url=""),
            ),
            patch.object(main, "prepare_web_audio", side_effect=fake_audio),
            patch.object(main, "_run_extraction", side_effect=fake_ocr),
        ):
            main._run_extraction_job("job", Path("input.pdf"))

        parallel = [
            item
            for item in fake_store.events
            if item.get("processing_phase") == "audio_ocr"
        ]
        self.assertTrue(parallel)
        self.assertTrue(any(item.get("ocr_progress") == 25 for item in parallel))
        self.assertTrue(any(item.get("audio_progress") == 100 for item in parallel))

    def test_ocr_failure_is_not_resurrected_by_late_audio_progress(self) -> None:
        fake_store = _ProgressStore()
        audio_started = threading.Event()
        failure_notified = threading.Event()

        def fake_audio(_store, _job_id, *, progress=None):
            self.assertIsNotNone(progress)
            progress(10, "Audio started")
            audio_started.set()
            self.assertTrue(failure_notified.wait(timeout=2))
            # This callback must be ignored after OCR has published failure.
            progress(100, "Audio completed after OCR failure")

        def fake_ocr(
            _job_id,
            _input_path,
            *,
            overall_progress_start=0,
            progress_callback=None,
            before_finalize=None,
            failure_callback=None,
        ):
            self.assertTrue(audio_started.wait(timeout=2))
            self.assertIsNotNone(failure_callback)
            failure_callback(RuntimeError("OCR failed"))
            failure_notified.set()
            failed = fake_store.read("job")
            failed.update({"status": "failed", "stage": "Xử lý thất bại"})
            fake_store.write("job", failed)

        with (
            patch.object(main, "store", fake_store),
            patch.object(
                main,
                "settings",
                SimpleNamespace(desktop=True, paddle_ocr_url=""),
            ),
            patch.object(main, "prepare_web_audio", side_effect=fake_audio),
            patch.object(main, "_run_extraction", side_effect=fake_ocr),
        ):
            main._run_extraction_job("job", Path("input.pdf"))

        self.assertEqual(fake_store.state["status"], "failed")


if __name__ == "__main__":
    unittest.main()
