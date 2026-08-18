#!/usr/bin/env bash
set -euo pipefail

# Reproducible minimal OpenCV.js build used only by the browser OCR
# preprocessing worker. OCR recognition remains Tesseract.js.
OPENCV_TAG="4.13.0"
EMSDK_IMAGE="emscripten/emsdk:3.1.64"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORK_DIR="$(mktemp -d)"
SOURCE_DIR="${WORK_DIR}/opencv"
OUTPUT_DIR="${WORK_DIR}/output"

cleanup() {
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

git clone --depth 1 --branch "${OPENCV_TAG}" https://github.com/opencv/opencv.git "${SOURCE_DIR}"
mkdir -p "${OUTPUT_DIR}/build"

docker run --rm \
  --user "$(id -u):$(id -g)" \
  --volume "${SOURCE_DIR}:/opencv" \
  --volume "${OUTPUT_DIR}:/output" \
  "${EMSDK_IMAGE}" \
  python3 /opencv/platforms/js/build_js.py /output/build \
    --build_wasm \
    --disable_single_file \
    --cmake_option="-DBUILD_LIST=core,imgproc,js" \
    --cmake_option="-DBUILD_TESTS=OFF" \
    --cmake_option="-DBUILD_PERF_TESTS=OFF" \
    --cmake_option="-DBUILD_EXAMPLES=OFF" \
    --cmake_option="-DBUILD_opencv_apps=OFF" \
    --cmake_option="-DWITH_PTHREADS_PF=OFF"

install -m 0644 "${OUTPUT_DIR}/build/bin/opencv.js" "${FRONTEND_DIR}/public/ocr/opencv/opencv.js"
install -m 0644 "${OUTPUT_DIR}/build/bin/opencv_js.wasm" "${FRONTEND_DIR}/public/ocr/opencv/opencv_js.wasm"
sha256sum \
  "${FRONTEND_DIR}/public/ocr/opencv/opencv.js" \
  "${FRONTEND_DIR}/public/ocr/opencv/opencv_js.wasm"
