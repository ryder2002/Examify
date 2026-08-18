"""Parse Teacher-provided DOCX/DOC/PDF solution tables into safe previews."""

from __future__ import annotations

import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import tempfile
import unicodedata
from typing import Any

import pdfplumber
from docx import Document

from exam_solutions import (
    SolutionValidationError,
    allowed_solution_groups,
    parse_solution_number,
    solution_key,
    validate_solutions,
)


def _header(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").replace("Đ", "D").replace("đ", "d"))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(re.sub(r"[^a-zA-Z0-9]+", " ", text).casefold().split())


def _is_header(row: list[str], _exam_type: str) -> bool:
    if len(row) < 3:
        return False
    # Remaining header names vary between Transcript, Nội dung đề, Giải thích
    # and Giải chi tiết. STT is the stable contract and cannot be a question.
    return _header(row[0]).replace(" ", "") == "stt"


def _docx_rows(path: Path) -> list[list[str]]:
    document = Document(str(path))
    rows: list[list[str]] = []
    for table in document.tables:
        for row in table.rows:
            rows.append([cell.text.replace("\r", "\n").strip() for cell in row.cells])
    return rows


def _row_solution_numbers(rows: list[list[str]]) -> set[int]:
    numbers: set[int] = set()
    for row in rows:
        if not row:
            continue
        try:
            numbers.update(parse_solution_number(row[0]))
        except ValueError:
            continue
    return numbers


def _pdf_rows(
    path: Path, exam_type: str
) -> tuple[list[list[str]], str, float | None]:
    rows: list[list[str]] = []
    text_pages: list[str] = []
    page_count = 0
    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)
        if page_count > 100:
            raise ValueError("PDF lời giải vượt quá 100 trang")
        expected = set(
            range(1, 101) if exam_type == "listening" else range(101, 201)
        )
        opposite = set(
            range(101, 201) if exam_type == "listening" else range(1, 101)
        )
        coverage: set[int] = set()
        lookahead = 0
        for page in pdf.pages:
            page_rows = [
                [str(cell or "").strip() for cell in row]
                for table in (page.extract_tables() or [])
                for row in table
                if row
            ]
            if page_rows:
                rows.extend(page_rows)
                coverage.update(_row_solution_numbers(page_rows))
                # Stop after one look-ahead page for both the requested range
                # and a clearly wrong component. This keeps a mistaken
                # Listening/Reading selection from scanning a repeated Quartz
                # table through every remaining PDF page.
                if expected.issubset(coverage) or opposite.issubset(coverage):
                    if lookahead >= 1:
                        break
                    lookahead += 1
                continue
            extracted_text = page.extract_text(layout=True) or ""
            if extracted_text.strip():
                text_pages.append(extracted_text)
    if rows:
        return rows, "pdf_text", None

    # Text PDFs without ruled table borders still retain column spacing when
    # extracted with layout=True. Parse those rows before considering OCR.
    for page_text in text_pages:
        for raw_line in page_text.splitlines():
            if not raw_line.strip():
                continue
            columns = [
                value.strip()
                for value in re.split(r"\t|\s{2,}", raw_line.strip())
                if value.strip()
            ]
            if len(columns) >= 3:
                rows.append([columns[0], columns[1], " ".join(columns[2:])])
    if text_pages:
        return rows, "pdf_text", None

    # Scanned solution PDFs are recognized in the browser and sent through
    # /solution-imports/validate. Never fall back to server OCR here.
    raise ValueError(
        "PDF lời giải là bản scan. Hãy dùng OCR cục bộ trong trình duyệt rồi xác nhận bản xem trước."
    )


def _limit_process() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
    resource.setrlimit(resource.RLIMIT_AS, (1_024 * 1024 * 1024, 1_024 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))


def _convert_doc(path: Path, destination: Path) -> Path:
    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if not executable:
        raise RuntimeError("Worker chưa cài LibreOffice để chuyển file .doc")
    with tempfile.TemporaryDirectory(prefix="solution-libreoffice-") as profile:
        command = [
            executable,
            "--headless",
            "--safe-mode",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--norestore",
            f"-env:UserInstallation=file://{profile}",
            "--convert-to",
            "docx",
            "--outdir",
            str(destination),
            str(path),
        ]
        subprocess.run(
            command,
            check=True,
            timeout=60,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": os.environ.get("PATH", ""), "HOME": profile},
            preexec_fn=_limit_process if os.name == "posix" else None,
        )
    converted = destination / f"{path.stem}.docx"
    if not converted.is_file():
        raise RuntimeError("LibreOffice không tạo được file DOCX")
    return converted


def _coalesce_rows(rows: list[list[str]], exam_type: str) -> list[list[str]]:
    result: list[list[str]] = []
    for raw in rows:
        row = (raw + ["", "", ""])[:3]
        row = [str(value or "").strip() for value in row]
        if not any(row) or _is_header(row, exam_type):
            continue
        if not row[0] and result:
            # Word/PDF may split a multiline table cell into continuation rows.
            for index in (1, 2):
                if row[index]:
                    result[-1][index] = "\n".join(
                        value for value in (result[-1][index], row[index]) if value
                    )
            continue
        result.append(row)
    return result


def _candidate_fingerprint(candidate: dict[str, Any]) -> tuple[str, str, str]:
    return tuple(
        re.sub(r"\s+", " ", str(candidate.get(field) or "")).strip()
        for field in ("transcript", "explanation", "translation")
    )


