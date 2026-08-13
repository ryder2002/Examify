"""Validate the system Tesseract installation for a build or sidecar."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
import shutil


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=False)
    args = parser.parse_args()
    binary = shutil.which("tesseract")
    if not binary:
        raise RuntimeError("Tesseract binary không được cài trong image")
    print(f"Using Tesseract OCR: {binary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
