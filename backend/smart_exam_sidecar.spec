# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import wordninja

root = Path(SPECPATH)
vendor = root / "vendor"
datas = [(str(root / "ocr_model_manifest.json"), ".")]
for relative in ("tesseract", "poppler", "ffmpeg"):
    source = vendor / relative
    if source.exists():
        datas.append((str(source), f"vendor/{relative}"))

# wordninja loads this gzip word list by resolving a path relative to its
# module file at import time. PyInstaller collects the Python module itself,
# but not package data unless it is listed explicitly.
wordninja_words = (
    Path(wordninja.__file__).resolve().parent
    / "wordninja"
    / "wordninja_words.txt.gz"
)
if not wordninja_words.is_file():
    raise RuntimeError(f"Missing wordninja data file: {wordninja_words}")
datas.append((str(wordninja_words), "wordninja"))

a = Analysis(
    ["desktop_entry.py"],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "main",
        "answer_key",
        "audio_processing",
        "config",
        "desktop_store",
        "extractor",
        "job_store",
        "parser",
        "pipeline",
        "rapid_ocr",
        "schemas",
        "toeic_audio_cutter",
        "pytesseract",
        "cv2",
        "numpy",
        "PIL",
        "PIL.Image",
        "pdfplumber",
        "pdf2image",
        "httpx",
        "httpx._transports",
        "httpx._transports.default",
        "multipart",
        "sqlite3",
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
    ],
    excludes=[
        "argon2",
        "auth_service",
        "celery",
        "celery_app",
        "database",
        "desktop_sync_api",
        "jwt",
        "minio",
        "models",
        "object_storage",
        "persistent_job_store",
        "platform_api",
        "psycopg",
        "redis",
        "sqlalchemy",
        "token_exports",
        "alembic",
    ],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="smart-exam-sidecar",
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="smart-exam-sidecar",
)