def _candidate_quality(candidate: dict[str, Any]) -> tuple[int, int, int, int]:
    content = str(candidate.get("transcript") or candidate.get("explanation") or "")
    translation = str(candidate.get("translation") or "")
    option_markers = len(
        set(re.findall(r"(?:^|\n)\s*[\(\[]?([A-D])[\)\].:]", translation, re.I))
    )
    answer_evidence = int(
        bool(
            re.search(r"(?:chọn|đáp\s*án)\s*[\(\[]?[A-D]", content, re.I)
            or re.match(r"\s*[A-D](?:\s|$)", content)
        )
    )
    complete_ending = int(bool(re.search(r"[.!?。\)\]”’]\s*$", content)))
    return option_markers, answer_evidence, complete_ending, len(content) + len(translation)


def _tail_looks_truncated(entry: dict[str, Any]) -> bool:
    content = str(entry.get("transcript") or entry.get("explanation") or "").strip()
    return bool(content) and re.search(r"[.!?。\)\]”’]\s*$", content) is None


def parse_solution_file(path: Path, exam_type: str) -> dict[str, Any]:
    if exam_type not in {"listening", "reading"}:
        raise ValueError("Loại lời giải phải là listening hoặc reading")
    suffix = path.suffix.casefold()
    mode = suffix.lstrip(".")
    ocr_confidence: float | None = None
    if suffix == ".docx":
        rows = _docx_rows(path)
    elif suffix == ".doc":
        with tempfile.TemporaryDirectory(prefix="solution-doc-") as directory:
            rows = _docx_rows(_convert_doc(path, Path(directory)))
        mode = "doc_via_libreoffice"
    elif suffix == ".pdf":
        rows, mode, ocr_confidence = _pdf_rows(path, exam_type)
    else:
        raise ValueError("Chỉ chấp nhận .docx, .doc hoặc .pdf")

    materialized = _coalesce_rows(rows, exam_type)
    candidates_by_key: dict[str, list[dict[str, Any]]] = {}
    issues: list[dict[str, Any]] = []
    parsed_numbers: set[int] = set()
    invalid_issues: list[dict[str, Any]] = []
    for row_index, row in enumerate(materialized, start=1):
        try:
            numbers = parse_solution_number(row[0])
        except ValueError as exc:
            invalid_issues.append(
                {"row": row_index, "code": "invalid_stt", "message": str(exc), "value": row[0]}
            )
            continue
        parsed_numbers.update(numbers)
        candidate = {
            "key": solution_key(numbers),
            "question_numbers": numbers,
            "transcript": row[1] if exam_type == "listening" else None,
            "explanation": row[1] if exam_type == "reading" else None,
            "translation": row[2],
        }
        try:
            validated = validate_solutions([candidate], exam_type)[0]
        except SolutionValidationError as exc:
            for issue in exc.issues:
                issues.append({"row": row_index, **issue})
            continue
        candidates_by_key.setdefault(validated["key"], []).append(validated)

    expected_range = (
        set(range(1, 101)) if exam_type == "listening" else set(range(101, 201))
    )
    opposite_range = (
        set(range(101, 201)) if exam_type == "listening" else set(range(1, 101))
    )
    if not candidates_by_key and parsed_numbers.intersection(opposite_range):
        detected = "Reading 101–200" if exam_type == "listening" else "Listening 1–100"
        issues = [
            {
                "code": "exam_type_mismatch",
                "message": (
                    f"File chứa lời giải {detected}, không khớp phần "
                    f"{exam_type.title()} đang chỉnh sửa."
                ),
            }
        ]
    else:
        issues.extend(invalid_issues[:20])
        if len(invalid_issues) > 20:
            issues.append(
                {
                    "code": "invalid_rows_suppressed",
                    "message": f"Đã ẩn {len(invalid_issues) - 20} dòng STT không hợp lệ lặp lại.",
                }
            )

    entries: list[dict[str, Any]] = []
    repeated_rows = 0
    variant_keys = 0
    for key, key_candidates in candidates_by_key.items():
        unique = {
            _candidate_fingerprint(candidate): candidate
            for candidate in key_candidates
        }
        repeated_rows += len(key_candidates) - len(unique)
        if len(unique) > 1:
            variant_keys += 1
        entries.append(max(unique.values(), key=_candidate_quality))
    entries.sort(key=lambda entry: entry["question_numbers"][0])
    # Exact Quartz table repeats are an extraction artifact that needs no user
    # action. Surface a review warning only when the same key genuinely has
    # different content and the importer had to choose between variants.
    if variant_keys:
        issues.append(
            {
                "code": "repeated_rows_consolidated",
                "message": (
                    f"Đã gom {repeated_rows} dòng lặp và chọn bản đầy đủ nhất "
                    f"cho {variant_keys} số câu có nhiều biến thể."
                ),
            }
        )

    try:
        entries = validate_solutions(entries, exam_type)
    except SolutionValidationError as exc:
        issues.extend(exc.issues)
        entries = []

    seen = {
        number
        for entry in entries
        for number in entry.get("question_numbers", [])
    }
    if expected_range and max(expected_range) in seen:
        tail = next(
            entry
            for entry in entries
            if max(expected_range) in entry["question_numbers"]
        )
        if _tail_looks_truncated(tail):
            issues.append(
                {
                    "code": "source_tail_requires_review",
                    "question_numbers": tail["question_numbers"],
                    "message": (
                        "Nội dung câu cuối không kết thúc như một câu hoàn chỉnh; "
                        "hãy đối chiếu PDF nguồn trước khi áp dụng."
                    ),
                }
            )

    expected = allowed_solution_groups(exam_type)
    missing = [
        solution_key(group)
        for group in expected
        if not set(group).intersection(seen)
    ]
    return {
        "entries": entries,
        "valid_count": len(entries),
        "mapped_question_count": len(seen),
        "issues": issues,
        "missing_keys": missing,
        "mode": mode,
        "ocr_confidence": ocr_confidence,
        "requires_preview": True,
    }
