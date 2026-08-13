"""Legacy text extraction helper backed by the shared local Tesseract adapter.

Hỗ trợ cả 2 dạng PDF:
- PDF có text: dùng pdfplumber
- PDF dạng ảnh scan: render từng trang thành ảnh rồi dùng Tesseract OCR
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pdfplumber

import re

logger = logging.getLogger(__name__)

OCR_LANG = "eng"


def _clean_watermarks(text: str) -> str:
    """Lọc bỏ watermark và header/footer rác trong tài liệu TOEIC/PDF scan."""
    lines = []
    for line in text.splitlines():
        l_strip = line.strip()
        # Lọc các dòng watermark, hướng dẫn TOEIC bị cắt nửa, footer rác
        if re.search(
            r"(?:Hien Nhung|Teach By Heart|GO ON TO THE NEXT PAGE|TEST\s*\d+|PART\s*\d+|Directions:|Four answer choices|Select the best answer|your answer sheet|such as magazine|is missing in parts|mark the letter|texts is followed by|exts, such as|A\), \(B\), \(C\))",
            l_strip,
            re.IGNORECASE,
        ):
            continue

        # Xóa các dòng rác cực ngắn chỉ có 1-2 ký tự đặc biệt hoặc chữ vô nghĩa (nhưng giữ lại nếu là đáp án)
        if len(l_strip) <= 2 and l_strip.lower() in ["a", "ee", "py", "|", "="]:
            continue
        lines.append(line.replace('\x0c', ''))
    return "\n".join(lines)


def _extract_with_pdfplumber(pdf_path: str) -> tuple[str, str]:
    """Trích xuất text theo 2 cột từ PDF có text layer."""
    split_chunks = []
    full_chunks = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            w, h = page.width, page.height
            # Đọc cột trái trước, cột phải sau
            left_t = page.crop((0, 0, w * 0.50, h)).extract_text() or ""
            right_t = page.crop((w * 0.50, 0, w, h)).extract_text() or ""
            full_t = page.extract_text() or ""

            page_split = f"{left_t}\n---COLUMN_BREAK---\n{right_t}".strip()
            if not page_split or len(page_split) < 20:
                page_split = full_t

            if page_split.strip():
                split_chunks.append(_clean_watermarks(page_split))
            if full_t.strip():
                full_chunks.append(_clean_watermarks(full_t))

    return "\n---PAGE_BREAK---\n".join(split_chunks), "\n---PAGE_BREAK---\n".join(full_chunks)


def _extract_with_ocr(pdf_path: str) -> tuple[str, str]:
    """OCR theo 2 cột cho PDF dạng scan."""
    try:
        from pdf2image import convert_from_path
        from rapid_ocr import recognize_text
    except ImportError as exc:
        raise RuntimeError(
            "Thiếu thư viện OCR. Cài dependency pytesseract, Tesseract và pdf2image."
        ) from exc

    images = convert_from_path(pdf_path, dpi=200)
    split_chunks = []
    full_chunks = []
    for idx, image in enumerate(images, start=1):
        logger.info("OCR trang %s/%s", idx, len(images))
        w, h = image.size
        left_img = image.crop((0, 0, int(w * 0.50), h))
        right_img = image.crop((int(w * 0.50), 0, w, h))

        t_left = recognize_text(left_img)
        t_right = recognize_text(right_img)
        t_full = recognize_text(image)
        left_img.close()
        right_img.close()
        image.close()

        page_split = f"{t_left}\n---COLUMN_BREAK---\n{t_right}".strip()
        if page_split:
            split_chunks.append(_clean_watermarks(page_split))

        page_full = t_full.strip()
        if page_full:
            full_chunks.append(_clean_watermarks(page_full))

    return "\n---PAGE_BREAK---\n".join(split_chunks), "\n---PAGE_BREAK---\n".join(full_chunks)


def extract_text(pdf_path: str, force_ocr: bool = False) -> tuple[str, str, str]:
    """Trả về (text_split, text_full, mode)"""
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy file: {pdf_path}")

    if not force_ocr:
        try:
            text_split, text_full = _extract_with_pdfplumber(str(path))
            if len(text_split.strip()) >= 30:
                logger.info("Trích xuất text bằng pdfplumber (%s ký tự)", len(text_split))
                return text_split, text_full, "pdfplumber"
        except Exception as exc:
            logger.warning("pdfplumber thất bại: %s. Chuyển sang OCR.", exc)

    logger.info("Chuyển sang OCR...")
    text_split, text_full = _extract_with_ocr(str(path))
    return text_split, text_full, "ocr"
