"""Unit and golden tests for the layout-aware extraction pipeline."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
import uuid
from collections import Counter
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

from job_store import JobStore
from main import _select_grouped_questions
from pipeline import (
    OCRToken,
    PageResult,
    ReadingPagePlan,
    ReadingHeader,
    _build_stimuli,
    _build_reading_page_plans,
    create_manual_stimulus,
    _detect_content_start,
    _dominant_content_bbox,
    _expected_document_count,
    _first_option_top,
    _full_page_ocr_scale,
    _listening_photo_coarse_bbox,
    _listening_text_page_is_usable,
    _option_letters,
    _page_workers,
    _paddle_page_result,
    _parse_column,
    _preprocess_for_ocr,
    _question_top,
    _reading_text_page_is_usable,
    _reading_headers,
    _reading_headers_with_layout_fallback,
    _reading_detected_range,
    _listening_detected_range,
    _reading_roi_fallback_pages,
    _merge_fallback_option_fragments,
    _merge_scan_recovery_candidates,
    _needs_sentence_punctuation_recovery,
    _question_block_rois,
    _resolve_sequence,
    _scan_quality_retry_pages,
    _save_crop,
    _to_questions,
    _trim_and_split_bboxes,
    extract_exam,
    recrop_asset,
)
from parser import _extract_options
from rapid_ocr import OCRLine, OCRResult
from schemas import Question


ROOT = Path(__file__).resolve().parent.parent


def candidate(number: int, page: int, order: int, raw: str | None = None) -> dict:
    return {
        "raw_number": raw or str(number),
        "number": number,
        "text": f"Question {number}",
        "options": {"A": "a", "B": "b", "C": "c", "D": "d"},
        "page": page,
        "column": 0,
        "order": order,
        "confidence": 90,
        "issues": [],
    }


class SequenceTests(unittest.TestCase):
    def test_repairs_damaged_leading_digits_using_exact_sequence(self) -> None:
        parsed, _issues = _resolve_sequence(
            [
                candidate(171, 16, 1),
                candidate(472, 18, 1, "472"),
                candidate(473, 18, 2, "473"),
                candidate(174, 18, 3),
                candidate(75, 18, 4, "75"),
            ],
            "reading",
        )
        by_number = {item["number"]: item for item in parsed}
        for number in range(171, 176):
            self.assertNotIn("question_missing", by_number[number]["issues"])
        self.assertIn("number_inferred", by_number[172]["issues"])
        self.assertIn("number_inferred", by_number[175]["issues"])

    def test_rejects_out_of_range_noise_instead_of_inventing_content(self) -> None:
        parsed, issues = _resolve_sequence(
            [
                candidate(149, 9, 1),
                candidate(150, 9, 2),
                candidate(4151, 10, 1, "4151"),
                candidate(152, 10, 2),
                candidate(153, 11, 1),
            ],
            "reading",
        )
        by_number = {item["number"]: item for item in parsed}
        self.assertIn(151, by_number)
        self.assertIn("question_missing", by_number[151]["issues"])
        self.assertNotIn("number_inferred", by_number[151]["issues"])
        self.assertEqual(by_number[151]["page"], 10)
        self.assertTrue(any(issue.question_number == 151 for issue in issues))

    def test_recovers_unmarked_complete_option_groups_in_visual_order(self) -> None:
        right_column = _parse_column(
            "(A) One\n(B) Two\n(C) Three\n(D) Four\n"
            "(A) Five\n(B) Six\n(C) Seven\n(D) Eight\n"
            "(A) Nine\n(B) Ten\n(C) Eleven\n(D) Twelve\n"
            "53. Next question\n(A) A\n(B) B\n(C) C\n(D) D",
            page=6,
            column=1,
            confidence=90,
        )
        parsed, issues = _resolve_sequence(
            [candidate(49, 6, 1), *right_column], "listening"
        )
        by_number = {item["number"]: item for item in parsed}
        self.assertEqual([number for number in by_number if 49 <= number <= 53], [49, 50, 51, 52, 53])
        self.assertEqual(by_number[50]["options"]["D"], "Four")
        self.assertEqual(by_number[52]["options"]["A"], "Nine")
        self.assertTrue(
            any(issue.code == "question_recovered_from_options" for issue in issues)
        )

    def test_unmarked_block_preserves_question_stem_for_missing_number(self) -> None:
        recovered = _parse_column(
            "What does the speaker imply?\n"
            "(A) One answer\n(B) Two answer\n(C) Three answer\n(D) Four answer",
            page=9,
            column=1,
            confidence=90,
        )
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["text"], "What does the speaker imply?")
        self.assertEqual(set(recovered[0]["options"]), {"A", "B", "C", "D"})

    def test_unmarked_partial_group_keeps_following_options_aligned(self) -> None:
        right_column = _parse_column(
            "(A) One\n(C) Three\n(D) Four\n"
            "(A) Five\n(B) Six\n(C) Seven\n(D) Eight\n"
            "(A) Nine\n(B) Ten\n(C) Eleven\n(D) Twelve\n"
            "53. Next question\n(A) A\n(B) B\n(C) C\n(D) D",
            page=6,
            column=1,
            confidence=90,
        )
        parsed, _issues = _resolve_sequence(
            [candidate(49, 6, 1), *right_column], "listening"
        )
        by_number = {item["number"]: item for item in parsed}
        self.assertNotIn("B", by_number[50]["options"])
        self.assertEqual(by_number[51]["options"]["A"], "Five")
        self.assertEqual(by_number[52]["options"]["A"], "Nine")

    def test_missing_question_uses_following_page_at_page_boundary(self) -> None:
        parsed, _ = _resolve_sequence(
            [candidate(149, 9, 1), candidate(150, 9, 2), candidate(152, 10, 2)],
            "reading",
        )
        question = next(item for item in parsed if item["number"] == 151)
        self.assertEqual(question["page"], 10)

    def test_prefers_part_six_answer_choices_over_passage_number_duplicates(self) -> None:
        parsed, issues = _resolve_sequence(
            [
                candidate(131, 4, 1, raw="131"),
                {**candidate(133, 4, 2, raw="133"), "text": "passage fragment", "options": {}},
                {**candidate(131, 4, 3, raw="131"), "text": "", "options": {"A": "serve", "B": "served", "C": "server", "D": "service"}},
                {**candidate(132, 4, 4, raw="132"), "text": "", "options": {"A": "Along", "B": "During", "C": "Without", "D": "Between"}},
                {**candidate(133, 4, 5, raw="133"), "text": "", "options": {"A": "apologize", "B": "organize", "C": "realize", "D": "recognize"}},
                {**candidate(134, 4, 6, raw="134"), "text": "", "options": {"A": "If", "B": "Thank", "C": "Please", "D": "Questions"}},
            ],
            "reading",
        )
        self.assertEqual([item["number"] for item in parsed], [131, 132, 133, 134])
        self.assertFalse(issues)

    def test_part_two_has_only_three_options(self) -> None:
        self.assertEqual(_option_letters("listening", 7), ["A", "B", "C"])
        self.assertEqual(_option_letters("listening", 31), ["A", "B", "C"])
        self.assertEqual(_option_letters("listening", 32), ["A", "B", "C", "D"])


class OcrRoutingTests(unittest.TestCase):
    @staticmethod
    def page(number: int, text: str) -> PageResult:
        return PageResult(
            number=number,
            width=1200,
            height=1600,
            columns=[text, ""],
            tokens=[],
            confidence=90,
        )

    def test_keeps_physical_page_one_after_caller_trims_cover(self) -> None:
        pages = [
            self.page(1, "ETS TOEIC PRACTICE TEST"),
            self.page(2, "READING TEST\nDirections: A word or phrase"),
            self.page(
                3,
                "101. What is the correct answer?\n"
                "(A) One\n(B) Two\n(C) Three\n(D) Four",
            ),
        ]
        self.assertEqual(_detect_content_start(pages, "reading"), 1)

    def test_reading_never_auto_skips_a_prefix_page(self) -> None:
        pages = [
            self.page(1, "RC TEST 01"),
            self.page(2, "101. First reading question\n(A) One\n(B) Two\n(C) Three\n(D) Four"),
        ]
        self.assertEqual(_detect_content_start(pages, "reading"), 1)

    def test_reading_keeps_page_one_even_when_ocr_looks_like_directions(self) -> None:
        pages = [self.page(number, "RC TEST\nDirections") for number in range(1, 7)]
        pages.append(self.page(7, "101. First reading question\n(A) One\n(B) Two\n(C) Three\n(D) Four"))
        self.assertEqual(_detect_content_start(pages, "reading"), 1)

    def test_never_skips_a_page_that_contains_a_valid_question(self) -> None:
        pages = [
            self.page(
                1,
                "ETS TOEIC READING\n"
                "101. What is the correct answer?\n"
                "(A) One\n(B) Two\n(C) Three\n(D) Four",
            ),
            self.page(2, "102. Next question"),
            self.page(3, "103. Next question"),
        ]
        self.assertEqual(_detect_content_start(pages, "reading"), 1)

    def test_accepts_question_marker_without_space_after_period(self) -> None:
        pages = [
            self.page(1, "101. First question\n(A) One\n(B) Two\n(C) Three\n(D) Four"),
            self.page(2, "186.What is the question?\n(A) One\n(B) Two\n(C) Three\n(D) Four"),
            self.page(3, "187. Next question"),
        ]
        self.assertEqual(_detect_content_start(pages, "reading"), 1)

    def test_accepts_scan_question_marker_when_period_is_lost(self) -> None:
        parsed = _parse_column(
            "78 According to the speaker, what is different?\n"
            "(A) One\n(B) Two\n(C) Three\n(D) Four",
            page=9,
            column=1,
            confidence=90,
        )
        self.assertEqual([item["number"] for item in parsed], [78])

    def test_does_not_treat_passage_number_as_question_marker(self) -> None:
        parsed = _parse_column(
            "The space can accommodate up to 200 guests and is ideal for\n"
            "wedding receptions.\n"
            "151. What is indicated about the venue?\n"
            "(A) One\n(B) Two\n(C) Three\n(D) Four",
            page=10,
            column=0,
            confidence=99,
        )
        self.assertEqual([item["number"] for item in parsed], [151])

    def test_reading_text_layer_falls_back_when_one_question_lacks_options(self) -> None:
        columns = [
            "109. Incomplete question\n"
            "110. Complete question\n(A) One\n(B) Two\n(C) Three\n(D) Four",
            "",
        ]
        self.assertFalse(_reading_text_page_is_usable(columns, 2))

    def test_reading_partial_part_six_and_seven_uses_the_real_sixty_question_span(self) -> None:
        pages = [
            self.page(
                1,
                "Questions 141-146 refer to the following notice.\n"
                "Questions 147-200 refer to the following documents.",
            )
        ]
        detected = set(range(142, 201))
        self.assertEqual(
            _reading_detected_range(
                pages,
                content_start_page=1,
                detected_numbers=detected,
            ),
            (141, 200),
        )

    def test_listening_part_two_without_markers_uses_part_range_not_full_test(self) -> None:
        pages = [self.page(1, "PART 2\nDirections: Three responses will be spoken.")]
        self.assertEqual(
            _listening_detected_range(
                pages,
                content_start_page=1,
                detected_numbers=set(),
            ),
            (7, 31),
        )

    def test_does_not_treat_ocr_letter_at_line_start_as_question_number(self) -> None:
        parsed = _parse_column(
            "57. According to the woman, what took place\n"
            "last Friday?\n(A) One\n(B) Two\n(C) Three\n(D) Four",
            page=7,
            column=0,
            confidence=99,
        )
        self.assertEqual([item["number"] for item in parsed], [57])
        self.assertEqual(sorted(parsed[0]["options"]), ["A", "B", "C", "D"])

    def test_listening_uses_part_two_as_anchor_for_photo_prefix(self) -> None:
        pages = [
            self.page(1, ""),
            self.page(2, "3."),
            self.page(3, "5."),
            self.page(4, "PART 2\nDirections"),
        ]
        self.assertEqual(_detect_content_start(pages, "listening"), 1)

    def test_listening_always_starts_at_physical_page_one(self) -> None:
        pages = [
            self.page(1, "LC TEST 01"),
            self.page(2, "LISTENING TEST\nPART 1\nDirections: Listen carefully."),
            self.page(3, "1.\nA photograph\n\n2.\nAnother photograph"),
            self.page(4, "PART 2\nDirections"),
        ]
        self.assertEqual(_detect_content_start(pages, "listening"), 1)

    def test_standalone_part_two_starts_at_its_section_page(self) -> None:
        pages = [
            self.page(1, "PART 2\nDirections: question or statement"),
            self.page(2, "7. What does the speaker mean?\n(A) One\n(B) Two\n(C) Three"),
        ]
        self.assertEqual(_detect_content_start(pages, "listening"), 1)

    def test_listening_does_not_auto_skip_numbered_directions(self) -> None:
        pages = [
            self.page(1, "LC TEST 01"),
            self.page(2, "LISTENING TEST\nPART 1\n1. Read the directions\n2. Mark the sheet"),
            self.page(3, "1.\nA photograph\n\n2.\nAnother photograph"),
            self.page(4, "PART 2\nDirections"),
        ]
        self.assertEqual(_detect_content_start(pages, "listening"), 1)

    def test_listening_ignores_part_two_anchor_for_page_start(self) -> None:
        pages = [
            self.page(1, "cover"),
            self.page(2, "PART 2\nA later-page OCR false positive"),
            self.page(3, "32. First printed question\n(A) One\n(B) Two"),
        ]
        self.assertEqual(_detect_content_start(pages, "listening"), 1)

    def test_listening_ignores_late_table_values_one_and_two(self) -> None:
        pages = [
            self.page(1, "1.\nFirst photo\n\n2.\nSecond photo"),
            self.page(2, "3.\nThird photo\n\n4.\nFourth photo"),
            self.page(3, "5.\nFifth photo\n\n6.\nSixth photo"),
            self.page(
                4,
                "PART 2\nDirections: question or statement and three responses\n"
                "7. Mark your answer\n8. Mark your answer",
            ),
            self.page(8, "65. Table values\n1. One month\n2. Two months"),
        ]
        self.assertEqual(_detect_content_start(pages, "listening"), 1)

    def test_high_resolution_retry_keeps_source_dimensions(self) -> None:
        source = Image.new("RGB", (400, 200), "white")
        try:
            full = _preprocess_for_ocr(source)
            fast = _preprocess_for_ocr(source, 0.75)
            self.assertEqual(full.size, (400, 200))
            self.assertEqual(fast.size, (300, 150))
            full.close()
            fast.close()
        finally:
            source.close()

    def test_listening_full_page_scale_defaults_to_source_resolution(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "OCR_LISTENING_PAGE_SCALE": "1.0",
                "OCR_READING_PAGE_SCALE": "0.75",
            },
        ):
            self.assertEqual(_full_page_ocr_scale("listening"), 1.0)
            self.assertEqual(_full_page_ocr_scale("reading"), 0.75)

    def test_listening_text_layer_rejects_a_page_with_a_missing_question(self) -> None:
        valid = (
            "71. Question one?\n(A) One\n(B) Two\n(C) Three\n(D) Four\n"
            "72. Question two?\n(A) One\n(B) Two\n(C) Three\n(D) Four"
        )
        damaged = (
            valid
            + "\n74. Question four?\n(A) One\n(B) Two\n(C) Three\n(D) Four"
        )
        self.assertTrue(_listening_text_page_is_usable([valid], 9))
        self.assertFalse(_listening_text_page_is_usable([damaged], 9))

    def test_listening_text_layer_rejects_image_only_pdf_page(self) -> None:
        # A scan has no selectable PDF text. It must run raster OCR instead of
        # being mistaken for a harmless Part 1/2 direction page.
        self.assertFalse(_listening_text_page_is_usable(["", "  "], 5))

    def test_question_block_recovery_ignores_direction_option_legend(self) -> None:
        tokens = [
            OCRToken("(A)", 95, 650, 100, 30, 20),
            OCRToken("(D)", 95, 760, 100, 30, 20),
        ]
        for number, top, left in (
            (71, 200, 100),
            (72, 500, 100),
            (73, 200, 600),
            (74, 500, 600),
        ):
            tokens.extend(
                [
                    OCRToken(f"{number}.", 95, left, top, 45, 24),
                    OCRToken("(A)", 95, left, top + 45, 40, 24),
                    OCRToken("(D)", 95, left, top + 120, 40, 24),
                ]
            )
        page = PageResult(9, 1000, 1000, ["", ""], tokens, 95)
        rois = _question_block_rois(page, [71, 72, 73, 74])
        self.assertEqual(set(rois), {71, 72, 73, 74})

    def test_question_block_recovery_uses_number_anchors_when_markers_are_missing(self) -> None:
        page = PageResult(
            number=9,
            width=1000,
            height=1200,
            columns=["", ""],
            tokens=[
                OCRToken("71.", 95, 100, 100, 42, 24),
                OCRToken("72.", 95, 100, 400, 42, 24),
                OCRToken("73.", 95, 620, 100, 42, 24),
                # Only one option marker survived. The previous exact-count
                # A/D strategy returned no crops for this damaged page.
                OCRToken("(A)", 95, 100, 160, 36, 24),
            ],
            confidence=95,
        )
        rois = _question_block_rois(page, [71, 72, 73])
        self.assertEqual(set(rois), {71, 72, 73})
        self.assertEqual(rois[71].kind, "scan_question_block_number_anchor")
        self.assertEqual(rois[73].column, 1)

    def test_page_worker_count_is_bounded_to_six(self) -> None:
        with mock.patch.dict(os.environ, {"OCR_PAGE_WORKERS": "99"}):
            self.assertEqual(_page_workers(50), 6)

    def test_scan_retry_includes_missing_options_but_not_listening_part_two(self) -> None:
        listening = [
            {
                **candidate(20, 4, 1),
                "options": {},
                "issues": [],
            },
            {
                **candidate(50, 6, 1),
                "options": {"A": "a", "B": "b"},
                "issues": [],
            },
            {
                **candidate(68, 8, 1),
                "options": {},
                "issues": ["question_missing"],
            },
        ]
        with mock.patch.dict(os.environ, {"OCR_SCAN_RETRY_PAGES": "12"}):
            self.assertEqual(_scan_quality_retry_pages(listening, "listening"), [6, 8])
            reading = [
                {
                    **candidate(117, 3, 1),
                    "options": {"A": "a", "B": "b", "C": "c"},
                    "issues": [],
                }
            ]
            self.assertEqual(_scan_quality_retry_pages(reading, "reading"), [3])

    def test_sentence_punctuation_recovery_requires_crop_evidence_to_merge(self) -> None:
        existing = {
            **candidate(200, 28, 1),
            "text": "According to the price list, what is true?",
            "options": {
                "A": "They can fit three adults",
                "B": "They can be rented overnight",
                "C": "They are suitable for small children",
                "D": "They are equipped with life jackets",
            },
        }
        recovered = {
            **existing,
            "options": {
                letter: f"{value}."
                for letter, value in existing["options"].items()
            },
        }
        self.assertTrue(_needs_sentence_punctuation_recovery(existing))
        merged = _merge_scan_recovery_candidates(
            [existing], [recovered], terminal_punctuation_numbers={200}
        )
        self.assertEqual(merged[0]["options"]["D"], "They are equipped with life jackets.")

    def test_rejects_scrambled_text_layer_without_complete_options(self) -> None:
        self.assertFalse(
            _reading_text_page_is_usable(["101. scrambled font mapping", ""], 1)
        )

    def test_marks_printed_question_with_no_text_for_review(self) -> None:
        questions, issues = _to_questions(
            [{**candidate(50, 6, 1), "text": "", "issues": []}],
            "listening",
        )
        self.assertIn("question_missing", questions[0]["issues"])
        self.assertTrue(any(issue.code == "question_missing" for issue in issues))
        self.assertTrue(
            _reading_text_page_is_usable(
                [
                    "101. Complete question?\n"
                    "(A) One\n(B) Two\n(C) Three\n(D) Four",
                    "",
                ],
                1,
            )
        )

    def test_spatial_row_merge_joins_split_question_number_fragment(self) -> None:
        result = OCRResult(
            lines=(
                OCRLine("What does the woman suggest?", 99, ((90, 100), (420, 100), (420, 130), (90, 130))),
                OCRLine("51.", 99, ((40, 102), (75, 102), (75, 130), (40, 130))),
            ),
            elapsed=0.1,
            provider="test",
        )
        page = _paddle_page_result(
            result,
            page_number=1,
            page_width=800,
            page_height=1000,
            processed_width=800,
            coordinate_scale=1.0,
        )
        self.assertIn("51. What does the woman suggest?", page.columns[0])


class ReadingNoiseTests(unittest.TestCase):
    def test_removes_trailing_test_fragments_from_question_and_options(self) -> None:
        text, options, _ = _extract_options(
            "Why did she contact customer service? T\n"
            "(A) To schedule a visit\n"
            "(B) To ask about a warranty\n"
            "(C) To obtain advice on making a repair T\n"
            "(D) To request a replacement machine E S T"
        )
        self.assertEqual(text, "Why did she contact customer service?")
        self.assertEqual(options["C"], "To obtain advice on making a repair")
        self.assertEqual(options["D"], "To request a replacement machine")

    def test_repairs_bare_option_marker_split_from_its_text(self) -> None:
        _text, options, _ = _extract_options(
            "After ------ the neighborhood?\n"
            "(A) evaluation\n"
            "B evaluate\n"
            "(C) evaluating\n"
            "(D) evaluated"
        )
        self.assertEqual(
            options,
            {
                "A": "evaluation",
                "B": "evaluate",
                "C": "evaluating",
                "D": "evaluated",
            },
        )

    def test_preserves_meaningful_trailing_answer_letters(self) -> None:
        _, options, _ = _extract_options(
            "Which plan applies?\n"
            "(A) Plan A\n(B) Plan B\n(C) Type C\n(D) Choice D"
        )
        self.assertEqual(options["A"], "Plan A")
        self.assertEqual(options["B"], "Plan B")
        self.assertEqual(options["C"], "Type C")

    def test_recovers_option_letter_hidden_by_handwritten_mark(self) -> None:
        _, options, _ = _extract_options(
            "Question\n(A) whichever\n(B) it\n) that\n(D) either"
        )
        self.assertEqual(
            options,
            {"A": "whichever", "C": "that", "B": "it", "D": "either"},
        )

    def test_normalizes_damaged_option_markers(self) -> None:
        for marker in ("{A)", "[A)", "(A}", "A)", "Ⓐ"):
            text, options, _ = _extract_options(
                f"Question text\n{marker} regional\n(B) b\n(C) c\n(D) d"
            )
            self.assertEqual(text, "Question text")
            self.assertEqual(options["A"], "regional", marker)

    def test_accepts_scan_bullet_before_option_marker(self) -> None:
        _, options, _ = _extract_options(
            "Question\n(A) one\n(B) two\n* (C) three\n7 (D) four"
        )
        self.assertEqual(options["C"], "three")
        self.assertEqual(options["D"], "four")

    def test_removes_duplicated_word_before_scan_option_marker(self) -> None:
        _text, options, _ = _extract_options(
            "Question\n(A) one\n(B) two\n(C) three\nA (D) television set"
        )
        self.assertEqual(options["D"], "television set")

    def test_trims_following_question_from_previous_option(self) -> None:
        _text, options, _ = _extract_options(
            "Question\n(A) one\n(B) two\n(C) three\n"
            "(D) four\nWhat does the speaker imply?\n(A) later"
        )
        self.assertEqual(options["D"], "four")

    def test_parses_option_marker_when_ocr_joins_it_to_question_line(self) -> None:
        text, options, _ = _extract_options(
            "What does the speaker imply? (A) One answer\n"
            "(B) Two answer\n(C) Three answer\n(D) Four answer"
        )
        self.assertEqual(text, "What does the speaker imply?")
        self.assertEqual(set(options), {"A", "B", "C", "D"})

    def test_removes_bare_page_number_after_last_option(self) -> None:
        parsed = _parse_column(
            "104. Hotel window?\n(A) up\n(B) except\n(C) onto\n(D) through\n20",
            page=1,
            column=0,
            confidence=90,
        )
        self.assertEqual(parsed[0]["options"]["D"], "through")

    def test_does_not_treat_am_as_option_a(self) -> None:
        parsed = _parse_column(
            "173. At 8:59 a.m., what happened?\n"
            "(A) First answer\n(B) Second\n(C) Third\n(D) Fourth",
            page=18,
            column=0,
            confidence=90,
        )
        self.assertEqual(parsed[0]["text"], "At 8:59 a.m., what happened?")
        self.assertEqual(parsed[0]["options"]["A"], "First answer")

    def test_repairs_mismatched_closing_smart_quote(self) -> None:
        parsed = _parse_column(
            "173. What does “Not at all’?\n"
            "(A) First\n(B) Second\n(C) Third\n(D) Fourth",
            page=18,
            column=0,
            confidence=90,
        )
        self.assertEqual(parsed[0]["text"], "What does “Not at all”?")

    def test_removes_end_of_test_footer_from_last_options(self) -> None:
        parsed = _parse_column(
            "198. What is the rate?\n(A) $11\n(B) $13\n(C) $14\n(D) $15\n"
            "Stop! This is the end of the test.\n"
            "200. What is included?\n(A) A\n(B) B\n(C) C\n"
            "(D) Life jackets.\nish before time is called, you may go on "
            "to Part 7 and check your work.",
            page=28,
            column=0,
            confidence=90,
        )
        self.assertEqual(parsed[0]["options"]["D"], "$15")
        self.assertEqual(parsed[1]["options"]["D"], "Life jackets.")

    def test_finds_range_header_and_its_page(self) -> None:
        page = PageResult(
            number=19,
            width=1000,
            height=1200,
            columns=[
                "Questions 176-180 refer to the following article and letter.",
                "",
            ],
            tokens=[
                OCRToken("Questions", 95, 100, 100, 100, 25),
                OCRToken("176-180", 95, 220, 100, 110, 25),
                OCRToken("refer", 95, 350, 100, 60, 25),
                OCRToken("to", 95, 420, 100, 30, 25),
                OCRToken("the", 95, 460, 100, 40, 25),
                OCRToken("following", 95, 510, 100, 100, 25),
                OCRToken("article", 95, 620, 100, 80, 25),
                OCRToken("and", 95, 710, 100, 45, 25),
                OCRToken("|", 95, 760, 100, 5, 25),
                OCRToken("etter.", 95, 770, 100, 60, 25),
            ],
            confidence=95,
        )
        headers = _reading_headers([page])
        self.assertEqual(
            [(item.start, item.end, item.page) for item in headers],
            [(176, 180, 19)],
        )
        self.assertEqual(
            headers[0].title,
            "Questions 176-180 refer to the following article and letter.",
        )

    def test_header_description_overrides_triple_passage_range_fallback(self) -> None:
        two_documents = ReadingHeader(
            start=186,
            end=190,
            page=42,
            top=0.05,
            bottom=0.09,
            description="e-mails and notice",
            title="",
        )
        three_documents = ReadingHeader(
            start=191,
            end=195,
            page=44,
            top=0.05,
            bottom=0.09,
            description="article, e-mail, and plan",
            title="",
        )
        self.assertEqual(_expected_document_count(two_documents), 2)
        self.assertEqual(_expected_document_count(three_documents), 3)


class ReadingRoiPlanningTests(unittest.TestCase):
    @staticmethod
    def _token(text: str, top: int, left: int = 80) -> OCRToken:
        return OCRToken(text, 95, left, top, max(20, len(text) * 8), 24)

    def test_part_six_skips_passage_for_high_quality_ocr(self) -> None:
        page = PageResult(
            number=5,
            width=1000,
            height=1400,
            columns=[
                "PART 6\nQuestions 131-134 refer to the following announcement.",
                "",
            ],
            tokens=[
                self._token("Questions", 100),
                self._token("131-134", 100, 170),
                self._token("131.", 520),
                self._token("A)", 800),
            ],
            confidence=85,
        )
        plans = _build_reading_page_plans(
            [page], content_start_page=2, text_pages=set()
        )
        self.assertEqual(plans[0].part, "Part 6")
        self.assertEqual(len(plans[0].question_rois), 1)
        self.assertGreater(plans[0].question_rois[0].bbox[1], 0.5)
        self.assertTrue(plans[0].passage_rois)

    def test_part_seven_passage_page_has_no_question_roi(self) -> None:
        passage = PageResult(
            number=3,
            width=1000,
            height=1400,
            columns=[
                "Questions 172-175 refer to the following online chat discussion.",
                "",
            ],
            tokens=[
                self._token("Questions", 100),
                self._token("172-175", 100, 170),
            ],
            confidence=85,
        )
        questions = PageResult(
            number=4,
            width=1000,
            height=1400,
            columns=["172. What is indicated?\n(A) One\n(B) Two", ""],
            tokens=[self._token("172.", 120), self._token("A)", 190)],
            confidence=85,
        )
        plans = _build_reading_page_plans(
            [passage, questions], content_start_page=2, text_pages=set()
        )
        self.assertEqual(plans[0].part, "Part 7")
        self.assertEqual(plans[0].question_rois, [])
        self.assertEqual(plans[1].part, "Part 7")
        self.assertEqual(len(plans[1].question_rois), 1)

    def test_uncertain_part_six_page_is_selected_for_full_page_fallback(self) -> None:
        plan = ReadingPagePlan(
            page=5,
            part="Part 6",
            source="low_dpi_ocr",
            expected_numbers=(131, 132, 133, 134),
            question_rois=[],
            passage_rois=[],
            confidence=40,
            fallback_reason="part6_option_anchor_missing",
        )
        self.assertEqual(
            _reading_roi_fallback_pages(
                [plan], [], [], skip_pages=set(), text_pages=set()
            ),
            [5],
        )

    def test_fallback_keeps_roi_option_block_when_full_page_misses_marker(self) -> None:
        full = [
            {
                "number": 109,
                "page": 3,
                "column": 0,
                "order": 0,
                "text": "Question",
                "options": {"A": "one"},
            }
        ]
        roi = [
            {
                "number": 0,
                "page": 3,
                "column": 0,
                "order": 40,
                "text": "",
                "options": {"A": "one", "B": "two", "C": "three", "D": "four"},
                "unmarked_options": True,
            }
        ]
        merged = _merge_fallback_option_fragments(full, roi)
        self.assertEqual(sorted(merged[0]["options"]), list("ABCD"))

    def test_recovery_merges_numbered_roi_candidate_without_overwriting_text(self) -> None:
        full = [
            {
                "number": 183,
                "page": 22,
                "column": 0,
                "order": 0,
                "text": "What tour did Ms. Bouton most likely take?",
                "options": {"A": "Tour 2", "B": "Tour 3", "C": "Tour 4"},
                "confidence": 94,
            }
        ]
        roi = [
            {
                "number": 183,
                "page": 22,
                "column": 0,
                "order": 0,
                "text": "lower quality replacement",
                "options": {
                    "A": "Tour 2",
                    "B": "Tour 3",
                    "C": "Tour 4",
                    "D": "Tour 5",
                },
                "confidence": 99,
            }
        ]
        merged = _merge_fallback_option_fragments(full, roi)
        self.assertEqual(merged[0]["text"], full[0]["text"])
        self.assertEqual(merged[0]["options"]["D"], "Tour 5")
        self.assertEqual(merged[0]["confidence"], 99)

    def test_empty_roi_fragments_do_not_abort_fallback_merge(self) -> None:
        full = [
            {
                "number": 102,
                "page": 1,
                "column": 0,
                "order": 0,
                "text": "Question",
                "options": {"A": "one"},
            }
        ]
        self.assertEqual(_merge_fallback_option_fragments(full, []), full)

    def test_question_block_rois_follow_a_d_markers_in_reading_order(self) -> None:
        page = PageResult(
            number=6,
            width=1000,
            height=1400,
            columns=["", ""],
            tokens=[
                OCRToken("(A) left one", 99, 80, 220, 300, 30),
                OCRToken("(D) left one", 99, 80, 430, 300, 30),
                OCRToken("(A) left two", 99, 80, 550, 300, 30),
                OCRToken("(D) left two", 99, 80, 760, 300, 30),
                OCRToken("(A) right one", 99, 580, 220, 300, 30),
                OCRToken("(D) right one", 99, 580, 430, 300, 30),
                OCRToken("(A) right two", 99, 580, 550, 300, 30),
                OCRToken("(D) right two", 99, 580, 760, 300, 30),
            ],
            confidence=99,
        )
        rois = _question_block_rois(page, [44, 45, 46, 47])
        self.assertEqual(sorted(rois), [44, 45, 46, 47])
        self.assertEqual(rois[44].column, 0)
        self.assertEqual(rois[46].column, 1)
        self.assertLess(rois[45].bbox[1], 550 / 1400)


class ReadingCropTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        (self.temp / "pages").mkdir()
        (self.temp / "assets").mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def _save_email_notice_page(self) -> None:
        page = Image.new("RGB", (800, 1100), "white")
        draw = ImageDraw.Draw(page)
        draw.rectangle((112, 110, 686, 545), outline="black", width=3)
        for top in range(132, 520, 22):
            draw.line((130, top, 666, top), fill="black", width=2)
        draw.rectangle((145, 598, 650, 730), outline="black", width=3)
        for top in range(620, 712, 18):
            draw.line((162, top, 632, top), fill="black", width=2)
        # Scanner/page-edge artifacts must not pull the crop sideways.
        draw.line((50, 125, 50, 525), fill="black", width=2)
        draw.line((728, 125, 728, 525), fill="black", width=2)
        # Simulate a small scanned page number separated by a large bottom gap.
        draw.rectangle((84, 1014, 101, 1026), fill="black")
        page.save(self.temp / "pages" / "page-042.jpg", quality=95)
        page.close()

    def test_spatial_anchors_accept_remote_ocr_whole_lines(self) -> None:
        page = PageResult(
            number=16,
            width=800,
            height=1000,
            columns=["", ""],
            tokens=[
                OCRToken("168. What is one purpose?", 99, 70, 590, 330, 24),
                OCRToken("（A） To announce a change", 99, 100, 640, 300, 24),
            ],
            confidence=99,
        )
        self.assertEqual(_question_top(page, range(168, 172)), 0.59)
        self.assertEqual(_first_option_top(page), 0.64)

    def test_part_six_answer_boundary_uses_numbered_option_a_line(self) -> None:
        page = PageResult(
            number=4,
            width=800,
            height=1000,
            columns=["", ""],
            tokens=[
                OCRToken("131.", 99, 350, 300, 40, 20),
                OCRToken("131.(A)serve", 99, 70, 501, 160, 24),
                OCRToken("134.(A) If you would like...", 99, 410, 502, 300, 24),
            ],
            confidence=99,
        )
        self.assertEqual(_first_option_top(page, range(131, 135)), 0.501)

    def test_part_one_photo_fit_ignores_number_and_next_photo(self) -> None:
        image = Image.new("RGB", (800, 1100), "white")
        draw = ImageDraw.Draw(image)
        draw.text((78, 75), "1.", fill="black")
        draw.rectangle((140, 75, 640, 405), fill=(150, 150, 150))
        for x in range(150, 640, 20):
            draw.line((x, 80, x, 400), fill=(80, 80, 80), width=2)
        # Beginning of the following photo lies inside the coarse safety box.
        draw.rectangle((140, 500, 640, 700), fill=(120, 120, 120))
        image.save(self.temp / "pages" / "page-001.jpg", quality=95)
        image.close()

        bbox = _dominant_content_bbox(
            job_dir=self.temp,
            page_number=1,
            bbox=(0.06, 0.035, 0.94, 0.47),
        )
        self.assertGreater(bbox[0], 0.12)
        self.assertLess(bbox[1], 0.08)
        self.assertLess(bbox[2], 0.85)
        self.assertLess(bbox[3], 0.40)

    def test_part_one_photo_coarse_box_stops_at_next_number_and_footer(self) -> None:
        page = PageResult(
            number=1,
            width=800,
            height=1000,
            columns=["", ""],
            tokens=[
                OCRToken("1.", 99, 30, 50, 20, 20),
                OCRToken("2.", 99, 30, 430, 20, 20),
                OCRToken("GO ON TO THE NEXT PAGE", 99, 500, 870, 250, 20),
            ],
            confidence=99,
        )
        upper = _listening_photo_coarse_bbox(
            page, 1, (0.06, 0.035, 0.94, 0.47)
        )
        lower = _listening_photo_coarse_bbox(
            page, 2, (0.06, 0.455, 0.94, 0.91)
        )
        self.assertAlmostEqual(upper[3], 0.418)
        self.assertAlmostEqual(lower[3], 0.858)

    def test_reading_header_accepts_collapsed_remote_ocr_spacing(self) -> None:
        page = PageResult(
            number=16,
            width=800,
            height=1000,
            columns=[
                "Questions168-171referto thefollowingletter.\n168. Question",
                "",
            ],
            tokens=[
                OCRToken(
                    "Questions168-171referto thefollowingletter.",
                    99,
                    80,
                    60,
                    500,
                    24,
                )
            ],
            confidence=99,
        )
        headers = _reading_headers([page])
        self.assertEqual([(item.start, item.end) for item in headers], [(168, 171)])

    def test_scan_header_fallback_uses_source_only_page_before_questions(self) -> None:
        pages = [
            PageResult(number=20, width=800, height=1000, columns=["", ""], tokens=[], confidence=90),
            PageResult(number=21, width=800, height=1000, columns=["source", ""], tokens=[], confidence=90),
            PageResult(
                number=22,
                width=800,
                height=1000,
                columns=["182. Question", "185. Question"],
                tokens=[OCRToken("182. Question", 99, 80, 600, 200, 24)],
                confidence=90,
            ),
        ]
        questions = [
            {"number": number, "_page": 20}
            for number in range(176, 181)
        ] + [
            {"number": number, "_page": 22}
            for number in range(181, 186)
        ]
        headers = _reading_headers_with_layout_fallback(pages, questions)
        target = next(item for item in headers if item.start == 181)
        self.assertEqual((target.start, target.end, target.page), (181, 185, 21))

    def test_part_seven_crop_stops_before_whole_line_question_token(self) -> None:
        image = Image.new("RGB", (800, 1000), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((100, 120, 700, 540), outline="black", width=3)
        for top in range(145, 520, 25):
            draw.line((125, top, 675, top), fill="black", width=2)
        # These rows represent questions and must never enter the asset.
        for top in range(620, 850, 30):
            draw.line((80, top, 720, top), fill="black", width=2)
        image.save(self.temp / "pages" / "page-016.jpg", quality=95)
        image.close()

        pages = [
            PageResult(
                number=16,
                width=800,
                height=1000,
                columns=[
                    "Questions 168-171 refer to the following letter.\n"
                    "168. What is one purpose?",
                    "",
                ],
                tokens=[
                    OCRToken(
                        "Questions 168-171 refer to the following letter.",
                        99,
                        80,
                        60,
                        520,
                        24,
                    ),
                    OCRToken(
                        "168. What is one purpose?", 99, 80, 600, 360, 24
                    ),
                ],
                confidence=99,
            )
        ]
        questions = [
            {"number": number, "_page": 16, "stimulus_id": None, "group_id": None}
            for number in range(168, 172)
        ]
        stimuli, issues = _build_stimuli(
            job_id="whole-line-anchor",
            job_dir=self.temp,
            exam_type="reading",
            pages=pages,
            questions=questions,
        )
        self.assertEqual(issues, [])
        self.assertEqual(len(stimuli), 1)
        self.assertEqual(len(stimuli[0]["assets"]), 1)
        self.assertLess(stimuli[0]["assets"][0]["bbox"][3], 0.60)

    def test_page_number_never_becomes_a_third_reading_document(self) -> None:
        self._save_email_notice_page()
        boxes, exact = _trim_and_split_bboxes(
            job_dir=self.temp,
            page_number=42,
            bbox=(0.045, 0.075, 0.92, 0.94),
            pieces=2,
        )
        self.assertTrue(exact)
        self.assertEqual(len(boxes), 2)
        self.assertLess(max(box[3] for box in boxes), 0.78)
        self.assertLess(boxes[0][3], boxes[1][1])
        self.assertLessEqual(boxes[0][0], 112 / 800)
        self.assertGreaterEqual(boxes[0][2], 686 / 800)
        self.assertLessEqual(boxes[0][1], 110 / 1100)
        self.assertGreaterEqual(boxes[0][3], 545 / 1100)
        self.assertGreater(boxes[0][0], 0.10)
        self.assertLess(boxes[0][2], 0.90)
        self.assertLessEqual(boxes[1][0], 145 / 800)
        self.assertGreaterEqual(boxes[1][2], 650 / 800)
        self.assertLessEqual(boxes[1][1], 598 / 1100)
        self.assertGreaterEqual(boxes[1][3], 730 / 1100)

    def test_unmet_triple_hint_returns_real_documents_without_footer_crop(self) -> None:
        self._save_email_notice_page()
        boxes, exact = _trim_and_split_bboxes(
            job_dir=self.temp,
            page_number=42,
            bbox=(0.045, 0.075, 0.92, 0.94),
            pieces=3,
        )
        self.assertFalse(exact)
        self.assertEqual(len(boxes), 2)
        self.assertLess(max(box[3] for box in boxes), 0.78)

    def test_reading_pipeline_creates_only_email_and_notice_assets(self) -> None:
        self._save_email_notice_page()
        Image.new("RGB", (800, 1100), "white").save(
            self.temp / "pages" / "page-043.jpg"
        )
        header_tokens = [
            OCRToken("Questions", 98, 85, 70, 92, 20),
            OCRToken("186-190", 98, 185, 70, 95, 20),
            OCRToken("refer", 98, 290, 70, 48, 20),
            OCRToken("to", 98, 345, 70, 24, 20),
            OCRToken("the", 98, 376, 70, 32, 20),
            OCRToken("following", 98, 414, 70, 72, 20),
            OCRToken("e-mails", 98, 492, 70, 62, 20),
            OCRToken("and", 98, 560, 70, 32, 20),
            OCRToken("notice.", 98, 598, 70, 58, 20),
        ]
        pages = [
            PageResult(
                number=42,
                width=800,
                height=1100,
                columns=[
                    "Questions 186-190 refer to the following e-mails and notice.",
                    "",
                ],
                tokens=header_tokens,
                confidence=98,
            ),
            PageResult(
                number=43,
                width=800,
                height=1100,
                columns=["186. Question", ""],
                tokens=[OCRToken("186.", 98, 80, 80, 42, 20)],
                confidence=98,
            ),
        ]
        questions = [
            {"number": number, "_page": 43, "stimulus_id": None, "group_id": None}
            for number in range(186, 191)
        ]
        stimuli, issues = _build_stimuli(
            job_id="crop-regression",
            job_dir=self.temp,
            exam_type="reading",
            pages=pages,
            questions=questions,
        )
        self.assertEqual(len(stimuli), 1)
        self.assertEqual(len(stimuli[0]["assets"]), 2)
        self.assertEqual(issues, [])
        self.assertTrue(
            all(asset["page"] == 42 for asset in stimuli[0]["assets"])
        )
        self.assertLess(
            max(asset["bbox"][3] for asset in stimuli[0]["assets"]),
            0.78,
        )


class GroupSelectionTests(unittest.TestCase):
    def test_count_is_exact_even_when_boundary_falls_inside_audio_group(self) -> None:
        questions = [
            Question(
                number=number,
                part="Part 3",
                option_letters=["A", "B", "C", "D"],
                group_id="g1" if number <= 3 else "g2",
            )
            for number in range(1, 7)
        ]
        selected = _select_grouped_questions(questions, requested_count=4, shuffle=False)
        self.assertEqual([question.number for question in selected], [1, 2, 3, 4])


class AssetSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        self.store = JobStore(self.temp)
        self.job_id, self.job_dir = self.store.create(
            filename="test.pdf", exam_type="reading", file_hash="abc"
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_crop_stays_inside_source_page(self) -> None:
        Image.new("RGB", (1000, 1000), "white").save(
            self.job_dir / "pages" / "page-001.jpg"
        )
        asset = _save_crop(
            job_id=self.job_id,
            job_dir=self.job_dir,
            page_number=1,
            bbox=(0.1, 0.2, 0.8, 0.9),
            lossless=True,
        )
        self.assertEqual((asset["width"], asset["height"]), (700, 700))
        self.assertTrue((self.job_dir / "assets" / asset["id"]).is_file())

    def test_manual_stimulus_uses_retained_source_page(self) -> None:
        Image.new("RGB", (1000, 1000), "white").save(
            self.job_dir / "pages" / "page-002.jpg"
        )
        stimulus = create_manual_stimulus(
            job_id=self.job_id,
            job_dir=self.job_dir,
            page_number=2,
            bbox=(0.1, 0.2, 0.8, 0.9),
            question_numbers=[63, 62, 63],
        )
        self.assertTrue(stimulus["id"].startswith("manual-"))
        self.assertEqual(stimulus["question_numbers"], [62, 63])
        self.assertEqual(stimulus["page_numbers"], [2])
        asset = stimulus["assets"][0]
        self.assertEqual(asset["page"], 2)
        self.assertTrue((self.job_dir / "assets" / asset["id"]).is_file())

    def test_asset_path_rejects_traversal(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.store.asset_path(self.job_id, "../state.json")

    def test_cache_hit_requires_crop_and_source_page(self) -> None:
        state = self.store.read(self.job_id)
        state.update(
            {
                "status": "review",
                "stimuli": [
                    {
                        "id": "reading-101-104",
                        "assets": [{"id": "crop.webp", "page": 1}],
                    }
                ],
            }
        )
        self.store.write(self.job_id, state)
        (self.job_dir / "assets" / "crop.webp").write_bytes(b"webp")

        self.assertIsNone(
            self.store.find_cached(file_hash="abc", exam_type="reading")
        )

        (self.job_dir / "pages" / "page-001.jpg").write_bytes(b"jpeg")
        cached = self.store.find_cached(file_hash="abc", exam_type="reading")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["job_id"], self.job_id)

    def test_cache_hit_rejects_empty_media_file(self) -> None:
        state = self.store.read(self.job_id)
        state.update(
            {
                "status": "review",
                "stimuli": [
                    {
                        "id": "reading-101-104",
                        "assets": [{"id": "crop.webp", "page": 1}],
                    }
                ],
            }
        )
        self.store.write(self.job_id, state)
        (self.job_dir / "assets" / "crop.webp").write_bytes(b"")
        (self.job_dir / "pages" / "page-001.jpg").write_bytes(b"jpeg")

        self.assertIsNone(
            self.store.find_cached(file_hash="abc", exam_type="reading")
        )

    def test_recrop_does_not_unlink_traversal_id(self) -> None:
        Image.new("RGB", (1000, 1000), "white").save(
            self.job_dir / "pages" / "page-001.jpg"
        )
        protected = self.job_dir / "outside.txt"
        protected.write_text("keep", encoding="utf-8")
        stimulus = {
            "assets": [
                {
                    "id": "../outside.txt",
                    "page": 1,
                    "bbox": [0.1, 0.1, 0.9, 0.9],
                }
            ],
            "page_numbers": [1],
        }
        recrop_asset(
            job_id=self.job_id,
            job_dir=self.job_dir,
            stimulus=stimulus,
            asset_id="../outside.txt",
        )
        self.assertTrue(protected.is_file())


@unittest.skipUnless((ROOT / "LC.pdf").is_file(), "LC.pdf golden fixture is missing")
class ListeningGoldenTest(unittest.TestCase):
    def test_lc_pdf(self) -> None:
        job_id = str(uuid.uuid4())
        job_dir = Path(tempfile.mkdtemp())
        (job_dir / "assets").mkdir()
        (job_dir / "pages").mkdir()
        try:
            result = extract_exam(
                job_id=job_id,
                pdf_path=str(ROOT / "LC.pdf"),
                exam_type="listening",
                job_dir=job_dir,
                progress=lambda _percent, _stage: None,
            )
            questions = result["questions"]
            self.assertEqual([question["number"] for question in questions], list(range(1, 101)))
            self.assertEqual(
                Counter(question["part"] for question in questions),
                {
                    "Part 1 - Phần 1": 6,
                    "Part 2 - Phần 2": 25,
                    "Part 3 - Phần 3": 39,
                    "Part 4 - Phần 4": 30,
                },
            )
            self.assertEqual(len(result["stimuli"]), 11)
            for question in questions:
                if question["number"] >= 32:
                    self.assertTrue(question["text"], question["number"])
                    self.assertTrue(
                        all(question["options"].get(letter) for letter in "ABCD"),
                        question["number"],
                    )
                    self.assertNotIn("question_missing", question["issues"])
                    self.assertNotIn("options_missing", question["issues"])
            mappings = {
                tuple(stimulus["question_numbers"]) for stimulus in result["stimuli"]
            }
            for expected in (
                (62, 63, 64),
                (65, 66, 67),
                (68, 69, 70),
                (95, 96, 97),
                (98, 99, 100),
            ):
                self.assertIn(expected, mappings)
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)


@unittest.skipUnless(
    os.getenv("RUN_GOLDEN_TES") == "1",
    "Set RUN_GOLDEN_TES=1 to run the ~50 second OCR golden test.",
)
class ReadingGoldenTest(unittest.TestCase):
    def test_tes_pdf(self) -> None:
        job_id = str(uuid.uuid4())
        job_dir = Path(tempfile.mkdtemp())
        (job_dir / "assets").mkdir()
        (job_dir / "pages").mkdir()
        try:
            result = extract_exam(
                job_id=job_id,
                pdf_path=str(ROOT / "TES.pdf"),
                exam_type="reading",
                job_dir=job_dir,
                progress=lambda _percent, _stage: None,
            )
            questions = result["questions"]
            self.assertEqual(
                [question["number"] for question in questions], list(range(101, 172))
            )
            self.assertNotIn(452, [question["number"] for question in questions])
            for question in questions:
                if not 131 <= question["number"] <= 146:
                    self.assertTrue(question["text"], question["number"])
                self.assertTrue(
                    all(question["options"].get(letter) for letter in "ABCD"),
                    question["number"],
                )
                self.assertNotIn("question_missing", question["issues"])
            self.assertEqual(
                Counter(question["part"] for question in questions),
                {
                    "Part 5 - Phần 5": 30,
                    "Part 6 - Phần 6": 16,
                    "Part 7 - Phần 7": 25,
                },
            )
            self.assertEqual(len(result["stimuli"]), 13)
            mappings = [
                stimulus["question_numbers"]
                for stimulus in result["stimuli"]
                if stimulus["question_numbers"][0] >= 147
            ]
            self.assertEqual(
                mappings,
                [
                    [147, 148],
                    [149, 150],
                    [151, 152],
                    [153, 154],
                    [155, 156, 157],
                    [158, 159, 160],
                    [161, 162, 163],
                    [164, 165, 166, 167],
                    [168, 169, 170, 171],
                ],
            )
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)


@unittest.skipUnless(
    os.getenv("RUN_GOLDEN_RC") == "1",
    "Set RUN_GOLDEN_RC=1 to run the full 100-question Reading golden test.",
)
class FullReadingGoldenTest(unittest.TestCase):
    def test_rc_pdf(self) -> None:
        job_id = str(uuid.uuid4())
        job_dir = Path(tempfile.mkdtemp())
        (job_dir / "assets").mkdir()
        (job_dir / "pages").mkdir()
        try:
            result = extract_exam(
                job_id=job_id,
                pdf_path=str(ROOT / "RC.pdf"),
                exam_type="reading",
                job_dir=job_dir,
                progress=lambda _percent, _stage: None,
            )
            questions = result["questions"]
            self.assertEqual(
                [question["number"] for question in questions],
                list(range(101, 201)),
            )
            self.assertEqual(
                Counter(question["part"] for question in questions),
                {
                    "Part 5 - Phần 5": 30,
                    "Part 6 - Phần 6": 16,
                    "Part 7 - Phần 7": 54,
                },
            )
            for question in questions[:30]:
                self.assertTrue(
                    all(question["options"].get(letter) for letter in "ABCD"),
                    question["number"],
                )
            self.assertEqual(questions[0]["options"]["A"], "regional")
            self.assertEqual(questions[3]["options"]["D"], "through")
            by_number = {question["number"]: question for question in questions}
            self.assertEqual(
                by_number[173]["text"],
                "At 8:59 a.m., what does Ms. Randolph most likely mean when "
                "she writes, “Not at all”?",
            )
            self.assertEqual(
                by_number[173]["options"]["A"],
                "She would like to participate in an interview.",
            )
            self.assertEqual(
                by_number[174]["options"]["A"],
                "He has never been on a job interview before.",
            )
            self.assertEqual(
                by_number[179]["options"]["A"], "She is a professional writer."
            )
            self.assertEqual(
                by_number[184]["text"],
                "What does the review suggest about Ms. Bouton?",
            )
            self.assertEqual(by_number[198]["options"]["D"], "$15")
            self.assertEqual(
                by_number[200]["options"]["D"],
                "They are equipped with life jackets.",
            )

            stimuli = {
                tuple(stimulus["question_numbers"]): stimulus
                for stimulus in result["stimuli"]
            }
            expected = {
                (172, 173, 174, 175): (1, [17]),
                (176, 177, 178, 179, 180): (2, [19, 19]),
                (181, 182, 183, 184, 185): (2, [21, 21]),
                (186, 187, 188, 189, 190): (3, [23, 23, 24]),
                (191, 192, 193, 194, 195): (3, [25, 25, 26]),
                (196, 197, 198, 199, 200): (3, [27, 27, 28]),
            }
            for numbers, (asset_count, pages) in expected.items():
                stimulus = stimuli[numbers]
                self.assertEqual(len(stimulus["assets"]), asset_count)
                self.assertEqual(
                    [asset["page"] for asset in stimulus["assets"]], pages
                )
                self.assertFalse(stimulus["issues"])
            self.assertEqual(
                stimuli[(186, 187, 188, 189, 190)]["title"],
                "Questions 186-190 refer to the following e-mails and notice.",
            )
            self.assertEqual(
                stimuli[(143, 144, 145, 146)]["title"],
                "Questions 143-146 refer to the following e-mail.",
            )
            # The fourth Part 6 document includes numbered blanks inside the
            # passage; its crop must extend beyond question number 143.
            self.assertGreater(
                stimuli[(143, 144, 145, 146)]["assets"][0]["bbox"][3], 0.5
            )
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)


@unittest.skipUnless(
    os.getenv("RUN_TEST1_RC_GOLDEN") == "1"
    and (ROOT / "TEST 1 RC .pdf").is_file(),
    "Set RUN_TEST1_RC_GOLDEN=1 to run the TEST 1 RC scan golden test.",
)
class Test1ReadingGoldenTest(unittest.TestCase):
    """Regression coverage for the scanned, non-flat TEST 1 Reading PDF."""

    def test_test1_rc_pdf(self) -> None:
        job_id = str(uuid.uuid4())
        job_dir = Path(tempfile.mkdtemp())
        (job_dir / "assets").mkdir()
        (job_dir / "pages").mkdir()
        try:
            result = extract_exam(
                job_id=job_id,
                pdf_path=str(ROOT / "TEST 1 RC .pdf"),
                exam_type="reading",
                job_dir=job_dir,
                progress=lambda _percent, _stage: None,
            )
            questions = result["questions"]
            self.assertEqual(
                [question["number"] for question in questions],
                list(range(101, 201)),
            )
            self.assertEqual(
                Counter(question["part"] for question in questions),
                {
                    "Part 5 - Phần 5": 30,
                    "Part 6 - Phần 6": 16,
                    "Part 7 - Phần 7": 54,
                },
            )
            for question in questions:
                self.assertNotIn("question_missing", question["issues"])
                self.assertNotIn("options_missing", question["issues"])
                self.assertTrue(
                    all(question["options"].get(letter) for letter in "ABCD"),
                    question["number"],
                )
                if not 131 <= question["number"] <= 146:
                    self.assertTrue(question["text"], question["number"])

            stimuli = {
                tuple(stimulus["question_numbers"]): stimulus
                for stimulus in result["stimuli"]
            }
            expected = {
                # The upload is already cover-trimmed, so keep the physical
                # source page containing the passage/header (page 17).
                (172, 173, 174, 175): (1, [17]),
                (176, 177, 178, 179, 180): (2, [19, 19]),
                (181, 182, 183, 184, 185): (2, [21, 21]),
                (186, 187, 188, 189, 190): (3, [23, 23, 24]),
                (191, 192, 193, 194, 195): (3, [25, 25, 26]),
                (196, 197, 198, 199, 200): (3, [27, 27, 28]),
            }
            for numbers, (asset_count, pages) in expected.items():
                self.assertIn(numbers, stimuli)
                stimulus = stimuli[numbers]
                self.assertEqual(len(stimulus["assets"]), asset_count)
                self.assertEqual(
                    [asset["page"] for asset in stimulus["assets"]], pages
                )
                self.assertNotIn("crop_review", stimulus["issues"])
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)


@unittest.skipUnless(
    os.getenv("RUN_ETS_2022_GOLDEN") == "1",
    "Set RUN_ETS_2022_GOLDEN=1 to run the ETS 2022 Test 2 regression.",
)
class Ets2022Test2GoldenTest(unittest.TestCase):
    def test_listening_photos_fit_the_image_frame(self) -> None:
        job_id = str(uuid.uuid4())
        job_dir = Path(tempfile.mkdtemp())
        (job_dir / "assets").mkdir()
        (job_dir / "pages").mkdir()
        try:
            result = extract_exam(
                job_id=job_id,
                pdf_path=str(ROOT / "ETS 2022 LC TEST 2.pdf"),
                exam_type="listening",
                job_dir=job_dir,
                progress=lambda _percent, _stage: None,
            )
            self.assertEqual(
                [question["number"] for question in result["questions"]],
                list(range(1, 101)),
            )
            photos = [
                stimulus
                for stimulus in result["stimuli"]
                if stimulus["id"].startswith("listening-photo-")
            ]
            self.assertEqual(len(photos), 6)
            for photo in photos:
                bbox = photo["assets"][0]["bbox"]
                self.assertGreater(bbox[0], 0.08)
                self.assertLess(bbox[2], 0.92)
                self.assertFalse(photo["issues"])
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    def test_reading_has_complete_questions_and_clean_crop_boundaries(self) -> None:
        job_id = str(uuid.uuid4())
        job_dir = Path(tempfile.mkdtemp())
        (job_dir / "assets").mkdir()
        (job_dir / "pages").mkdir()
        try:
            result = extract_exam(
                job_id=job_id,
                pdf_path=str(ROOT / "ETS 2022 RC TEST 2.pdf"),
                exam_type="reading",
                job_dir=job_dir,
                progress=lambda _percent, _stage: None,
            )
            self.assertEqual(
                [question["number"] for question in result["questions"]],
                list(range(101, 201)),
            )
            for question in result["questions"]:
                self.assertNotIn("question_missing", question["issues"])
                self.assertNotIn("options_missing", question["issues"])
                if not 131 <= question["number"] <= 146:
                    self.assertTrue(question["text"], question["number"])
                self.assertTrue(
                    all(question["options"].get(letter) for letter in "ABCD"),
                    question["number"],
                )
            stimuli = {
                tuple(stimulus["question_numbers"]): stimulus
                for stimulus in result["stimuli"]
            }
            expected = {
                (172, 173, 174, 175): (1, [17]),
                (176, 177, 178, 179, 180): (2, [19, 19]),
                (181, 182, 183, 184, 185): (2, [21, 21]),
                (186, 187, 188, 189, 190): (3, [23, 23, 24]),
                (191, 192, 193, 194, 195): (3, [25, 25, 26]),
                (196, 197, 198, 199, 200): (3, [27, 27, 28]),
            }
            for numbers, (asset_count, pages) in expected.items():
                stimulus = stimuli[numbers]
                self.assertEqual(len(stimulus["assets"]), asset_count)
                self.assertEqual(
                    [asset["page"] for asset in stimulus["assets"]],
                    pages,
                )
                self.assertNotIn("crop_review", stimulus["issues"])
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
