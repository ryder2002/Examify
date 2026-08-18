import { getClientOcrBlob, loadClientOcrSource, putClientOcrBlob } from "./local-drafts";
import type { NormalizedBbox } from "./types";

const CROP_DPI = 225;
const MAX_CROP_PIXELS = 12_000_000;

async function pdfDocument(draftKey: string) {
  const source = await loadClientOcrSource(draftKey);
  if (!source) throw new Error("Không tìm thấy PDF nguồn trong kho local.");
  const pdfjs = await import("pdfjs-dist");
  pdfjs.GlobalWorkerOptions.workerSrc = "/ocr/pdfjs/pdf.worker.min.mjs";
  return pdfjs.getDocument({ data: await source.arrayBuffer() }).promise;
}

export async function renderClientOcrPage(
  draftKey: string,
  pageNumber: number,
  dpi = 150,
): Promise<Blob> {
  const pdf = await pdfDocument(draftKey);
  try {
    if (pageNumber < 1 || pageNumber > pdf.numPages) throw new Error("Trang PDF không hợp lệ.");
    const page = await pdf.getPage(pageNumber);
    let viewport = page.getViewport({ scale: dpi / 72 });
    if (viewport.width * viewport.height > 24_000_000) {
      const scale = Math.sqrt(24_000_000 / (viewport.width * viewport.height));
      viewport = page.getViewport({ scale: (dpi / 72) * scale });
    }
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(viewport.width);
    canvas.height = Math.ceil(viewport.height);
    const context = canvas.getContext("2d", { alpha: false });
    if (!context) throw new Error("Không tạo được canvas trang PDF.");
    context.fillStyle = "white";
    context.fillRect(0, 0, canvas.width, canvas.height);
    await page.render({ canvas, canvasContext: context, viewport }).promise;
    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (result) => (result ? resolve(result) : reject(new Error("Không tạo được ảnh trang PDF."))),
        "image/webp",
        0.88,
      ),
    );
    canvas.width = 0;
    canvas.height = 0;
    page.cleanup();
    return blob;
  } finally {
    await pdf.destroy();
  }
}

export async function createClientOcrCrop(
  draftKey: string,
  assetId: string,
  pageNumber: number,
  bbox: NormalizedBbox,
): Promise<{ blobKey: string; blob: Blob; width: number; height: number }> {
  const pdf = await pdfDocument(draftKey);
  try {
    if (pageNumber < 1 || pageNumber > pdf.numPages) throw new Error("Trang PDF không hợp lệ.");
    const page = await pdf.getPage(pageNumber);
    let viewport = page.getViewport({ scale: CROP_DPI / 72 });
    const rawWidth = Math.ceil((bbox[2] - bbox[0]) * viewport.width);
    const rawHeight = Math.ceil((bbox[3] - bbox[1]) * viewport.height);
    if (rawWidth * rawHeight > MAX_CROP_PIXELS) {
      const scale = Math.sqrt(MAX_CROP_PIXELS / (rawWidth * rawHeight));
      viewport = page.getViewport({ scale: (CROP_DPI / 72) * scale });
    }
    const pageCanvas = document.createElement("canvas");
    pageCanvas.width = Math.ceil(viewport.width);
    pageCanvas.height = Math.ceil(viewport.height);
    const context = pageCanvas.getContext("2d", { alpha: false });
    if (!context) throw new Error("Không tạo được canvas crop.");
    context.fillStyle = "white";
    context.fillRect(0, 0, pageCanvas.width, pageCanvas.height);
    await page.render({ canvas: pageCanvas, canvasContext: context, viewport }).promise;
    const x = Math.floor(bbox[0] * pageCanvas.width);
    const y = Math.floor(bbox[1] * pageCanvas.height);
    const width = Math.max(1, Math.ceil((bbox[2] - bbox[0]) * pageCanvas.width));
    const height = Math.max(1, Math.ceil((bbox[3] - bbox[1]) * pageCanvas.height));
    const crop = document.createElement("canvas");
    crop.width = width;
    crop.height = height;
    crop.getContext("2d")?.drawImage(pageCanvas, x, y, width, height, 0, 0, width, height);
    const blob = await new Promise<Blob>((resolve, reject) =>
      crop.toBlob(
        (result) => (result ? resolve(result) : reject(new Error("Không mã hóa được crop WebP."))),
        "image/webp",
        0.9,
      ),
    );
    const blobKey = `${draftKey}:asset:${assetId}`;
    await putClientOcrBlob(blobKey, blob);
    pageCanvas.width = 0;
    pageCanvas.height = 0;
    crop.width = 0;
    crop.height = 0;
    page.cleanup();
    return { blobKey, blob, width, height };
  } finally {
    await pdf.destroy();
  }
}

export async function clientOcrAssetBlob(source: string): Promise<Blob | null> {
  if (!source.startsWith("client-ocr:")) return null;
  return getClientOcrBlob(source.slice("client-ocr:".length));
}
