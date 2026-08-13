#!/usr/bin/env python3
"""Benchmark one representative PDF through the real OCR pipeline.

This intentionally requires an explicit input PDF. It never invents accuracy
numbers and does not write benchmark artifacts into the application data dir.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

try:
    import resource
except ImportError:  # pragma: no cover - Windows has no resource module.
    resource = None  # type: ignore[assignment]


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from pipeline import extract_exam  # noqa: E402
from rapid_ocr import runtime_status  # noqa: E402


def peak_rss_bytes() -> int:
    if resource is None:
        return 0
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB; Windows has no ``resource`` module and returns 0.
    return int(value * 1024)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument(
        "--exam-type", choices=("reading", "listening"), default="reading"
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=None,
        help="Exit 2 when the extraction exceeds this regression budget.",
    )
    parser.add_argument(
        "--min-questions",
        type=int,
        default=0,
        help="Exit 2 when fewer questions are extracted.",
    )
    args = parser.parse_args()
    if not args.pdf.is_file() or args.pdf.suffix.lower() != ".pdf":
        parser.error("pdf phải là file .pdf tồn tại")

    events: list[dict[str, object]] = []
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="examify-ocr-benchmark-") as directory:
        job_id = f"benchmark-{uuid.uuid4().hex[:12]}"
        job_dir = Path(directory) / job_id
        job_dir.mkdir(parents=True)
        (job_dir / "pages").mkdir()
        (job_dir / "assets").mkdir()

        def progress(percent: int, stage: str) -> None:
            events.append(
                {
                    "percent": percent,
                    "stage": stage,
                    "elapsed_seconds": round(time.perf_counter() - started, 4),
                }
            )

        result = extract_exam(
            job_id=job_id,
            pdf_path=str(args.pdf),
            exam_type=args.exam_type,
            job_dir=job_dir,
            progress=progress,
        )

    duration = time.perf_counter() - started
    questions = result.get("questions") or []
    question_numbers = sorted(
        int(question["number"])
        for question in questions
        if question.get("number") is not None
    )
    expected_numbers = set(
        range(1, 101) if args.exam_type == "listening" else range(101, 201)
    )
    output = {
        "pdf": str(args.pdf.resolve()),
        "exam_type": args.exam_type,
        "duration_seconds": round(duration, 4),
        "peak_rss_bytes": peak_rss_bytes(),
        "page_count": result.get("metadata", {}).get("page_count"),
        "question_count": len(questions),
        "missing_question_numbers": sorted(
            expected_numbers - set(question_numbers)
        ),
        "issue_count": len(result.get("issues") or []),
        "events": events,
        "ocr_page_workers": os.getenv("OCR_PAGE_WORKERS", "auto"),
        "runtime": runtime_status(),
        "within_budget": args.max_seconds is None or duration <= args.max_seconds,
        "coverage_pass": len(questions) >= max(0, args.min_questions),
        "metadata": result.get("metadata") or {},
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["within_budget"] and output["coverage_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
