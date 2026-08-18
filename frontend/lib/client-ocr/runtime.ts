import type { PDFDocumentProxy, PDFPageProxy } from "pdfjs-dist";
import type { TextItem } from "pdfjs-dist/types/src/display/api";
import type { PSM } from "tesseract.js";

import type { Issue } from "@/lib/utils";
import { assertClientOcrAssets, detectBrowserOcrCapabilities, clientOcrWorkerCount } from "./capabilities";
import {
  detectRecurringRegions,
  extractAnchors,
  median,
  mergeRecoveryTokens,
  suppressRepeatedMarginLines,
  unionBbox,
} from "./layout";
import {
  clientOcrDraftKey,
  deleteClientOcrDraft,
  getClientOcrDraft,
  persistClientOcrSource,
  putClientOcrBlob,
  putClientOcrDraft,
} from "./local-drafts";
import { parseToeicPages } from "./parser";
import { preprocessForOcr, terminatePreprocessWorker } from "./preprocess";
import { BrowserTesseractPool } from "./tesseract-runtime";
import {
  CLIENT_OCR_PIPELINE_VERSION,
  CLIENT_OCR_MODEL_VERSION,
  CLIENT_OCR_SCHEMA_VERSION,
  type ClientOcrDraft,
  type ClientOcrManifestV1,
  type ClientOcrProgress,
  type ClientOcrRunOptions,
  type NormalizedBbox,
  type OcrLine,
  type OcrToken,
  type PageLayoutEvidence,
} from "./types";

const MAX_PDF_BYTES = 50 * 1024 * 1024;
const MAX_PAGES = 500;
const MAX_CANVAS_PIXELS = 24_000_000;
const THUMBNAIL_DPI = 120;
const DEFAULT_DPI = 225;
const MAX_SAME_LINE_GAP = 0.03;

type PdfJs = typeof import("pdfjs-dist");

function emit(options: ClientOcrRunOptions, progress: ClientOcrProgress): void {
  options.onProgress?.(progress);
}

function abortIfNeeded(signal?: AbortSignal): void {
  if (signal?.aborted) throw new DOMException("OCR đã bị hủy.", "AbortError");
}

async function mapBounded<T>(
  values: number[],
  concurrency: number,
  task: (value: number) => Promise<T>,
): Promise<T[]> {
  const results = new Array<T>(values.length);
  let cursor = 0;
  const runners = Array.from({ length: Math.min(Math.max(1, concurrency), values.length) }, async () => {
    while (true) {
      const index = cursor;
      cursor += 1;
      if (index >= values.length) return;
      results[index] = await task(values[index]);
    }
  });
  await Promise.all(runners);
  return results;
}

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

async function sha256(buffer: ArrayBuffer): Promise<string> {
  if (!crypto?.subtle) throw new Error("Trình duyệt không hỗ trợ SHA-256 cho checkpoint OCR.");
  return bytesToHex(new Uint8Array(await crypto.subtle.digest("SHA-256", buffer)));
}

function validatePdfFile(file: File, bytes: ArrayBuffer): void {
  if (file.size <= 0) throw new Error("File PDF trống.");
  if (file.size > MAX_PDF_BYTES) throw new Error("PDF vượt quá giới hạn 50 MiB.");
  const signature = new TextDecoder("ascii").decode(bytes.slice(0, 5));
  if (signature !== "%PDF-") throw new Error("File không có PDF magic hợp lệ.");
}

async function loadPdfJs(): Promise<PdfJs> {
  const pdfjs = await import("pdfjs-dist");
  pdfjs.GlobalWorkerOptions.workerSrc = "/ocr/pdfjs/pdf.worker.min.mjs";
  return pdfjs;
}

