"""Layout-aware TOEIC extraction and image cropping pipeline."""

from __future__ import annotations

import logging
import math
import os
import re
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Iterable

import pdfplumber
from pdf2image import convert_from_path, pdfinfo_from_path
from pdf2image.exceptions import PDFInfoNotInstalledError
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from config import settings

from parser import _extract_options, normalize_part5_question_text
from rapid_ocr import OCRResult, recognize, runtime_status
from schemas import Issue, Question, Stimulus


def _poppler_path() -> str | None:
    return os.getenv("POPPLER_PATH") or None


def ocr_dependency_status() -> dict[str, object]:
    """Expose OCR readiness without exposing local model paths."""
    poppler_value = _poppler_path()
    poppler = Path(poppler_value) if poppler_value else None
    pdfinfo = poppler / ("pdfinfo.exe" if os.name == "nt" else "pdfinfo") if poppler else None
    pdftoppm = poppler / ("pdftoppm.exe" if os.name == "nt" else "pdftoppm") if poppler else None
    status: dict[str, object] = {
        "poppler": bool(
            (pdfinfo and pdfinfo.is_file() and pdftoppm and pdftoppm.is_file())
            or (shutil.which("pdfinfo") and shutil.which("pdftoppm"))
        ),
    }
    status.update(runtime_status())
    status["ocr_ready"] = bool(status.get("poppler") and status.get("ocr_ready"))
    return status


def _pdf_runtime_error(error: Exception) -> RuntimeError:
    return RuntimeError(
        "Không thể đọc số trang PDF vì bộ xử lý Poppler trong ứng dụng bị thiếu hoặc hỏng. "
        "Hãy cài lại bản Examify Desktop mới nhất; không cần tự cài Poppler."
    )


