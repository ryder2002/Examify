import type { BrowserOcrCapabilities } from "./types";

const MODEL_AND_RUNTIME_HEADROOM = 180 * 1024 * 1024;
const WASM_MAGIC = new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0]);
let assetCheck: Promise<void> | null = null;

function supportsWebAssembly(): boolean {
  try {
    return (
      typeof WebAssembly === "object" &&
      WebAssembly.validate(new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0]))
    );
  } catch {
    return false;
  }
}

export async function detectBrowserOcrCapabilities(
  sourceSize = 0,
): Promise<BrowserOcrCapabilities> {
  const webAssembly = supportsWebAssembly();
  const worker = typeof Worker !== "undefined";
  const indexedDb = typeof indexedDB !== "undefined";
  let opfs = false;
  let availableBytes: number | null = null;
  if (typeof navigator !== "undefined" && navigator.storage) {
    opfs = typeof navigator.storage.getDirectory === "function";
    try {
      const estimate = await navigator.storage.estimate();
      if (typeof estimate.quota === "number") {
        availableBytes = Math.max(0, estimate.quota - (estimate.usage || 0));
      }
    } catch {
      // Storage estimate can be blocked in privacy modes; IndexedDB remains a
      // valid fallback and writes will still surface quota errors explicitly.
    }
  }
  const requiredBytes = Math.max(MODEL_AND_RUNTIME_HEADROOM, sourceSize * 3);
  const reasons: string[] = [];
  if (!webAssembly) reasons.push("Trình duyệt không hỗ trợ WebAssembly.");
  if (!worker) reasons.push("Trình duyệt không hỗ trợ Web Worker.");
  if (!indexedDb) reasons.push("Trình duyệt không hỗ trợ IndexedDB để lưu checkpoint.");
  if (availableBytes !== null && availableBytes < requiredBytes) {
    reasons.push(
      `Không đủ dung lượng local (cần khoảng ${Math.ceil(requiredBytes / 1024 / 1024)} MiB).`,
    );
  }
  return {
    supported: reasons.length === 0,
    webAssembly,
    worker,
    indexedDb,
    opfs,
    availableBytes,
    requiredBytes,
    reasons,
  };
}

async function readPrefix(response: Response, length: number): Promise<Uint8Array> {
  if (!response.body) return new Uint8Array((await response.arrayBuffer()).slice(0, length));
  const reader = response.body.getReader();
  const bytes: number[] = [];
  try {
    while (bytes.length < length) {
      const next = await reader.read();
      if (next.done) break;
      bytes.push(...next.value);
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
  return new Uint8Array(bytes.slice(0, length));
}

/** Fail before PDF rendering when a deployment serves HTML for an OCR asset. */
export async function assertClientOcrAssets(): Promise<void> {
  if (typeof fetch !== "function") return;
  assetCheck ??= (async () => {
    const paths = [
      "/ocr/tesseract/core/tesseract-core-simd-lstm.wasm",
      "/ocr/opencv/opencv_js.wasm",
    ];
    await Promise.all(
      paths.map(async (path) => {
        const response = await fetch(path, {
          cache: "no-store",
          headers: { Range: "bytes=0-7" },
        });
        if (!response.ok) {
          throw new Error(`Tài nguyên OCR không tải được (HTTP ${response.status}): ${path}`);
        }
        const contentType = response.headers.get("content-type") || "";
        if (/text\/html/i.test(contentType)) {
          throw new Error(`Máy chủ đang trả HTML thay vì WebAssembly: ${path}`);
        }
        const prefix = await readPrefix(response, WASM_MAGIC.length);
        if (
          prefix.length < WASM_MAGIC.length ||
          !WASM_MAGIC.every((value, index) => prefix[index] === value)
        ) {
          throw new Error(`Tài nguyên OCR không phải WebAssembly hợp lệ: ${path}`);
        }
      }),
    );
  })().catch((reason) => {
    assetCheck = null;
    throw reason;
  });
  return assetCheck;
}

export function clientOcrWorkerCount(): 1 | 2 {
  if (typeof navigator === "undefined") return 1;
  const cores = navigator.hardwareConcurrency || 1;
  if (cores >= 8) return 2;

  // Tesseract WASM workers are single-threaded. Two workers are useful on a
  // normal 4-core desktop when there is enough memory, but are harmful on
  // phones/low-memory devices because each worker owns a model/runtime copy.
  const deviceMemory = (navigator as Navigator & { deviceMemory?: number }).deviceMemory;
  const mobile = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
  return cores >= 4 && !mobile && (deviceMemory == null || deviceMemory >= 4) ? 2 : 1;
}
