from __future__ import annotations

import unittest

from pydantic import ValidationError

from client_extraction_api import (
    ClientExtractionCreate,
    ClientExtractionManifestV1,
    _canonical_hash,
    _validate_manifest_structure,
)
from schemas import Question, SolutionEntry


SOURCE_HASH = "a" * 64


def valid_manifest() -> ClientExtractionManifestV1:
    return ClientExtractionManifestV1(
        schema_version=1,
        pipeline_version="client-tesseract-v1",
        source_sha256=SOURCE_HASH,
        source_filename="RC.pdf",
        source_size=1024,
        page_count=1,
        exam_type="reading",
        requested_count=1,
        questions=[
            Question(
                number=101,
                part="Part 5 - Phần 5",
                text="The report is ready.",
                options={"A": "at", "B": "by", "C": "for", "D": "to"},
                option_letters=["A", "B", "C", "D"],
                correct="B",
                issues=[],
            )
        ],
        stimuli=[],
        assets=[],
        media=[],
        issues=[],
        answer_key={"101": "B"},
        metadata={"ingest_mode": "client_ocr"},
    )


class ClientExtractionContractTests(unittest.TestCase):
    def test_create_requires_exactly_one_source_pdf(self) -> None:
        with self.assertRaises(ValidationError):
            ClientExtractionCreate(
                client_request_id="d937181b-b726-4c94-8438-7035b62f42d1",
                component="reading",
                source_sha256=SOURCE_HASH,
                uploads=[],
            )

    def test_manifest_hash_is_canonical_and_structure_is_complete(self) -> None:
        manifest = valid_manifest()
        _validate_manifest_structure(manifest)
        first = _canonical_hash(manifest)
        second = _canonical_hash(
            ClientExtractionManifestV1.model_validate(manifest.model_dump(mode="json"))
        )
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_missing_option_is_rejected_before_persist(self) -> None:
        manifest = valid_manifest()
        manifest.questions[0].options["D"] = ""
        with self.assertRaisesRegex(Exception, "thiếu phương án"):
            _validate_manifest_structure(manifest)

    def test_shortened_option_letter_list_cannot_bypass_required_options(self) -> None:
        manifest = valid_manifest()
        manifest.questions[0].option_letters = ["A", "B"]
        with self.assertRaisesRegex(Exception, "option letter chuẩn"):
            _validate_manifest_structure(manifest)

    def test_unresolved_ocr_error_is_rejected(self) -> None:
        manifest = valid_manifest()
        manifest.questions[0].issues = ["manual_review"]
        with self.assertRaisesRegex(Exception, "issue chưa review"):
            _validate_manifest_structure(manifest)

    def test_local_solution_rows_are_part_of_the_commit_manifest(self) -> None:
        manifest = valid_manifest()
        manifest.solutions = [
            SolutionEntry(
                key="q-101",
                question_numbers=[101],
                transcript=None,
                explanation="Choose the preposition that completes the sentence.",
                translation="Chọn giới từ phù hợp.",
            )
        ]
        _validate_manifest_structure(manifest)
        self.assertEqual(manifest.model_dump(mode="json")["solutions"][0]["key"], "q-101")


if __name__ == "__main__":
    unittest.main()
