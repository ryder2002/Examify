from pathlib import Path

import pytest

import solution_importer
from exam_solutions import (
    SolutionValidationError,
    allowed_solution_groups,
    merge_solutions,
    parse_solution_number,
    validate_solutions,
)
from solution_importer import parse_solution_file


ROOT = Path(__file__).resolve().parent.parent


def _entry(numbers: list[int], *, text: str = "Nội dung") -> dict:
    return {
        "question_numbers": numbers,
        "transcript": text,
        "explanation": None,
        "translation": "Bản dịch",
    }


def test_toeic_solution_groups_are_exact() -> None:
    listening = allowed_solution_groups("listening")
    assert listening[:2] == [[1], [2]]
    assert [31] in listening
    assert [32, 33, 34] in listening
    assert [68, 69, 70] in listening
    assert [71, 72, 73] in listening
    assert listening[-1] == [98, 99, 100]
    reading = allowed_solution_groups("reading")
    assert reading[0] == [101]
    assert reading[-1] == [200]
    assert len(reading) == 100


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("32-34", [32, 33, 34]),
        ("71–73", [71, 72, 73]),
        ("q-101", [101]),
        ("1 0 1", [101]),
        ("1\n0\n1", [101]),
        ("Câu 101", [101]),
        ("Question 101.", [101]),
    ],
)
def test_parse_singleton_and_dash_variants(raw: str, expected: list[int]) -> None:
    assert parse_solution_number(raw) == expected


@pytest.mark.parametrize("numbers", [[32], [31, 32], [33, 34, 35], [99, 100, 101]])
def test_rejects_wrong_part_ranges(numbers: list[int]) -> None:
    with pytest.raises(SolutionValidationError):
        validate_solutions([_entry(numbers)], "listening")


def test_rejects_duplicate_or_overlapping_questions() -> None:
    with pytest.raises(SolutionValidationError) as error:
        validate_solutions([_entry([32, 33, 34]), _entry([32, 33, 34])], "listening")
    assert any(issue["code"] == "overlap" for issue in error.value.issues)


def test_merge_replaces_matching_key_and_preserves_other_entries() -> None:
    current = [_entry([1], text="cũ"), _entry([2], text="giữ lại")]
    incoming = [_entry([1], text="mới")]
    merged = merge_solutions(current, incoming, "listening")
    assert [entry["key"] for entry in merged] == ["q-1", "q-2"]
    assert merged[0]["transcript"] == "mới"
    assert merged[1]["transcript"] == "giữ lại"


@pytest.mark.parametrize(
    ("filename", "exam_type", "expected_keys"),
    [
        ("Listening_Sample.docx", "listening", {"q-1", "q-32-34"}),
        ("Reading_Sample.docx", "reading", {"q-101", "q-131"}),
    ],
)
def test_provided_docx_samples_parse_into_preview(
    filename: str, exam_type: str, expected_keys: set[str]
) -> None:
    preview = parse_solution_file(ROOT / "Giai_Chi_Tiet" / filename, exam_type)
    assert preview["requires_preview"] is True
    assert preview["issues"] == []
    assert {entry["key"] for entry in preview["entries"]} == expected_keys