function canvasFor(width: number, height: number): HTMLCanvasElement {
  if (width * height > MAX_CANVAS_PIXELS) {
    throw new Error(`Trang PDF vượt giới hạn canvas 24 MP (${width}×${height}).`);
  }
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

async function renderPage(page: PDFPageProxy, dpi: number): Promise<HTMLCanvasElement> {
  const viewport = page.getViewport({ scale: dpi / 72 });
  let width = Math.max(1, Math.ceil(viewport.width));
  let height = Math.max(1, Math.ceil(viewport.height));
  if (width * height > MAX_CANVAS_PIXELS) {
    const scale = Math.sqrt(MAX_CANVAS_PIXELS / (width * height));
    width = Math.max(1, Math.floor(width * scale));
    height = Math.max(1, Math.floor(height * scale));
    const boundedViewport = page.getViewport({ scale: (dpi / 72) * scale });
    const canvas = canvasFor(width, height);
    const context = canvas.getContext("2d", { alpha: false, willReadFrequently: true });
    if (!context) throw new Error("Không tạo được PDF canvas.");
    context.fillStyle = "white";
    context.fillRect(0, 0, width, height);
    await page.render({ canvas, canvasContext: context, viewport: boundedViewport }).promise;
    return canvas;
  }
  const canvas = canvasFor(width, height);
  const context = canvas.getContext("2d", { alpha: false, willReadFrequently: true });
  if (!context) throw new Error("Không tạo được PDF canvas.");
  context.fillStyle = "white";
  context.fillRect(0, 0, width, height);
  await page.render({ canvas, canvasContext: context, viewport }).promise;
  return canvas;
}

/**
 * A cheap raster-only column hint. Text columns leave a white gutter near the
 * page center; photos/one-column pages do not. Returning null keeps the old
 * OCR locator as a conservative fallback for unusual scans.
 */
function detectTwoColumnHint(image: ImageData): boolean | null {
  const { width, height, data } = image;
  const xStart = Math.floor(width * 0.08);
  const xEnd = Math.ceil(width * 0.92);
  const yStart = Math.floor(height * 0.1);
  const yEnd = Math.ceil(height * 0.9);
  const xStep = Math.max(1, Math.floor(width / 320));
  const yStep = Math.max(1, Math.floor(height / 240));
  let leftInk = 0;
  let rightInk = 0;
  let centerInk = 0;
  let leftSamples = 0;
  let rightSamples = 0;
  let centerSamples = 0;
  for (let y = yStart; y < yEnd; y += yStep) {
    for (let x = xStart; x < xEnd; x += xStep) {
      const offset = (y * width + x) * 4;
      const ink = data[offset] * 0.299 + data[offset + 1] * 0.587 + data[offset + 2] * 0.114 < 180 ? 1 : 0;
      const normalized = x / width;
      if (normalized >= 0.1 && normalized < 0.44) {
        leftInk += ink;
        leftSamples += 1;
      } else if (normalized >= 0.56 && normalized < 0.9) {
        rightInk += ink;
        rightSamples += 1;
      } else if (normalized >= 0.46 && normalized < 0.54) {
        centerInk += ink;
        centerSamples += 1;
      }
    }
  }
  const leftDensity = leftSamples ? leftInk / leftSamples : 0;
  const rightDensity = rightSamples ? rightInk / rightSamples : 0;
  const centerDensity = centerSamples ? centerInk / centerSamples : 0;
  const sideDensity = Math.min(leftDensity, rightDensity);
  if (sideDensity < 0.012) return null;
  if (centerDensity <= sideDensity * 0.32) return true;
  if (centerDensity >= sideDensity * 0.62) return false;
  return null;
}

function clearCanvas(canvas: HTMLCanvasElement): void {
  const context = canvas.getContext("2d");
  context?.clearRect(0, 0, canvas.width, canvas.height);
  canvas.width = 0;
  canvas.height = 0;
}

type CanvasRegion = { x: number; y: number; width: number; height: number };
type PageLayoutPlan = { evidence: PageLayoutEvidence; twoColumns: boolean };

function cropCanvas(source: HTMLCanvasElement, region: CanvasRegion): HTMLCanvasElement {
  const width = Math.max(1, Math.round(source.width * region.width));
  const height = Math.max(1, Math.round(source.height * region.height));
  const canvas = canvasFor(width, height);
  const context = canvas.getContext("2d", { alpha: false });
  if (!context) throw new Error("Không tạo được canvas cho cột OCR.");
  context.fillStyle = "white";
  context.fillRect(0, 0, width, height);
  context.drawImage(
    source,
    Math.round(source.width * region.x),
    Math.round(source.height * region.y),
    width,
    height,
    0,
    0,
    width,
    height,
  );
  return canvas;
}

function mapRegionBox(bbox: NormalizedBbox, region: CanvasRegion): NormalizedBbox {
  return [
    region.x + bbox[0] * region.width,
    region.y + bbox[1] * region.height,
    region.x + bbox[2] * region.width,
    region.y + bbox[3] * region.height,
  ];
}

function mapRegionEvidence(
  result: Awaited<ReturnType<BrowserTesseractPool["recognize"]>>,
  region: CanvasRegion,
): Awaited<ReturnType<BrowserTesseractPool["recognize"]>> {
  const tokens = result.tokens.map((token) => ({ ...token, bbox: mapRegionBox(token.bbox, region) }));
  const lines = result.lines.map((line) => ({
    ...line,
    bbox: mapRegionBox(line.bbox, region),
    tokens: line.tokens.map((token) => ({ ...token, bbox: mapRegionBox(token.bbox, region) })),
  }));
  return { ...result, tokens, lines };
}

function likelyTwoColumns(layout: PageLayoutEvidence): boolean {
  const body = layout.lines.filter((line) => line.bbox[1] >= 0.1 && line.bbox[3] <= 0.93);
  const left = body.filter((line) => (line.bbox[0] + line.bbox[2]) / 2 < 0.46).length;
  const right = body.filter((line) => (line.bbox[0] + line.bbox[2]) / 2 > 0.54).length;
  return left >= 3 && right >= 3;
}

function deduplicateMappedEvidence(
  results: Array<Awaited<ReturnType<BrowserTesseractPool["recognize"]>>>,
): Awaited<ReturnType<BrowserTesseractPool["recognize"]>> {
  const lines: OcrLine[] = [];
  const tokens: OcrToken[] = [];
  for (const result of results) {
    for (const token of result.tokens) {
      const duplicate = tokens.some(
        (existing) =>
          normalizeLayoutToken(existing.text) === normalizeLayoutToken(token.text) &&
          bboxOverlap(existing.bbox, token.bbox) >= 0.65,
      );
      if (!duplicate) tokens.push(token);
    }
    for (const line of result.lines) {
      const duplicate = lines.some(
        (existing) =>
          normalizeLayoutToken(existing.text) === normalizeLayoutToken(line.text) &&
          bboxOverlap(existing.bbox, line.bbox) >= 0.65,
      );
      if (!duplicate) lines.push(line);
    }
  }
  return {
    lines,
    tokens,
    medianConfidence: median(tokens.map((token) => token.confidence)),
  };
}

function normalizeLayoutToken(value: string): string {
  return value.normalize("NFKC").toUpperCase().replace(/\s+/g, " ").trim();
}

function bboxOverlap(left: NormalizedBbox, right: NormalizedBbox): number {
  const x0 = Math.max(left[0], right[0]);
  const y0 = Math.max(left[1], right[1]);
  const x1 = Math.min(left[2], right[2]);
  const y1 = Math.min(left[3], right[3]);
  const intersection = Math.max(0, x1 - x0) * Math.max(0, y1 - y0);
  const smaller = Math.min(
    Math.max(0, left[2] - left[0]) * Math.max(0, left[3] - left[1]),
    Math.max(0, right[2] - right[0]) * Math.max(0, right[3] - right[1]),
  );
  return smaller > 0 ? intersection / smaller : 0;
}

async function recognizeBaselinePage(
  pool: BrowserTesseractPool,
  canvas: HTMLCanvasElement,
  layout: PageLayoutEvidence,
  pageNumber: number,
  twoColumns?: boolean,
): Promise<Awaited<ReturnType<BrowserTesseractPool["recognize"]>>> {
  if (!(twoColumns ?? likelyTwoColumns(layout))) {
    return pool.recognize(canvas, pageNumber, "baseline", "6" as PSM);
  }
  // A narrow overlap protects glyphs sitting exactly on the gutter. Duplicate
  // evidence is removed only when normalized text and geometry both agree.
  const regions: CanvasRegion[] = [
    { x: 0.02, y: 0, width: 0.49, height: 1 },
    { x: 0.49, y: 0, width: 0.49, height: 1 },
  ];
  const recognized = await Promise.all(
    regions.map(async (region) => {
      const column = cropCanvas(canvas, region);
      try {
        const result = await pool.recognize(column, pageNumber, "baseline", "6" as PSM);
        return mapRegionEvidence(result, region);
      } finally {
        clearCanvas(column);
      }
    }),
  );
  return deduplicateMappedEvidence(recognized);
}

async function recognizeRecoveryPage(
  pool: BrowserTesseractPool,
  canvas: HTMLCanvasElement,
  layout: PageLayoutEvidence,
  pageNumber: number,
  twoColumns?: boolean,
): Promise<Awaited<ReturnType<BrowserTesseractPool["recognize"]>>> {
  if (!(twoColumns ?? likelyTwoColumns(layout))) {
    return pool.recognize(canvas, pageNumber, "recovery", "11" as PSM);
  }
  const regions: CanvasRegion[] = [
    { x: 0.02, y: 0, width: 0.49, height: 1 },
    { x: 0.49, y: 0, width: 0.49, height: 1 },
  ];
  const recognized = await Promise.all(
    regions.map(async (region) => {
      const column = cropCanvas(canvas, region);
      try {
        const result = await pool.recognize(column, pageNumber, "recovery", "11" as PSM);
        return mapRegionEvidence(result, region);
      } finally {
        clearCanvas(column);
      }
    }),
  );
  return deduplicateMappedEvidence(recognized);
}

function textLayerLines(items: TextItem[], pageNumber: number, width: number, height: number): OcrLine[] {
  const tokens: OcrToken[] = items
    .filter((item) => item.str.trim())
    .map((item) => {
      const x = item.transform[4];
      const baselineY = item.transform[5];
      const itemHeight = Math.max(item.height || Math.abs(item.transform[3]) || 1, 1);
      const bbox: NormalizedBbox = [
        Math.max(0, Math.min(1, x / width)),
        Math.max(0, Math.min(1, (height - baselineY - itemHeight) / height)),
        Math.max(0, Math.min(1, (x + Math.max(item.width, 1)) / width)),
        Math.max(0, Math.min(1, (height - baselineY + itemHeight * 0.2) / height)),
      ];
      return {
        text: item.str.trim(),
        confidence: 100,
        bbox,
        page: pageNumber,
        source: "text-layer" as const,
      };
    })
    .sort((left, right) => left.bbox[1] - right.bbox[1] || left.bbox[0] - right.bbox[0]);

  const rows: OcrToken[][] = [];
  for (const token of tokens) {
    const center = (token.bbox[1] + token.bbox[3]) / 2;
    const row = rows.find((candidate) => {
      const box = unionBbox(candidate.map((item) => item.bbox));
      const candidateCenter = (box[1] + box[3]) / 2;
      // PDF.js emits both columns in baseline order.  Matching on Y alone
      // would merge a left-column question with a right-column question when
      // their baselines coincide, destroying anchors before the parser sees
      // them.  Keep a small horizontal-gap allowance for words on one line,
      // but never bridge the page gutter.
      const horizontalGap = token.bbox[0] > box[2]
        ? token.bbox[0] - box[2]
        : box[0] > token.bbox[2]
          ? box[0] - token.bbox[2]
          : 0;
      return horizontalGap <= MAX_SAME_LINE_GAP &&
        Math.abs(center - candidateCenter) <= Math.max(0.004, box[3] - box[1]) * 0.65;
    });
    if (row) row.push(token);
    else rows.push([token]);
  }
  return rows.map((row) => {
    row.sort((left, right) => left.bbox[0] - right.bbox[0]);
    return {
      text: row.map((token) => token.text).join(" ").replace(/\s+/g, " ").trim(),
      confidence: 100,
      bbox: unionBbox(row.map((token) => token.bbox)),
      page: pageNumber,
      tokens: row,
      source: "text-layer",
    };
  });
}

async function extractPdfTextLayer(page: PDFPageProxy, pageNumber: number): Promise<OcrLine[]> {
  const viewport = page.getViewport({ scale: 1 });
  const content = await page.getTextContent();
  return textLayerLines(
    content.items.filter((item): item is TextItem => "str" in item),
    pageNumber,
    viewport.width,
    viewport.height,
  );
}

function validToeicTextLayer(lines: OcrLine[]): boolean {
  const text = lines.map((line) => line.text).join("\n");
  const compactLength = text.replace(/\s/g, "").length;
  if (compactLength < 80 || lines.length < 5) return false;
  const replacementRatio = (text.match(/�/g)?.length || 0) / compactLength;
  if (replacementRatio > 0.02) return false;
  const hasStructuralAnchor =
    /\bPART\s+[1-7]\b/i.test(text) ||
    /\bDirections\b/i.test(text) ||
    /^\s*\d{1,3}[.)]?\s+/m.test(text) ||
    /[(][A-D][)]/i.test(text);
  return hasStructuralAnchor || compactLength >= 500;
}

