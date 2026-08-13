"""Entry point for the locally bundled OCR sidecar."""

from __future__ import annotations

import argparse
import logging
import os
import platform
import shutil
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def desktop_ocr_runtime_defaults(
    *,
    system: str | None = None,
    machine: str | None = None,
    cpu_count: int | None = None,
) -> dict[str, str]:
    """Return bounded OCR defaults for a single-user desktop sidecar.

    Page parsing can run concurrently, but Tesseract subprocesses remain
    explicitly bounded so the sidecar does not oversubscribe a user machine.
    """
    detected_system = system or platform.system()
    detected_machine = machine or platform.machine()
    logical_cpus = max(
        1, cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    )
    # Keep one FFmpeg/Tesseract job per worker.  Three workers on 8+ logical
    # CPUs materially improves multi-page scans while keeping the desktop
    # sidecar bounded; OMP stays at one so Tesseract does not oversubscribe.
    if logical_cpus >= 8:
        page_workers, engine_pool = 3, 3
    elif logical_cpus >= 4:
        page_workers, engine_pool = 2, 2
    else:
        page_workers, engine_pool = 1, 1
    return {
        "OCR_PAGE_WORKERS": str(page_workers),
        "OCR_ENGINE_POOL_SIZE": str(engine_pool),
        "OMP_THREAD_LIMIT": "1",
        "OMP_NUM_THREADS": "1",
        "OCR_CPU_MEM_ARENA": "true",
        "OCR_DESKTOP_SYSTEM": detected_system,
        "OCR_DESKTOP_MACHINE": detected_machine,
        "OCR_DESKTOP_LOGICAL_CPUS": str(logical_cpus),
    }


def _configure_desktop_ocr_runtime(logger: logging.Logger) -> None:
    defaults = desktop_ocr_runtime_defaults()
    for name, value in defaults.items():
        os.environ.setdefault(name, value)
    logger.info(
        "Desktop OCR runtime system=%s machine=%s logical_cpus=%s "
        "page_workers=%s cpu_pool=%s intra_threads=%s",
        os.environ["OCR_DESKTOP_SYSTEM"],
        os.environ["OCR_DESKTOP_MACHINE"],
        os.environ["OCR_DESKTOP_LOGICAL_CPUS"],
        os.environ["OCR_PAGE_WORKERS"],
        os.environ["OCR_ENGINE_POOL_SIZE"],
        os.environ["OMP_THREAD_LIMIT"],
    )


def bundled_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _external_path(path: Path) -> str:
    """Return a Windows path accepted by bundled native executables.

    ``Path.resolve()`` can produce the extended ``\\\\?\\`` form on Windows.
    Native Poppler and model runtimes only receive regular drive/UNC paths.
    """
    value = str(path)
    if os.name == "nt" and value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if os.name == "nt" and value.startswith("\\\\?\\"):
        return value[4:]
    return value


def _binary(names: tuple[str, ...], directories: list[Path]) -> Path | None:
    for directory in directories:
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def _vendor_roots(resource_dir: str | None) -> list[Path]:
    roots: list[Path] = []
    if resource_dir:
        resources = Path(resource_dir)
        roots.extend([resources / "_internal" / "vendor", resources / "vendor"])
    roots.extend(
        [
            bundled_root() / "vendor",
            Path(sys.executable).resolve().parent / "_internal" / "vendor",
            Path(sys.executable).resolve().parent / "vendor",
        ]
    )
    return list(dict.fromkeys(root.resolve() for root in roots if root.exists()))


def _poppler_bin(vendor_roots: list[Path]) -> Path | None:
    for vendor in vendor_roots:
        root = vendor / "poppler"
        if not root.exists():
            continue
        for candidate in [root / "Library" / "bin", root, *root.rglob("bin")]:
            if _binary(("pdfinfo.exe", "pdfinfo"), [candidate]) and _binary(
                ("pdftoppm.exe", "pdftoppm"), [candidate]
            ):
                return candidate
    system_pdfinfo = shutil.which("pdfinfo")
    system_pdftoppm = shutil.which("pdftoppm")
    if system_pdfinfo and system_pdftoppm:
        return Path(system_pdfinfo).parent
    return None