def test_doc_conversion_branch_uses_safe_preview(monkeypatch, tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.doc"
    legacy.write_bytes(b"legacy-word-fixture")
    monkeypatch.setattr(
        solution_importer,
        "_convert_doc",
        lambda _source, _destination: ROOT / "Giai_Chi_Tiet" / "Reading_Sample.docx",
    )
    preview = parse_solution_file(legacy, "reading")
    assert preview["mode"] == "doc_via_libreoffice"
    assert {entry["key"] for entry in preview["entries"]} == {"q-101", "q-131"}


def test_pdf_text_repeated_headers_multiline_and_partial_errors(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "solution-text.pdf"
    source.write_bytes(b"pdf-text-fixture")
    rows = [
        ["STT", "Transcript", "Dịch"],
        ["32–34", "First line", "Dòng một"],
        ["", "continued", "tiếp tục"],
        ["STT", "Transcript", "Dịch"],
        ["32-34", "duplicate", "trùng"],
        ["69-71", "wrong group", "sai range"],
        ["71-73", "Valid second group", "Nhóm hợp lệ"],
    ]
    monkeypatch.setattr(
        solution_importer,
        "_pdf_rows",
        lambda _path, _exam_type: (rows, "pdf_text", None),
    )
    preview = parse_solution_file(source, "listening")
    assert [entry["key"] for entry in preview["entries"]] == ["q-32-34", "q-71-73"]
    assert preview["entries"][0]["transcript"] == "First line\ncontinued"
    assert {issue["code"] for issue in preview["issues"]} == {
        "invalid_range",
        "repeated_rows_consolidated",
    }
    assert "q-1" in preview["missing_keys"]


def test_pdf_scan_preview_exposes_ocr_confidence(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "solution-scan.pdf"
    source.write_bytes(b"pdf-scan-fixture")
    monkeypatch.setattr(
        solution_importer,
        "_pdf_rows",
        lambda _path, _exam_type: (
            [["STT", "Nội dung đề", "Dịch"], ["101", "Giải thích", "Bản dịch"]],
            "pdf_scan_ocr",
            0.55,
        ),
    )
    preview = parse_solution_file(source, "reading")
    assert preview["mode"] == "pdf_scan_ocr"
    assert preview["ocr_confidence"] == 0.55
    assert preview["entries"][0]["key"] == "q-101"


def test_vertical_header_and_repeated_pdf_rows_are_consolidated(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "vertical.pdf"
    source.write_bytes(b"fixture")
    rows = [
        ["S\nT\nT", "Giải chi tiết", "Dịch"],
        ["1\n0\n1", "Chọn (B).", "(A) Một\n(B) Hai"],
        ["1 0 1", "Chọn (B). Giải thích đầy đủ hơn.", "(A) Một\n(B) Hai\n(C) Ba\n(D) Bốn"],
    ]
    monkeypatch.setattr(
        solution_importer,
        "_pdf_rows",
        lambda _path, _exam_type: (rows, "pdf_text", None),
    )
    preview = parse_solution_file(source, "reading")
    assert preview["valid_count"] == 1
    assert preview["entries"][0]["explanation"].endswith("đầy đủ hơn.")
    assert [issue["code"] for issue in preview["issues"]] == [
        "repeated_rows_consolidated"
    ]


def test_component_mismatch_is_reported_once(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "reading-as-listening.pdf"
    source.write_bytes(b"fixture")
    rows = [
        ["STT", "Giải chi tiết", "Dịch"],
        ["101", "Chọn (A).", "Bản dịch"],
        ["102", "Chọn (B).", "Bản dịch"],
    ]
    monkeypatch.setattr(
        solution_importer,
        "_pdf_rows",
        lambda _path, _exam_type: (rows, "pdf_text", None),
    )
    preview = parse_solution_file(source, "listening")
    assert preview["valid_count"] == 0
    assert [issue["code"] for issue in preview["issues"]] == [
        "exam_type_mismatch"
    ]


def test_full_listening_contract_accepts_vertical_quartz_groups(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "full-script-listening.pdf"
    source.write_bytes(b"fixture")
    rows: list[list[str]] = [["S\nT\nT", "Transcript", "Dịch nghĩa tiếng Việt"]]
    for numbers in allowed_solution_groups("listening"):
        stt = (
            "-".join(str(number) for number in (numbers[0], numbers[-1]))
            if len(numbers) > 1
            else str(numbers[0])
        )
        vertical_stt = "\n".join(stt)
        row = [vertical_stt, f"Transcript {stt}.", f"Bản dịch {stt}."]
        rows.extend([row, row.copy()])
    monkeypatch.setattr(
        solution_importer,
        "_pdf_rows",
        lambda _path, _exam_type: (rows, "pdf_text", None),
    )

    preview = parse_solution_file(source, "listening")

    assert preview["valid_count"] == 54
    assert preview["mapped_question_count"] == 100
    assert preview["missing_keys"] == []
    assert preview["issues"] == []
    assert [entry["question_numbers"] for entry in preview["entries"]] == (
        allowed_solution_groups("listening")
    )