function toCanvas(image: ImageData): HTMLCanvasElement {
  const canvas = canvasFor(image.width, image.height);
  const context = canvas.getContext("2d", { alpha: false });
  if (!context) throw new Error("Không tạo được OCR canvas.");
  context.putImageData(image, 0, 0);
  return canvas;
}

function rebuildLines(tokens: OcrToken[]): OcrLine[] {
  const rows: OcrToken[][] = [];
  for (const token of [...tokens].sort((left, right) => left.bbox[1] - right.bbox[1] || left.bbox[0] - right.bbox[0])) {
    const center = (token.bbox[1] + token.bbox[3]) / 2;
    const row = rows.find((candidate) => {
      const box = unionBbox(candidate.map((item) => item.bbox));
      const horizontalGap = token.bbox[0] > box[2]
        ? token.bbox[0] - box[2]
        : box[0] > token.bbox[2]
          ? box[0] - token.bbox[2]
          : 0;
      return horizontalGap <= MAX_SAME_LINE_GAP &&
        Math.abs(center - (box[1] + box[3]) / 2) <= Math.max(0.004, box[3] - box[1]) * 0.7;
    });
    if (row) row.push(token);
    else rows.push([token]);
  }
  return rows.map((row) => {
    row.sort((left, right) => left.bbox[0] - right.bbox[0]);
    return {
      text: row.map((token) => token.text).join(" "),
      confidence: median(row.map((token) => token.confidence)),
      bbox: unionBbox(row.map((token) => token.bbox)),
      page: row[0].page,
      tokens: row,
      source: row.some((token) => token.source === "recovery") ? "recovery" : row[0].source,
    };
  });
}

