"""Tesseract OCR adapter used by the existing layout-aware pipeline.

The module name is kept for compatibility with the desktop sidecar and older
callers.  The recognition contract is unchanged: callers still receive
``OCRResult`` with line and word boxes, confidence scores, and a provider name.
Tesseract supplies those boxes through ``image_to_data``; the parser and
question/column layout stages therefore do not need a second implementation.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import LifoQueue
from typing import Any, Iterable

from PIL import Image

from config import settings


logger = logging.getLogger(__name__)

ENGINE_VERSION = "tesseract"
MODEL_VERSION = "tesseract-eng"
# Keep the conservative global default for Reading's dense layout. The
# Listening pipeline explicitly lowers its full-page threshold at 300 DPI,
# where small Part 3/4 labels otherwise disappear before recovery can use them.
DEFAULT_TEXT_SCORE = 0.45
DEFAULT_CPU_POOL_SIZE = 2
# A pool slot represents one bounded Tesseract subprocess. The worker also
# limits page-level parallelism, so the two ceilings cannot multiply without
# control on the 16-core host.
MAX_CPU_POOL_SIZE = 3


@dataclass(frozen=True)
class OCRWord:
    text: str
    confidence: float
    box: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class OCRLine:
    text: str
    confidence: float
    box: tuple[tuple[float, float], ...]
    words: tuple[OCRWord, ...] = ()


@dataclass(frozen=True)
class OCRResult:
    lines: tuple[OCRLine, ...]
    elapsed: float
    provider: str

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines if line.text)


@dataclass
class _EngineSlot:
    engine: Any
    provider: str


class _EnginePool:
    """Bounded Tesseract subprocess slots shared by page worker threads."""

    def __init__(self, slots: Iterable[_EngineSlot]):
        slots = list(slots)
        self.provider = slots[0].provider if slots else ""
        self.size = len(slots)
        self._slots: LifoQueue[_EngineSlot] = LifoQueue()
        for slot in slots:
            self._slots.put(slot)

    def recognize(
        self,
        image: Image.Image,
        *,
        text_score: float,
        config: str | None = None,
    ) -> OCRResult:
        slot = self._slots.get()
        try:
            result = slot.engine(image, text_score=text_score, config=config)
            if isinstance(result, OCRResult):
                return result
            # Keep the adapter test seam and compatibility with older local
            # callers that return the previous engine-shaped objects.
            return _normalize_result(result, 0.0, slot.provider)
        finally:
            self._slots.put(slot)


class _TesseractEngine:
    def __init__(self, *, provider: str = "tesseract:cpu") -> None:
        self.provider = provider

    def __call__(
        self,
        image: Image.Image,
        *,
        text_score: float,
        config: str | None = None,
    ) -> OCRResult:
        import pytesseract
        from pytesseract import Output

        configured_binary = os.getenv("TESSERACT_CMD", "").strip()
        if configured_binary:
            pytesseract.pytesseract.tesseract_cmd = configured_binary

        started = time.perf_counter()
        data = pytesseract.image_to_data(
            image.convert("RGB"),
            lang=settings.tesseract_lang,
            config=_tesseract_config(config),
            output_type=Output.DICT,
            timeout=settings.tesseract_timeout_seconds,
        )
        return _normalize_tesseract_data(
            data,
            elapsed=time.perf_counter() - started,
            provider=self.provider,
            text_score=text_score,
        )


def _tesseract_config(context: str | None) -> str:
    """Build a bounded Tesseract command line for the existing OCR stages."""
    try:
        oem = max(0, min(3, int(os.getenv("TESSERACT_OEM", "1"))))
    except ValueError:
        oem = 1
    try:
        psm = max(3, min(13, int(os.getenv("TESSERACT_PSM", "11"))))
    except ValueError:
        psm = 11

    # answer_key already labels its bounded passes. Preserve that intent while
    # changing only the engine: sparse full pages use PSM 11, compact table
    # crops use PSM 6, and explicit recovery labels may request PSM 4/11.
    context_value = (context or "").strip().lower()
    if "grid" in context_value:
        psm = 6
    if "question-recovery" in context_value or "question-roi" in context_value:
        # Recovery inputs are one physical question block/column. PSM 6 keeps
        # the question line and A-D rows together instead of treating the
        # short crop as sparse unrelated text.
        psm = 6
    recovery = re.search(r"recovery-(\d+)", context_value)
    if recovery:
        psm = max(3, min(13, int(recovery.group(1))))
    if "full-page" in context_value:
        psm = 11
    data_dir = settings.tesseract_data_dir.strip()
    tessdata = f' --tessdata-dir "{data_dir}"' if data_dir else ""
    # Answer-key scans contain only a small alphabet. Restricting recognition
    # for those bounded passes prevents surrounding prose from turning a sharp
    # `16(B)` into an ignored word. The normal exam pipeline keeps the full
    # language model because question text is arbitrary.
    answer_key = "answer-key" in context_value
    whitelist = (
        " -c tessedit_char_whitelist=0123456789ABCDabcd()[]{}.-:"
        " -c preserve_interword_spaces=1"
        if answer_key
        else ""
    )
    return f"--oem {oem} --psm {psm}{whitelist}{tessdata}"


def _box(left: float, top: float, width: float, height: float) -> tuple[tuple[float, float], ...]:
    return (
        (left, top),
        (left + width, top),
        (left + width, top + height),
        (left, top + height),
    )


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _normalize_tesseract_data(
    data: dict[str, list[Any]],
    *,
    elapsed: float,
    provider: str,
    text_score: float,
) -> OCRResult:
    threshold = max(0.0, min(100.0, text_score * 100.0))
    grouped: dict[tuple[int, int, int, int], list[OCRWord]] = {}
    count = len(data.get("text", []))
    for index in range(count):
        text = str(data.get("text", [""] * count)[index] or "").strip()
        confidence = _as_float(data.get("conf", [0] * count)[index], -1.0)
        if not text or confidence < threshold:
            continue
        left = _as_float(data.get("left", [0] * count)[index])
        top = _as_float(data.get("top", [0] * count)[index])
        width = _as_float(data.get("width", [0] * count)[index])
        height = _as_float(data.get("height", [0] * count)[index])
        if width <= 0 or height <= 0:
            continue
        key = (
            _as_int(data.get("block_num", [0] * count)[index]),
            _as_int(data.get("par_num", [0] * count)[index]),
            _as_int(data.get("line_num", [0] * count)[index]),
            _as_int(data.get("page_num", [0] * count)[index]),
        )
        grouped.setdefault(key, []).append(
            OCRWord(text, confidence, _box(left, top, width, height))
        )

    lines: list[OCRLine] = []
    for words in grouped.values():
        words.sort(key=lambda word: min(point[0] for point in word.box))
        left = min(point[0] for word in words for point in word.box)
        top = min(point[1] for word in words for point in word.box)
        right = max(point[0] for word in words for point in word.box)
        bottom = max(point[1] for word in words for point in word.box)
        confidence = sum(word.confidence for word in words) / len(words)
        lines.append(
            OCRLine(
                " ".join(word.text for word in words),
                confidence,
                _box(left, top, right - left, bottom - top),
                tuple(words),
            )
        )
    lines.sort(key=lambda line: (min(point[1] for point in line.box), min(point[0] for point in line.box)))
    return OCRResult(tuple(lines), elapsed, provider)

def _tesseract_binary() -> str | None:
    configured = os.getenv("TESSERACT_CMD", "").strip()
    if configured:
        return configured if Path(configured).is_file() else None
    return shutil.which("tesseract")


def _tesseract_languages() -> list[str]:
    try:
        import pytesseract

        return list(pytesseract.get_languages(config=""))
    except Exception:
        return []


def validate_model_files() -> dict[str, object]:
    """Validate the Tesseract binary and trained language data."""
    binary = _tesseract_binary()
    languages = _tesseract_languages() if binary else []
    language = settings.tesseract_lang
    return {
        "directory": str(Path(settings.tesseract_data_dir or "")),
        "models": MODEL_VERSION,
        "binary": binary or "",
        "languages": languages,
        "missing": [] if binary else ["tesseract"],
        "invalid": [] if language in languages else [language],
        "ready": bool(binary and language in languages),
    }


def _create_pool() -> _EnginePool:
    status = validate_model_files()
    if not status["ready"]:
        raise RuntimeError(
            "Thiếu Tesseract OCR hoặc traineddata: "
            f"missing={status['missing']} invalid={status['invalid']}"
        )
    count = max(
        1,
        min(
            MAX_CPU_POOL_SIZE,
            int(os.getenv("OCR_ENGINE_POOL_SIZE", str(DEFAULT_CPU_POOL_SIZE))),
        ),
    )
    slots = [_EngineSlot(_TesseractEngine(), "tesseract:cpu") for _ in range(count)]
    logger.info(
        "Tesseract OCR ready engine=%s model=%s slots=%s omp_threads=%s",
        ENGINE_VERSION,
        MODEL_VERSION,
        count,
        os.getenv("OMP_THREAD_LIMIT", "default"),
    )
    return _EnginePool(slots)


_POOL_LOCK = threading.Lock()
_POOL: _EnginePool | None = None
_ACTIVE_PROVIDER = ""


def _pool() -> _EnginePool:
    global _POOL, _ACTIVE_PROVIDER
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                pool = _create_pool()
                _POOL = pool
                _ACTIVE_PROVIDER = pool.provider
    return _POOL


def warmup_ocr() -> dict[str, object]:
    """Create the Tesseract runtime and run a deterministic white-page probe."""
    with Image.new("RGB", (96, 64), "white") as probe:
        _pool().recognize(probe, text_score=0.0)
    return runtime_status()


def runtime_status() -> dict[str, object]:
    models = validate_model_files()
    status: dict[str, object] = {
        "ocr_engine": ENGINE_VERSION,
        "ocr_model": MODEL_VERSION,
        "ocr_local": True,
        "ocr_remote": False,
        "ocr_hardware": {
            "system": platform.system(),
            "machine": platform.machine(),
            "logical_cpus": os.cpu_count() or 1,
        },
        "ocr_page_workers": os.getenv("OCR_PAGE_WORKERS", "auto"),
        "ocr_engine_pool_size": os.getenv("OCR_ENGINE_POOL_SIZE", str(DEFAULT_CPU_POOL_SIZE)),
        "tesseract_lang": settings.tesseract_lang,
        "tesseract_omp_threads": os.getenv("OMP_THREAD_LIMIT", "default"),
        "ocr_provider": _ACTIVE_PROVIDER or "tesseract:cpu",
        "ocr_models": models,
        "ocr_ready": False,
    }
    if not models.get("ready"):
        return status
    try:
        pool = _pool()
        status["ocr_engine_pool_size"] = pool.size
        status["ocr_ready"] = True
    except Exception as exc:
        status["ocr_error"] = str(exc)[:300]
    return status


def recognize(
    image: Image.Image,
    *,
    text_score: float = DEFAULT_TEXT_SCORE,
    config: str | None = None,
) -> OCRResult:
    """Recognize an image while preserving the existing layout result contract."""
    return _pool().recognize(image, text_score=text_score, config=config)


def recognize_text(
    image: Image.Image,
    *,
    text_score: float = DEFAULT_TEXT_SCORE,
    config: str | None = None,
) -> str:
    return recognize(image, text_score=text_score, config=config).text


def recognize_lines(image: Image.Image) -> str:
    """Compatibility helper for callers outside the main pipeline."""
    return recognize_text(image)


def _normalize_result(result: Any, elapsed: float, provider: str) -> OCRResult:
    """Normalize the old test seam and any legacy engine-shaped result."""
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    word_results = getattr(result, "word_results", ()) or ()
    if boxes is None or texts is None or scores is None:
        return OCRResult((), elapsed, provider)
    lines: list[OCRLine] = []
    for index, (box, text, score) in enumerate(zip(boxes, texts, scores)):
        value = str(text).strip()
        polygon = tuple((float(point[0]), float(point[1])) for point in box)
        if not value or not polygon:
            continue
        words: list[OCRWord] = []
        if index < len(word_results):
            for item in word_results[index] or ():
                if len(item) < 3:
                    continue
                word_box = tuple(
                    (float(point[0]), float(point[1])) for point in item[2]
                )
                if word_box and str(item[0]).strip():
                    words.append(OCRWord(str(item[0]).strip(), float(item[1]) * 100.0, word_box))
        lines.append(OCRLine(value, float(score) * 100.0, polygon, tuple(words)))
    lines.sort(key=lambda line: (min(point[1] for point in line.box), min(point[0] for point in line.box)))
    return OCRResult(tuple(lines), elapsed, provider)
