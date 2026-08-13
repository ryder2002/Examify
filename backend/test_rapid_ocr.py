"""Unit coverage for the Tesseract adapter and layout conversion contract."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

import rapid_ocr
from pipeline import _paddle_page_result


class _FakeEngine:
    def __call__(self, _image, **_kwargs):
        return SimpleNamespace(
            boxes=np.array(
                [
                    [[10, 10], [100, 10], [100, 30], [10, 30]],
                    [[520, 10], [630, 10], [630, 30], [520, 30]],
                ]
            ),
            txts=("101.", "(A) Alpha"),
            scores=(0.98, 0.92),
            word_results=(
                (("101.", 0.98, [[10, 10], [100, 10], [100, 30], [10, 30]]),),
                (("(A)", 0.92, [[520, 10], [560, 10], [560, 30], [520, 30]]),),
            ),
        )


class TesseractOCRAdapterTests(unittest.TestCase):
    def tearDown(self) -> None:
        rapid_ocr._POOL = None
        rapid_ocr._ACTIVE_PROVIDER = ""

    def test_normalizes_engine_result_and_provider(self) -> None:
        slot = rapid_ocr._EngineSlot(_FakeEngine(), "tesseract:cpu")
        with patch.object(
            rapid_ocr,
            "_create_pool",
            return_value=rapid_ocr._EnginePool([slot]),
        ):
            result = rapid_ocr.recognize(Image.new("RGB", (700, 100), "white"))
        self.assertEqual(result.provider, "tesseract:cpu")
        self.assertEqual([line.text for line in result.lines], ["101.", "(A) Alpha"])
        self.assertEqual(result.lines[0].words[0].text, "101.")
        self.assertGreater(result.lines[1].confidence, 90)

    def test_normalizes_tesseract_word_data_into_lines(self) -> None:
        result = rapid_ocr._normalize_tesseract_data(
            {
                "text": ["101.", "What", "is", "this?", "noise"],
                "conf": ["96.0", "92.0", "93.0", "91.0", "20.0"],
                "left": [10, 60, 130, 160, 10],
                "top": [10, 10, 10, 10, 80],
                "width": [35, 55, 20, 50, 30],
                "height": [20, 20, 20, 20, 20],
                "block_num": [1, 1, 1, 1, 1],
                "par_num": [1, 1, 1, 1, 1],
                "line_num": [1, 1, 1, 1, 2],
                "page_num": [1, 1, 1, 1, 1],
            },
            elapsed=0.1,
            provider="tesseract:cpu",
            text_score=0.45,
        )
        self.assertEqual(result.text, "101. What is this?")
        self.assertEqual(len(result.lines[0].words), 4)
        self.assertEqual(result.lines[0].box[0], (10.0, 10.0))

    def test_full_page_result_keeps_existing_two_column_parser_contract(self) -> None:
        result = rapid_ocr.OCRResult(
            lines=(
                rapid_ocr.OCRLine(
                    "101.",
                    98.0,
                    ((10.0, 10.0), (100.0, 10.0), (100.0, 30.0), (10.0, 30.0)),
                ),
                rapid_ocr.OCRLine(
                    "(A) Alpha",
                    92.0,
                    ((520.0, 10.0), (630.0, 10.0), (630.0, 30.0), (520.0, 30.0)),
                ),
            ),
            elapsed=0.1,
            provider="tesseract:cpu",
        )
        page = _paddle_page_result(
            result,
            page_number=1,
            page_width=1000,
            page_height=1000,
            processed_width=700,
            coordinate_scale=0.5,
        )
        self.assertEqual(page.columns, ["101.", "(A) Alpha"])
        self.assertEqual(page.tokens[0].left, 20)
        self.assertEqual(page.tokens[0].top, 20)

    def test_tesseract_context_selects_table_and_recovery_modes(self) -> None:
        self.assertIn("--psm 6", rapid_ocr._tesseract_config("answer-key-grid"))
        self.assertIn("--psm 4", rapid_ocr._tesseract_config("answer-key-recovery-4"))
        self.assertIn("--psm 11", rapid_ocr._tesseract_config("answer-key-full-page"))

    def test_runtime_status_reports_local_tesseract_pool_and_hardware(self) -> None:
        pool = rapid_ocr._EnginePool(
            [
                rapid_ocr._EngineSlot(_FakeEngine(), "tesseract:cpu"),
                rapid_ocr._EngineSlot(_FakeEngine(), "tesseract:cpu"),
            ]
        )
        with (
            patch.object(
                rapid_ocr,
                "validate_model_files",
                return_value={
                    "models": rapid_ocr.MODEL_VERSION,
                    "binary": "/usr/bin/tesseract",
                    "languages": ["eng"],
                    "missing": [],
                    "invalid": [],
                    "ready": True,
                },
            ),
            patch.object(rapid_ocr, "_pool", return_value=pool),
        ):
            status = rapid_ocr.runtime_status()
        self.assertTrue(status["ocr_local"])
        self.assertFalse(status["ocr_remote"])
        self.assertEqual(status["ocr_engine"], "tesseract")
        self.assertEqual(status["ocr_engine_pool_size"], 2)
        self.assertIn("machine", status["ocr_hardware"])


if __name__ == "__main__":
    unittest.main()