logger = logging.getLogger(__name__)
ProgressCallback = Callable[[int, str], None]
# The OCR worker runs one document at a time.  Up to six page pipelines can
# therefore use the six-core worker quota without multiplying concurrent PDF
# jobs and starving the API/database containers.
MAX_PAGE_WORKERS = 6
DEFAULT_PAGE_WORKERS = max(2, min(MAX_PAGE_WORKERS, (os.cpu_count() or 4) // 2))


def _page_workers(page_count: int) -> int:
    """Bound parallel OCR to the end-user CPU without oversubscribing it."""
    try:
        configured = int(os.getenv("OCR_PAGE_WORKERS", str(DEFAULT_PAGE_WORKERS)))
    except ValueError:
        configured = DEFAULT_PAGE_WORKERS
    return max(1, min(MAX_PAGE_WORKERS, page_count, configured))

try:
    import numpy as np
except ImportError:  # The Pillow-only fallback keeps development installs usable.
    np = None

try:
    import cv2  # type: ignore[import-not-found]
except ImportError:
    # Passage whitespace analysis only needs NumPy. A missing OpenCV runtime
    # must not silently disable Reading crop trimming/splitting as well.
    cv2 = None


@dataclass
class OCRToken:
    text: str
    confidence: float
    left: int
    top: int
    width: int
    height: int


@dataclass
class PageResult:
    number: int
    width: int
    height: int
    columns: list[str]
    tokens: list[OCRToken]
    confidence: float


@dataclass
class ReadingHeader:
    start: int
    end: int
    page: int
    top: float
    bottom: float
    description: str
    title: str


@dataclass(frozen=True)
class ReadingROI:
    """A normalized question/options region on a retained source page."""

    bbox: tuple[float, float, float, float]
    kind: str
    column: int | None = None


@dataclass
class ReadingPagePlan:
    """Cheap layout decision made before high-quality Reading OCR."""

    page: int
    part: str | None
    source: str
    expected_numbers: tuple[int, ...]
    question_rois: list[ReadingROI]
    passage_rois: list[tuple[float, float, float, float]]
    confidence: float
    fallback_reason: str | None = None


QUESTION_START = re.compile(
    # Scanned booklets sometimes place the question text immediately after
    # the marker (for example ``186.What``).  The marker is line-anchored so
    # allowing zero whitespace here does not turn ordinary prose into a
    # question start, and it also preserves bare Part 1 photo numbers.
    # A no-punctuation OCR marker must start with an actual digit.  Allowing
    # ``l``/``I`` here makes ordinary lines such as ``last Friday`` or
    # ``in likely`` look like question numbers and truncates the preceding
    # question before its options.  Letter substitutions remain supported by
    # the punctuation form below (``l84.``/``O97.``).
    r"(?m)^[ \t]*(?P<number>[0-9]{1,4})[ \t]*"
    r"(?:[\.\)][ \t]*|(?=[A-Za-z]))"
)
UNMARKED_OPTION_A = re.compile(r"(?m)^[ \t]*\(A\)[ \t]*")
NUMBER_TOKEN = re.compile(r"^[\(\[]?([0-9IlOo]{1,4})[\.\)\]]?$")
LEADING_OPTION_A = re.compile(
    r"^[ \t]*(?:[\(\[\{][ \t]*A[ \t]*[\)\]\}]|A[ \t]*[\.\)])[ \t]*",
    re.IGNORECASE,
)
NUMBERED_OPTION_A = re.compile(
    r"^[ \t]*(?P<number>[0-9IlOo]{3,4})[ \t]*[\.\)][ \t]*"
    r"(?:[\(\[\{][ \t]*A[ \t]*[\)\]\}]|A[ \t]*[\.\)])[ \t]*",
    re.IGNORECASE,
)
SPATIAL_MARKER_TRANSLATION = str.maketrans(
    {"（": "(", "）": ")", "［": "[", "］": "]"}
)
BOILERPLATE = re.compile(
    r"(?:"
    r"GO ON TO THE NEXT PAGE"
    r"|TEST\s*1\s*\d+"
    r"|Stop!"
    r"|This is the end of the"
    r"|[A-Za-z]{0,4}ish before time is called"
    r"|you may go (?:on )?to Part"
    r"|and check your work"
    r"|he Listening test"
    r"|Directions:"
    r")",
    re.IGNORECASE,
)
READING_GROUP_HEADER = re.compile(
    # OCR may collapse inter-word spaces on a downscaled full-page pass:
    # ``Questions168-171referto thefollowingletter``. The fixed vocabulary
    # and three-digit TOEIC range keep this permissive whitespace safe.
    r"Questions\s*(?P<start>\d{3})\s*[-–—]\s*(?P<end>\d{3})"
    r"\s*refer\s*to\s*the\s*following\s*(?P<description>[^\n|]+)",
    re.IGNORECASE,
)

# Patterns to detect cover and direction pages that should be skipped.
_COVER_PAGE = re.compile(
    r"(?:"
    r"^\s*TEST\s*0?[1-9]"
    r"|기출\s*TEST"
    r"|실전\s*TEST"
    r"|ETS\s+TOEIC"
    r"|ACTUAL\s*TEST"
    r"|PRACTICE\s*TEST"
    r"|TOEIC\s+LISTENING"
    r"|TOEIC\s+READING"
    r"|LC\s+TEST"
    r"|RC\s+TEST"
    r")",
    re.IGNORECASE | re.MULTILINE,
)
_DIRECTION_PAGE = re.compile(
    r"(?:"
    r"LISTENING\s+TEST"
    r"|READING\s+TEST"
    r"|Directions:\s+For\s+each\s+question"
    r"|Directions:\s+A\s+word\s+or\s+phrase"
    r"|In\s+the\s+Listening\s+test"
    r"|In\s+the\s+Reading\s+test"
    r"|PART\s*1\b"
    r"|PART\s*5\b"
    r")",
    re.IGNORECASE | re.MULTILINE,
)


def _render_dpi(exam_type: str | None = None) -> int:
    """Choose a resolution that balances OCR accuracy and scan throughput."""
    try:
        # Listening pages include small labels below photos and dense Part 3/4
        # question blocks. Rendering at 240 DPI and then scaling to 75% fed
        # Tesseract an effective 180-DPI image, which loses thin characters.
        default = "300"
        return max(150, min(300, int(os.getenv("OCR_RENDER_DPI", default))))
    except ValueError:
        return 300


def _full_page_ocr_scale(exam_type: str | None = None) -> float:
    """Return the scale for the first full-page OCR pass.

    Reading keeps its established 75% fast path. Listening defaults to the
    unscaled render so that small Part 3/4 text is recognized before bounded
    recovery is needed. Both values remain capped to avoid accidental memory
    exhaustion from a malformed environment variable.
    """
    variable = (
        "OCR_LISTENING_PAGE_SCALE"
        if exam_type == "listening"
        else "OCR_READING_PAGE_SCALE"
    )
    default = "1.0" if exam_type == "listening" else "0.75"
    try:
        return max(0.5, min(1.0, float(os.getenv(variable, default))))
    except ValueError:
        return float(default)


def _reading_roi_enabled() -> bool:
    """Allow a fast rollback while the ROI pipeline is being benchmarked."""
    # The two-stage mode is opt-in until a representative production machine
    # proves it beats the single-pass CPU baseline.  Targeted ROI recovery
    # below remains enabled for incomplete pages in the normal path.
    value = os.getenv("OCR_READING_ROI_ENABLED", "0").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _reading_locator_scale(render_dpi: int) -> float:
    """Return the scale for the cheap locator pass derived from render DPI."""
    try:
        locator_dpi = int(os.getenv("OCR_READING_LOCATOR_DPI", "150"))
    except ValueError:
        locator_dpi = 150
    locator_dpi = max(100, min(180, locator_dpi))
    return max(0.25, min(0.8, locator_dpi / max(1, render_dpi)))


def _detect_content_start(
    page_results: list["PageResult"],
    exam_type: str,
) -> int:
    """Return the 1-based page number where actual question content begins.

    Scans OCR/text output of early pages to find where question content begins,
    skipping standalone cover and directions pages (pages without question numbers).
    """
    if not page_results:
        return 1

    pages = sorted(page_results, key=lambda p: p.number)

    def _has_questions(text: str) -> bool:
        for match in QUESTION_START.finditer(text):
            num = _normalize_number(match.group("number"))
            if (1 <= num <= 100) if exam_type == "listening" else (101 <= num <= 200):
                return True
        return False

    def _is_dir(text: str) -> bool:
        return _DIRECTION_PAGE.search(text) is not None

    def _is_cov(text: str) -> bool:
        return _COVER_PAGE.search(text) is not None

    def _is_listening_part_one_photo_page(page: "PageResult") -> bool:
        """Recognize the first real Listening Part 1 page without parsing it.

        Some scanned TOEIC booklets put the Part 1 directions on their own
        page, followed by a page containing only photos labelled ``1.`` and
        ``2.``.  A lone marker is not enough: directions can contain numbered
        steps.  Two markers on a non-direction page are a reliable anchor;
        when the Part 1 heading is printed on the same page, require their
        OCR boxes to be vertically separated like two photo captions.
        """
        combined = "\n".join(page.columns)
        markers: set[int] = set()
        marker_tops: list[int] = []

        for match in QUESTION_START.finditer(combined):
            number = _normalize_number(match.group("number"))
            if number in {1, 2}:
                markers.add(number)

        for token in page.tokens:
            match = NUMBER_TOKEN.match(token.text)
            if not match:
                continue
            number = _normalize_number(match.group(1))
            if number in {1, 2}:
                markers.add(number)
                marker_tops.append(token.top)

        if markers != {1, 2}:
            return False
        if not _is_dir(combined):
            # Tables and answer choices on later Parts can also contain the
            # isolated values 1 and 2. Photo captions are sparse and vertically
            # separated; require that spatial evidence whenever token boxes are
            # available.
            return not marker_tops or (
                len(marker_tops) >= 2
                and max(marker_tops) - min(marker_tops)
                >= max(120, page.height // 6)
            )

        # A Part 1 heading may share the first photo page.  Its two labels
        # are far apart vertically, unlike a numbered instruction list.
        return (
            len(marker_tops) >= 2
            and max(marker_tops) - min(marker_tops) >= max(120, page.height // 6)
        )

    # Part 1 is image-only in standard TOEIC booklets, so OCR may see only a
    # stray question number (or no number at all). Part 2 is the reliable
    # anchor: it starts three physical pages after the six Part 1 photos.
    if exam_type == "listening":
        def _is_part_two_anchor(page: "PageResult") -> bool:
            combined = "\n".join(page.columns)
            if not re.search(r"\bPART\s*2\b", combined, re.IGNORECASE):
                return False
            # A vertical TEST 2 tab or stray sentence on a later page can be
            # misread as PART 2. The real section page also contains its
            # spoken-response directions or several question numbers 7..31.
            if re.search(
                r"(?:Directions|answer\s+sheet|question\s+or\s+statement|"
                r"three\s+responses|will\s+be\s+spoken)",
                combined,
                re.IGNORECASE,
            ):
                return True
            part_two_numbers = {
                number
                for match in QUESTION_START.finditer(combined)
                if (number := _normalize_number(match.group("number"))) is not None
                and 7 <= number <= 31
            }
            return len(part_two_numbers) >= 2

        part2_page = next(
            (page.number for page in pages if _is_part_two_anchor(page)),
            None,
        )
        photo_search_pages = [
            page
            for page in pages[: min(12, len(pages))]
            if part2_page is None or page.number < part2_page
        ]
        for page in photo_search_pages:
            if _is_listening_part_one_photo_page(page):
                logger.info(
                    "[LISTENING_PREFIX] Part 1 markers 1/2 found at page %d; skipping %d prefix page(s)",
                    page.number,
                    page.number - 1,
                )
                return page.number

        if part2_page is not None:
            marker_pages: list[int] = []
            marker_numbers: set[int] = set()
            for page in pages:
                if page.number >= part2_page:
                    continue
                combined = "\n".join(page.columns)
                for match in QUESTION_START.finditer(combined):
                    number = _normalize_number(match.group("number"))
                    if number is not None and 1 <= number <= 6:
                        marker_numbers.add(number)
                        marker_pages.append(page.number)
                        break
                for token in page.tokens:
                    marker = NUMBER_TOKEN.match(token.text)
                    if not marker:
                        continue
                    number = _normalize_number(marker.group(1))
                    if number is not None and 1 <= number <= 6:
                        marker_numbers.add(number)
                        marker_pages.append(page.number)
                        break
            # A standalone Part 2 PDF has no Part 1 photo pages to retain.
            # Start at the first page carrying the real Part 2 heading unless
            # OCR found a reliable earlier photo page marker.
            photo_prefix = (
                pages[0].number
                if len(set(marker_pages)) >= 2 and len(marker_numbers) >= 2
                else None
            )
            start = min([part2_page, photo_prefix] if photo_prefix else [part2_page])
            logger.info(
                "[LISTENING_PREFIX] Part 2 page=%d; Part 1 photo pages start at page=%d",
                part2_page,
                start,
            )
            return start

    # Step 1: Find the first page that actually contains valid question starts.
    # Twelve pages covers booklets with a cover, copyright, table of contents
    # and separate direction sheets while keeping the prefix rule bounded.
    prefix_pages = pages[: min(12, len(pages))]
    for page in prefix_pages:
        combined = "\n".join(page.columns)
        if _has_questions(combined):
            if page.number > 1:
                logger.info(
                    "[SKIP_PAGES] exam_type=%s content starts at page %d (skipping %d prefix page(s))",
                    exam_type,
                    page.number,
                    page.number - 1,
                )
            return page.number

    # Step 2: If no question numbers were found on early pages, skip standalone cover/direction pages.
    skip = 0
    for page in prefix_pages:
        combined = "\n".join(page.columns)
        if (_is_cov(combined) or _is_dir(combined) or len(combined.strip()) < 800) and not _has_questions(combined):
            skip = page.number
        else:
            break

    if 0 < skip < len(pages):
        logger.info(
            "[SKIP_PAGES] exam_type=%s skipping %d prefix page(s) by cover analysis; content starts at page %d",
            exam_type,
            skip,
            skip + 1,
        )
        return skip + 1

    return 1


def _part(exam_type: str, number: int) -> str:
    if exam_type == "listening":
        if number <= 6:
            return "Part 1 - Phần 1"
        if number <= 31:
            return "Part 2 - Phần 2"
        if number <= 70:
            return "Part 3 - Phần 3"
        return "Part 4 - Phần 4"
    if number <= 130:
        return "Part 5 - Phần 5"
    if number <= 146:
        return "Part 6 - Phần 6"
    return "Part 7 - Phần 7"


def _group_id(exam_type: str, number: int) -> str:
    if exam_type == "listening":
        if number <= 31:
            return f"q-{number}"
        start = 32 if number <= 70 else 71
        group_start = start + ((number - start) // 3) * 3
        return f"listening-{group_start}-{group_start + 2}"
    if number <= 130:
        return f"q-{number}"
    if number <= 146:
        start = 131 + ((number - 131) // 4) * 4
        return f"reading-{start}-{start + 3}"
    return f"reading-q-{number}"


def _option_letters(exam_type: str, number: int) -> list[str]:
    if exam_type == "listening" and 7 <= number <= 31:
        return ["A", "B", "C"]
    return ["A", "B", "C", "D"]


def _normalize_number(raw: str) -> int | None:
    value = raw.strip().translate(str.maketrans({"I": "1", "l": "1", "O": "0", "o": "0"}))
    if not value.isdigit():
        return None
    return int(value)


def _preprocess_for_ocr(
    image: Image.Image,
    scale: float = 1.0,
    *,
    normalize_scan: bool = False,
    preserve_clean_glyphs: bool = False,
) -> Image.Image:
    """Prepare a page for OCR without changing its retained crop source.

    ``normalize_scan`` is intentionally used only for bounded recovery pages.
    It removes uneven paper illumination/bleed-through common in photographed
    or printed booklets, but avoids degrading clean PDFs or Listening photos in
    the normal fast path.
    """
    w = max(1, round(image.width * scale))
    h = max(1, round(image.height * scale))
    gray = ImageOps.grayscale(image)
    if gray.size != (w, h):
        # LANCZOS preserves thin glyph strokes better than bilinear sampling.
        # The normal pass explicitly asks for 75%; the missing-question retry
        # deliberately keeps the full 300-DPI source.
        resized = gray.resize((w, h), Image.Resampling.LANCZOS)
        gray.close()
        gray = resized
    if normalize_scan and np is not None:
        array = np.asarray(gray, dtype=np.uint8)
        # Divide by a smooth local paper-background estimate. Unlike a hard
        # threshold this retains thin punctuation and small option markers.
        if cv2 is not None:
            background = cv2.GaussianBlur(array, (0, 0), sigmaX=27, sigmaY=27)
            normalized = cv2.divide(array, background, scale=255)
            normalized = cv2.createCLAHE(
                clipLimit=1.6, tileGridSize=(16, 16)
            ).apply(normalized)
        else:
            # Server/headless test hosts may not have OpenCV's shared runtime.
            # Pillow plus NumPy keeps the same illumination-division behavior
            # so recovery never silently becomes an unnormalized second pass.
            background_image = gray.filter(ImageFilter.GaussianBlur(radius=27))
            try:
                background = np.asarray(background_image, dtype=np.float32)
                normalized = np.clip(
                    array.astype(np.float32) * 255.0 / np.maximum(background, 1.0),
                    0,
                    255,
                ).astype(np.uint8)
            finally:
                background_image.close()
        gray.close()
        gray = Image.fromarray(normalized)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    gray = ImageEnhance.Contrast(gray).enhance(1.25)
    if normalize_scan or not preserve_clean_glyphs:
        # Median filtering removes salt-and-pepper noise on degraded scans,
        # but erodes thin glyphs on clean Listening PDF renders. Reading keeps
        # the established filter; Listening opts out for its source-resolution
        # full-page pass.
        filtered = gray.filter(ImageFilter.MedianFilter(size=3))
        gray.close()
        return filtered
    return gray


def _deskew_color(image: Image.Image) -> Image.Image:
    """Deskew small scan rotations while preserving a color source for crops."""
    if cv2 is None or np is None:
        return image
    array = np.array(image)
    gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
    # Angle estimation does not need the full 300-DPI bitmap. A 25% preview
    # cuts the OpenCV work by ~16x; only the final rotation uses full size.
    preview = cv2.resize(
        gray,
        (max(1, gray.shape[1] // 4), max(1, gray.shape[0] // 4)),
        interpolation=cv2.INTER_AREA,
    )
    binary = cv2.threshold(
        preview, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )[1]
    coordinates = np.column_stack(np.where(binary > 0))
    if len(coordinates) < 100:
        return image
    angle = cv2.minAreaRect(coordinates)[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    if abs(angle) < 0.12 or abs(angle) > 3.0:
        return image
    height, width = array.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    rotated = cv2.warpAffine(
        array,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
    return Image.fromarray(rotated)


def _data_to_text(
    data: dict[str, list[Any]], *, offset_x: int = 0, coordinate_scale: float = 1.0
) -> tuple[str, list[OCRToken], float]:
    grouped: dict[tuple[int, int, int, int], list[tuple[int, str]]] = {}
    tokens: list[OCRToken] = []
    confidences: list[float] = []
    count = len(data.get("text", []))
    for index in range(count):
        text = str(data["text"][index]).strip()
        if not text:
            continue
        try:
            confidence = float(data["conf"][index])
        except (ValueError, TypeError):
            confidence = -1
        if confidence >= 0:
            confidences.append(confidence)
        token = OCRToken(
            text=text,
            confidence=max(0.0, confidence),
            left=round((int(data["left"][index]) + offset_x) / coordinate_scale),
            top=round(int(data["top"][index]) / coordinate_scale),
            width=round(int(data["width"][index]) / coordinate_scale),
            height=round(int(data["height"][index]) / coordinate_scale),
        )
        tokens.append(token)
        key = (
            int(data["page_num"][index]),
            int(data["block_num"][index]),
            int(data["par_num"][index]),
            int(data["line_num"][index]),
        )
        grouped.setdefault(key, []).append((int(data["left"][index]), text))
    lines = [
        " ".join(text for _, text in sorted(words))
        for _, words in sorted(grouped.items(), key=lambda item: item[0])
    ]
    return "\n".join(lines), tokens, (
        sum(confidences) / len(confidences) if confidences else 0.0
    )


def _box_rect(box: tuple[tuple[float, float], ...]) -> tuple[float, float, float, float] | None:
    if not box:
        return None
    xs = [point[0] for point in box]
    ys = [point[1] for point in box]
    return min(xs), min(ys), max(xs), max(ys)


def _paddle_page_result(
    result: OCRResult,
    *,
    page_number: int,
    page_width: int,
    page_height: int,
    processed_width: int,
    coordinate_scale: float,
) -> PageResult:
    """Convert full-page OCR polygons into the parser's two-column model."""
    # Keep line rectangles long enough to merge OCR fragments that occupy the
    # same printed row.  Small scan artifacts can make ``(B)`` and ``evaluate``
    # separate OCR lines even though they form one answer choice.
    columns: list[list[tuple[float, float, float, str]]] = [[], []]
    tokens: list[OCRToken] = []
    confidences: list[float] = []

    def add_token(text: str, confidence: float, box: tuple[tuple[float, float], ...]) -> None:
        rect = _box_rect(box)
        if rect is None or not text.strip():
            return
        left, top, right, bottom = rect
        tokens.append(
            OCRToken(
                text=text.strip(),
                confidence=confidence,
                left=round(left / coordinate_scale),
                top=round(top / coordinate_scale),
                width=max(1, round((right - left) / coordinate_scale)),
                height=max(1, round((bottom - top) / coordinate_scale)),
            )
        )

    for line in result.lines:
        rect = _box_rect(line.box)
        if rect is None:
            continue
        left, top, right, bottom = rect
        compact_text = re.sub(r"[^A-Z0-9]", "", line.text.upper())
        # Printed navigation/footer text is outside every TOEIC question but
        # can otherwise be appended to the final D option on a page. The
        # vertical TEST tab is filtered only at the far-right edge so normal
        # question text containing the word "test" remains intact.
        if (
            compact_text.startswith("GOONTOTHENEXTPAGE")
            or (
                compact_text.startswith("BACKTOPARTS5")
                and compact_text.endswith("CHECKYOURWORK")
            )
            or compact_text.startswith("THISISTHEENDOFTHELISTENINGTEST")
            # Tiled OCR can split the Listening end marker at a tile edge and
            # return only its trailing words (for example
            # ``of the Listening test.``). It is still footer text, never an
            # answer option.
            or (
                compact_text.endswith("OFTHELISTENINGTEST")
                and top >= page_height * coordinate_scale * 0.60
            )
            or (
                compact_text == "TEST"
                and left >= processed_width * 0.88
            )
            or (
                re.fullmatch(r"TEST\d{1,3}", compact_text)
                and top >= page_height * coordinate_scale * 0.88
            )
        ):
            continue
        confidences.append(line.confidence)
        if line.words:
            for word in line.words:
                word_rect = _box_rect(word.box)
                if word_rect is None:
                    continue
                word_left, word_top, word_right, word_bottom = word_rect
                word_column = (
                    0
                    if (word_left + word_right) / 2 < processed_width / 2
                    else 1
                )
                # Tesseract can split a visually single printed row into
                # several line IDs when one token sits a few pixels lower
                # (for example `8:59` / `a.m.,` in RC). Preserve word boxes
                # and rebuild rows spatially below instead of accepting the
                # engine's arbitrary line ordering.
                columns[word_column].append(
                    (word_top, word_bottom, word_left, word.text)
                )
                # Token confidence is intentionally amplified for the existing
                # layout/fragment ranking contract. Keep this stable across
                # Reading golden fixtures; OCR admission itself uses the raw
                # Tesseract confidence in rapid_ocr.
                add_token(word.text, word.confidence * 100.0, word.box)
        else:
            center_x = (left + right) / 2
            column = 0 if center_x < processed_width / 2 else 1
            columns[column].append((top, bottom, left, line.text))
            add_token(line.text, line.confidence, line.box)

    rendered_columns: list[str] = []
    word_heights = sorted(
        bottom - top
        for column_items in columns
        for top, bottom, _left, _text in column_items
        if bottom > top
    )
    median_word_height = (
        word_heights[len(word_heights) // 2] if word_heights else 0.0
    )
    # Crop OCR uses a much shorter local page height than full-page OCR. A
    # page-relative tolerance alone then splits a visually single row whose
    # baseline varies by a few pixels (e.g. `are` after `They`), losing words
    # or punctuation on short answer choices. Bound it by glyph height too.
    same_row_tolerance = max(
        6.0,
        page_height * coordinate_scale * 0.004,
        median_word_height * 0.35,
    )

    for items in columns:
        rows: list[list[tuple[float, float, float, str]]] = []
        for item in sorted(items, key=lambda value: (value[0], value[2])):
            top, bottom, _left, _text = item
            overlapping = next(
                (
                    row
                    for row in reversed(rows)
                    if abs(top - min(entry[0] for entry in row)) <= same_row_tolerance
                    and max(entry[0] for entry in row) < min(entry[1] for entry in row)
                    and max(top, min(entry[0] for entry in row))
                    < min(bottom, max(entry[1] for entry in row))
                ),
                None,
            )
            if overlapping is None:
                rows.append([item])
            else:
                overlapping.append(item)
        rendered_columns.append(
            "\n".join(
                " ".join(text for _top, _bottom, _left, text in sorted(row, key=lambda value: value[2]))
                for row in rows
            )
        )
    # Some OCR engines do not return word boxes. A line token still
    # preserves the layout anchors needed by question and stimulus cropping.
    if not tokens:
        for line in result.lines:
            add_token(line.text, line.confidence, line.box)
    return PageResult(
        page_number,
        page_width,
        page_height,
        rendered_columns,
        sorted(tokens, key=lambda token: (token.top, token.left)),
        sum(confidences) / len(confidences) if confidences else 0.0,
    )


def _ocr_page_image(
    image: Image.Image,
    *,
    page_number: int,
    page_width: int,
    page_height: int,
    coordinate_scale: float,
    normalize_scan: bool = False,
    ocr_config: str | None = None,
    text_score: float | None = None,
    preserve_clean_glyphs: bool = False,
) -> PageResult:
    processed = _preprocess_for_ocr(
        image,
        coordinate_scale,
        normalize_scan=normalize_scan,
        preserve_clean_glyphs=preserve_clean_glyphs,
    )
    try:
        recognize_kwargs: dict[str, Any] = {"config": ocr_config}
        if text_score is not None:
            recognize_kwargs["text_score"] = text_score
        result = recognize(processed, **recognize_kwargs)
        return _paddle_page_result(
            result,
            page_number=page_number,
            page_width=page_width,
            page_height=page_height,
            processed_width=processed.width,
            coordinate_scale=coordinate_scale,
        )
    finally:
        processed.close()


def _ocr_region(
    image: Image.Image,
    *,
    page_number: int,
    roi: ReadingROI,
    scale_limit: float = 0.75,
    normalize_scan: bool = False,
    binary_threshold: int | None = None,
    ocr_config: str | None = None,
) -> PageResult:
    """OCR one normalized ROI and map its boxes back to the source page.

    The retained page remains the crop source.  Only the grayscale ROI is sent
    to the OCR adapter, so passage pixels never enter the high-quality question pass.
    """
    page_width, page_height = image.size
    left, top, right, bottom = (
        max(0.0, min(1.0, value)) for value in roi.bbox
    )
    pixel_bbox = (
        max(0, round(left * page_width)),
        max(0, round(top * page_height)),
        min(page_width, round(right * page_width)),
        min(page_height, round(bottom * page_height)),
    )
    x1, y1, x2, y2 = pixel_bbox
    if x2 <= x1 or y2 <= y1:
        return PageResult(page_number, page_width, page_height, ["", ""], [], 0.0)

    crop = image.crop(pixel_bbox)
    try:
        # Match the existing full-page fast pass (~225 DPI) while keeping the
        # ROI smaller.  Letting a short ROI run at native 300 DPI would erase
        # the performance gain and is unnecessary for printed question text.
        scale = min(scale_limit, 2000.0 / max(crop.width, crop.height))
        if binary_threshold is None:
            processed = _preprocess_for_ocr(
                crop,
                scale,
                normalize_scan=normalize_scan,
            )
        else:
            processed = ImageOps.grayscale(crop)
            target_size = (
                max(1, round(crop.width * scale)),
                max(1, round(crop.height * scale)),
            )
            if processed.size != target_size:
                resized = processed.resize(target_size, Image.Resampling.LANCZOS)
                processed.close()
                processed = resized
            threshold = max(1, min(254, int(binary_threshold)))
            binary = processed.point(
                lambda value: 255 if value > threshold else 0
            )
            processed.close()
            processed = binary
        try:
            result = recognize(processed, config=ocr_config)
            local = _paddle_page_result(
                result,
                page_number=page_number,
                page_width=crop.width,
                page_height=crop.height,
                # A block retry is already cropped to one physical page
                # column. Keep every detected line in one parser column even
                # when a long answer happens to cross the crop midpoint.
                processed_width=(
                    processed.width * 2 if roi.column is not None else processed.width
                ),
                coordinate_scale=scale,
            )
        finally:
            processed.close()
    finally:
        crop.close()

    translated_tokens = [
        OCRToken(
            text=token.text,
            confidence=token.confidence,
            left=token.left + x1,
            top=token.top + y1,
            width=token.width,
            height=token.height,
        )
        for token in local.tokens
    ]
    if roi.column is None:
        columns = local.columns
    else:
        text = "\n".join(column for column in local.columns if column).strip()
        columns = ["", ""]
        columns[roi.column] = text
    return PageResult(
        page_number,
        page_width,
        page_height,
        columns,
        sorted(translated_tokens, key=lambda token: (token.top, token.left)),
        local.confidence,
    )


def _reading_locator_page(
    page_path: Path,
    *,
    page_number: int,
    coordinate_scale: float,
) -> PageResult:
    """Run the low-resolution structural OCR pass for one retained page."""
    with Image.open(page_path) as source:
        image = source.convert("RGB")
    try:
        return _ocr_page_image(
            image,
            page_number=page_number,
            page_width=image.width,
            page_height=image.height,
            coordinate_scale=coordinate_scale,
        )
    finally:
        image.close()


def _reading_page_numbers(page: PageResult) -> tuple[int, ...]:
    numbers: set[int] = set()
    for column in page.columns:
        for match in QUESTION_START.finditer(column):
            number = _normalize_number(match.group("number"))
            if number is not None and 101 <= number <= 200:
                numbers.add(number)
    for token in page.tokens:
        match = NUMBER_TOKEN.match(token.text)
        if not match:
            continue
        number = _normalize_number(match.group(1))
        if number is not None and 101 <= number <= 200:
            numbers.add(number)
    return tuple(sorted(numbers))


def _reading_active_header(
    page_number: int, headers: list[ReadingHeader]
) -> ReadingHeader | None:
    active: ReadingHeader | None = None
    for header in headers:
        if header.page > page_number:
            break
        active = header
    return active


def _reading_page_part(
    page: PageResult,
    *,
    current_part: str | None,
    numbers: tuple[int, ...],
) -> str | None:
    text = "\n".join(page.columns)
    if re.search(r"\bPART\s*7\b", text, re.IGNORECASE):
        return "Part 7"
    if re.search(r"\bPART\s*6\b", text, re.IGNORECASE):
        return "Part 6"
    if re.search(r"\bPART\s*5\b", text, re.IGNORECASE):
        return "Part 5"
    if numbers:
        if numbers[0] <= 130:
            return "Part 5"
        if numbers[0] <= 146:
            return "Part 6"
        return "Part 7"
    return current_part


def _reading_expand_bbox(
    bbox: tuple[float, float, float, float],
    *,
    margin_x: float = 0.015,
    margin_y: float = 0.02,
) -> tuple[float, float, float, float]:
    left, top, right, bottom = bbox
    return (
        max(0.0, left - margin_x),
        max(0.0, top - margin_y),
        min(1.0, right + margin_x),
        min(1.0, bottom + margin_y),
    )


def _build_reading_page_plans(
    pages: list[PageResult],
    *,
    content_start_page: int,
    text_pages: set[int],
) -> list[ReadingPagePlan]:
    """Build conservative question/passages ROIs from low-cost layout OCR."""
    headers = _reading_headers(pages)
    plans: list[ReadingPagePlan] = []
    current_part: str | None = None

    for page in sorted(pages, key=lambda item: item.number):
        numbers = _reading_page_numbers(page)
        if page.number < content_start_page:
            plans.append(
                ReadingPagePlan(
                    page=page.number,
                    part=None,
                    source="prefix",
                    expected_numbers=(),
                    question_rois=[],
                    passage_rois=[],
                    confidence=100.0,
                )
            )
            continue

        current_part = _reading_page_part(
            page,
            current_part=current_part,
            numbers=numbers,
        )
        active_header = _reading_active_header(page.number, headers)
        if active_header is not None and active_header.start >= 131:
            if active_header.start >= 147:
                current_part = "Part 7"
            elif current_part is None or current_part == "Part 6":
                current_part = "Part 6"

        source = "native_text" if page.number in text_pages else "low_dpi_ocr"
        expected = numbers
        if not expected and active_header is not None and current_part in {"Part 6", "Part 7"}:
            expected = tuple(range(active_header.start, active_header.end + 1))

        question_rois: list[ReadingROI] = []
        passage_rois: list[tuple[float, float, float, float]] = []
        fallback_reason: str | None = None

        if current_part == "Part 5":
            # Part 5 has no passage, so one question-only ROI still preserves
            # both columns while avoiding two separate detector invocations.
            question_rois = [
                ReadingROI((0.03, 0.08, 0.97, 0.94), "question_options"),
            ]
        elif current_part == "Part 6":
            option_top = _first_option_top(page, expected)
            if option_top is None:
                fallback_reason = "part6_option_anchor_missing"
            else:
                top = max(0.06, option_top - 0.035)
                question_rois = [
                    ReadingROI(
                        _reading_expand_bbox((0.03, top, 0.97, 0.93)),
                        "question_options",
                    )
                ]
        elif current_part == "Part 7":
            question_top = _question_top(page, expected)
            if question_top is not None:
                question_rois = [
                    ReadingROI(
                        _reading_expand_bbox((0.03, max(0.05, question_top - 0.025), 0.97, 0.93)),
                        "question_options",
                    )
                ]
            elif active_header is not None:
                # This is a passage-only page.  It still contributes a source
                # crop but must not enter the high-quality OCR pass.  If a
                # dense A-D block is visible while question numbers are not,
                # treat it as an uncertain question page and use fallback.
                if _option_marker_count(page) >= 4:
                    fallback_reason = "part7_question_anchor_missing"
            elif numbers:
                fallback_reason = "part7_question_anchor_missing"

        if active_header is not None:
            passage_top = active_header.bottom + 0.01 if active_header.page == page.number else 0.035
            boundary = 0.92
            if current_part == "Part 6":
                option_top = _first_option_top(page, expected)
                if option_top is not None:
                    boundary = min(boundary, option_top - 0.018)
            elif current_part == "Part 7":
                question_top = _question_top(page, expected)
                if question_top is not None:
                    boundary = min(boundary, question_top - 0.018)
            if boundary - passage_top >= 0.075:
                passage_rois.append((0.045, passage_top, 0.92, boundary))

        plans.append(
            ReadingPagePlan(
                page=page.number,
                part=current_part,
                source=source,
                expected_numbers=expected,
                question_rois=question_rois,
                passage_rois=passage_rois,
                confidence=(99.0 if source == "native_text" else 85.0)
                if question_rois or passage_rois
                else 50.0,
                fallback_reason=fallback_reason,
            )
        )
    return plans


def _ocr_reading_plan(
    plan: ReadingPagePlan,
    *,
    page_path: Path,
) -> tuple[int, list[PageResult], float]:
    started = time.perf_counter()
    if not plan.question_rois or not page_path.is_file():
        return plan.page, [], time.perf_counter() - started
    with Image.open(page_path) as source:
        image = source.convert("RGB")
    results: list[PageResult] = []
    try:
        for roi in plan.question_rois:
            results.append(
                _ocr_region(image, page_number=plan.page, roi=roi)
            )
    finally:
        image.close()
    return plan.page, results, time.perf_counter() - started


def _merge_layout_page(
    page: PageResult,
    roi_results: list[PageResult],
) -> PageResult:
    tokens = list(page.tokens)
    confidence = page.confidence
    for result in roi_results:
        tokens.extend(result.tokens)
        confidence = max(confidence, result.confidence)
    return PageResult(
        page.number,
        page.width,
        page.height,
        page.columns,
        sorted(tokens, key=lambda token: (token.top, token.left)),
        confidence,
    )


def _candidates_from_page_result(page: PageResult) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for column, text in enumerate(page.columns):
        candidates.extend(
            _parse_column(
                text,
                page=page.number,
                column=column,
                confidence=page.confidence,
            )
        )
    return candidates


def _full_page_candidates(
    page_path: Path,
    *,
    page_number: int,
    normalize_scan: bool = False,
) -> list[dict[str, Any]]:
    if not page_path.is_file():
        return []
    with Image.open(page_path) as source:
        image = source.convert("RGB")
    try:
        page = _ocr_page_image(
            image,
            page_number=page_number,
            page_width=image.width,
            page_height=image.height,
            coordinate_scale=3 / 4 if not normalize_scan else 1.0,
            normalize_scan=normalize_scan,
        )
    finally:
        image.close()
    return _candidates_from_page_result(page)


def _render_and_ocr_page(
    pdf_path: str,
    page_number: int,
    pages_dir: Path,
    do_ocr: bool,
    *,
    ocr_scale: float = 3 / 4,
    ocr_text_score: float | None = None,
    preserve_clean_glyphs: bool = False,
) -> PageResult:
    images = convert_from_path(
        pdf_path,
        dpi=_render_dpi(),
        first_page=page_number,
        last_page=page_number,
        fmt="ppm",
        thread_count=1,
        poppler_path=_poppler_path(),
    )
    if not images:
        raise RuntimeError(f"Không render được trang {page_number}")
    image = _deskew_color(images[0].convert("RGB"))
    width, height = image.size
    page_path = pages_dir / f"page-{page_number:03d}.jpg"
    # OCR runs from these retained page files. Quality 94 drops punctuation in
    # small text (`a.m.,` on the RC reference), so use the highest JPEG quality
    # while the job is active; page count/worker limits still bound disk use.
    image.save(page_path, "JPEG", quality=100, optimize=True)

    if not do_ocr:
        image.close()
        return PageResult(page_number, width, height, [], [], 100.0)
    try:
        return _ocr_page_image(
            image,
            page_number=page_number,
            page_width=width,
            page_height=height,
            coordinate_scale=ocr_scale,
            text_score=ocr_text_score,
            preserve_clean_glyphs=preserve_clean_glyphs,
        )
    finally:
        image.close()


def _process_rendered_page(
    rendered_path: str | Path,
    page_number: int,
    pages_dir: Path,
    do_ocr: bool,
    *,
    ocr_scale: float = 3 / 4,
    ocr_text_score: float | None = None,
    preserve_clean_glyphs: bool = False,
) -> PageResult:
    """Normalize and OCR a page already rendered by one shared Poppler pass."""
    source_path = Path(rendered_path)
    with Image.open(source_path) as source:
        original = source.convert("RGB")
    image = _deskew_color(original)
    width, height = image.size
    page_path = pages_dir / f"page-{page_number:03d}.jpg"
    if image is original:
        # Poppler already emitted the requested high-quality JPEG. Renaming it
        # avoids a second full-page JPEG encoding pass.
        os.replace(source_path, page_path)
    else:
        temporary = pages_dir / f".page-{page_number:03d}.jpg"
        image.save(temporary, "JPEG", quality=100, optimize=True)
        os.replace(temporary, page_path)
        source_path.unlink(missing_ok=True)

    if not do_ocr:
        image.close()
        return PageResult(page_number, width, height, [], [], 100.0)
    try:
        return _ocr_page_image(
            image,
            page_number=page_number,
            page_width=width,
            page_height=height,
            coordinate_scale=ocr_scale,
            text_score=ocr_text_score,
            preserve_clean_glyphs=preserve_clean_glyphs,
        )
    finally:
        image.close()


def _retry_page_ocr(page_path: Path, page_number: int) -> list[dict[str, Any]]:
    """Recover weak scanned pages with illumination normalization at full size."""
    if not page_path.is_file():
        return []
    with Image.open(page_path) as source:
        image = source.convert("RGB")
        try:
            page = _ocr_page_image(
                image,
                page_number=page_number,
                page_width=image.width,
                page_height=image.height,
                coordinate_scale=1.0,
                normalize_scan=True,
            )
        finally:
            image.close()
    candidates: list[dict[str, Any]] = []
    for column, text in enumerate(page.columns):
        candidates.extend(
            _parse_column(
                text,
                page=page_number,
                column=column,
                confidence=page.confidence,
            )
        )
    return candidates


def _read_pdf_layout(
    pdf_path: str,
    *,
    render_dpi: int = 300,
) -> tuple[dict[int, list[str]], dict[int, list[OCRToken]]]:
    pages: dict[int, list[str]] = {}
    page_tokens: dict[int, list[OCRToken]] = {}
    render_scale = render_dpi / 72
    with pdfplumber.open(pdf_path) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            x0, top, x1, bottom = page.bbox
            midpoint = x0 + (x1 - x0) / 2
            left = page.crop((x0, top, midpoint, bottom), strict=False).extract_text(
                x_tolerance=2, y_tolerance=3
            ) or ""
            right = page.crop((midpoint, top, x1, bottom), strict=False).extract_text(
                x_tolerance=2, y_tolerance=3
            ) or ""
            pages[index] = [left, right]
            words = page.extract_words(x_tolerance=2, y_tolerance=3) or []
            page_tokens[index] = [
                OCRToken(
                    text=str(word.get("text", "")).strip(),
                    confidence=99.0,
                    left=round(float(word["x0"]) * render_scale),
                    top=round(float(word["top"]) * render_scale),
                    width=round((float(word["x1"]) - float(word["x0"])) * render_scale),
                    height=round(
                        (float(word["bottom"]) - float(word["top"])) * render_scale
                    ),
                )
                for word in words
                if str(word.get("text", "")).strip()
            ]
    return pages, page_tokens


def _read_text_layer(pdf_path: str) -> dict[int, list[str]]:
    pages, _tokens = _read_pdf_layout(pdf_path)
    return pages


def _reading_text_page_is_usable(columns: list[str], page_number: int) -> bool:
    """Use native PDF text when it contains a real header or complete questions."""
    combined = "\n".join(columns)
    if READING_GROUP_HEADER.search(combined):
        return True
    for column, text in enumerate(columns):
        for item in _parse_column(
            text,
            page=page_number,
            column=column,
            confidence=99.0,
        ):
            if 101 <= int(item["number"]) <= 200 and len(item["options"]) >= 3:
                return True
    return False


def _text_layer_is_usable(pages: dict[int, list[str]], exam_type: str) -> bool:
    text = "\n".join(column for columns in pages.values() for column in columns)
    if exam_type == "listening":
        numbers = {
            int(match.group(1))
            for match in re.finditer(r"(?m)^\s*(\d{2,3})\.\s+", text)
            if 32 <= int(match.group(1)) <= 100
        }
        return len(text) > 5_000 and len(numbers) >= 60
    else:
        numbers = {
            int(match.group(1))
            for match in re.finditer(r"(?m)^\s*(\d{3})\.\s+", text)
            if 101 <= int(match.group(1)) <= 200
        }
        return len(text) > 5_000 and len(numbers) >= 50


def _listening_text_page_is_usable(
    columns: list[str], page_number: int
) -> bool:
    """Validate one Listening text layer before skipping raster OCR.

    A PDF can contain a mostly-good text layer with one missing question. The
    former document-wide check accepted that file and silently skipped the
    image OCR for the damaged page. Listening pages 3/4 have no printed text
    questions, while Parts 3/4 must expose a contiguous local question range
    and at least three choices per parsed question.
    """
    combined = "\n".join(columns).strip()
    # Image-only PDFs have no native text at all.  The previous fallback below
    # treated an empty page as a harmless Part 1/2 page and consequently set
    # ``do_ocr=False`` for every page in a scanned Listening booklet.  The
    # sequence resolver then filled 32–100 with blank review placeholders.
    # Require actual selectable text before allowing a page to bypass raster
    # OCR; a blank text layer is never evidence that the printed page is blank.
    if not re.search(r"[A-Za-z0-9]{2,}", combined):
        return False
    parsed = [
        item
        for column, text in enumerate(columns)
        for item in _parse_column(
            text,
            page=page_number,
            column=column,
            confidence=99.0,
        )
        if 32 <= int(item["number"]) <= 100
    ]
    numbers = sorted({int(item["number"]) for item in parsed})
    if numbers:
        if numbers != list(range(numbers[0], numbers[-1] + 1)):
            return False
        return all(len(item.get("options") or {}) >= 3 for item in parsed)
    # Parts 1/2 and their directions intentionally do not provide printed
    # question text. A page advertising Part 3/4 without any valid number is
    # damaged and must fall through to raster OCR.  Other native-text pages
    # (for example a cover or empty text artifact) are also OCRed rather than
    # silently accepted, because only clear Listening Part 1/2 evidence is
    # safe to skip.
    return bool(
        re.search(r"\b(?:PART\s*[12]|LISTENING|DIRECTIONS)\b", combined, re.IGNORECASE)
        and not re.search(r"\bPART\s*[34]\b", combined, re.IGNORECASE)
    )


def _parse_column(
    text: str, *, page: int, column: int, confidence: float
) -> list[dict[str, Any]]:
    cleaned = text.replace("\r", "\n").replace("\x0c", "")
    matches = list(QUESTION_START.finditer(cleaned))
    parsed: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        raw_number = match.group("number")
        number = _normalize_number(raw_number)
        if number is None:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
        body = cleaned[match.end() : end].strip()
        body = BOILERPLATE.split(body, maxsplit=1)[0].strip()
        # Scanned booklets often place a bare printed page number after the
        # final option in the left column (for example "through\n20").
        body = re.sub(r"\n\s*\d{1,2}\s*$", "", body).strip()
        question_text, options, _ = _extract_options(body)
        parsed.append(
            {
                "raw_number": raw_number,
                "number": number,
                "text": question_text,
                "options": options,
                "page": page,
                "column": column,
                "order": match.start(),
                "confidence": confidence,
                "issues": [],
            }
        )
    # A photo/scan can lose only the dark question-number line while keeping
    # all four answer choices (notably the right column of two-column TOEIC
    # pages).  Preserve those complete option groups as numbered-later
    # candidates.  `_resolve_sequence` will bind them only to the exact next
    # expected number, which is safer than inventing a number from OCR noise.
    prefix_end = matches[0].start() if matches else len(cleaned)
    prefix = cleaned[:prefix_end]
    option_starts = list(UNMARKED_OPTION_A.finditer(prefix))
    for index, option_match in enumerate(option_starts):
        end = (
            option_starts[index + 1].start()
            if index + 1 < len(option_starts)
            else len(prefix)
        )
        # When a question number is missing, the first A marker is still a
        # reliable block boundary but the text before it is the question stem.
        # Preserve that stem for the first unmarked block; later blocks remain
        # option-only fragments used solely for sequence alignment.
        block_start = 0 if index == 0 else option_match.start()
        _question_text, options, _ = _extract_options(prefix[block_start:end])
        # Two markers are enough to identify a real answer block.  We retain
        # an incomplete block too: it preserves its position between adjacent
        # numbered questions, while `_to_questions` will still flag the exact
        # missing choice for review instead of shifting later answers upward.
        if len(options) < 2:
            continue
        parsed.append(
            {
                "raw_number": "",
                "number": 0,
                "text": _question_text,
                "options": options,
                "page": page,
                "column": column,
                "order": option_match.start(),
                "confidence": confidence,
                "issues": [],
                "unmarked_options": True,
            }
        )
    return parsed


def _resolve_sequence(
    candidates: list[dict[str, Any]], exam_type: str
) -> tuple[list[dict[str, Any]], list[Issue]]:
    # Part 6 repeats question numbers inside passage blanks and again beside
    # the answer choices.  Resolve same-page duplicates before advancing the
    # sequence; otherwise a passage marker (131/133) can make the valid 132
    # candidate appear to be out of order.
    best_by_page_number: dict[tuple[int, int, int], dict[str, Any]] = {}
    for item in candidates:
        # Keep every unmarked option group.  They share the
        # temporary number 0 and are assigned in visual order below.
        temporary_number = int(item["number"])
        key = (
            int(item["page"]),
            temporary_number,
            int(item["order"]) if item.get("unmarked_options") else -1,
        )
        current = best_by_page_number.get(key)
        if current is None:
            best_by_page_number[key] = item
            continue
        current_score = (
            len(current.get("options") or {}) * 100
            + (20 if current.get("text") else 0)
            + float(current.get("confidence") or 0)
        )
        next_score = (
            len(item.get("options") or {}) * 100
            + (20 if item.get("text") else 0)
            + float(item.get("confidence") or 0)
        )
        if next_score > current_score:
            best_by_page_number[key] = item
    candidates = list(best_by_page_number.values())
    candidates.sort(key=lambda item: (item["page"], item["column"], item["order"]))
    start = 1 if exam_type == "listening" else 101
    absolute_max = 100 if exam_type == "listening" else 200
    recoverable_unmarked_positions: set[int] = set()
    for index, item in enumerate(candidates):
        if not item.get("unmarked_options") or index in recoverable_unmarked_positions:
            continue
        end = index
        while end < len(candidates) and candidates[end].get("unmarked_options"):
            end += 1
        previous = next(
            (
                int(candidates[position]["number"])
                for position in range(index - 1, -1, -1)
                if start <= int(candidates[position]["number"]) <= absolute_max
            ),
            None,
        )
        following = next(
            (
                int(candidates[position]["number"])
                for position in range(end, len(candidates))
                if start <= int(candidates[position]["number"]) <= absolute_max
            ),
            None,
        )
        if (
            previous is not None
            and following is not None
            and following - previous - 1 == end - index
        ):
            recoverable_unmarked_positions.update(range(index, end))
    expected = start
    accepted: dict[int, dict[str, Any]] = {}
    issues: list[Issue] = []

    for candidate_index, item in enumerate(candidates):
        number = item["number"]
        inferred = False
        recovered_from_options = (
            bool(item.get("unmarked_options"))
            and candidate_index in recoverable_unmarked_positions
        )
        if recovered_from_options:
            number = expected
            inferred = True
        elif item.get("unmarked_options"):
            # A partial option block without matching numbered neighbours is
            # not enough evidence to invent its question number.
            continue
        elif exam_type == "reading" and number != expected:
            repaired: int | None = None
            # Scans frequently turn the leading "1" into 4/7 (172→472) or
            # lose the first digits entirely (184→4, 185→35). Page/column
            # order gives us a safe anchor: only repair to the exact next
            # expected number, never to an arbitrary value in the range.
            if (
                absolute_max < number <= 999
                and number % 100 == expected % 100
            ):
                repaired = expected
            elif number < start:
                if number == expected % 100 or number == expected % 10:
                    repaired = expected
            if repaired is not None:
                item["raw_number"] = str(item.get("raw_number", number))
                number = repaired
                inferred = True
        if exam_type == "listening" and number == 0 and expected == 100:
            number = 100
            inferred = True
        elif number < start:
            continue
        elif number > absolute_max:
            # Do not turn a page number/watermark into a made-up question.
            # Missing questions are represented by an empty review placeholder.
            continue
        if number in accepted:
            existing = accepted[number]
            existing_score = (
                len(existing.get("options") or {}) * 100
                + (20 if existing.get("text") else 0)
                + float(existing.get("confidence") or 0)
            )
            next_score = (
                len(item.get("options") or {}) * 100
                + (20 if item.get("text") else 0)
                + float(item.get("confidence") or 0)
            )
            if next_score > existing_score:
                accepted[number] = item
            continue
        if number < expected:
            continue
        if inferred:
            issue_code = (
                "question_recovered_from_options"
                if recovered_from_options
                else "number_inferred"
            )
            item["issues"].append(issue_code)
            issues.append(
                Issue(
                    code=issue_code,
                    message=(
                        f"Đã khôi phục vị trí câu {number} từ nhóm phương án OCR."
                        if recovered_from_options
                        else f"Đã suy luận số câu {item['raw_number']} thành {number}."
                    ),
                    page=item["page"],
                    question_number=number,
                )
            )
        item["number"] = number
        accepted[number] = item
        expected = number + 1

    known_numbers = sorted(accepted)
    if not known_numbers:
        start = 1 if exam_type == "listening" else 101
        last = 100 if exam_type == "listening" else 200
    else:
        min_q, max_q = known_numbers[0], known_numbers[-1]
        is_full = (min_q <= 10 and max_q >= 90) if exam_type == "listening" else (min_q <= 110 and max_q >= 190)
        if is_full:
            start = 1 if exam_type == "listening" else 101
            last = 100 if exam_type == "listening" else 200
        else:
            start = min_q
            last = max_q

    result: list[dict[str, Any]] = []
    for number in range(start, last + 1):
        item = accepted.get(number)
        if item is None:
            previous = max((n for n in known_numbers if n < number), default=None)
            following = min((n for n in known_numbers if n > number), default=None)
            if previous is not None and following is not None:
                previous_page = accepted[previous]["page"]
                following_page = accepted[following]["page"]
                page = following_page if previous_page != following_page else previous_page
            elif previous is not None:
                page = accepted[previous]["page"]
            elif following is not None:
                page = accepted[following]["page"]
            else:
                page = 1
            item = {
                "number": number,
                "raw_number": "",
                "text": "",
                "options": {},
                "page": page,
                "column": 0,
                "order": 0,
                "confidence": 0.0,
                "issues": ["question_missing"],
            }
            issues.append(
                Issue(
                    code="question_missing",
                    message=f"Không đọc chắc chắn được câu {number}; đã tạo ô để review.",
                    page=page,
                    question_number=number,
                    severity="error",
                )
            )
        result.append(item)
    return result, issues


def _to_questions(
    parsed: list[dict[str, Any]], exam_type: str
) -> tuple[list[dict[str, Any]], list[Issue]]:
    questions: list[dict[str, Any]] = []
    issues: list[Issue] = []
    for item in parsed:
        number = item["number"]
        if exam_type == "reading" and 101 <= int(number) <= 130:
            # Part 5 has exactly one answer blank. Normalize only its question
            # sentence after parsing so option boundaries stay untouched.
            item = dict(item)
            item["text"] = normalize_part5_question_text(str(item.get("text", "")))
        letters = _option_letters(exam_type, number)
        item_issues = list(item["issues"])
        # Part 6 questions are blanks inside the retained passage image.  They
        # intentionally have no standalone question sentence; only their
        # answer choices are OCR content.  Part 5/7 and Listening Parts 3/4
        # still require question text.
        requires_question_text = not (
            exam_type == "reading" and 131 <= int(number) <= 146
        )
        if number >= 32 or exam_type == "reading":
            if (
                requires_question_text
                and not _question_text_is_usable(item["text"])
                and "question_missing" not in item_issues
            ):
                item_issues.append("question_missing")
                issues.append(
                    Issue(
                        code="question_missing",
                        message=f"Câu {number} thiếu nội dung câu hỏi.",
                        page=item["page"],
                        question_number=number,
                        severity="error",
                    )
                )
            missing = [letter for letter in letters if letter not in item["options"]]
            if missing:
                item_issues.append("options_missing")
                issues.append(
                    Issue(
                        code="options_missing",
                        message=f"Câu {number} thiếu đáp án {', '.join(missing)}.",
                        page=item["page"],
                        question_number=number,
                    )
                )
        if item["confidence"] < 70 and "low_confidence" not in item_issues:
            item_issues.append("low_confidence")
        options = {letter: item["options"].get(letter, "") for letter in letters}
        question = Question(
            number=number,
            part=_part(exam_type, number),
            text=item["text"],
            options=options,
            option_letters=letters,
            group_id=_group_id(exam_type, number),
            confidence=round(float(item["confidence"]), 1),
            issues=item_issues,
        ).model_dump()
        question["_page"] = item["page"]
        questions.append(question)
    return questions, issues


def _save_crop(
    *,
    job_id: str,
    job_dir: Path,
    page_number: int,
    bbox: tuple[float, float, float, float],
    lossless: bool,
) -> dict[str, Any]:
    bbox = tuple(max(0.0, min(1.0, value)) for value in bbox)
    left, top, right, bottom = bbox
    if right <= left or bottom <= top:
        raise ValueError("Vùng crop không hợp lệ")
    page_path = job_dir / "pages" / f"page-{page_number:03d}.jpg"
    if not page_path.is_file():
        logger.warning("[CROP] Page %d missing image, skipping crop", page_number)
        return {
            "id": f"missing-{uuid.uuid4().hex[:8]}",
            "url": "",
            "page": page_number,
            "bbox": [round(v, 5) for v in bbox],
            "width": 0,
            "height": 0,
        }
    with Image.open(page_path) as page:
        pixel_bbox = (
            round(left * page.width),
            round(top * page.height),
            round(right * page.width),
            round(bottom * page.height),
        )
        crop = page.crop(pixel_bbox)
        asset_id = f"{uuid.uuid4().hex}.webp"
        asset_path = job_dir / "assets" / asset_id
        if lossless:
            # Method 0 is still lossless and cuts document compression time in
            # half. Assets are slightly larger but remain outside sessionStorage.
            crop.save(asset_path, "WEBP", lossless=True, method=0)
        else:
            # Source pages are already JPEG. Re-encoding their document crops
            # as lossless WebP preserves JPEG artifacts rather than extra text
            # detail, while tripling payload size. Quality 95/method 0 remains
            # visually indistinguishable for print and encodes quickly.
            crop.save(asset_path, "WEBP", quality=95, method=0)
        width, height = crop.size
        crop.close()
    return {
        "id": asset_id,
        "url": f"/api/extractions/{job_id}/assets/{asset_id}",
        "page": page_number,
        "bbox": [round(value, 5) for value in bbox],
        "width": width,
        "height": height,
    }


def _question_top(page: PageResult, numbers: Iterable[int]) -> float | None:
    expected = set(numbers)
    candidates: list[int] = []
    for token in page.tokens:
        text = token.text.translate(SPATIAL_MARKER_TRANSLATION)
        match = NUMBER_TOKEN.match(text)
        raw_number = match.group(1) if match else None
        if raw_number is None:
            question_match = QUESTION_START.match(text)
            raw_number = (
                question_match.group("number") if question_match else None
            )
        if raw_number is None:
            continue
        number = _normalize_number(raw_number)
        if number in expected or (
            number is not None
            and number > 999
            and number % 1000 in expected
        ):
            candidates.append(token.top)
    if not candidates:
        return None
    return min(candidates) / page.height


def _first_option_top(
    page: PageResult,
    numbers: Iterable[int] | None = None,
) -> float | None:
    """Locate the answer list below a Part 6 document.

    Part 6 question numbers are printed inside the passage blanks, so treating
    the first number as the crop boundary truncates the document.
    """
    expected = set(numbers or [])
    option_tokens: list[int] = []
    for token in page.tokens:
        text = token.text.translate(SPATIAL_MARKER_TRANSLATION)
        numbered = NUMBERED_OPTION_A.match(text)
        if numbered is not None:
            number = _normalize_number(numbered.group("number"))
            if not expected or number in expected:
                option_tokens.append(token.top)
            continue
        if LEADING_OPTION_A.match(text):
            option_tokens.append(token.top)
    return min(option_tokens) / page.height if option_tokens else None


def _option_marker_count(page: PageResult) -> int:
    return sum(
        1
        for token in page.tokens
        if re.fullmatch(r"[\(\[\{]?[A-Da-d][\)\]\}\.]", token.text)
    )


def _reading_headers(pages: list[PageResult]) -> list[ReadingHeader]:
    """Find TOEIC passage ranges before associating question pages with crops."""
    headers: dict[tuple[int, int], ReadingHeader] = {}
    for page in pages:
        for column in page.columns:
            for match in READING_GROUP_HEADER.finditer(column):
                start = int(match.group("start"))
                end = int(match.group("end"))
                if not (131 <= start <= end <= 200):
                    continue
                range_tokens = [
                    token
                    for token in page.tokens
                    if str(start) in token.text
                    and str(end) in token.text
                    and abs(token.top / page.height) < 0.35
                ]
                questions_tokens = [
                    token
                    for token in page.tokens
                    if token.text.lower().startswith("question")
                    and token.top / page.height < 0.35
                ]
                anchor = min(
                    range_tokens or questions_tokens,
                    key=lambda token: (token.top, token.left),
                    default=None,
                )
                if anchor is None:
                    top, bottom = 0.035, 0.075
                else:
                    top = max(0.0, anchor.top / page.height - 0.004)
                    bottom = min(
                        1.0, (anchor.top + anchor.height) / page.height + 0.008
                    )
                header_tokens = (
                    sorted(
                        [
                            token
                            for token in page.tokens
                            if anchor is not None
                            and abs(token.top - anchor.top) <= 30
                        ],
                        key=lambda token: token.left,
                    )
                    if anchor is not None
                    else []
                )
                header_line = " ".join(
                    token.text for token in header_tokens if token.text != "|"
                )
                header_line = re.sub(r"\banc\s+1?\s*notice\b", "and notice", header_line)
                header_line = re.sub(
                    r"\be-mc\s+iil\b", "e-mail", header_line, flags=re.IGNORECASE
                )
                header_line = re.sub(
                    r"\band\s+etter\b", "and letter", header_line, flags=re.IGNORECASE
                )
                full_description = match.group("description").strip(" .:|")
                description_match = re.search(
                    r"\bfollowing\s+(.+?)(?:\.\s*)?$",
                    header_line,
                    re.IGNORECASE,
                )
                if description_match:
                    full_description = description_match.group(1).strip(" .:|")
                title = (
                    f"Questions {start}-{end} refer to the following "
                    f"{full_description}."
                )
                headers.setdefault(
                    (start, end),
                    ReadingHeader(
                        start=start,
                        end=end,
                        page=page.number,
                        top=top,
                        bottom=bottom,
                        description=full_description,
                        title=title,
                    ),
                )
    return sorted(headers.values(), key=lambda item: (item.page, item.start))


def _reading_detected_range(
    pages: list[PageResult],
    *,
    content_start_page: int,
    detected_numbers: set[int],
) -> tuple[int, int] | None:
    """Infer the real Reading span without silently expanding a partial PDF.

    A full Reading test is 101–200, but a teacher may upload only Part 6,
    Part 7, or a custom 60-question slice.  Question markers are strongest
    evidence; printed ``Questions X-Y`` headers are the next best source when
    OCR loses the individual blank number.  Only a document with unmistakable
    full-test evidence is allowed to fall back to 101–200 elsewhere.
    """
    retained = [page for page in pages if page.number >= content_start_page]
    headers = _reading_headers(retained)
    valid = sorted(
        number for number in detected_numbers if 101 <= number <= 200
    )
    if headers:
        start = min(header.start for header in headers)
        end = max(header.end for header in headers)
        if valid:
            start = min(start, valid[0])
            end = max(end, valid[-1])
        return start, end
    if valid:
        if valid[0] >= 101 and valid[-1] <= 130:
            return 101, 130
        if valid[0] >= 131 and valid[-1] <= 146:
            return 131, 146
        return valid[0], valid[-1]

    text = "\n".join(
        column
        for page in retained
        for column in page.columns
    )
    has_part_5 = re.search(r"\bPART\s*5\b", text, re.IGNORECASE) is not None
    has_part_6 = re.search(r"\bPART\s*6\b", text, re.IGNORECASE) is not None
    has_part_7 = re.search(r"\bPART\s*7\b", text, re.IGNORECASE) is not None
    if has_part_5 and has_part_6 and has_part_7:
        return 101, 200
    if has_part_6 and not has_part_7:
        return 131, 146
    if has_part_7 and not has_part_6:
        return 147, 200
    return None


def _listening_detected_range(
    pages: list[PageResult],
    *,
    content_start_page: int,
    detected_numbers: set[int],
) -> tuple[int, int] | None:
    """Infer a Listening Part span without fabricating 1–100 questions."""
    retained = [page for page in pages if page.number >= content_start_page]
    valid = sorted(number for number in detected_numbers if 1 <= number <= 100)
    if valid:
        if valid[0] <= 6 and valid[-1] <= 6:
            return 1, 6
        if 7 <= valid[0] <= valid[-1] <= 31:
            return 7, 31
        if 32 <= valid[0] <= valid[-1] <= 70:
            return 32, 70
        if 71 <= valid[0] <= valid[-1] <= 100:
            return 71, 100
        return valid[0], valid[-1]

    text = "\n".join(column for page in retained for column in page.columns)
    parts = {
        part
        for part in range(1, 5)
        if re.search(rf"\bPART\s*{part}\b", text, re.IGNORECASE)
    }
    ranges = {1: (1, 6), 2: (7, 31), 3: (32, 70), 4: (71, 100)}
    if not parts:
        return None
    return min(ranges[part][0] for part in parts), max(ranges[part][1] for part in parts)


def _reading_headers_with_layout_fallback(
    pages: list[PageResult],
    questions: list[dict[str, Any]],
) -> list[ReadingHeader]:
    """Supplement unread scan headers from question-page layout.

    The fallback is used only for ranges not covered by a real OCR header. A
    photographed TOEIC booklet places one contiguous question group on its
    question page; when a source-only page precedes it, that page begins right
    after the previous group's question page. This recovers crop association
    without guessing passage text or changing the clean-PDF path.
    """
    headers = _reading_headers(pages)
    by_page: dict[int, list[int]] = {}
    for question in questions:
        number = int(question.get("number", 0))
        page_number = int(question.get("_page", 0) or 0)
        if number >= 131 and page_number > 0:
            by_page.setdefault(page_number, []).append(number)
    question_pages = sorted(by_page)

    def consecutive_runs(numbers: list[int]) -> list[list[int]]:
        runs: list[list[int]] = []
        for number in sorted(set(numbers)):
            if not runs or number != runs[-1][-1] + 1:
                runs.append([number])
            else:
                runs[-1].append(number)
        return runs

    for page_number in question_pages:
        for run in consecutive_runs(by_page[page_number]):
            start, end = run[0], run[-1]
            if any(
                header.start <= start <= end <= header.end
                for header in headers
            ):
                continue
            source_page = page_number
            if start >= 147:
                previous_question_page = max(
                    (page for page in question_pages if page < page_number),
                    default=page_number - 1,
                )
                source_page = min(page_number, previous_question_page + 1)
            description = "document" if start < 176 else "documents"
            headers.append(
                ReadingHeader(
                    start=start,
                    end=end,
                    page=source_page,
                    top=0.025,
                    bottom=0.075,
                    description=description,
                    title=(
                        f"Questions {start}-{end} refer to the following "
                        f"{description}."
                    ),
                )
            )
    return sorted(headers, key=lambda item: (item.page, item.start))


def _expected_document_count(header: ReadingHeader) -> int:
    """Infer the real document count before using TOEIC range fallbacks.

    Newer TOEIC books usually reserve 186-200 for triple passages, but older
    and custom books can still put only an e-mail and a notice in 186-190. The
    printed header is stronger evidence than the question-number range.
    """
    description = (
        header.description.lower().replace("–", "-").replace("—", "-")
    )
    if re.search(r"\b(?:three|triple)\b", description):
        return 3
    if re.search(r"\b(?:two|double)\b", description):
        return 2
    document_kinds = re.findall(
        r"\b(?:"
        r"e[\s-]?mails?|letters?|articles?|notices?|advertisements?|ads?|"
        r"text\s+messages?|messages?|memos?|forms?|schedules?|invoices?|"
        r"receipts?|web\s*pages?|websites?|charts?|tables?|coupons?|"
        r"brochures?|itinerar(?:y|ies)|plans?|reviews?|announcements?|"
        r"reports?"
        r")\b",
        description,
    )
    if len(document_kinds) >= 2:
        return min(3, len(document_kinds))
    if header.start >= 186:
        return 3
    if header.start >= 176:
        return 2
    return 1


def _trim_and_split_bboxes(
    *,
    job_dir: Path,
    page_number: int,
    bbox: tuple[float, float, float, float],
    pieces: int,
) -> tuple[list[tuple[float, float, float, float]], bool]:
    """Trim whitespace/footer noise and split stacked source documents.

    Page numbers and short navigation footers sit far below the passages and
    used to be selected as a third document because they create the largest
    whitespace gap on the page. We remove only small, isolated bottom bands,
    then accept split points only when every resulting segment contains a
    substantial two-dimensional content block.
    """
    if np is None:
        return [bbox], pieces == 1
    page_path = job_dir / "pages" / f"page-{page_number:03d}.jpg"
    if not page_path.is_file():
        return [bbox], pieces == 1
    with Image.open(page_path) as source:
        gray = np.asarray(source.convert("L"))
        page_width, page_height = source.size

    left, top, right, bottom = bbox
    x1, x2 = round(left * page_width), round(right * page_width)
    y1, y2 = round(top * page_height), round(bottom * page_height)
    if x2 <= x1 or y2 <= y1:
        return [], False
    region = gray[y1:y2, x1:x2]
    # A high 230 threshold treats pale reverse-side bleed-through as real ink
    # and erases the whitespace gap between stacked documents in photographed
    # booklets. 210 still retains printed strokes/borders while rejecting that
    # low-contrast background; clean PDF scans are unchanged in practice.
    dark = region < 210
    row_threshold = max(8, round(region.shape[1] * 0.002))
    ink_rows = dark.sum(axis=1) > row_threshold
    ink_indexes = np.flatnonzero(ink_rows)
    if len(ink_indexes) == 0:
        return [], False

    trim_margin = round(page_height * 0.008)
    content_start = max(0, int(ink_indexes[0]) - trim_margin)
    content_end = min(
        region.shape[0], int(ink_indexes[-1]) + trim_margin + 1
    )

    minimum_gap = max(36, round(page_height * 0.011))

    def whitespace_gaps(start: int, end: int) -> list[tuple[int, int]]:
        gaps: list[tuple[int, int]] = []
        gap_start: int | None = None
        for index in range(start, end):
            if not ink_rows[index] and gap_start is None:
                gap_start = index
            elif ink_rows[index] and gap_start is not None:
                if index - gap_start >= minimum_gap:
                    gaps.append((gap_start, index))
                gap_start = None
        if gap_start is not None and end - gap_start >= minimum_gap:
            gaps.append((gap_start, end))
        return gaps

    # Remove page numbers such as "42"/"44" and short footer instructions.
    # Re-run so a footer plus a page number can both be discarded safely.
    for _ in range(3):
        trailing_gaps = whitespace_gaps(content_start, content_end)
        if not trailing_gaps:
            break
        gap_start, gap_end = trailing_gaps[-1]
        trailing_rows = np.flatnonzero(ink_rows[gap_end:content_end])
        if len(trailing_rows) == 0:
            break
        local_top = gap_end + int(trailing_rows[0])
        local_bottom = gap_end + int(trailing_rows[-1]) + 1
        trailing_mask = dark[local_top:local_bottom]
        trailing_columns = np.flatnonzero(trailing_mask.sum(axis=0) > 0)
        if len(trailing_columns) == 0:
            break
        trailing_width = int(trailing_columns[-1] - trailing_columns[0] + 1)
        trailing_height = local_bottom - local_top
        trailing_ink = int(trailing_mask.sum())
        preceding_ink = int(dark[content_start:gap_start].sum())
        absolute_top = (y1 + local_top) / page_height
        looks_like_footer = (
            absolute_top >= 0.82
            and gap_end - gap_start >= max(minimum_gap, round(page_height * 0.03))
            and trailing_height <= max(24, round(page_height * 0.04))
            and (
                trailing_width <= region.shape[1] * 0.38
                or (
                    absolute_top >= 0.88
                    and trailing_ink <= max(160, preceding_ink * 0.025)
                )
            )
        )
        if not looks_like_footer:
            break
        preceding_rows = np.flatnonzero(ink_rows[content_start:gap_start])
        if len(preceding_rows) == 0:
            break
        content_end = min(
            gap_start,
            content_start + int(preceding_rows[-1]) + trim_margin + 1,
        )

    split_points: list[int] = []
    if pieces > 1:
        internal_gaps = [
            gap
            for gap in whitespace_gaps(content_start, content_end)
            if gap[0] > content_start + minimum_gap
            and gap[1] < content_end - minimum_gap
        ]
        total_ink = max(1, int(dark[content_start:content_end].sum()))
        for requested_splits in range(pieces - 1, 0, -1):
            best: tuple[float, list[int]] | None = None
            for selected_gaps in combinations(internal_gaps, requested_splits):
                points = sorted((start + end) // 2 for start, end in selected_gaps)
                candidate_boundaries = [content_start, *points, content_end]
                valid = True
                segment_masses: list[int] = []
                for segment_start, segment_end in zip(
                    candidate_boundaries, candidate_boundaries[1:]
                ):
                    segment_mask = dark[segment_start:segment_end]
                    segment_rows = np.flatnonzero(
                        segment_mask.sum(axis=1) > row_threshold
                    )
                    segment_columns = np.flatnonzero(segment_mask.sum(axis=0) > 0)
                    segment_ink = int(segment_mask.sum())
                    if (
                        len(segment_rows) == 0
                        or len(segment_columns) == 0
                        or int(segment_rows[-1] - segment_rows[0] + 1)
                        < max(18, round(page_height * 0.018))
                        or int(segment_columns[-1] - segment_columns[0] + 1)
                        < region.shape[1] * 0.10
                        or segment_ink < total_ink * 0.012
                    ):
                        valid = False
                        break
                    segment_masses.append(segment_ink)
                if not valid:
                    continue
                gap_score = sum(end - start for start, end in selected_gaps)
                balance = min(segment_masses) / max(segment_masses)
                score = float(gap_score) + balance * minimum_gap
                if best is None or score > best[0]:
                    best = (score, points)
            if best is not None:
                split_points = best[1]
                break

    successful = len(split_points) == pieces - 1
    boundaries = [content_start, *split_points, content_end]
    boxes: list[tuple[float, float, float, float]] = []

    def significant_columns(
        segment_mask: Any, threshold: int
    ) -> Any:
        """Ignore distant scanner/page-edge lines without trimming document borders."""
        profile = segment_mask.sum(axis=0)
        active = np.flatnonzero(profile > threshold)
        if len(active) < 2:
            return active
        bridge = max(8, round(segment_mask.shape[1] * 0.02))
        run_starts = [0]
        run_ends: list[int] = []
        for index in range(1, len(active)):
            if int(active[index] - active[index - 1]) > bridge:
                run_ends.append(index)
                run_starts.append(index)
        run_ends.append(len(active))
        total_mass = max(1, int(profile[active].sum()))
        core_runs: list[tuple[int, int]] = []
        for start_index, end_index in zip(run_starts, run_ends):
            run = active[start_index:end_index]
            run_left, run_right = int(run[0]), int(run[-1])
            run_mass = int(profile[run].sum())
            if (
                run_right - run_left + 1 >= segment_mask.shape[1] * 0.04
                or run_mass >= total_mass * 0.06
            ):
                core_runs.append((run_left, run_right))
        if not core_runs:
            return active
        allowance = round(segment_mask.shape[1] * 0.04)
        core_left = max(0, min(run[0] for run in core_runs) - allowance)
        core_right = min(
            segment_mask.shape[1] - 1,
            max(run[1] for run in core_runs) + allowance,
        )
        relevant = active[(active >= core_left) & (active <= core_right)]
        return relevant if len(relevant) else active

    for segment_start, segment_end in zip(boundaries, boundaries[1:]):
        segment = dark[segment_start:segment_end]
        segment_rows = np.flatnonzero(segment.sum(axis=1) > row_threshold)
        if len(segment_rows) == 0:
            continue
        local_top = segment_start + int(segment_rows[0])
        local_bottom = segment_start + int(segment_rows[-1]) + 1
        column_threshold = max(6, round((local_bottom - local_top) * 0.002))
        ink_columns = significant_columns(
            dark[local_top:local_bottom], column_threshold
        )
        if len(ink_columns) == 0:
            local_left, local_right = 0, region.shape[1]
        else:
            margin_x = round(page_width * 0.012)
            local_left = max(0, int(ink_columns[0]) - margin_x)
            local_right = min(
                region.shape[1], int(ink_columns[-1]) + margin_x + 1
            )
        margin_y = round(page_height * 0.008)
        local_top = max(content_start, local_top - margin_y)
        local_bottom = min(content_end, local_bottom + margin_y)
        boxes.append(
            (
                (x1 + local_left) / page_width,
                (y1 + local_top) / page_height,
                (x1 + local_right) / page_width,
                (y1 + local_bottom) / page_height,
            )
        )
    return boxes, successful and len(boxes) == pieces


def _dominant_content_bbox(
    *,
    job_dir: Path,
    page_number: int,
    bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Fit a coarse crop to its dominant photo and ignore isolated labels."""
    if np is None:
        return bbox
    page_path = job_dir / "pages" / f"page-{page_number:03d}.jpg"
    if not page_path.is_file():
        return bbox
    with Image.open(page_path) as source:
        gray = np.asarray(source.convert("L"))
        page_width, page_height = source.size
    left, top, right, bottom = bbox
    x1, x2 = round(left * page_width), round(right * page_width)
    y1, y2 = round(top * page_height), round(bottom * page_height)
    region = gray[y1:y2, x1:x2]
    if region.size == 0:
        return bbox
    # A photographed sheet is commonly light gray (and may contain mirrored
    # bleed-through).  Treating every pixel below 245 as foreground connects
    # the photo to the next label/footer.  A dark-ink threshold still retains
    # enough texture to locate both grayscale and clean PDF photos while
    # ignoring the paper background; the original pixels are preserved in the
    # saved crop.
    dark = region < 200

    def active_runs(active: Any, bridge: int) -> list[Any]:
        if len(active) == 0:
            return []
        starts = [0]
        ends: list[int] = []
        for index in range(1, len(active)):
            if int(active[index] - active[index - 1]) > bridge:
                ends.append(index)
                starts.append(index)
        ends.append(len(active))
        return [active[start:end] for start, end in zip(starts, ends)]

    row_active = np.flatnonzero(
        dark.sum(axis=1) > max(8, region.shape[1] * 0.025)
    )
    row_runs = [
        run
        for run in active_runs(
            row_active,
            max(3, round(region.shape[0] * 0.008)),
        )
        if int(run[-1] - run[0] + 1) >= region.shape[0] * 0.08
    ]
    if not row_runs:
        return bbox

    rows = max(
        row_runs,
        key=lambda run: int(dark[int(run[0]) : int(run[-1]) + 1].sum())
        * int(run[-1] - run[0] + 1),
    )
    local_y = int(rows[0])
    bottom = int(rows[-1]) + 1
    row_height = bottom - local_y
    column_active = np.flatnonzero(
        dark[local_y:bottom].sum(axis=0) > max(6, row_height * 0.018)
    )
    column_runs = [
        run
        for run in active_runs(
            column_active,
            max(4, round(region.shape[1] * 0.012)),
        )
        if int(run[-1] - run[0] + 1) >= region.shape[1] * 0.15
    ]
    if not column_runs:
        return bbox
    columns = max(
        column_runs,
        key=lambda run: int(
            dark[
                local_y:bottom,
                int(run[0]) : int(run[-1]) + 1,
            ].sum()
        )
        * int(run[-1] - run[0] + 1),
    )
    local_x = int(columns[0])
    right_edge = int(columns[-1]) + 1
    margin_x = round(page_width * 0.006)
    margin_y = round(page_height * 0.006)
    return (
        max(0, x1 + local_x - margin_x) / page_width,
        max(0, y1 + local_y - margin_y) / page_height,
        min(page_width, x1 + right_edge + margin_x) / page_width,
        min(page_height, y1 + bottom + margin_y) / page_height,
    )


def _listening_photo_coarse_bbox(
    page: PageResult | None,
    number: int,
    fallback: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Bound a Part 1 photo before fitting its visual content.

    Printed/photographed booklets have much less stable vertical spacing than
    exported PDFs.  The next printed question number is a safer lower bound
    for the upper photo, while the page-turn footer is a safer lower bound for
    the bottom photo.  The dominant-content fit can then ignore the small
    number label without ever seeing the neighbouring photo/footer.
    """
    if page is None or page.height <= 0:
        return fallback
    left, top, right, bottom = fallback
    current_top = _question_top(page, [number])
    if current_top is not None:
        top = max(0.01, min(top, current_top - 0.008))

    if number % 2 == 1:
        next_top = _question_top(page, [number + 1])
        if next_top is not None and next_top > top + 0.08:
            bottom = min(bottom, next_top - 0.012)
    else:
        footer_tops = [
            token.top / page.height
            for token in page.tokens
            if token.top / page.height > 0.72
            and re.search(
                r"GO\s+ON\s+TO\s+THE\s+NEXT\s+PAGE|NEXT\s+PAGE",
                token.text,
                re.IGNORECASE,
            )
        ]
        if footer_tops:
            bottom = min(bottom, min(footer_tops) - 0.012)
    if bottom <= top + 0.08:
        return fallback
    return left, top, right, bottom


def _build_stimuli(
    *,
    job_id: str,
    job_dir: Path,
    exam_type: str,
    pages: list[PageResult],
    questions: list[dict[str, Any]],
    content_offset: int = 0,
) -> tuple[list[dict[str, Any]], list[Issue]]:
    stimuli: list[dict[str, Any]] = []
    issues: list[Issue] = []

    def add(
        stimulus_id: str,
        page_number: int,
        bbox: tuple[float, float, float, float],
        question_numbers: list[int],
        *,
        lossless: bool,
        title: str = "",
        confidence: float = 100.0,
        stimulus_issues: list[str] | None = None,
    ) -> None:
        asset = _save_crop(
            job_id=job_id,
            job_dir=job_dir,
            page_number=page_number,
            bbox=bbox,
            lossless=lossless,
        )
        stimulus = Stimulus(
            id=stimulus_id,
            title=title,
            assets=[asset],
            question_numbers=question_numbers,
            page_numbers=[page_number],
            confidence=confidence,
            issues=stimulus_issues or [],
        ).model_dump()
        existing = next(
            (item for item in stimuli if item["id"] == stimulus_id), None
        )
        if existing:
            existing["assets"].append(asset)
            existing["page_numbers"] = sorted(
                set(existing["page_numbers"] + [page_number])
            )
            existing["question_numbers"] = sorted(
                set(existing["question_numbers"] + question_numbers)
            )
            existing["confidence"] = min(existing["confidence"], confidence)
            existing["issues"] = sorted(
                set(existing["issues"] + (stimulus_issues or []))
            )
            if title:
                existing["title"] = title
        else:
            stimuli.append(stimulus)
        for question in questions:
            if question["number"] in question_numbers:
                question["stimulus_id"] = stimulus_id
                question["group_id"] = stimulus_id

    if exam_type == "listening":
        q_numbers_set = {q["number"] for q in questions}
        page_by_number = {page.number: page for page in pages}
        photo_boxes = (
            (0.06, 0.035, 0.94, 0.47),
            (0.06, 0.455, 0.94, 0.91),
        )
        for number in range(1, 7):
            if number not in q_numbers_set:
                continue
            page_number = math.ceil(number / 2) + content_offset
            page_path = job_dir / "pages" / f"page-{page_number:03d}.jpg"
            if not page_path.is_file():
                continue
            coarse_bbox = _listening_photo_coarse_bbox(
                page_by_number.get(page_number),
                number,
                photo_boxes[(number - 1) % 2],
            )
            bbox = _dominant_content_bbox(
                job_dir=job_dir,
                page_number=page_number,
                bbox=coarse_bbox,
            )
            add(
                f"listening-photo-{number}",
                page_number,
                bbox,
                [number],
                lossless=False,
            )
        graphic_specs = {
            "listening-62-64": (7 + content_offset, (0.56, 0.05, 0.88, 0.24), [62, 63, 64]),
            "listening-65-67": (8 + content_offset, (0.05, 0.04, 0.50, 0.255), [65, 66, 67]),
            "listening-68-70": (8 + content_offset, (0.55, 0.04, 0.86, 0.19), [68, 69, 70]),
            "listening-95-97": (11 + content_offset, (0.05, 0.04, 0.50, 0.195), [95, 96, 97]),
            "listening-98-100": (11 + content_offset, (0.505, 0.04, 0.90, 0.285), [98, 99, 100]),
        }
        for stimulus_id, (page_number, bbox, numbers) in graphic_specs.items():
            if not any(n in q_numbers_set for n in numbers):
                continue
            page_path = job_dir / "pages" / f"page-{page_number:03d}.jpg"
            if not page_path.is_file():
                continue
            page = page_by_number.get(page_number)
            question_top = _question_top(page, numbers) if page is not None else None
            if question_top is not None and question_top > 0.09:
                # Group/page/column is stable in TOEIC Parts 3/4, but the
                # illustration width and height are not. Use the entire
                # physical column up to the semantic question boundary and let
                # ink trimming fit the actual Test 1/Test 2 graphic.
                is_right = bbox[0] >= 0.5
                bbox = (
                    0.49 if is_right else 0.03,
                    0.025,
                    0.97 if is_right else 0.51,
                    max(0.085, question_top - 0.015),
                )
            fitted_boxes, _exact = _trim_and_split_bboxes(
                job_dir=job_dir,
                page_number=page_number,
                bbox=bbox,
                pieces=1,
            )
            fitted_bbox = fitted_boxes[0] if fitted_boxes else bbox
            add(
                stimulus_id,
                page_number,
                fitted_bbox,
                numbers,
                lossless=True,
            )
        return stimuli, issues

    page_by_number = {page.number: page for page in pages}
    headers = _reading_headers_with_layout_fallback(pages, questions)
    if headers:
        for header_index, header in enumerate(headers):
            numbers = list(range(header.start, header.end + 1))
            question_pages = sorted(
                {
                    int(question["_page"])
                    for question in questions
                    if question["number"] in numbers and question.get("_page")
                }
            )
            next_header_page = (
                headers[header_index + 1].page
                if header_index + 1 < len(headers)
                else max(page_by_number)
            )
            detected_question_pages = [
                page_number
                for page_number in range(header.page, next_header_page + 1)
                if (
                    page_by_number.get(page_number) is not None
                    and _question_top(page_by_number[page_number], numbers) is not None
                )
            ]
            question_page = min(
                (
                    page
                    for page in [*detected_question_pages, *question_pages]
                    if page >= header.page
                ),
                default=header.page,
            )
            question_page = min(question_page, next_header_page)
            zones: list[tuple[int, tuple[float, float, float, float]]] = []
            for page_number in range(header.page, question_page + 1):
                page = page_by_number.get(page_number)
                if page is None or not (job_dir / "pages" / f"page-{page_number:03d}.jpg").is_file():
                    continue
                zone_top = header.bottom + 0.01 if page_number == header.page else 0.035
                zone_bottom = 0.92
                if page_number == question_page:
                    content_boundary = (
                        _first_option_top(page, numbers)
                        if header.start <= 146
                        else _question_top(page, numbers)
                    )
                    if content_boundary is not None:
                        zone_bottom = min(zone_bottom, content_boundary - 0.018)
                    elif page_number == header.page:
                        zone_bottom = 0.76
                if page_number == question_page and page_number != header.page:
                    # Some triple-passage layouts put the final document above
                    # the questions on the following page.  Keep that region
                    # only when the source image contains a real content block;
                    # a mostly blank question page must not become a stimulus.
                    candidate_zone = (0.045, zone_top, 0.92, zone_bottom)
                    candidate_boxes, _exact = _trim_and_split_bboxes(
                        job_dir=job_dir,
                        page_number=page_number,
                        bbox=candidate_zone,
                        pieces=1,
                    )
                    if not candidate_boxes:
                        continue
                # A following page that begins immediately with questions has no
                # source document content and must not become a stimulus asset.
                if zone_bottom - zone_top >= 0.075:
                    zones.append(
                        (page_number, (0.045, zone_top, 0.92, zone_bottom))
                    )

            expected_assets = _expected_document_count(header)
            # A header such as "e-mails and notice" names two document kinds,
            # but its plural e-mails can represent two separate source blocks.
            # If a following page has independently passed the content-zone
            # check, preserve both e-mails on the header page plus that later
            # document. This is layout evidence, not a numeric-range guess.
            if (
                len(zones) >= 2
                and re.search(r"\be[\s-]?mails\b", header.description, re.IGNORECASE)
            ):
                expected_assets = max(expected_assets, len(zones) + 1)
            stimulus_id = f"reading-{header.start}-{header.end}"
            stimulus_issues: list[str] = []
            confidence = 100.0
            if not zones:
                zones = [(header.page, (0.045, header.bottom, 0.92, 0.88))]
                stimulus_issues.append("crop_review")
                confidence = 45.0

            allocations = [1] * len(zones)
            for _ in range(max(0, expected_assets - len(zones))):
                allocations[0] += 1

            created = 0
            split_ok = True
            for (page_number, zone), pieces in zip(zones, allocations):
                boxes, page_split_ok = _trim_and_split_bboxes(
                    job_dir=job_dir,
                    page_number=page_number,
                    bbox=zone,
                    pieces=pieces,
                )
                if not boxes:
                    boxes = [zone]
                    page_split_ok = False
                split_ok = split_ok and page_split_ok
                for bbox in boxes:
                    add(
                        stimulus_id,
                        page_number,
                        bbox,
                        numbers,
                        lossless=False,
                        title=header.title,
                        confidence=confidence,
                        stimulus_issues=stimulus_issues,
                    )
                    created += 1

            if not split_ok or created != expected_assets:
                stimulus = next(
                    item for item in stimuli if item["id"] == stimulus_id
                )
                stimulus["confidence"] = min(stimulus["confidence"], 55.0)
                stimulus["issues"] = sorted(
                    set(stimulus["issues"] + ["crop_review"])
                )
                issues.append(
                    Issue(
                        code="crop_review",
                        message=(
                            f"Nhóm {header.start}-{header.end} cần kiểm tra: "
                            f"đã cắt {created}/{expected_assets} tài liệu."
                        ),
                        page=header.page,
                    )
                )
        return stimuli, issues

    # Compatibility fallback for unusual booklets whose range header could not
    # be read. It deliberately keeps a broad crop and marks uncertain pages.
    for page_number in sorted(page_by_number):
        page_questions = [
            q["number"]
            for q in questions
            if q.get("_page") == page_number and q["number"] >= 131
        ]
        if not page_questions or not (job_dir / "pages" / f"page-{page_number:03d}.jpg").is_file():
            continue
        start, end = min(page_questions), max(page_questions)
        top_of_questions = _question_top(page_by_number[page_number], page_questions)
        confidence = 100.0
        stimulus_issues: list[str] = []
        if top_of_questions is None or top_of_questions < 0.35:
            top_of_questions = 0.76
            confidence = 55.0
            stimulus_issues.append("crop_review")
            issues.append(
                Issue(
                    code="crop_review",
                    message=f"Cần kiểm tra lại vùng passage trang {page_number}.",
                    page=page_number,
                )
            )
        bottom = max(0.38, min(0.88, top_of_questions - 0.018))
        add(
            f"reading-{start}-{end}",
            page_number,
            (0.045, 0.035, 0.955, bottom),
            list(range(start, end + 1)),
            lossless=False,
            confidence=confidence,
            stimulus_issues=stimulus_issues,
        )
    return stimuli, issues


def _listening_prefix(
    content_offset: int = 0, existing_numbers: set[int] | None = None
) -> list[dict[str, Any]]:
    existing = existing_numbers or set()
    # Do not manufacture Part 1/2 placeholders when a standalone Part 3/4
    # file starts at 32/71. For a partial upload the OCR span itself is the
    # authoritative range; a complete 1–100 upload naturally remains 1–100.
    fill_range = (
        range(min(existing), max(existing) + 1)
        if existing
        else range(1, 32)
    )

    questions: list[dict[str, Any]] = []
    for number in fill_range:
        if number not in existing:
            letters = _option_letters("listening", number)
            question = Question(
                number=number,
                part=_part("listening", number),
                text="",
                options={letter: "" for letter in letters},
                option_letters=letters,
                group_id=_group_id("listening", number),
                confidence=100.0,
            ).model_dump()
            question["_page"] = (math.ceil(number / 2) + content_offset) if number <= 6 else (4 + content_offset)
            questions.append(question)
    return questions


def _needs_scan_recovery(item: dict[str, Any], exam_type: str) -> bool:
    """Whether a parsed question is incomplete enough to justify a second OCR."""
    number = int(item.get("number", 0))
    # Listening Part 1/2 intentionally contains blank question text and often
    # no printed choices, so option completeness begins at Part 3.
    if exam_type == "listening" and number < 32:
        return False
    if "question_missing" in item.get("issues", []):
        return True
    if not (exam_type == "reading" and 131 <= number <= 146) and not _question_text_is_usable(
        str(item.get("text") or "")
    ):
        return True
    expected_options = _option_letters(exam_type, number)
    return any(letter not in item.get("options", {}) for letter in expected_options)


def _needs_sentence_punctuation_recovery(item: dict[str, Any]) -> bool:
    """Identify a likely lost final full stop without guessing its presence.

    This only schedules a compact OCR crop. The merge later accepts a full stop
    only when that crop independently reads the same option words followed by
    punctuation. It is deliberately one candidate per page and never invents
    punctuation from grammar alone.
    """
    number = int(item.get("number", 0))
    if number < 147:
        return False
    options = item.get("options") or {}
    values = [str(options.get(letter) or "").strip() for letter in "ABCD"]
    if any(not value or re.search(r"[.!?][\"'”’)]?$", value) for value in values):
        return False
    # Noun-phrase choices are legitimately unpunctuated. This merely finds a
    # page where all four choices have the shape of complete sentence text.
    return all(
        len(re.findall(r"[A-Za-z0-9]+", value)) >= 4
        and bool(re.match(r"^[A-Z]", value))
        for value in values
    )


def _question_text_is_usable(value: str) -> bool:
    text = str(value or "").strip()
    # OCR can collapse spaces on curved scans (for example
    # ``Why willthefactoryberenovated?``). Character coverage is the reliable
    # signal here; a one-letter/noise fragment is not.
    return len(re.sub(r"[^A-Za-z0-9]", "", text)) >= 12


def _option_marker_token(token: OCRToken, letter: str) -> bool:
    text = token.text.translate(SPATIAL_MARKER_TRANSLATION)
    return (
        re.match(
            rf"^[ \t]*(?:[\(\[\{{][ \t]*{letter}[ \t]*[\)\]\}}]|{letter}[ \t]*[\.\)])[ \t]*",
            text,
            re.IGNORECASE,
        )
        is not None
    )


def _option_marker_question_block_rois(
    page: PageResult,
    page_numbers: Iterable[int],
) -> dict[int, ReadingROI]:
    """Map printed A-D blocks to question numbers in page reading order.

    Poor photographed pages often lose a curved question line while still
    detecting every A and D choice. TOEIC pages read down the left column and
    then down the right, so a complete set of A markers is a stronger local
    layout anchor than another full-page OCR attempt.
    """
    numbers = sorted(set(int(number) for number in page_numbers))
    if not numbers:
        return {}
    # Tesseract also detects the `(A) (B) (C) (D)` legend in Listening
    # directions. That legend sits above the first real question and used to
    # make the anchor count 13 for a 12-question page, disabling recovery.
    # Gate markers below the first printed question number. If every question
    # number was lost, keep the old ungated behavior and let the exact-anchor
    # check prevent speculative crops.
    expected = set(numbers)
    question_number_tops: list[int] = []
    for token in page.tokens:
        text = token.text.translate(SPATIAL_MARKER_TRANSLATION).strip()
        match = NUMBER_TOKEN.match(text) or QUESTION_START.match(text)
        if match is None:
            continue
        raw_number = match.group(1) if match.lastindex else None
        normalized = _normalize_number(raw_number or "")
        if normalized in expected:
            question_number_tops.append(token.top)
    marker_top_floor = (
        max(0, min(question_number_tops) - max(4, round(page.height * 0.006)))
        if question_number_tops
        else 0
    )
    a_markers: list[tuple[int, OCRToken]] = []
    d_markers: dict[int, list[OCRToken]] = {0: [], 1: []}
    for token in page.tokens:
        if token.top < marker_top_floor:
            continue
        column = 0 if token.left + token.width / 2 < page.width / 2 else 1
        if _option_marker_token(token, "A"):
            a_markers.append((column, token))
        if _option_marker_token(token, "D"):
            d_markers[column].append(token)

    # Remote OCR may return the same marker twice around a tile seam. Collapse
    # only near-identical vertical positions; real questions are much farther
    # apart than this tolerance.
    ordered_a: list[tuple[int, OCRToken]] = []
    tolerance = max(6, round(page.height * 0.004))
    for column, token in sorted(a_markers, key=lambda item: (item[0], item[1].top)):
        if (
            ordered_a
            and ordered_a[-1][0] == column
            and abs(ordered_a[-1][1].top - token.top) <= tolerance
        ):
            if token.confidence > ordered_a[-1][1].confidence:
                ordered_a[-1] = (column, token)
            continue
        ordered_a.append((column, token))
    for column in d_markers:
        d_markers[column].sort(key=lambda token: token.top)
    ordered_d = [
        (column, token)
        for column in (0, 1)
        for token in d_markers[column]
    ]
    use_d_anchors = len(ordered_a) != len(numbers) and len(ordered_d) == len(numbers)
    if len(ordered_a) != len(numbers) and not use_d_anchors:
        return {}
    anchors = ordered_d if use_d_anchors else ordered_a
    rois: dict[int, ReadingROI] = {}
    for index, (number, (column, marker_a)) in enumerate(zip(numbers, anchors)):
        if use_d_anchors:
            previous_d = anchors[index - 1] if index else None
            top = (
                max(
                    0.0,
                    (previous_d[1].top + previous_d[1].height) / page.height
                    + 0.002,
                )
                if previous_d is not None and previous_d[0] == column
                else max(0.0, marker_a.top / page.height - 0.10)
            )
            bottom = min(
                0.98,
                (marker_a.top + marker_a.height) / page.height + 0.008,
            )
            left, right = ((0.045, 0.525) if column == 0 else (0.49, 0.965))
            rois[number] = ReadingROI(
                (left, top, right, bottom),
                "scan_question_block_d_anchor",
                column=column,
            )
            continue

        previous_a = anchors[index - 1] if index else None
        next_a = anchors[index + 1] if index + 1 < len(anchors) else None
        current_top = marker_a.top / page.height
        previous_ds = [
            token
            for token in d_markers[column]
            if token.top + token.height <= marker_a.top + tolerance
            and (
                previous_a is None
                or previous_a[0] != column
                or token.top >= previous_a[1].top
            )
        ]
        if previous_ds:
            top = max(
                0.0,
                (previous_ds[-1].top + previous_ds[-1].height) / page.height
                + 0.002,
            )
        else:
            top = max(0.0, current_top - 0.075)

        next_top = (
            next_a[1].top / page.height
            if next_a is not None and next_a[0] == column
            else 0.96
        )
        following_ds = [
            token
            for token in d_markers[column]
            if token.top >= marker_a.top - tolerance
            and token.top / page.height < next_top
        ]
        if following_ds:
            bottom = min(
                0.98,
                (following_ds[0].top + following_ds[0].height) / page.height
                + 0.008,
            )
        else:
            bottom = min(0.98, next_top - 0.004)
        if bottom - top < 0.025:
            continue
        # Exclude photographed page edges and the centre gutter; both create
        # high-contrast curves that can dominate a short OCR crop.
        left, right = ((0.045, 0.525) if column == 0 else (0.49, 0.965))
        rois[number] = ReadingROI(
            (left, top, right, bottom),
            "scan_question_block",
            column=column,
        )
    return rois


def _number_anchor_question_block_rois(
    page: PageResult,
    page_numbers: Iterable[int],
) -> dict[int, ReadingROI]:
    """Create question crops from printed question numbers when available.

    A/D markers are useful fallback anchors, but one missing marker used to
    disable recovery for an entire Part 3/4 page. A correctly detected printed
    number is a direct, per-question layout anchor, so it lets recovery repair
    just the damaged block without guessing where adjacent questions begin.
    """
    numbers = sorted(set(int(number) for number in page_numbers))
    if not numbers:
        return {}
    expected = set(numbers)
    tolerance = max(6, round(page.height * 0.004))
    anchors: dict[int, list[tuple[int, OCRToken]]] = {number: [] for number in numbers}
    for token in page.tokens:
        text = token.text.translate(SPATIAL_MARKER_TRANSLATION).strip()
        marker = NUMBER_TOKEN.match(text)
        raw_number: str | None = marker.group(1) if marker is not None else None
        if raw_number is None:
            question_start = QUESTION_START.match(text)
            raw_number = question_start.group("number") if question_start is not None else None
        number = _normalize_number(raw_number or "")
        if number not in expected:
            continue
        column = 0 if token.left + token.width / 2 < page.width / 2 else 1
        anchors[number].append((column, token))

    # A single number can be page furniture/noise. Part 3/4 pages normally
    # carry several numbers, therefore require two independent anchors before
    # trusting this fallback for a multi-question page.
    detected = [number for number in numbers if anchors[number]]
    if len(detected) < min(2, len(numbers)):
        return {}

    selected: dict[int, tuple[int, OCRToken]] = {}
    for number in detected:
        candidates = sorted(
            anchors[number],
            key=lambda item: (-item[1].confidence, item[1].top, item[1].left),
        )
        selected[number] = candidates[0]

    by_column: dict[int, list[tuple[int, OCRToken]]] = {0: [], 1: []}
    for number, (column, token) in selected.items():
        by_column[column].append((number, token))
    rois: dict[int, ReadingROI] = {}
    for column, column_anchors in by_column.items():
        ordered = sorted(column_anchors, key=lambda item: item[1].top)
        for index, (number, token) in enumerate(ordered):
            next_token = ordered[index + 1][1] if index + 1 < len(ordered) else None
            top = max(0.0, token.top / page.height - 0.012)
            bottom = (
                max(top + 0.025, next_token.top / page.height - 0.006)
                if next_token is not None
                else 0.965
            )
            bottom = min(0.98, bottom)
            if bottom - top < 0.025:
                continue
            left, right = ((0.045, 0.525) if column == 0 else (0.49, 0.965))
            rois[number] = ReadingROI(
                (left, top, right, bottom),
                "scan_question_block_number_anchor",
                column=column,
            )
    return rois


def _question_block_rois(
    page: PageResult,
    page_numbers: Iterable[int],
    *,
    use_number_anchors: bool = True,
) -> dict[int, ReadingROI]:
    """Return direct number-anchor crops with marker-based fallback crops."""
    numbers = tuple(page_numbers)
    marker_rois = _option_marker_question_block_rois(page, numbers)
    # A number anchor is stronger than an A/D estimate for that same question.
    if use_number_anchors:
        marker_rois.update(_number_anchor_question_block_rois(page, numbers))
    return marker_rois


def _recover_page_question_blocks(
    page_path: Path,
    page: PageResult,
    *,
    page_numbers: Iterable[int],
    recover_numbers: set[int],
    use_number_anchors: bool = False,
) -> list[dict[str, Any]]:
    rois = _question_block_rois(
        page,
        page_numbers,
        use_number_anchors=use_number_anchors,
    )
    selected = [
        (number, rois[number])
        for number in sorted(recover_numbers)
        if number in rois
    ]
    if not selected or not page_path.is_file():
        return []
    with Image.open(page_path) as source:
        image = source.convert("RGB")
    recovered: list[dict[str, Any]] = []
    try:
        for expected_number, roi in selected:
            result = _ocr_region(
                image,
                page_number=page.number,
                roi=roi,
                scale_limit=1.0,
                normalize_scan=False,
                ocr_config="question-recovery",
            )
            candidates = _candidates_from_page_result(result)
            header_roi: ReadingROI | None = None

            # OCR can detect every option in a tall block but omit its
            # curved question line. A second, very short header crop makes the
            # line dominant and costs far less than another full-page pass.
            option_tops = [
                token.top / page.height
                for token in page.tokens
                if _option_marker_token(token, "A")
                and (0 if token.left + token.width / 2 < page.width / 2 else 1)
                == roi.column
                and roi.bbox[1] <= token.top / page.height <= roi.bbox[3]
            ]
            header_bottom = (
                min(roi.bbox[3], min(option_tops) + 0.020)
                if option_tops
                else min(
                    roi.bbox[3],
                    roi.bbox[1] + max(0.025, (roi.bbox[3] - roi.bbox[1]) * 0.48),
                )
            )
            if header_bottom - roi.bbox[1] >= 0.012:
                header_roi = ReadingROI(
                    (
                        roi.bbox[0],
                        roi.bbox[1],
                        roi.bbox[2],
                        header_bottom,
                    ),
                    "scan_question_header",
                    column=roi.column,
                )
                header_result = _ocr_region(
                    image,
                    page_number=page.number,
                    roi=header_roi,
                    scale_limit=1.0,
                    normalize_scan=False,
                    ocr_config="question-recovery",
                )
                candidates.extend(
                    _candidates_from_page_result(header_result)
                )

            def scoped_candidates() -> list[dict[str, Any]]:
                return [
                    candidate
                    for candidate in candidates
                    if int(candidate.get("number", 0)) in {0, expected_number}
                ]

            def has_question_text() -> bool:
                return any(
                    int(candidate.get("number", 0)) == expected_number
                    and str(candidate.get("text") or "").strip()
                    for candidate in candidates
                )

            def recovered_letters() -> set[str]:
                return {
                    letter
                    for candidate in scoped_candidates()
                    for letter, value in (candidate.get("options") or {}).items()
                    if str(value or "").strip()
                }

            if header_roi is not None and not has_question_text():
                # Illumination varies even inside one photographed page. Try a
                # tiny bounded ensemble only for the missing header; each crop
                # is a few percent of a page and variants stop as soon as text
                # is recovered.
                for threshold in (195, 205, 185):
                    alternate = _ocr_region(
                        image,
                        page_number=page.number,
                        roi=header_roi,
                        scale_limit=1.0,
                        binary_threshold=threshold,
                        ocr_config="question-recovery",
                    )
                    candidates.extend(_candidates_from_page_result(alternate))
                    if has_question_text():
                        break
                if not has_question_text():
                    enlarged = _ocr_region(
                        image,
                        page_number=page.number,
                        roi=header_roi,
                        scale_limit=2.0,
                        ocr_config="question-recovery",
                    )
                    candidates.extend(_candidates_from_page_result(enlarged))

            expected_letters = set(_option_letters(
                "listening" if expected_number <= 100 else "reading",
                expected_number,
            ))
            if not expected_letters.issubset(recovered_letters()):
                for threshold in (190, 200):
                    alternate = _ocr_region(
                        image,
                        page_number=page.number,
                        roi=roi,
                        scale_limit=1.0,
                        binary_threshold=threshold,
                        ocr_config="question-recovery",
                    )
                    candidates.extend(_candidates_from_page_result(alternate))
                    if expected_letters.issubset(recovered_letters()):
                        break
            matching = [
                candidate
                for candidate in candidates
                if int(candidate.get("number", 0)) == expected_number
            ]
            # The local crop is tied to one A-D block. If OCR still loses
            # only the small printed number, infer that number from the block
            # mapping but never infer text or choices.
            usable = [
                candidate
                for candidate in scoped_candidates()
                if candidate.get("options") or str(candidate.get("text") or "").strip()
            ]
            if not matching and not usable:
                continue
            sources = usable
            candidate = dict(
                max(
                    sources,
                    key=lambda item: (
                        len(item.get("options") or {}),
                        len(str(item.get("text") or "")),
                    ),
                ),
                options={},
            )
            candidate["number"] = expected_number
            candidate["raw_number"] = str(expected_number)
            candidate["text"] = max(
                (
                    str(item.get("text") or "")
                    for item in sources
                    if _question_text_is_usable(str(item.get("text") or ""))
                ),
                key=len,
                default="",
            )
            complete_sources = [
                source
                for source in sources
                if all(
                    str((source.get("options") or {}).get(letter) or "").strip()
                    for letter in expected_letters
                )
            ]
            if complete_sources:
                option_source = max(
                    complete_sources,
                    key=lambda item: sum(
                        len(str(value))
                        for value in (item.get("options") or {}).values()
                    ),
                )
                candidate["options"] = dict(option_source.get("options") or {})
            else:
                # Partial threshold variants sometimes shift one line to the
                # neighbouring letter. Require the same normalized value twice
                # before filling an absent choice; returning a review warning
                # is preferable to silently attaching a wrong answer.
                option_votes: dict[str, dict[str, tuple[int, str]]] = {}
                for source in sources:
                    for letter, raw_value in (source.get("options") or {}).items():
                        value = str(raw_value or "").strip()
                        normalized_value = re.sub(r"\W+", "", value).lower()
                        if len(normalized_value) < 2:
                            continue
                        count, _old_value = option_votes.setdefault(
                            letter, {}
                        ).get(normalized_value, (0, value))
                        option_votes[letter][normalized_value] = (count + 1, value)
                used_values: set[str] = set()
                for letter in sorted(expected_letters):
                    votes = option_votes.get(letter, {})
                    if not votes:
                        continue
                    normalized_value, (count, value) = max(
                        votes.items(), key=lambda item: item[1][0]
                    )
                    if count >= 2 and normalized_value not in used_values:
                        candidate["options"][letter] = value
                        used_values.add(normalized_value)
            if not matching:
                candidate["issues"] = sorted(
                    set(candidate.get("issues", []) + ["number_inferred"])
                )
            recovered.append(candidate)
    finally:
        image.close()
    return recovered


def _merge_scan_recovery_candidates(
    existing: list[dict[str, Any]],
    recovered: list[dict[str, Any]],
    *,
    terminal_punctuation_numbers: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Fill only absent fields from a richer local OCR block."""
    punctuation_numbers = terminal_punctuation_numbers or set()

    def normalized_option_words(value: str) -> str:
        return " ".join(re.findall(r"[A-Za-z0-9]+", value)).casefold()

    def terminal_punctuation(value: str) -> str:
        match = re.search(r"([.!?])[\"'”’)]?$", value.strip())
        return match.group(1) if match else ""

    best: dict[int, dict[str, Any]] = {}
    for item in recovered:
        number = int(item.get("number", 0))
        current = best.get(number)
        if current is None or (
            len(item.get("options") or {}), len(str(item.get("text") or ""))
        ) > (
            len(current.get("options") or {}), len(str(current.get("text") or ""))
        ):
            best[number] = item
    result: list[dict[str, Any]] = []
    existing_numbers: set[int] = set()
    for item in existing:
        merged = dict(item, options=dict(item.get("options") or {}))
        number = int(merged.get("number", 0))
        existing_numbers.add(number)
        candidate = best.get(number)
        if candidate is not None:
            if not _question_text_is_usable(str(merged.get("text") or "")):
                merged["text"] = str(candidate.get("text") or "")
            for letter, value in (candidate.get("options") or {}).items():
                existing_value = str(merged["options"].get(letter) or "").strip()
                # A full-page OCR chunk can append the next unnumbered
                # question to the previous D option. A block-tied recovery is
                # safer evidence in that specific shape and may replace it;
                # ordinary non-empty OCR values remain untouched.
                contaminated = "?" in existing_value
                if value and (not existing_value or contaminated):
                    merged["options"][letter] = value
                elif (
                    number in punctuation_numbers
                    and existing_value
                    and not terminal_punctuation(existing_value)
                    and terminal_punctuation(str(value))
                    and normalized_option_words(existing_value)
                    == normalized_option_words(str(value))
                ):
                    # The local crop is accepted only as independent evidence
                    # for the same words plus the missing terminal mark.
                    merged["options"][letter] = str(value).strip()
            merged["confidence"] = max(
                float(merged.get("confidence", 0.0)),
                float(candidate.get("confidence", 0.0)),
            )
        result.append(merged)
    result.extend(
        dict(item, options=dict(item.get("options") or {}))
        for number, item in best.items()
        if number not in existing_numbers
    )
    return result


def _scan_quality_retry_pages(
    parsed: list[dict[str, Any]], exam_type: str
) -> list[int]:
    """Return a bounded set of pages where OCR lost content, not just numbers."""
    pages: set[int] = set()
    for item in parsed:
        if _needs_scan_recovery(item, exam_type):
            pages.add(int(item["page"]))
    try:
        cap = int(os.getenv("OCR_SCAN_RETRY_PAGES", "12"))
    except ValueError:
        cap = 0
    # Recovery is page- and question-block bounded. Clean documents never enter
    # it, while a damaged booklet cannot turn into an unbounded second pass.
    return sorted(pages)[: max(0, min(12, cap))]


def _reading_roi_fallback_pages(
    plans: list[ReadingPagePlan],
    candidates: list[dict[str, Any]],
    parsed: list[dict[str, Any]],
    *,
    skip_pages: set[int],
    text_pages: set[int],
) -> list[int]:
    """Select pages that need one conservative full-page OCR fallback."""
    question_plans = {
        plan.page: plan
        for plan in plans
        if plan.question_rois and plan.page not in skip_pages
        and plan.page not in text_pages
    }
    fallback: set[int] = set()
    candidate_pages = {
        int(candidate["page"])
        for candidate in candidates
        if 101 <= int(candidate.get("number", 0)) <= 200
    }
    for plan in plans:
        if plan.page in skip_pages or plan.page in text_pages:
            continue
        if plan.part == "Part 5" and plan.question_rois:
            # Part 5 has no passage area to protect and only three pages in a
            # standard Reading booklet.  A full-page pass is a cheap,
            # deterministic accuracy guard for small first-column markers.
            fallback.add(plan.page)
        elif plan.fallback_reason and (
            plan.part in {"Part 5", "Part 6"}
            or bool(_reading_page_numbers_from_expected(plan))
        ):
            fallback.add(plan.page)
        elif plan.question_rois and plan.page not in candidate_pages:
            fallback.add(plan.page)

    for item in parsed:
        number = int(item.get("number", 0))
        if 101 <= number <= 200 and _needs_scan_recovery(item, "reading"):
            page = int(item.get("page", 0))
            if page in question_plans:
                fallback.add(page)
    return sorted(fallback)


def _merge_fallback_option_fragments(
    full_page: list[dict[str, Any]],
    roi_page: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge only missing question fields from a higher-resolution ROI pass."""
    unmarked = [
        item
        for item in roi_page
        if item.get("unmarked_options") and item.get("options")
    ]
    result = [
        dict(item, options=dict(item.get("options") or {}))
        for item in full_page
    ]

    # A normal ROI candidate carries its question number. Prefer the first
    # candidate with the richest option set, then fill only fields that the
    # full-page pass lost. Existing recognized text is never overwritten.
    numbered_roi: dict[int, dict[str, Any]] = {}
    for candidate in roi_page:
        number = int(candidate.get("number", 0))
        if not 101 <= number <= 200:
            continue
        current = numbered_roi.get(number)
        if current is None or len(candidate.get("options") or {}) > len(
            current.get("options") or {}
        ):
            numbered_roi[number] = candidate

    existing_numbers = {
        int(item.get("number", 0))
        for item in result
        if 101 <= int(item.get("number", 0)) <= 200
    }
    for item in result:
        number = int(item.get("number", 0))
        candidate = numbered_roi.get(number)
        if candidate is None:
            continue
        if not str(item.get("text") or "").strip() and str(
            candidate.get("text") or ""
        ).strip():
            item["text"] = candidate["text"]
        candidate_options = candidate.get("options") or {}
        for letter in _option_letters("reading", number):
            if not str(item["options"].get(letter) or "").strip() and str(
                candidate_options.get(letter) or ""
            ).strip():
                item["options"][letter] = candidate_options[letter]
        item["confidence"] = max(
            float(item.get("confidence", 0.0)),
            float(candidate.get("confidence", 0.0)),
        )

    for number, candidate in numbered_roi.items():
        if number not in existing_numbers:
            result.append(
                dict(candidate, options=dict(candidate.get("options") or {}))
            )

    for item in result:
        if not (101 <= int(item.get("number", 0)) <= 200):
            continue
        expected = _option_letters("reading", int(item["number"]))
        missing = [letter for letter in expected if letter not in item["options"]]
        if not missing:
            continue
        choices = [
            fragment
            for fragment in unmarked
            if int(fragment.get("column", 0)) == int(item.get("column", 0))
        ] or unmarked
        if not choices:
            # A weak scan can yield neither numbered ROI candidates nor an
            # unmarked A-D block. Keeping the first-pass item is safer than
            # inventing fields, and one empty recovery page must not abort the
            # complete document extraction.
            continue
        fragment = min(
            choices,
            key=lambda candidate: abs(
                int(candidate.get("order", 0)) - int(item.get("order", 0))
            ),
        )
        for letter in missing:
            value = fragment["options"].get(letter)
            if value:
                item["options"][letter] = value
    return result


def _reading_targeted_recovery_pages(
    parsed: list[dict[str, Any]],
    *,
    skip_pages: set[int],
    text_pages: set[int],
) -> list[int]:
    """Find a small set of Reading pages worth a cheap ROI recovery pass.

    The normal Reading path already paid for one full-page OCR pass.  A second
    pass is therefore restricted to pages containing an incomplete question;
    it must not turn into a document-wide two-stage pipeline by accident.
    """
    pages = {
        int(item.get("page", 0))
        for item in parsed
        if 101 <= int(item.get("number", 0)) <= 200
        and _needs_scan_recovery(item, "reading")
        and int(item.get("page", 0)) not in skip_pages
        and int(item.get("page", 0)) not in text_pages
    }
    try:
        limit = int(os.getenv("OCR_READING_RECOVERY_PAGES", "6"))
    except ValueError:
        limit = 6
    return sorted(pages)[: max(0, min(6, limit))]


def _reading_page_numbers_from_expected(plan: ReadingPagePlan) -> tuple[int, ...]:
    """Keep fallback selection explicit without treating passage pages as Q pages."""
    return plan.expected_numbers if plan.part in {"Part 6", "Part 7"} else ()


def extract_exam(
    *,
    job_id: str,
    pdf_path: str,
    exam_type: str,
    job_dir: Path,
    progress: ProgressCallback,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        info = pdfinfo_from_path(pdf_path, poppler_path=_poppler_path())
    except (PDFInfoNotInstalledError, FileNotFoundError, OSError) as exc:
        raise _pdf_runtime_error(exc) from exc
    page_count = int(info["Pages"])
    if page_count < 1 or page_count > settings.max_pdf_pages:
        raise RuntimeError(
            f"PDF có {page_count} trang; giới hạn xử lý là {settings.max_pdf_pages} trang"
        )
    workers = _page_workers(page_count)
    render_dpi = _render_dpi(exam_type)
    full_page_ocr_scale = _full_page_ocr_scale(exam_type)
    listening_full_page = exam_type == "listening"
    reading_roi_mode = exam_type == "reading" and _reading_roi_enabled()
    roi_started = time.perf_counter()
    roi_metrics: dict[str, Any] = {
        "enabled": reading_roi_mode,
        "locator_pages": 0,
        "roi_pages": 0,
        "roi_count": 0,
        "fallback_pages": [],
        "recovery_pages": [],
        "recovery_count": 0,
        "locator_seconds": 0.0,
        "roi_seconds": 0.0,
        "recovery_seconds": 0.0,
    }
    logger.info(
        "[OCR_PIPELINE] job=%s type=%s pages=%s page_workers=%s cpu=%s reading_roi=%s",
        job_id,
        exam_type,
        page_count,
        workers,
        os.cpu_count() or 1,
        reading_roi_mode,
    )
    progress(2, f"Chuẩn bị render {page_count} trang")

    text_layer: dict[int, list[str]] = {}
    text_tokens: dict[int, list[OCRToken]] = {}
    text_pages: set[int] = set()
    use_text_layer = False
    if exam_type in {"listening", "reading"}:
        try:
            text_layer, text_tokens = _read_pdf_layout(pdf_path, render_dpi=render_dpi)
        except Exception:
            logger.warning(
                "[OCR_TEXT_LAYER] job=%s unavailable; using raster OCR",
                job_id,
                exc_info=True,
            )
    if exam_type == "listening":
        text_pages = {
            page_number
            for page_number, columns in text_layer.items()
            if _listening_text_page_is_usable(columns, page_number)
        }
        use_text_layer = bool(text_pages)
        logger.info(
            "[OCR_TEXT_LAYER] job=%s accepted_pages=%s/%s fallback_pages=%s",
            job_id,
            len(text_pages),
            page_count,
            sorted(set(range(1, page_count + 1)) - text_pages),
        )
    else:
        text_pages = {
            page_number
            for page_number, columns in text_layer.items()
            if _reading_text_page_is_usable(columns, page_number)
        }
        logger.info(
            "[OCR_TEXT_LAYER] job=%s accepted_pages=%s/%s fallback_pages=%s",
            job_id,
            len(text_pages),
            page_count,
            sorted(set(range(1, page_count + 1)) - text_pages),
        )

    # Launch Poppler only once for the document. Starting it independently for
    # every page costs more than ten seconds on a typical 16-page booklet.
    try:
        rendered_paths = convert_from_path(
            pdf_path,
            dpi=render_dpi,
            output_folder=str(job_dir / "pages"),
            fmt="jpeg",
            # The rendered JPEG is also the OCR source. Avoid an early lossy
            # compression pass before recognizing small Part 3/4 labels and
            # Reading punctuation.
            jpegopt={"quality": 100, "optimize": True},
            paths_only=True,
            thread_count=workers,
            output_file=f"render-{uuid.uuid4().hex}",
            poppler_path=_poppler_path(),
        )
    except (PDFInfoNotInstalledError, FileNotFoundError, OSError) as exc:
        raise _pdf_runtime_error(exc) from exc
    if len(rendered_paths) != page_count:
        raise RuntimeError(
            f"Render thiếu trang: nhận {len(rendered_paths)}/{page_count}"
        )
    progress(14, f"Đã render {page_count} trang")

    page_results: list[PageResult] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _process_rendered_page,
                rendered_path,
                page_number,
                job_dir / "pages",
                page_number not in text_pages and not reading_roi_mode,
                ocr_scale=full_page_ocr_scale,
                ocr_text_score=0.30 if listening_full_page else None,
                preserve_clean_glyphs=listening_full_page,
            ): page_number
            for page_number, rendered_path in enumerate(rendered_paths, start=1)
        }
        completed = 0
        for future in as_completed(futures):
            result = future.result()
            if result.number in text_pages:
                result.columns = text_layer[result.number]
                result.tokens = text_tokens.get(result.number, [])
                result.confidence = 99.0
            page_results.append(result)
            completed += 1
            percent = 14 + round(59 * completed / page_count)
            stage = (
                f"Đọc bố cục trang {completed}/{page_count}"
                if result.number in text_pages
                else f"OCR trang {completed}/{page_count}"
            )
            progress(percent, stage)
    page_results.sort(key=lambda page: page.number)

    reading_plans: list[ReadingPagePlan] = []
    reading_roi_results: dict[int, list[PageResult]] = {}
    if reading_roi_mode:
        locator_started = time.perf_counter()
        locator_scale = _reading_locator_scale(render_dpi)
        locator_pages = {
            page.number: page
            for page in page_results
            if page.number not in text_pages
        }
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    _reading_locator_page,
                    job_dir / "pages" / f"page-{page_number:03d}.jpg",
                    page_number=page_number,
                    coordinate_scale=locator_scale,
                ): page_number
                for page_number in locator_pages
            }
            completed = 0
            for future in as_completed(futures):
                page_number = futures[future]
                locator_pages[page_number] = future.result()
                completed += 1
                progress(
                    14 + round(35 * completed / max(1, len(locator_pages))),
                    f"Định vị vùng Reading {completed}/{len(locator_pages)}",
                )
        for page_number, page in locator_pages.items():
            page_results[page_number - 1] = page
        page_results.sort(key=lambda page: page.number)
        roi_metrics["locator_pages"] = len(locator_pages)
        roi_metrics["locator_seconds"] = round(
            time.perf_counter() - locator_started, 3
        )

    # Detect cover/direction prefix pages AFTER OCR so we can inspect the
    # actual OCR output (works for both text-based and scanned PDFs).
    content_start_page = _detect_content_start(page_results, exam_type)
    content_offset = content_start_page - 1  # 0 when no prefix pages
    skip_pages: set[int] = set(range(1, content_start_page))

    candidates: list[dict[str, Any]] = []
    if reading_roi_mode:
        reading_plans = _build_reading_page_plans(
            page_results,
            content_start_page=content_start_page,
            text_pages=text_pages,
        )
        roi_started = time.perf_counter()
        roi_tasks = [
            plan
            for plan in reading_plans
            if plan.page not in skip_pages
            and plan.page not in text_pages
            and plan.question_rois
        ]
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    _ocr_reading_plan,
                    plan,
                    page_path=job_dir / "pages" / f"page-{plan.page:03d}.jpg",
                ): plan.page
                for plan in roi_tasks
            }
            completed = 0
            for future in as_completed(futures):
                page_number, results, _elapsed = future.result()
                reading_roi_results[page_number] = results
                completed += 1
                progress(
                    49 + round(20 * completed / max(1, len(roi_tasks))),
                    f"OCR vùng câu hỏi {completed}/{len(roi_tasks)}",
                )
        roi_metrics["roi_pages"] = len(reading_roi_results)
        roi_metrics["roi_count"] = sum(
            len(results) for results in reading_roi_results.values()
        )
        roi_metrics["roi_seconds"] = round(time.perf_counter() - roi_started, 3)
        page_results = [
            _merge_layout_page(page, reading_roi_results.get(page.number, []))
            for page in page_results
        ]
        for page in page_results:
            if page.number in skip_pages:
                continue
            if page.number in text_pages:
                candidates.extend(_candidates_from_page_result(page))
            else:
                # Keep reliable native-PDF candidates even when one question
                # on the page is incomplete.  The raster pass is still used
                # for the page (and later recovery), but discarding the good
                # text-layer rows made an isolated OCR miss erase otherwise
                # perfect choices such as 57/96/97.
                if page.number in text_layer:
                    native_page = PageResult(
                        page.number,
                        page.width,
                        page.height,
                        text_layer[page.number],
                        text_tokens.get(page.number, []),
                        99.0,
                    )
                    candidates.extend(_candidates_from_page_result(native_page))
                for roi_page in reading_roi_results.get(page.number, []):
                    candidates.extend(_candidates_from_page_result(roi_page))
    else:
        for page in page_results:
            # Do not parse OCR text from skipped prefix pages.
            if page.number in skip_pages:
                continue
            if page.number in text_layer and page.number not in text_pages:
                # Hybrid extraction: use native rows as a second evidence
                # source on fallback pages instead of forcing raster OCR to
                # rediscover text that the PDF already exposes accurately.
                native_page = PageResult(
                    page.number,
                    page.width,
                    page.height,
                    text_layer[page.number],
                    text_tokens.get(page.number, []),
                    # This page already failed the native-text usability gate.
                    # Retain it as supplementary evidence, but never let a
                    # partial/broken embedded text layer outrank a complete
                    # raster OCR block solely because it was labelled 99.
                    80.0,
                )
                candidates.extend(_candidates_from_page_result(native_page))
            candidates.extend(_candidates_from_page_result(page))
    progress(76, "Ghép số câu và đáp án")
    parsed, sequence_issues = _resolve_sequence(candidates, exam_type)
    if reading_roi_mode:
        fallback_pages = _reading_roi_fallback_pages(
            reading_plans,
            candidates,
            parsed,
            skip_pages=skip_pages,
            text_pages=text_pages,
        )
        if fallback_pages:
            logger.info(
                "[OCR_READING_ROI_FALLBACK] job=%s pages=%s",
                job_id,
                fallback_pages,
            )
            # A low-resolution/ROI candidate can advance the global sequence
            # before a late OCR fragment supplies the first number on that
            # page.  Replace candidates from a fallback page as one atomic
            # unit; otherwise a valid full-page candidate such as 101 can be
            # discarded as "older than expected".
            fallback_page_set = set(fallback_pages)
            roi_fallback_candidates = [
                candidate
                for candidate in candidates
                if int(candidate.get("page", 0)) in fallback_page_set
            ]
            candidates = [
                candidate
                for candidate in candidates
                if int(candidate.get("page", 0)) not in fallback_page_set
            ]
            for page_number in fallback_pages:
                full_page = _full_page_candidates(
                    job_dir / "pages" / f"page-{page_number:03d}.jpg",
                    page_number=page_number,
                )
                roi_page = [
                    candidate
                    for candidate in roi_fallback_candidates
                    if int(candidate.get("page", 0)) == page_number
                ]
                candidates.extend(
                    _merge_fallback_option_fragments(full_page, roi_page)
                )
            parsed, sequence_issues = _resolve_sequence(candidates, exam_type)
        roi_metrics["fallback_pages"] = fallback_pages

    # Keep the production/default path single-pass.  If full-page OCR loses a
    # few choices, recover only those pages with the same bounded question ROI
    # used by the opt-in two-stage pipeline.  This fixes isolated scan misses
    # without paying locator+ROI cost for every page in a 29-page booklet.
    if exam_type == "reading" and not reading_roi_mode:
        recovery_pages = _reading_targeted_recovery_pages(
            parsed,
            skip_pages=skip_pages,
            text_pages=text_pages,
        )
        if recovery_pages:
            recovery_plans = _build_reading_page_plans(
                page_results,
                content_start_page=content_start_page,
                text_pages=text_pages,
            )
            plan_by_page = {
                plan.page: plan
                for plan in recovery_plans
                if plan.question_rois
            }
            recovery_tasks = [
                plan_by_page[page]
                for page in recovery_pages
                if page in plan_by_page
            ]
            recovery_started = time.perf_counter()
            recovery_results: dict[int, list[PageResult]] = {}
            if recovery_tasks:
                with ThreadPoolExecutor(
                    max_workers=min(workers, len(recovery_tasks))
                ) as executor:
                    futures = {
                        executor.submit(
                            _ocr_reading_plan,
                            plan,
                            page_path=job_dir / "pages" / f"page-{plan.page:03d}.jpg",
                        ): plan.page
                        for plan in recovery_tasks
                    }
                    for future in as_completed(futures):
                        page_number, results, _elapsed = future.result()
                        recovery_results[page_number] = results

                for page_number, roi_pages in recovery_results.items():
                    roi_candidates = [
                        candidate
                        for roi_page in roi_pages
                        for candidate in _candidates_from_page_result(roi_page)
                    ]
                    if not roi_candidates:
                        continue
                    page_candidates = [
                        candidate
                        for candidate in candidates
                        if int(candidate.get("page", 0)) == page_number
                    ]
                    candidates = [
                        candidate
                        for candidate in candidates
                        if int(candidate.get("page", 0)) != page_number
                    ]
                    candidates.extend(
                        _merge_fallback_option_fragments(
                            page_candidates,
                            roi_candidates,
                        )
                    )

            roi_metrics["recovery_pages"] = sorted(recovery_results)
            roi_metrics["recovery_count"] = sum(
                len(results) for results in recovery_results.values()
            )
            roi_metrics["recovery_seconds"] = round(
                time.perf_counter() - recovery_started, 3
            )
            if recovery_results:
                parsed, sequence_issues = _resolve_sequence(candidates, exam_type)

    retry_pages = _scan_quality_retry_pages(parsed, exam_type)
    punctuation_recover_numbers: set[int] = set()
    if exam_type == "reading":
        # At most one sentence-punctuation probe per page. Normal missing
        # content recovery keeps precedence, and the existing page cap still
        # prevents this quality check from turning into unbounded OCR work.
        punctuation_by_page: dict[int, int] = {}
        for item in parsed:
            if _needs_sentence_punctuation_recovery(item):
                page_number = int(item.get("page", 0))
                number = int(item.get("number", 0))
                punctuation_by_page[page_number] = max(
                    punctuation_by_page.get(page_number, 0), number
                )
        try:
            retry_limit = int(os.getenv("OCR_SCAN_RETRY_PAGES", "12"))
        except ValueError:
            retry_limit = 12
        retry_limit = max(0, min(12, retry_limit))
        for page_number in sorted(punctuation_by_page):
            if page_number not in retry_pages and len(retry_pages) >= retry_limit:
                continue
            if page_number not in retry_pages:
                retry_pages.append(page_number)
            punctuation_recover_numbers.add(punctuation_by_page[page_number])
        retry_pages.sort()
    if retry_pages and (not use_text_layer or len(text_pages) < page_count):
        logger.info(
            "[OCR_RETRY] job=%s pages=%s reason=missing-question-or-option",
            job_id,
            retry_pages,
        )
        # A local normalization pass is a recovery tool, not a replacement for
        # clean OCR.  Keep its candidates only for questions already known to
        # be incomplete; otherwise bleed-through on one page could overwrite a
        # valid neighbouring question from the first pass.
        recover_numbers = {
            int(item["number"])
            for item in parsed
            if _needs_scan_recovery(item, exam_type)
        } | punctuation_recover_numbers
        page_by_number = {page.number: page for page in page_results}
        parsed_by_page: dict[int, list[int]] = {}
        recover_by_page: dict[int, set[int]] = {}
        for item in parsed:
            page_number = int(item.get("page", 0))
            number = int(item.get("number", 0))
            parsed_by_page.setdefault(page_number, []).append(number)
            if number in recover_numbers:
                recover_by_page.setdefault(page_number, set()).add(number)

        retry_candidates_by_page: dict[int, list[dict[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=min(workers, len(retry_pages))) as executor:
            futures = {
                executor.submit(
                    _recover_page_question_blocks,
                    job_dir / "pages" / f"page-{page_number:03d}.jpg",
                    page_by_number[page_number],
                    page_numbers=parsed_by_page.get(page_number, []),
                    recover_numbers=recover_by_page.get(page_number, set()),
                    # The additional per-number anchor is for the damaged
                    # Listening Part 3/4 layout. Keep Reading's established
                    # A/D recovery path byte-for-byte stable.
                    use_number_anchors=exam_type == "listening",
                ): page_number
                for page_number in retry_pages
                if page_number in page_by_number
            }
            for future in as_completed(futures):
                page_number = futures[future]
                retry_candidates_by_page[page_number] = future.result()

        if retry_candidates_by_page:
            recovered_items: list[dict[str, Any]] = []
            for page_number in sorted(retry_candidates_by_page):
                recovered = retry_candidates_by_page[page_number]
                if not recovered:
                    # Keep the normalized full-page escape hatch for unusual
                    # layouts where A/D marker counts cannot be mapped safely.
                    recovered = [
                        candidate
                        for candidate in _retry_page_ocr(
                            job_dir / "pages" / f"page-{page_number:03d}.jpg",
                            page_number,
                        )
                        if int(candidate.get("number", 0))
                        in recover_by_page.get(page_number, set())
                    ]
                recovered_items.extend(recovered)
            if recovered_items:
                # These candidates already belong to semantically mapped A-D
                # blocks. Merge them into the resolved sequence directly;
                # running unmarked full-page fragments through the global
                # resolver again can incorrectly shift a recovered block to
                # its neighbour.
                parsed = _merge_scan_recovery_candidates(
                    parsed,
                    recovered_items,
                    terminal_punctuation_numbers=punctuation_recover_numbers,
                )
                for item in parsed:
                    number = int(item.get("number", 0))
                    if _question_text_is_usable(str(item.get("text") or "")) or (
                        exam_type == "reading" and 131 <= number <= 146
                    ):
                        item["issues"] = [
                            issue
                            for issue in item.get("issues", [])
                            if issue != "question_missing"
                        ]
                    if all(
                        str((item.get("options") or {}).get(letter) or "").strip()
                        for letter in _option_letters(exam_type, number)
                    ):
                        item["issues"] = [
                            issue
                            for issue in item.get("issues", [])
                            if issue not in {"options_missing", "low_confidence"}
                        ]
                still_incomplete = {
                    int(item["number"])
                    for item in parsed
                    if _needs_scan_recovery(item, exam_type)
                }
                sequence_issues = [
                    issue
                    for issue in sequence_issues
                    if issue.question_number in still_incomplete
                    or issue.code not in {"question_missing", "options_missing"}
                ]
    questions, question_issues = _to_questions(parsed, exam_type)
    if exam_type == "listening":
        # Part 1/2 prompts are delivered by audio.  A scan may omit individual
        # printed number markers there, but that does not mean the exam content
        # is missing: model them deterministically as blank A-D / A-C questions
        # and reserve OCR recovery time for Parts 3/4 where printed text is
        # essential.
        for question in questions:
            number = int(question["number"])
            if not 1 <= number <= 31:
                continue
            letters = _option_letters("listening", number)
            question["text"] = ""
            question["options"] = {letter: "" for letter in letters}
            question["option_letters"] = letters
            question["confidence"] = 100.0
            question["issues"] = [
                issue
                for issue in question["issues"]
                if issue
                not in {
                    "question_missing",
                    "options_missing",
                    "low_confidence",
                    "number_inferred",
                    "question_recovered_from_options",
                }
            ]
        sequence_issues = [
            issue
            for issue in sequence_issues
            if not (issue.question_number is not None and 1 <= issue.question_number <= 31)
        ]
        question_issues = [
            issue
            for issue in question_issues
            if not (issue.question_number is not None and 1 <= issue.question_number <= 31)
        ]
        found_nums = {int(q["number"]) for q in questions}
        prefix = _listening_prefix(content_offset, existing_numbers=found_nums)
        questions = sorted(prefix + questions, key=lambda q: q["number"])
        # Part 1 questions are represented by their six image crops rather
        # than OCR text/options.  Do not report an OCR-missing error when the
        # corresponding physical pages were rendered successfully.
        photo_pages = {
            math.ceil(number / 2) + content_offset for number in range(1, 7)
        }
        if all(
            (job_dir / "pages" / f"page-{page:03d}.jpg").is_file()
            for page in photo_pages
        ):
            sequence_issues = [
                issue
                for issue in sequence_issues
                if not (
                    issue.code == "question_missing"
                    and issue.question_number is not None
                    and 1 <= issue.question_number <= 6
                )
            ]
            question_issues = [
                issue
                for issue in question_issues
                if not (
                    issue.code == "question_missing"
                    and issue.question_number is not None
                    and 1 <= issue.question_number <= 6
                )
            ]
            for question in questions:
                if 1 <= question["number"] <= 6:
                    question["issues"] = [
                        issue for issue in question["issues"] if issue != "question_missing"
                    ]
    detected_numbers = {
        int(question["number"])
        for question in questions
        if (
            1 <= int(question["number"]) <= 100
            if exam_type == "listening"
            else 101 <= int(question["number"]) <= 200
        )
    }
    range_detector = (
        _listening_detected_range
        if exam_type == "listening"
        else _reading_detected_range
    )
    detected_range = range_detector(
        page_results,
        content_start_page=content_start_page,
        detected_numbers=detected_numbers,
    )
    expected_numbers = (
        set(range(detected_range[0], detected_range[1] + 1))
        if detected_range
        else set()
    )
    found_numbers = {int(question["number"]) for question in questions}
    missing_numbers = sorted(expected_numbers - found_numbers)
    noisy_candidates = sum(
        1
        for candidate in candidates
        if not (
            (32 <= int(candidate["number"]) <= 100)
            if exam_type == "listening"
            else (101 <= int(candidate["number"]) <= 200)
        )
    )
    logger.info(
        "[OCR_QUALITY] job=%s type=%s questions=%s missing=%s noisy_rejected=%s mode=%s roi=%s roi_pages=%s roi_count=%s fallback_pages=%s recovery_pages=%s recovery_count=%s",
        job_id,
        exam_type,
        len(questions),
        missing_numbers or "none",
        noisy_candidates,
        (
            "text-layer"
            if len(text_pages) == page_count
            else "hybrid"
            if text_pages
            else "ocr"
        ),
        reading_roi_mode,
        roi_metrics["roi_pages"],
        roi_metrics["roi_count"],
        roi_metrics["fallback_pages"] or "none",
        roi_metrics["recovery_pages"] or "none",
        roi_metrics["recovery_count"],
    )

    progress(84, "Cắt passage và hình minh họa")
    stimuli, crop_issues = _build_stimuli(
        job_id=job_id,
        job_dir=job_dir,
        exam_type=exam_type,
        pages=page_results,
        questions=questions,
        content_offset=content_offset,
    )
    for question in questions:
        question.pop("_page", None)
    questions.sort(key=lambda question: question["number"])

    elapsed = time.perf_counter() - started
    logger.info(
        "Extraction %s complete: %s pages, %s questions, %.2fs",
        job_id,
        page_count,
        len(questions),
        elapsed,
    )
    progress(98, "Hoàn tất bản nháp")
    return {
        "questions": questions,
        "stimuli": stimuli,
        "issues": [
            issue.model_dump()
            for issue in sequence_issues + question_issues + crop_issues
        ],
        "metadata": {
            "page_count": page_count,
            "duration_seconds": round(elapsed, 2),
            "text_mode": (
                "text-layer"
                if len(text_pages) == page_count
                else "hybrid"
                if text_pages
                else "ocr"
            ),
            "text_layer_pages": sorted(text_pages),
            "skipped_pages": sorted(skip_pages),
            "content_start_page": content_start_page,
            "detected_question_range": list(detected_range) if detected_range else None,
            "reading_roi": roi_metrics,
        },
    }


def recrop_asset(
    *,
    job_id: str,
    job_dir: Path,
    stimulus: dict[str, Any],
    asset_id: str | None = None,
) -> dict[str, Any]:
    if not stimulus.get("assets"):
        raise ValueError("Stimulus không có asset")
    asset_index = 0
    if asset_id:
        asset_index = next(
            (
                index
                for index, item in enumerate(stimulus["assets"])
                if item.get("id") == asset_id
            ),
            -1,
        )
        if asset_index < 0:
            raise ValueError("Không tìm thấy asset cần crop")
    source = stimulus["assets"][asset_index]
    bbox = tuple(float(value) for value in source["bbox"])
    page_number = int(source["page"])
    old_id = source.get("id")
    asset = _save_crop(
        job_id=job_id,
        job_dir=job_dir,
        page_number=page_number,
        bbox=bbox,
        lossless=True,
    )
    if old_id:
        old_name = str(old_id)
        if Path(old_name).name == old_name:
            old_path = (job_dir / "assets" / old_name).resolve()
            if (
                old_path.parent == (job_dir / "assets").resolve()
                and old_path.is_file()
            ):
                old_path.unlink(missing_ok=True)
    stimulus["assets"][asset_index] = asset
    stimulus["page_numbers"] = sorted(
        {int(item["page"]) for item in stimulus["assets"]}
    )
    return stimulus


def create_manual_stimulus(
    *,
    job_id: str,
    job_dir: Path,
    page_number: int,
    bbox: tuple[float, float, float, float],
    question_numbers: Iterable[int],
    title: str = "Ảnh cắt thủ công",
) -> dict[str, Any]:
    """Create a review-only stimulus directly from a retained PDF page.

    The automatically generated crop is deliberately not used as the source:
    teachers can recover from a wrong detector result by selecting any original
    rendered page that belongs to the extraction job.  Source pages are kept in
    the job store/MinIO for this purpose.
    """
    if page_number < 1:
        raise ValueError("Trang nguồn không hợp lệ")
    if len(bbox) != 4 or not all(math.isfinite(value) for value in bbox):
        raise ValueError("Vùng crop không hợp lệ")
    left, top, right, bottom = bbox
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError("Vùng crop phải nằm trong trang PDF")
    # Very small selections are virtually always accidental and create
    # unreadable assets, while normal manual selections are far larger.
    if right - left < 0.01 or bottom - top < 0.01:
        raise ValueError("Vùng crop quá nhỏ")
    numbers = sorted({int(number) for number in question_numbers})
    if not numbers:
        raise ValueError("Cần chọn ít nhất một câu hỏi")

    asset = _save_crop(
        job_id=job_id,
        job_dir=job_dir,
        page_number=page_number,
        bbox=bbox,
        lossless=True,
    )
    if not asset["url"]:
        raise ValueError("Không tìm thấy trang nguồn để cắt ảnh")
    return {
        "id": f"manual-{uuid.uuid4().hex[:12]}",
        "kind": "image",
        "title": title.strip() or "Ảnh cắt thủ công",
        "assets": [asset],
        "question_numbers": numbers,
        "page_numbers": [page_number],
        "confidence": 100.0,
        "issues": [],
    }