function needsRecovery(page: PageLayoutEvidence): boolean {
  if (page.medianConfidence < 70) return true;
  const characterCount = page.lines.reduce((total, line) => total + line.text.replace(/\s/g, "").length, 0);
  if (characterCount < 100) return true;
  const anchors = [...new Set(page.questionAnchors)].sort((left, right) => left - right);
  // A high page-level median can hide one faint question number under a
  // watermark. Any gap inside the detected page range is a cheap, bounded
  // signal to run PSM 11 recovery before the parser creates a missing slot.
  for (let index = 1; index < anchors.length; index += 1) {
    if (anchors[index] > anchors[index - 1] + 1) return true;
  }
  return page.questionAnchors.length > 0 && page.optionAnchorCount < Math.min(3, page.questionAnchors.length * 3);
}

function browserName(): string {
  return typeof navigator === "undefined" ? "unknown" : navigator.userAgent.slice(0, 300);
}

function makePageEvidence(
  page: number,
  width: number,
  height: number,
  lines: OcrLine[],
  textLayerUsed: boolean,
): PageLayoutEvidence {
  const anchors = extractAnchors(lines);
  return {
    page,
    width,
    height,
    lines,
    textLayerUsed,
    medianConfidence: median(lines.flatMap((line) => line.tokens.map((token) => token.confidence))),
    ...anchors,
  };
}

