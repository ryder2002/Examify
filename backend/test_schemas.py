"""Coverage rules for OCR-missing manual question slots."""

from __future__ import annotations

import unittest

from schemas import (
    AudioRef,
    ExamDraft,
    Question,
    ensure_question_coverage,
    manual_question_placeholder,
    normalize_question_issues,
)


class ManualQuestionCoverageTests(unittest.TestCase):
    def test_exam_draft_exposes_separate_audio_phase_progress(self) -> None:
        draft = ExamDraft(
            job_id="job",
            exam_type="listening",
            status="processing",
            stage="Đang cắt audio 12/55",
            progress=8,
            processing_phase="audio",
            phase_progress=22,
            filename="LC.pdf",
        )
        self.assertEqual(draft.processing_phase, "audio")
        self.assertEqual(draft.phase_progress, 22)

        parallel = draft.model_copy(
            update={
                "processing_phase": "audio_ocr",
                "audio_progress": 40,
                "ocr_progress": 65,
            }
        )
        self.assertEqual(parallel.processing_phase, "audio_ocr")
        self.assertEqual(parallel.audio_progress, 40)
        self.assertEqual(parallel.ocr_progress, 65)

    def test_part_one_direction_audio_is_a_valid_manifest_asset(self) -> None:
        audio = AudioRef(
            id="directions.mp3",
            url="/audio/directions.mp3",
            filename="directions.mp3",
            content_type="audio/mpeg",
            size=128,
            part="directions_part_1",
            scope="part",
        )
        self.assertEqual(audio.part, "directions_part_1")

    def test_reading_missing_numbers_become_manual_placeholders(self) -> None:
        questions, inserted = ensure_question_coverage(
            "reading",
            [
                Question(
                    number=101,
                    part="Part 5 - Phần 5",
                    text="Question",
                    options={"A": "a", "B": "b", "C": "c", "D": "d"},
                    option_letters=["A", "B", "C", "D"],
                ),
                Question(
                    number=200,
                    part="Part 7 - Phần 7",
                    text="Question",
                    options={"A": "a", "B": "b", "C": "c", "D": "d"},
                    option_letters=["A", "B", "C", "D"],
                ),
            ],
        )
        by_number = {question.number: question for question in questions}
        self.assertEqual(inserted[0], 102)
        self.assertEqual(inserted[-1], 199)
        self.assertEqual(len(questions), 100)
        self.assertEqual(by_number[102].part, "Part 5 - Phần 5")
        self.assertEqual(by_number[192].issues, ["question_missing", "options_missing"])
        self.assertEqual(by_number[192].options, {"A": "", "B": "", "C": "", "D": ""})

    def test_part_six_can_be_completed_without_question_text(self) -> None:
        question = manual_question_placeholder("reading", 138)
        question.options = {"A": "a", "B": "b", "C": "c", "D": "d"}
        normalized = normalize_question_issues("reading", question)
        self.assertNotIn("question_missing", normalized.issues)
        self.assertNotIn("options_missing", normalized.issues)

    def test_listening_part_two_placeholder_keeps_three_options(self) -> None:
        question = manual_question_placeholder("listening", 12)
        self.assertEqual(question.option_letters, ["A", "B", "C"])
        self.assertEqual(question.part, "Part 2 - Phần 2")

    def test_partial_listening_range_does_not_recreate_questions_before_part_two(self) -> None:
        questions, inserted = ensure_question_coverage(
            "listening",
            [
                manual_question_placeholder("listening", 7),
                manual_question_placeholder("listening", 31),
            ],
            (7, 31),
        )
        self.assertEqual(len(questions), 25)
        self.assertEqual(inserted[0], 8)
        self.assertEqual(inserted[-1], 30)
        self.assertEqual([question.number for question in questions], list(range(7, 32)))

    def test_partial_reading_range_does_not_recreate_full_test(self) -> None:
        questions, inserted = ensure_question_coverage(
            "reading",
            [manual_question_placeholder("reading", 101)],
            (101, 130),
        )
        self.assertEqual(len(questions), 30)
        self.assertEqual(inserted[-1], 130)
        self.assertNotIn(131, {question.number for question in questions})


if __name__ == "__main__":
    unittest.main()