def _ffmpeg_bin(vendor_roots: list[Path]) -> Path | None:
    """Locate the bundled FFmpeg directory used by OCR/audio jobs."""
    for vendor in vendor_roots:
        root = vendor / "ffmpeg"
        if _binary(("ffmpeg.exe", "ffmpeg"), [root]) and _binary(
            ("ffprobe.exe", "ffprobe"), [root]
        ):
            return root
    system_ffmpeg = shutil.which("ffmpeg")
    system_ffprobe = shutil.which("ffprobe")
    if system_ffmpeg and system_ffprobe:
        return Path(system_ffmpeg).parent
    return None


def _run_probe(binary: Path, arguments: list[str], label: str) -> None:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        result = subprocess.run(
            [_external_path(binary), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            check=False,
            text=True,
            errors="replace",
            creationflags=creationflags,
        )
    except OSError as exc:
        raise RuntimeError(f"Không chạy được {label}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{label} khởi động quá lâu") from exc
    if result.returncode != 0:
        output = result.stdout.strip().replace("\n", " ")[:300]
        raise RuntimeError(f"{label} không hoạt động (mã {result.returncode}): {output}")


def _disable_windows_ocr_consoles() -> None:
    """Prevent Poppler subprocesses from flashing console windows on Windows."""
    if os.name != "nt":
        return
    import pdf2image.pdf2image as pdf2image_module

    # pdf2image imports Popen into its module namespace and uses it for both
    # pdfinfo.exe and pdftoppm.exe. Patch that reference before pipeline.py is
    # imported so PDF rendering never opens a transient terminal.
    original_popen = pdf2image_module.Popen

    def popen_without_console(*args, **kwargs):
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
        return original_popen(*args, **kwargs)

    pdf2image_module.Popen = popen_without_console


def _tesseract_binary(vendor_roots: list[Path]) -> Path | None:
    candidates: list[Path] = []
    for root in vendor_roots:
        candidates.extend(
            [
                root / "tesseract" / "tesseract.exe",
                root / "tesseract" / "tesseract",
                root / "tesseract.exe",
                root / "tesseract",
            ]
        )
    candidates.extend(
        [
            Path(__file__).resolve().parent / "tesseract" / "tesseract.exe",
            Path(__file__).resolve().parent / "tesseract" / "tesseract",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    system_binary = shutil.which("tesseract")
    return Path(system_binary) if system_binary else None


def configure(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(exist_ok=True)
    handler = RotatingFileHandler(
        data_dir / "logs" / "sidecar.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=4,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logging.getLogger().addHandler(handler)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logging.getLogger().addHandler(console_handler)
    logging.getLogger().setLevel(logging.INFO)
    logger = logging.getLogger("desktop-startup")

    vendor_roots = _vendor_roots(args.resource_dir)
    logger.info("Python %s on %s", sys.version, sys.platform)
    logger.info("Resource directory: %s", args.resource_dir or "(none)")
    logger.info("Bundle root: %s", bundled_root())
    logger.info("Vendor roots: %s", ", ".join(map(str, vendor_roots)) or "(none)")
    logger.info("Data dir: %s", data_dir)

    os.environ.update(
        {
            "APP_PROFILE": "desktop",
            "DESKTOP_SECRET": args.secret,
            "DESKTOP_DATA_DIR": str(data_dir),
            "TOOL_TAO_DE_DATA_DIR": str(data_dir / "jobs"),
            "AUTH_REQUIRED": "false",
            "USE_CELERY": "false",
            "OCR_ENABLED": "true",
            "DATABASE_URL": "",
            "REDIS_URL": "",
            "MINIO_ENDPOINT": "",
        }
    )
    _configure_desktop_ocr_runtime(logger)

    poppler_bin = _poppler_bin(vendor_roots)
    tesseract_bin = _tesseract_binary(vendor_roots)
    ffmpeg_bin = _ffmpeg_bin(vendor_roots)
    if poppler_bin is None or tesseract_bin is None or ffmpeg_bin is None:
        missing = []
        if poppler_bin is None:
            missing.append("Poppler (pdfinfo/pdftoppm)")
        if tesseract_bin is None:
            missing.append("Tesseract OCR")
        if ffmpeg_bin is None:
            missing.append("FFmpeg/FFprobe audio")
        raise RuntimeError(
            "Thiếu thành phần OCR đã đóng gói: " + ", ".join(missing)
            + ". Hãy tải lại bộ cài Examify mới nhất."
        )

    os.environ["POPPLER_PATH"] = _external_path(poppler_bin)
    os.environ["TESSERACT_CMD"] = _external_path(tesseract_bin)
    ffmpeg = _binary(("ffmpeg.exe", "ffmpeg"), [ffmpeg_bin])
    ffprobe = _binary(("ffprobe.exe", "ffprobe"), [ffmpeg_bin])
    assert ffmpeg is not None and ffprobe is not None
    os.environ["FFMPEG_CMD"] = _external_path(ffmpeg)
    os.environ["FFPROBE_CMD"] = _external_path(ffprobe)
    tessdata = tesseract_bin.parent / "tessdata"
    if tessdata.is_dir():
        os.environ["TESSERACT_DATA_DIR"] = _external_path(tessdata)
    os.environ["PATH"] = os.pathsep.join(
        [
            _external_path(ffmpeg_bin),
            _external_path(poppler_bin),
            os.environ.get("PATH", ""),
        ]
    )
    _disable_windows_ocr_consoles()
    pdfinfo = _binary(("pdfinfo.exe", "pdfinfo"), [poppler_bin])
    pdftoppm = _binary(("pdftoppm.exe", "pdftoppm"), [poppler_bin])
    assert pdfinfo is not None and pdftoppm is not None
    _run_probe(pdfinfo, ["-v"], "Poppler pdfinfo")
    _run_probe(pdftoppm, ["-v"], "Poppler pdftoppm")
    _run_probe(ffmpeg, ["-version"], "FFmpeg")
    _run_probe(ffprobe, ["-version"], "FFprobe")
    from rapid_ocr import runtime_status, warmup_ocr

    model_status = runtime_status()
    if not model_status.get("ocr_models", {}).get("ready"):
        raise RuntimeError(
            "Tesseract OCR hoặc traineddata thiếu: "
            f"{model_status.get('ocr_models')}"
        )
    model_status = warmup_ocr()
    os.environ["OCR_DEPENDENCIES_READY"] = "true"
    logger.info(
        "Tesseract engine=%s model=%s provider=%s local=%s pool=%s "
        "page_workers=%s tesseract_bin=%s",
        model_status.get("ocr_engine"),
        model_status.get("ocr_model"),
        model_status.get("ocr_provider"),
        model_status.get("ocr_local"),
        model_status.get("ocr_engine_pool_size"),
        model_status.get("ocr_page_workers"),
        _external_path(tesseract_bin),
    )
    logger.info("Poppler bin: %s", _external_path(poppler_bin))
    logger.info(
        "FFmpeg bin: %s codec=libopus output=ogg",
        _external_path(ffmpeg_bin),
    )
    logger.info("Sidecar configured. Starting on %s:%d", args.host, args.port)
    logger.info(
        "[OCR_ROUTE] location=LOCAL_EDGE event=sidecar_ready network=loopback-only "
        "remote_ocr=false"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--secret", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--resource-dir", default="")
    args = parser.parse_args()
    if args.host != "127.0.0.1":
        parser.error("Desktop sidecar may only bind to 127.0.0.1")
    configure(args)
    import uvicorn
    from main import app

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        access_log=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
