"""Exercise a packaged OCR sidecar with a real one-page PDF."""

from __future__ import annotations

import argparse
import json
import socket
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def validate_desktop_readiness(
    payload: dict[str, object], *, expected_provider: str | None = None
) -> None:
    """Prove that the packaged artifact is using bundled local OCR."""
    expected = {
        "status": "ready",
        "profile": "desktop",
        "processing_location": "LOCAL_EDGE",
        "edge_ocr": True,
        "ocr_enabled": True,
        "ocr_local": True,
        "ocr_remote": False,
        "ocr_ready": True,
    }
    mismatches = {
        key: {"expected": expected_value, "actual": payload.get(key)}
        for key, expected_value in expected.items()
        if payload.get(key) != expected_value
    }
    provider = str(payload.get("ocr_provider") or "")
    if not provider or "remote" in provider.lower():
        mismatches["ocr_provider"] = {
            "expected": "local Tesseract OCR engine",
            "actual": provider,
        }
    if expected_provider and provider != expected_provider:
        mismatches["ocr_provider_target"] = {
            "expected": expected_provider,
            "actual": provider,
        }
    if mismatches:
        raise RuntimeError(f"Sidecar is not using bundled local OCR: {mismatches}")


def request(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15,
) -> tuple[int, bytes]:
    """Make a bounded request without turning transient local failures into a crash.

    The sidecar is a local process and OCR can briefly starve the Windows
    runner while native tools start.  Polling callers treat status ``0`` as a
    retryable transport failure; real HTTP errors still retain their status.
    """
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, data=body, headers=headers or {}, method=method),
            timeout=timeout,
        ) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (TimeoutError, socket.timeout, ConnectionError, OSError):
        return 0, b""
    except urllib.error.URLError:
        # The packaged process needs a moment to unpack/start Uvicorn.  A
        # refused connection is expected during the readiness retry loop.
        return 0, b""


def multipart(pdf: Path) -> tuple[bytes, str]:
    boundary = f"----ExamifySmoke{uuid.uuid4().hex}"
    chunks = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"exam_type\"\r\n\r\nreading\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio_mode\"\r\n\r\nnone\r\n".encode(),
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"smoke.pdf\"\r\nContent-Type: application/pdf\r\n\r\n"
        ).encode(),
        pdf.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    return b"".join(chunks), boundary


def _smoke_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def smoke_reading_questions(primary: dict[str, object]) -> list[dict[str, object]]:
    """Build a complete Reading review payload for the finalize contract.

    The production review API deliberately inserts explicit placeholders for
    OCR-missing numbers 101..200 and finalize rejects unresolved placeholders.
    Keep question 101 as the OCR/manual-edit assertion, but provide valid
    bounded fixture data for the remaining questions so this installer smoke
    test exercises persistence instead of bypassing data-integrity checks.
    """

    questions = [primary]
    for number in range(102, 201):
        questions.append(
            {
                "number": number,
                "part": (
                    "Part 5 - Phần 5"
                    if number <= 130
                    else "Part 6 - Phần 6"
                    if number <= 146
                    else "Part 7 - Phần 7"
                ),
                "text": f"Smoke fixture question {number}",
                "options": {
                    "A": "One",
                    "B": "Two",
                    "C": "Three",
                    "D": "Four",
                },
                "option_letters": ["A", "B", "C", "D"],
                "correct": None,
                "group_id": None,
                "stimulus_id": None,
                "confidence": 100,
                "issues": [],
            }
        )
    return questions