async function initializeDraft(
  options: ClientOcrRunOptions,
  hash: string,
  pageCount: number,
): Promise<ClientOcrDraft> {
  const key = clientOcrDraftKey(hash, CLIENT_OCR_PIPELINE_VERSION);
  if (options.forceRestart) await deleteClientOcrDraft(key);
  const existing = await getClientOcrDraft(key);
  if (existing && existing.examType === options.examType && existing.sourceSize === options.file.size) {
    const resumed = { ...existing, active: true, updatedAt: new Date().toISOString(), error: undefined };
    await putClientOcrDraft(resumed);
    return resumed;
  }
  const now = new Date().toISOString();
  await persistClientOcrSource(key, options.file);
  const media = [] as ClientOcrDraft["media"];
  for (const item of options.media || []) {
    const localBlobKey = `${key}:media:${item.uploadId}`;
    await putClientOcrBlob(localBlobKey, item.file);
    const { file: _file, ...metadata } = item;
    media.push({ ...metadata, localBlobKey });
  }
  const draft: ClientOcrDraft = {
    key,
    sourceSha256: hash,
    pipelineVersion: CLIENT_OCR_PIPELINE_VERSION,
    sourceName: options.file.name,
    sourceSize: options.file.size,
    examType: options.examType,
    requestedCount: options.requestedCount,
    pageCount,
    completedPages: [],
    pages: [],
    media,
    status: "active",
    active: true,
    createdAt: now,
    updatedAt: now,
  };
  await putClientOcrDraft(draft);
  return draft;
}

