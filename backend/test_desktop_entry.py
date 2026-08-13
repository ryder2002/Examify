"""Regression tests for bounded Desktop OCR hardware profiles."""

from __future__ import annotations

import logging
import os
from unittest.mock import patch

from desktop_entry import _configure_desktop_ocr_runtime, desktop_ocr_runtime_defaults


def test_four_core_cpu_uses_measured_two_by_two_profile() -> None:
    defaults = desktop_ocr_runtime_defaults(
        system="Windows", machine="AMD64", cpu_count=4
    )
    assert defaults["OCR_PAGE_WORKERS"] == "2"
    assert defaults["OCR_ENGINE_POOL_SIZE"] == "2"
    assert defaults["OMP_THREAD_LIMIT"] == "1"


def test_two_core_cpu_avoids_oversubscription() -> None:
    defaults = desktop_ocr_runtime_defaults(
        system="Windows", machine="AMD64", cpu_count=2
    )
    assert defaults["OCR_PAGE_WORKERS"] == "1"
    assert defaults["OCR_ENGINE_POOL_SIZE"] == "1"
    assert defaults["OMP_THREAD_LIMIT"] == "1"


def test_explicit_desktop_override_is_preserved() -> None:
    with patch.dict(os.environ, {"OCR_ENGINE_POOL_SIZE": "1"}, clear=True):
        _configure_desktop_ocr_runtime(logging.getLogger("test"))
        assert os.environ["OCR_ENGINE_POOL_SIZE"] == "1"
        assert os.environ["OCR_PAGE_WORKERS"] in {"1", "2", "3"}
