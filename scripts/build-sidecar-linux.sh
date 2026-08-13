#!/usr/bin/env bash
# Build the Python sidecar on Linux for testing or CI.
# On production, run scripts/build-sidecar-windows.ps1 on Windows instead.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$REPO_ROOT/backend"
TAURI="$REPO_ROOT/src-tauri"
TARGET="x86_64-unknown-linux-gnu"

echo "=== Installing Python dependencies ==="
python3 -m pip install --upgrade pip
python3 -m pip install --only-binary=cryptography -r "$BACKEND/requirements-desktop.txt"

echo "=== Verifying Tesseract OCR ==="
command -v tesseract >/dev/null || {
    echo "Tesseract OCR is required (apt install tesseract-ocr tesseract-ocr-eng)" >&2
    exit 1
}
tesseract --version | head -1

echo "=== Building sidecar with PyInstaller ==="
pushd "$BACKEND" > /dev/null
pyinstaller --noconfirm --clean smart_exam_sidecar.spec
popd > /dev/null

DIST="$BACKEND/dist/smart-exam-sidecar"
BINARY_DIR="$TAURI/binaries"
RESOURCE_DIR="$TAURI/resources/sidecar"

mkdir -p "$BINARY_DIR" "$RESOURCE_DIR"

echo "=== Copying sidecar binary ==="
cp "$DIST/smart-exam-sidecar" "$BINARY_DIR/smart-exam-sidecar-$TARGET"
chmod +x "$BINARY_DIR/smart-exam-sidecar-$TARGET"

if [ -d "$DIST/_internal" ]; then
    echo "=== Copying _internal runtime ==="
    mkdir -p "$BINARY_DIR/_internal" "$RESOURCE_DIR/_internal"
    cp -r "$DIST/_internal/." "$BINARY_DIR/_internal/"
    cp -r "$DIST/_internal/." "$RESOURCE_DIR/_internal/"
fi

echo "=== Sidecar build complete ==="
echo "Binary: $BINARY_DIR/smart-exam-sidecar-$TARGET"

# Smoke test
echo "=== Running smoke test ==="
PORT=18765
DATA_DIR=$(mktemp -d)
"$BINARY_DIR/smart-exam-sidecar-$TARGET" \
    --port "$PORT" --secret "ci-smoke-secret" --data-dir "$DATA_DIR" &
SIDECAR_PID=$!

cleanup() {
    kill "$SIDECAR_PID" 2>/dev/null || true
    rm -rf "$DATA_DIR"
}
trap cleanup EXIT

READY=false
for i in $(seq 1 90); do
    if curl -sf "http://127.0.0.1:$PORT/health" > /dev/null 2>&1; then
        READY=true
        break
    fi
    sleep 0.5
done

if [ "$READY" = true ]; then
    echo "✅ Sidecar health check passed!"
else
    echo "❌ Sidecar health check FAILED after 45s"
    echo "Check logs at: $DATA_DIR/logs/sidecar.log"
    if [ -f "$DATA_DIR/logs/sidecar.log" ]; then
        echo "=== sidecar.log ==="
        cat "$DATA_DIR/logs/sidecar.log"
    fi
    exit 1
fi