export async function runClientOcr(options: ClientOcrRunOptions): Promise<ClientOcrManifestV1> {
  const startedAt = performance.now();
  emit(options, { phase: "capability", progress: 1, message: "Đang kiểm tra trình duyệt…" });
  const capabilities = await detectBrowserOcrCapabilities(options.file.size);
  if (!capabilities.supported) {
    throw new Error(`${capabilities.reasons.join(" ")} Bạn vẫn có thể nhập đề thủ công.`);
  }
  await assertClientOcrAssets();
  abortIfNeeded(options.signal);
  emit(options, { phase: "hashing", progress: 3, message: "Đang tạo mã checkpoint cho PDF…" });
  const bytes = await options.file.arrayBuffer();
  validatePdfFile(options.file, bytes);
  const hash = await sha256(bytes.slice(0));
  abortIfNeeded(options.signal);

  emit(options, { phase: "loading-pdf", progress: 5, message: "Đang mở PDF tại trình duyệt…" });
  const pdfjs = await loadPdfJs();
  let document: PDFDocumentProxy;
  try {
    document = await pdfjs.getDocument({ data: bytes }).promise;
  } catch (reason) {
    const message = reason instanceof Error ? reason.message : String(reason);
    if (/password/i.test(message)) throw new Error("PDF có mật khẩu; hãy mở khóa trước khi OCR.");
    throw new Error(`Không mở được PDF: ${message}`);
  }
  if (document.numPages > MAX_PAGES) {
    await document.destroy();
    throw new Error(`PDF có ${document.numPages} trang, vượt giới hạn ${MAX_PAGES} trang.`);
  }

  let draft = await initializeDraft(options, hash, document.numPages);
  const pool = new BrowserTesseractPool(clientOcrWorkerCount(), (message) => {
    if (message.status === "recognizing text") {
      const base = 10;
      emit(options, {
        phase: "ocr",
        progress: Math.min(92, base + Math.round(message.progress * 4)),
        message: "Tesseract đang nhận dạng trên thiết bị…",
      });
    }
  });
  const runtimeIssues: Issue[] = [];

  try {
    // Layout pass: text-layer pages are free; scanned pages use a bounded 120
    // DPI locator pass. No thumbnail canvas survives its page.
    const pageNumbers = Array.from({ length: document.numPages }, (_, index) => index + 1);
    const concurrency = clientOcrWorkerCount();
    const layoutPlans = await mapBounded(pageNumbers, concurrency, async (pageNumber): Promise<PageLayoutPlan> => {
      abortIfNeeded(options.signal);
      emit(options, {
        phase: "layout",
        page: pageNumber,
        pageCount: document.numPages,
        progress: 5 + Math.round((pageNumber / document.numPages) * 12),
        message: `Đang phân tích bố cục trang ${pageNumber}/${document.numPages}…`,
      });
      const page = await document.getPage(pageNumber);
      try {
        const textLines = await extractPdfTextLayer(page, pageNumber);
        if (validToeicTextLayer(textLines)) {
          const viewport = page.getViewport({ scale: 1 });
          const evidence = makePageEvidence(pageNumber, viewport.width, viewport.height, textLines, true);
          return { evidence, twoColumns: likelyTwoColumns(evidence) };
        }
        const canvas = await renderPage(page, THUMBNAIL_DPI);
        try {
          const hint = detectTwoColumnHint(canvas.getContext("2d")?.getImageData(0, 0, canvas.width, canvas.height)
            || new ImageData(1, 1));
          if (hint !== null) {
            return {
              evidence: makePageEvidence(pageNumber, canvas.width, canvas.height, [], false),
              twoColumns: hint,
            };
          }
          const result = await pool.recognize(canvas, pageNumber, "baseline", "11" as PSM);
          const evidence = makePageEvidence(pageNumber, canvas.width, canvas.height, result.lines, false);
          return { evidence, twoColumns: likelyTwoColumns(evidence) };
        } finally {
          clearCanvas(canvas);
        }
      } finally {
        page.cleanup();
      }
    });
    const layoutPages = layoutPlans.map((plan) => plan.evidence);
    const twoColumnHints = layoutPlans.map((plan) => plan.twoColumns);
    const recurring = detectRecurringRegions(layoutPages);
    const watermarkRegions = recurring.filter((region) => region.kind === "watermark");

    const finalPages = [...draft.pages];
    const completed = new Set(draft.completedPages);
    const pendingPages = pageNumbers.filter((pageNumber) => !completed.has(pageNumber));
    const processPage = async (pageNumber: number): Promise<PageLayoutEvidence> => {
      abortIfNeeded(options.signal);
      emit(options, {
        phase: "ocr",
        page: pageNumber,
        pageCount: document.numPages,
        progress: 18 + Math.round((pageNumber / document.numPages) * 70),
        message: `Đang xử lý trang ${pageNumber}/${document.numPages} trên thiết bị…`,
      });
      const page = await document.getPage(pageNumber);
      try {
        const layout = layoutPages[pageNumber - 1];
        if (layout.textLayerUsed) return suppressRepeatedMarginLines(layout, recurring);

        const canvas = await renderPage(page, DEFAULT_DPI);
        try {
          const context = canvas.getContext("2d", { willReadFrequently: true });
          if (!context) throw new Error("Không đọc được pixel của trang PDF.");
          // Transfer the canvas read directly to the preprocessing worker. The
          // old extra Uint8ClampedArray copy kept a second full-page RGBA buffer
          // alive for every page. If recovery is needed, read the unchanged
          // canvas again instead of copying the first buffer up front.
          const original = context.getImageData(0, 0, canvas.width, canvas.height);
          const baselineImage = await preprocessForOcr(original, "baseline");
          const baselineCanvas = toCanvas(baselineImage);
          try {
            let baseline = await recognizeBaselinePage(
              pool,
              baselineCanvas,
              layout,
              pageNumber,
              twoColumnHints[pageNumber - 1],
            );
            let evidence = makePageEvidence(pageNumber, canvas.width, canvas.height, baseline.lines, false);
            // Raster projection is only a fast hint. If a page declared as
            // one-column produces evidence from both sides, pay for the
            // conservative split pass before parsing it.
            if (!twoColumnHints[pageNumber - 1] && likelyTwoColumns(evidence)) {
              baseline = await recognizeBaselinePage(pool, baselineCanvas, layout, pageNumber, true);
              evidence = makePageEvidence(pageNumber, canvas.width, canvas.height, baseline.lines, false);
            }

            if (needsRecovery(evidence)) {
              const pageWatermarks = watermarkRegions
                .filter((region) => region.pageNumbers.includes(pageNumber))
                .map((region) => region.bbox);
              const recoveryInput = context.getImageData(0, 0, canvas.width, canvas.height);
              const recoveryImage = await preprocessForOcr(recoveryInput, "recovery", pageWatermarks);
              const recoveryCanvas = toCanvas(recoveryImage);
              try {
                const recovery = await recognizeRecoveryPage(
                  pool,
                  recoveryCanvas,
                  layout,
                  pageNumber,
                  twoColumnHints[pageNumber - 1],
                );
                const merged = mergeRecoveryTokens(baseline.tokens, recovery.tokens);
                evidence = makePageEvidence(pageNumber, canvas.width, canvas.height, rebuildLines(merged.tokens), false);
                if (merged.conflicts.length) {
                  runtimeIssues.push({
                    code: "ocr_evidence_conflict",
                    message: `Trang ${pageNumber} có ${merged.conflicts.length} token bất đồng giữa ảnh gốc và recovery.`,
                    page: pageNumber,
                    question_number: null,
                    severity: "warning",
                  });
                }
              } finally {
                clearCanvas(recoveryCanvas);
              }
            }
            return suppressRepeatedMarginLines(evidence, recurring);
          } finally {
            clearCanvas(baselineCanvas);
          }
        } finally {
          clearCanvas(canvas);
        }
      } finally {
        page.cleanup();
      }
    };

    for (let cursor = 0; cursor < pendingPages.length; cursor += concurrency) {
      const batch = pendingPages.slice(cursor, cursor + concurrency);
      const evidences = await Promise.all(batch.map((pageNumber) => processPage(pageNumber)));
      for (let index = 0; index < batch.length; index += 1) {
        const pageNumber = batch[index];
        finalPages.push(evidences[index]);
        finalPages.sort((left, right) => left.page - right.page);
        completed.add(pageNumber);
        draft = {
          ...draft,
          pages: [...finalPages],
          completedPages: [...completed].sort((left, right) => left - right),
          updatedAt: new Date().toISOString(),
        };
        emit(options, {
          phase: "checkpoint",
          page: pageNumber,
          pageCount: document.numPages,
          progress: 18 + Math.round((pageNumber / document.numPages) * 70),
          message: `Đã lưu checkpoint trang ${pageNumber}/${document.numPages}.`,
        });
        await putClientOcrDraft(draft, { checkQuota: false });
      }
    }

    emit(options, { phase: "parsing", progress: 92, message: "Đang ghép câu, phương án và passage…" });
    const parsed = parseToeicPages(finalPages, options.examType, options.requestedCount);
    const manifest: ClientOcrManifestV1 = {
      schema_version: CLIENT_OCR_SCHEMA_VERSION,
      pipeline_version: CLIENT_OCR_PIPELINE_VERSION,
      source_sha256: hash,
      source_filename: options.file.name,
      source_size: options.file.size,
      page_count: document.numPages,
      exam_type: options.examType,
      requested_count: options.requestedCount,
      questions: parsed.questions,
      stimuli: parsed.stimuli,
      assets: [],
      media: draft.media || [],
      solutions: [],
      issues: [...parsed.issues, ...runtimeIssues],
      answer_key: {},
      metadata: {
        ocr_model: CLIENT_OCR_MODEL_VERSION,
        text_layer_pages: finalPages.filter((page) => page.textLayerUsed).length,
        ocr_pages: finalPages.filter((page) => !page.textLayerUsed).length,
        recovery_pages: finalPages.filter((page) => page.lines.some((line) => line.source === "recovery")).length,
        duration_ms: Math.round(performance.now() - startedAt),
        browser: browserName(),
      },
    };
    draft = {
      ...draft,
      status: "review",
      active: true,
      manifest,
      updatedAt: new Date().toISOString(),
    };
    await putClientOcrDraft(draft);
    emit(options, { phase: "review", progress: 100, message: "OCR local hoàn tất; cần kiểm tra các issue trước khi lưu." });
    return manifest;
  } catch (reason) {
    draft = {
      ...draft,
      status: "failed",
      active: false,
      error: reason instanceof Error ? reason.message : String(reason),
      updatedAt: new Date().toISOString(),
    };
    await putClientOcrDraft(draft).catch(() => undefined);
    throw reason;
  } finally {
    await pool.terminate();
    terminatePreprocessWorker();
    await document.destroy();
  }
}
