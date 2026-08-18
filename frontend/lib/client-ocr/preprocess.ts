import { createClientId } from "@/lib/utils";
import type { NormalizedBbox } from "./types";

type PreprocessResponse = {
  id: string;
  ok: boolean;
  image?: ImageData;
  angle?: number;
  engine?: string;
  error?: string;
};

let sharedWorker: Worker | null = null;
const pending = new Map<
  string,
  { resolve: (value: ImageData) => void; reject: (reason: Error) => void }
>();

function worker(): Worker {
  if (sharedWorker) return sharedWorker;
  sharedWorker = new Worker("/ocr/preprocess.worker.js");
  sharedWorker.onmessage = (event: MessageEvent<PreprocessResponse>) => {
    const request = pending.get(event.data.id);
    if (!request) return;
    pending.delete(event.data.id);
    if (event.data.ok && event.data.image) request.resolve(event.data.image);
    else request.reject(new Error(event.data.error || "Tiền xử lý OCR thất bại."));
  };
  sharedWorker.onerror = () => {
    for (const request of pending.values()) request.reject(new Error("OCR preprocessing worker bị dừng."));
    pending.clear();
    sharedWorker?.terminate();
    sharedWorker = null;
  };
  return sharedWorker;
}

export function preprocessForOcr(
  image: ImageData,
  mode: "baseline" | "recovery",
  watermarkBoxes: NormalizedBbox[] = [],
): Promise<ImageData> {
  const id = createClientId();
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    worker().postMessage({ id, image, mode, watermarkBoxes }, [image.data.buffer]);
  });
}

export function terminatePreprocessWorker(): void {
  sharedWorker?.terminate();
  sharedWorker = null;
  for (const request of pending.values()) request.reject(new Error("OCR preprocessing worker đã đóng."));
  pending.clear();
}