def multipart_image(image: Path) -> tuple[bytes, str]:
    boundary = f"----ExamifySmokeImage{uuid.uuid4().hex}"
    chunks = [
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{image.name}\"\r\nContent-Type: image/png\r\n\r\n"
        ).encode(),
        image.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    return b"".join(chunks), boundary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--resource-dir", type=Path, default=None)
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--request-timeout", type=float, default=15.0)
    parser.add_argument("--job-timeout", type=float, default=180.0)
    parser.add_argument("--pdf-pages", type=int, default=1)
    parser.add_argument("--max-ocr-seconds", type=float, default=None)
    parser.add_argument("--diagnostics-dir", type=Path, default=None)
    parser.add_argument(
        "--expected-provider",
        choices=(
            "DmlExecutionProvider",
            "CoreMLExecutionProvider",
            "CPUExecutionProvider",
        ),
        default=None,
        help="Require the provider selected by this native release target.",
    )
    args = parser.parse_args()
    if not 1 <= args.pdf_pages <= 12:
        parser.error("--pdf-pages must be between 1 and 12")
    secret = "ci-sidecar-smoke-secret"
    desktop_user_id = "11111111-1111-4111-8111-111111111111"
    with tempfile.TemporaryDirectory(prefix="examify-sidecar-") as temporary:
        root = Path(temporary)
        pdf = root / "smoke.pdf"
        font = _smoke_font(52)
        pages: list[Image.Image] = []
        for index in range(args.pdf_pages):
            page = Image.new("RGB", (1700, 2200), "white")
            ImageDraw.Draw(page).multiline_text(
                (180, 240),
                f"{101 + index}. The local OCR smoke test is working.\n"
                "(A) Alpha answer\n(B) Beta answer\n"
                "(C) Gamma answer\n(D) Delta answer",
                fill="black",
                font=font,
                spacing=28,
            )
            pages.append(page)
        pages[0].save(
            pdf,
            "PDF",
            resolution=300.0,
            save_all=True,
            append_images=pages[1:],
        )
        for page in pages:
            page.close()
        answer_key = root / "answer-key.png"
        key_page = Image.new("RGB", (1800, 600), "white")
        ImageDraw.Draw(key_page).text(
            (260, 170), "101 A", fill="black", font=_smoke_font(180)
        )
        key_page.save(answer_key, "PNG")
        key_page.close()
        command = [
            str(args.sidecar), "--port", str(args.port), "--secret", secret,
            "--data-dir", str(root / "data"),
        ]
        if args.resource_dir:
            command.extend(["--resource-dir", str(args.resource_dir)])
        stdout_path = root / "sidecar.stdout.log"
        stderr_path = root / "sidecar.stderr.log"
        stdout_file = stdout_path.open("w", encoding="utf-8", errors="replace")
        stderr_file = stderr_path.open("w", encoding="utf-8", errors="replace")
        process = subprocess.Popen(command, stdout=stdout_file, stderr=stderr_file)
        job_id: str | None = None
        succeeded = False
        try:
            base = f"http://127.0.0.1:{args.port}"
            health_deadline = time.monotonic() + 90
            while time.monotonic() < health_deadline:
                status, _ = request(f"{base}/health", timeout=args.request_timeout)
                if status == 200:
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("Sidecar health check failed")
            ready_deadline = time.monotonic() + 30
            while time.monotonic() < ready_deadline:
                status, payload = request(
                    f"{base}/health/ready", timeout=args.request_timeout
                )
                if status == 0:
                    time.sleep(0.5)
                    continue
                readiness = json.loads(payload)
                if status != 200:
                    raise RuntimeError(
                        "OCR dependencies are not ready: "
                        f"{payload.decode(errors='replace')}"
                    )
                validate_desktop_readiness(
                    readiness, expected_provider=args.expected_provider
                )
                break
            else:
                raise RuntimeError("OCR readiness check timed out")
            body, boundary = multipart(pdf)
            ocr_started = time.monotonic()
            status, payload = request(
                f"{base}/api/extractions",
                method="POST",
                body=body,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "X-Desktop-Secret": secret,
                    "X-TOEICDOC-User-ID": desktop_user_id,
                },
                timeout=args.request_timeout,
            )
            if status != 202:
                raise RuntimeError(f"PDF upload failed ({status}): {payload.decode(errors='replace')}")
            job_id = json.loads(payload)["job_id"]
            job_deadline = time.monotonic() + args.job_timeout
            last_job: dict[str, object] | None = None
            while time.monotonic() < job_deadline:
                status, payload = request(
                    f"{base}/api/extractions/{job_id}?desktop_secret={secret}",
                    headers={"X-TOEICDOC-User-ID": desktop_user_id},
                    timeout=args.request_timeout,
                )
                if status == 0:
                    time.sleep(0.5)
                    continue
                if status != 200:
                    raise RuntimeError(f"Job polling failed ({status}): {payload.decode(errors='replace')}")
                job = json.loads(payload)
                last_job = job
                if job["status"] in {"review", "ready"}:
                    if job.get("metadata", {}).get("page_count") != args.pdf_pages:
                        raise RuntimeError(f"Unexpected page count: {job}")
                    ocr_seconds = time.monotonic() - ocr_started
                    if (
                        args.max_ocr_seconds is not None
                        and ocr_seconds > args.max_ocr_seconds
                    ):
                        raise RuntimeError(
                            "Packaged OCR performance regression: "
                            f"{ocr_seconds:.2f}s > {args.max_ocr_seconds:.2f}s "
                            f"for {args.pdf_pages} pages; readiness={readiness}"
                        )
                    extracted = next(
                        (item for item in job.get("questions", []) if item.get("number") == 101),
                        None,
                    )
                    if not extracted or not extracted.get("text") or any(
                        not extracted.get("options", {}).get(letter)
                        for letter in ("A", "B", "C", "D")
                    ):
                        raise RuntimeError(f"Tesseract OCR did not extract the smoke question: {job}")
                    break
                if job["status"] == "failed":
                    raise RuntimeError(f"OCR failed: {job.get('error')}")
                time.sleep(0.5)
            else:
                raise RuntimeError(
                    "OCR job did not complete in time: "
                    f"job_id={job_id}, last_state={last_job}"
                )

            key_body, key_boundary = multipart_image(answer_key)
            status, payload = request(
                f"{base}/api/extractions/{job_id}/answer-key-image",
                method="POST",
                body=key_body,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={key_boundary}",
                    "X-Desktop-Secret": secret,
                    "X-TOEICDOC-User-ID": desktop_user_id,
                },
                timeout=args.request_timeout,
            )
            answer_key_payload = json.loads(payload) if payload else {}
            if status != 200 or answer_key_payload.get("answer_key", {}).get("101") != "A":
                raise RuntimeError(
                    "Answer-key Tesseract OCR smoke failed "
                    f"({status}): {json.dumps(answer_key_payload, ensure_ascii=False)}"
                )

            # Save one OCR-controlled review question plus a complete bounded
            # Reading fixture. This verifies the complete desktop contract:
            # OCR -> review save -> final exam in SQLite -> attempt history,
            # while preserving the OCR result above without bypassing the
            # production unresolved-question guard.
            question = {
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
            status, payload = request(
                f"{base}/api/extractions/{job_id}/draft",
                method="PATCH",
                body=json.dumps(
                    {"questions": smoke_reading_questions(question)}
                ).encode(),
                headers={
                    "Content-Type": "application/json",
                    "X-Desktop-Secret": secret,
                    "X-TOEICDOC-User-ID": desktop_user_id,
                },
                timeout=args.request_timeout,
            )
            if status != 200:
                raise RuntimeError(f"Draft save failed ({status}): {payload.decode(errors='replace')}")
            status, payload = request(
                f"{base}/api/extractions/{job_id}/finalize",
                method="POST",
                body=json.dumps(
                    {
                        "title": "Examify installer smoke",
                        "count": 1,
                        "answer_key": {"101": "A"},
                    }
                ).encode(),
                headers={
                    "Content-Type": "application/json",
                    "X-Desktop-Secret": secret,
                    "X-TOEICDOC-User-ID": desktop_user_id,
                },
                timeout=args.request_timeout,
            )
            if status != 200:
                raise RuntimeError(f"Exam finalize failed ({status}): {payload.decode(errors='replace')}")
            exam = json.loads(payload)
            client_exam_id = exam.get("client_exam_id")
            if not client_exam_id:
                raise RuntimeError(f"Finalized desktop exam has no client id: {exam}")
            status, payload = request(
                f"{base}/api/desktop/exams",
                headers={
                    "X-Desktop-Secret": secret,
                    "X-TOEICDOC-User-ID": desktop_user_id,
                },
                timeout=args.request_timeout,
            )
            if status != 200 or not any(
                item.get("client_exam_id") == client_exam_id
                for item in json.loads(payload).get("items", [])
            ):
                raise RuntimeError("Finalized exam was not persisted in desktop SQLite")
            attempt_id = uuid.uuid4().hex
            status, payload = request(
                f"{base}/api/desktop/attempts/history",
                method="POST",
                body=json.dumps(
                    {
                        "id": attempt_id,
                        "client_exam_id": client_exam_id,
                        "exam_title": exam.get("title") or "Examify installer smoke",
                        "exam_type": "reading",
                        "score_toeic": 5,
                        "listening_score": 0,
                        "reading_score": 5,
                        "correct_count": 1,
                        "total_questions": exam.get("returned_count") or 1,
                        "duration_seconds": 60,
                        "time_spent_seconds": 10,
                        "mode": "practice",
                        "answers": {"101": "A"},
                    }
                ).encode(),
                headers={
                    "Content-Type": "application/json",
                    "X-Desktop-Secret": secret,
                    "X-TOEICDOC-User-ID": desktop_user_id,
                },
                timeout=args.request_timeout,
            )
            if status != 200:
                raise RuntimeError(f"Attempt save failed ({status}): {payload.decode(errors='replace')}")
            status, payload = request(
                f"{base}/api/desktop/attempts/history",
                headers={
                    "X-Desktop-Secret": secret,
                    "X-TOEICDOC-User-ID": desktop_user_id,
                },
                timeout=args.request_timeout,
            )
            if status != 200:
                raise RuntimeError(
                    "Attempt history query failed: "
                    f"HTTP {status}, payload={payload.decode(errors='replace')}"
                )
            history_payload = json.loads(payload)
            if not any(
                item.get("id") == attempt_id
                for item in history_payload.get("items", [])
            ):
                raise RuntimeError(
                    "Attempt history was not persisted in desktop SQLite: "
                    f"payload={history_payload}"
                )

            # Restart the exact packaged process against the same data dir.
            # This is the installer-level proof that an exam finalized while
            # offline and its upload intent survive an app/sidecar restart.
            process.terminate()
            process.wait(timeout=10)
            process = subprocess.Popen(command, stdout=stdout_file, stderr=stderr_file)
            restart_deadline = time.monotonic() + 90
            while time.monotonic() < restart_deadline:
                status, _ = request(f"{base}/health", timeout=args.request_timeout)
                if status == 200:
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("Sidecar did not become healthy after restart")

            status, payload = request(
                f"{base}/api/desktop/exams",
                headers={
                    "X-Desktop-Secret": secret,
                    "X-TOEICDOC-User-ID": desktop_user_id,
                },
                timeout=args.request_timeout,
            )
            restarted_exams = json.loads(payload).get("items", []) if status == 200 else []
            if not any(
                item.get("client_exam_id") == client_exam_id
                for item in restarted_exams
            ):
                raise RuntimeError(
                    "Finalized exam disappeared after packaged sidecar restart: "
                    f"HTTP {status}, payload={payload.decode(errors='replace')}"
                )
            status, payload = request(
                f"{base}/api/desktop/sync/pending",
                headers={
                    "X-Desktop-Secret": secret,
                    "X-TOEICDOC-User-ID": desktop_user_id,
                },
                timeout=args.request_timeout,
            )
            pending_items = json.loads(payload).get("items", []) if status == 200 else []
            if not any(
                item.get("client_exam_id") == client_exam_id
                for item in pending_items
            ):
                raise RuntimeError(
                    "Offline sync intent disappeared after packaged sidecar restart: "
                    f"HTTP {status}, payload={payload.decode(errors='replace')}"
                )
            succeeded = True
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "ocr_seconds": round(ocr_seconds, 3),
                        "pdf_pages": args.pdf_pages,
                        "runtime": readiness,
                    },
                    ensure_ascii=False,
                )
            )
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            stdout_file.close()
            stderr_file.close()
            if not succeeded:
                def tail(path: Path) -> str:
                    try:
                        return path.read_text(encoding="utf-8", errors="replace")[-4000:]
                    except OSError:
                        return "<unavailable>"

                print(f"Sidecar diagnostics: job_id={job_id!r}")
                print(f"--- stdout ({stdout_path}) ---\n{tail(stdout_path)}")
                print(f"--- stderr ({stderr_path}) ---\n{tail(stderr_path)}")
                if args.diagnostics_dir:
                    diagnostics = args.diagnostics_dir.resolve()
                    diagnostics.mkdir(parents=True, exist_ok=True)
                    for source in (stdout_path, stderr_path):
                        if source.exists():
                            shutil.copy2(source, diagnostics / source.name)
                    data_dir = root / "data"
                    if data_dir.exists():
                        shutil.copytree(
                            data_dir,
                            diagnostics / "data",
                            dirs_exist_ok=True,
                        )


if __name__ == "__main__":
    main()
