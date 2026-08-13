from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).with_name("smoke-sidecar.py")
SPEC = importlib.util.spec_from_file_location("smoke_sidecar", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_request_turns_socket_timeout_into_retryable_status(monkeypatch):
    def timeout(*args, **kwargs):
        raise TimeoutError("native process was busy")

    monkeypatch.setattr(MODULE.urllib.request, "urlopen", timeout)
    assert MODULE.request("http://127.0.0.1:1/health", timeout=0.01) == (0, b"")


def test_request_turns_connection_reset_into_retryable_status(monkeypatch):
    def reset(*args, **kwargs):
        raise ConnectionResetError("sidecar restarted")

    monkeypatch.setattr(MODULE.urllib.request, "urlopen", reset)
    assert MODULE.request("http://127.0.0.1:1/health", timeout=0.01) == (0, b"")


def test_reading_smoke_fixture_satisfies_full_finalize_coverage():
    primary = {
        "number": 101,
        "part": "Part 5 - Phần 5",
        "text": "Smoke test question",
        "options": {"A": "One", "B": "Two", "C": "Three", "D": "Four"},
        "option_letters": ["A", "B", "C", "D"],
        "correct": "A",
        "group_id": None,
        "stimulus_id": None,
        "confidence": 100,
        "issues": [],
    }
    questions = MODULE.smoke_reading_questions(primary)

    assert [question["number"] for question in questions] == list(range(101, 201))
    assert all(not question["issues"] for question in questions)
    assert questions[0] is primary


def test_readiness_contract_proves_ocr_is_local():
    MODULE.validate_desktop_readiness(
        {
            "status": "ready",
            "profile": "desktop",
            "processing_location": "LOCAL_EDGE",
            "edge_ocr": True,
            "ocr_enabled": True,
            "ocr_local": True,
            "ocr_remote": False,
            "ocr_ready": True,
            "ocr_provider": "tesseract:cpu",
        }
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (("ocr_remote", True), ("processing_location", "REMOTE_SERVER")),
)
def test_readiness_contract_rejects_remote_route(field, value):
    readiness = {
        "status": "ready",
        "profile": "desktop",
        "processing_location": "LOCAL_EDGE",
        "edge_ocr": True,
        "ocr_enabled": True,
        "ocr_local": True,
        "ocr_remote": False,
        "ocr_ready": True,
        "ocr_provider": "CPUExecutionProvider",
    }
    readiness[field] = value
    with pytest.raises(RuntimeError, match="not using bundled local OCR"):
        MODULE.validate_desktop_readiness(readiness)


def test_readiness_contract_rejects_wrong_native_provider():
    readiness = {
        "status": "ready",
        "profile": "desktop",
        "processing_location": "LOCAL_EDGE",
        "edge_ocr": True,
        "ocr_enabled": True,
        "ocr_local": True,
        "ocr_remote": False,
        "ocr_ready": True,
        "ocr_provider": "CPUExecutionProvider",
    }
    with pytest.raises(RuntimeError, match="ocr_provider_target"):
        MODULE.validate_desktop_readiness(
            readiness, expected_provider="DmlExecutionProvider"
        )


def test_native_specs_bundle_audio_processing_modules():
    root = SCRIPT.parents[1] / "backend"
    for filename in ("smart_exam_sidecar.spec",):
        content = (root / filename).read_text(encoding="utf-8")
        assert '"audio_processing"' in content
        assert '"toeic_audio_cutter"' in content
        assert '"ffmpeg"' in content


def test_native_specs_bundle_wordninja_data_file():
    root = SCRIPT.parents[1] / "backend"
    for filename in ("smart_exam_sidecar.spec",):
        content = (root / filename).read_text(encoding="utf-8")
        assert '"wordninja"' in content
        assert "wordninja_words.txt.gz" in content


def test_worker_image_explicitly_copies_audio_runtime_modules():
    dockerfile = (SCRIPT.parents[1] / "backend" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    assert (
        "COPY --chown=examify:examify audio_processing.py toeic_audio_cutter.py ./"
        in dockerfile
    )
