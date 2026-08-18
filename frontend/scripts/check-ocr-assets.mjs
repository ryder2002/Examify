import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const wasmMagic = Buffer.from([0, 0x61, 0x73, 0x6d, 1, 0, 0, 0]);
const assets = [
  { path: "public/ocr/tesseract/core/tesseract-core-simd-lstm.wasm", wasm: true },
  { path: "public/ocr/opencv/opencv_js.wasm", wasm: true },
  { path: "public/ocr/tesseract/core/tesseract-core-simd-lstm.wasm.js", wasm: false },
  { path: "public/ocr/tesseract/worker.min.js", wasm: false },
  { path: "public/ocr/pdfjs/pdf.worker.min.mjs", wasm: false },
  { path: "public/ocr/tesseract/lang/eng.traineddata.gz", wasm: false },
];

for (const asset of assets) {
  const absolute = resolve(root, asset.path);
  const bytes = await readFile(absolute);
  if (!bytes.length) throw new Error(`OCR asset is empty: ${asset.path}`);
  if (asset.wasm && !bytes.subarray(0, wasmMagic.length).equals(wasmMagic)) {
    throw new Error(`OCR asset is not a WebAssembly binary: ${asset.path}`);
  }
}

console.log(`OCR assets OK (${assets.length} files)`);
