"""Tests for local answer-key OCR and audio asset safety."""

from __future__ import annotations

import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from answer_key import (
    AnswerKeyOcrTimeout,
    _grid_answer_candidate,
    _layout_answer_candidate,
    _preferred_grid_column_counts,
    answer_key_scope_detail,
    extract_answer_key_image,
    parse_answer_key_text,
)
from job_store import JobStore
from pipeline import _listening_prefix
from rapid_ocr import OCRLine, OCRResult
from schemas import AudioRef, ExamDraft


class AnswerKeyTextTests(unittest.TestCase):
    def test_spatial_paddle_pairs_are_filtered_and_preserved(self) -> None:
        result = OCRResult(
            lines=(
                OCRLine(
                    "101 (A)",
                    96.0,
                    ((10, 10), (180, 10), (180, 35), (10, 35)),
                ),
                OCRLine(
                    "102 (B)",
                    31.0,
                    ((10, 45), (180, 45), (180, 70), (10, 70)),
                ),
                OCRLine(
                    "103 (C)",
                    94.0,
                    ((10, 80), (180, 80), (180, 105), (10, 105)),
                ),
            ),
            elapsed=0.02,
            provider="CPUExecutionProvider",
        )
        image = Image.new("RGB", (200, 120), "white")
        try:
            with patch("answer_key._ocr_layout", return_value=result):
                answers, raw_text, issues = _layout_answer_candidate(
                    image,
                    expected_numbers={101, 102, 103},
                )
        finally:
            image.close()
        self.assertEqual(answers, {101: "A", 103: "C"})
        self.assertNotIn("102", raw_text)
        self.assertEqual(issues, [])

    def test_listening_blank_prefix_preserves_part_specific_choices(self) -> None:
        """Part 1 has four visual choices; Part 2 intentionally has A-C only."""
        prefix = {item["number"]: item for item in _listening_prefix()}
        self.assertEqual(prefix[1]["text"], "")
        self.assertEqual(prefix[1]["option_letters"], ["A", "B", "C", "D"])
        self.assertEqual(prefix[7]["text"], "")
        self.assertEqual(prefix[7]["option_letters"], ["A", "B", "C"])

    def test_supports_parenthesized_and_plain_toeic_formats(self) -> None:
        answers, duplicates = parse_answer_key_text(
            "1(D) 2 (A), 3B\n101 (B) 102(A) 200 (D)"
        )
        self.assertEqual(
            answers,
            {1: "D", 2: "A", 3: "B", 101: "B", 102: "A", 200: "D"},
        )
        self.assertEqual(duplicates, [])

    def test_normalizes_common_number_ocr_noise_and_reports_conflicts(self) -> None:
        answers, duplicates = parse_answer_key_text(
            "I (D) 1(A) l0 (B) 1O(C) O1(D)"
        )
        self.assertEqual(answers[1], "D")
        self.assertEqual(answers[10], "B")
        self.assertIn("1A", duplicates)

    def test_explains_reading_key_pasted_into_listening_review(self) -> None:
        detail = answer_key_scope_detail(
            "101 (A) 102 (B) 199 (C) 200 (D)",
            set(range(1, 101)),
        )
        self.assertIn("Reading (101–200)", detail or "")
        self.assertIn("Listening (1–100)", detail or "")

    def test_does_not_report_scope_mismatch_when_ranges_match(self) -> None:
        self.assertIsNone(
            answer_key_scope_detail(
                "101 (A) 102 (B)",
                set(range(101, 201)),
            )
        )

    def test_image_pipeline_uses_best_paddle_layout(self) -> None:
        image = Image.new("RGB", (400, 180), "white")
        payload = io.BytesIO()
        image.save(payload, format="PNG")
        image.close()
        with patch(
            "answer_key._ocr_text",
            return_value="1(D) 2(A) 3(B) 4(C)",
        ):
            answers, raw_text, duplicates = extract_answer_key_image(payload.getvalue())
        self.assertEqual(answers, {1: "D", 2: "A", 3: "B", 4: "C"})
        self.assertIn("4(C)", raw_text)
        self.assertEqual(duplicates, [])

    def test_rejects_invalid_image(self) -> None:
        with self.assertRaises(ValueError):
            extract_answer_key_image(b"not an image")

    def test_four_column_grid_binds_letters_to_expected_question_rows(self) -> None:
        image = Image.new("RGB", (480, 900), "white")
        payload = io.BytesIO()
        image.save(payload, format="PNG")
        image.close()
        expected = set(range(1, 101))
        letters = ["D", "A", "B", "C"]
        columns = [
            "\n".join(f"{number} ({letters[(number - 1) % 4]})" for number in range(start, start + 25))
            for start in (1, 26, 51, 76)
        ]
        full_page_hint = "\n".join(
            " ".join(
                f"{number} ({letters[(number - 1) % 4]})"
                for number in range(row, row + 4)
            )
            for row in (1, 5)
        )
        with patch(
            "answer_key._ocr_text",
            side_effect=[full_page_hint] + columns,
        ):
            answers, _raw_text, duplicates = extract_answer_key_image(
                payload.getvalue(), expected_numbers=expected
            )
        self.assertEqual(len(answers), 100)
        self.assertEqual(answers[1], "D")
        self.assertEqual(answers[25], "D")
        self.assertEqual(answers[26], "A")
        self.assertEqual(answers[100], "C")
        self.assertEqual(duplicates, [])

    def test_five_column_photo_layout_is_prioritized_in_full_pipeline(self) -> None:
        image = Image.new("RGB", (512, 715), "white")
        payload = io.BytesIO()
        image.save(payload, format="PNG")
        image.close()
        expected = set(range(101, 201))
        # Exact answer rows from the supplied 101-200 five-column sample.
        row_letters = "".join(
            (
                "CDDAC", "CDABB", "CCDDB", "CDACA", "DCBAB",
                "ABBBC", "BCDAC", "ACBCC", "BDBDA", "DBCBA",
                "ADACB", "CCBAD", "CDBDD", "BACAD", "BBAAC",
                "DBABA", "BBCCA", "CADBB", "DABCD", "CDDAD",
            )
        )
        self.assertEqual(len(row_letters), 100)
        answer_key = dict(zip(range(101, 201), row_letters))
        columns = [
            "\n".join(
                f"{number} ({answer_key[number]})"
                for number in range(101 + column, 201, 5)
            )
            for column in range(5)
        ]
        with patch(
            "answer_key._ocr_text",
            side_effect=["TEST 5"] + columns,
        ) as ocr:
            answers, _raw_text, issues = extract_answer_key_image(
                payload.getvalue(), expected_numbers=expected
            )
        self.assertEqual(answers, answer_key)
        self.assertEqual(ocr.call_count, 6)
        self.assertEqual(issues, [])

    def test_five_column_listening_photo_layout_maps_one_to_one_hundred(self) -> None:
        image = Image.new("RGB", (378, 543), "white")
        payload = io.BytesIO()
        image.save(payload, format="PNG")
        image.close()
        expected = set(range(1, 101))
        # Exact answer rows from the supplied 1-100 five-column sample.
        row_letters = "".join(
            (
                "BCAAB", "DBCBC", "ABAAC", "BACAC", "ACBCB",
                "AABBA", "CCDAC", "BACDA", "DDCCC", "BDBAD",
                "ABDAC", "BACBD", "ABACB", "BCABA", "BADCB",
                "ACABD", "ACDBB", "DACDB", "CCABD", "DBCAB",
            )
        )
        answer_key = dict(zip(range(1, 101), row_letters))
        columns = [
            "\n".join(
                f"{number} ({answer_key[number]})"
                for number in range(1 + column, 101, 5)
            )
            for column in range(5)
        ]
        with patch(
            "answer_key._ocr_text",
            side_effect=["TEST 1"] + columns,
        ) as ocr:
            answers, _raw_text, issues = extract_answer_key_image(
                payload.getvalue(), expected_numbers=expected
            )
        self.assertEqual(answers, answer_key)
        self.assertEqual(ocr.call_count, 6)
        self.assertEqual(issues, [])

    def test_full_page_rows_select_the_matching_grid_width(self) -> None:
        self.assertEqual(
            _preferred_grid_column_counts("1(A) 2(B) 3(C) 4(D)\n5(A) 6(B) 7(C) 8(D)"),
            (4, 5),
        )
        self.assertEqual(
            _preferred_grid_column_counts(
                "101(A) 102(B) 103(C) 104(D) 105(A)\n"
                "106(B) 107(C) 108(D) 109(A) 110(B)"
            ),
            (5, 4),
        )

    def test_five_column_row_major_grid_uses_visual_row_order(self) -> None:
        image = Image.new("RGB", (750, 900), "white")
        expected = set(range(101, 201))
        columns = [
            "\n".join(
                f"{number} ({'ABCD'[(number - 101) % 4]})"
                for number in range(101 + column, 201, 5)
            )
            for column in range(5)
        ]
        with patch("answer_key._ocr_text", side_effect=columns):
            candidate = _grid_answer_candidate(image, expected, 5)
        image.close()
        self.assertIsNotNone(candidate)
        answers = candidate[0] if candidate else {}
        self.assertEqual(len(answers), 100)
        self.assertEqual(answers[101], "A")
        self.assertEqual(answers[105], "A")
        self.assertEqual(answers[200], "D")

    def test_grid_does_not_remap_reading_numbers_into_listening_range(self) -> None:
        image = Image.new("RGB", (750, 900), "white")
        columns = [
            "\n".join(
                f"{number} (A)"
                for number in range(101 + column, 201, 5)
            )
            for column in range(5)
        ]
        with patch("answer_key._ocr_text", side_effect=columns):
            candidate = _grid_answer_candidate(image, set(range(1, 101)), 5)
        image.close()
        self.assertIsNone(candidate)

    def test_complete_regular_key_finishes_after_one_ocr_call(self) -> None:
        image = Image.new("RGB", (443, 851), "white")
        payload = io.BytesIO()
        image.save(payload, format="PNG")
        image.close()
        expected = set(range(101, 201))
        full_text = "\n".join(
            " ".join(
                f"{number} ({'ABCD'[(number - 101) % 4]})"
                for number in range(row, row + 5)
            )
            for row in range(101, 201, 5)
        )
        with patch(
            "answer_key._ocr_text",
            return_value=full_text,
        ) as ocr:
            answers, _raw_text, issues = extract_answer_key_image(
                payload.getvalue(), expected_numbers=expected
            )
        self.assertEqual(len(answers), 100)
        self.assertEqual(ocr.call_count, 1)
        self.assertEqual(issues, [])

    def test_paddle_timeout_stops_recovery_instead_of_spinning(self) -> None:
        image = Image.new("RGB", (1200, 1800), "white")
        payload = io.BytesIO()
        image.save(payload, format="PNG")
        image.close()
        with patch(
            "answer_key._ocr_text",
            side_effect=AnswerKeyOcrTimeout("PaddleOCR process timeout"),
        ) as ocr:
            answers, _raw_text, issues = extract_answer_key_image(
                payload.getvalue(), expected_numbers=set(range(1, 101))
            )
        self.assertEqual(answers, {})
        self.assertEqual(ocr.call_count, 1)
        self.assertTrue(any("dừng" in issue and "30 giây" in issue for issue in issues))

    def test_consensus_conflict_keeps_best_observed_answer_for_review(self) -> None:
        image = Image.new("RGB", (400, 180), "white")
        payload = io.BytesIO()
        image.save(payload, format="PNG")
        image.close()
        with patch(
            "answer_key._ocr_text",
            side_effect=["1(A) 2(B)", "1(B) 2(B)", "2(B)"],
        ):
            answers, _raw_text, issues = extract_answer_key_image(
                payload.getvalue(), expected_numbers={1, 2}
            )
        self.assertEqual(answers, {1: "A", 2: "B"})
        self.assertTrue(any("1:" in issue and "kiểm tra" in issue for issue in issues))


class AudioStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        self.store = JobStore(self.temp)
        self.job_id, self.job_dir = self.store.create(
            filename="lc.pdf", exam_type="listening", file_hash="hash"
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_audio_path_is_scoped_to_job(self) -> None:
        audio_id = "sample.mp3"
        path = self.job_dir / "audio" / audio_id
        path.write_bytes(b"ID3")
        self.assertEqual(self.store.audio_path(self.job_id, audio_id), path)
        with self.assertRaises(FileNotFoundError):
            self.store.audio_path(self.job_id, "../state.json")

    def test_draft_accepts_audio_metadata(self) -> None:
        audio = AudioRef(
            id="sample.mp3",
            url=f"/api/extractions/{self.job_id}/audio/sample.mp3",
            filename="LC audio.mp3",
            content_type="audio/mpeg",
            size=25 * 1024 * 1024,
        )
        draft = ExamDraft(
            job_id=self.job_id,
            exam_type="listening",
            status="review",
            stage="Sẵn sàng",
            progress=100,
            filename="LC.pdf",
            audio=audio,
        )
        self.assertEqual(draft.audio, audio)

    def test_draft_accepts_four_part_audio_files(self) -> None:
        audios = [
            AudioRef(
                id=f"part-{part}.mp3",
                url=f"/audio/part-{part}.mp3",
                filename=f"Part {part}.mp3",
                content_type="audio/mpeg",
                size=1024,
                part=f"part_{part}",
            )
            for part in range(1, 5)
        ]
        draft = ExamDraft(
            job_id=self.job_id,
            exam_type="listening",
            status="review",
            stage="Sẵn sàng",
            progress=100,
            filename="LC.pdf",
            audios=audios,
        )
        self.assertEqual([audio.part for audio in draft.audios], [
            "part_1",
            "part_2",
            "part_3",
            "part_4",
        ])


if __name__ == "__main__":
    unittest.main()
