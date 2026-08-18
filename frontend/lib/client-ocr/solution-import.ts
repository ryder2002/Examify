import type { PDFDocumentProxy, PDFPageProxy } from "pdfjs-dist";
import type { TextItem } from "pdfjs-dist/types/src/display/api";
import type { PSM } from "tesseract.js";

import type { ExamType, SolutionEntry } from "@/lib/utils";
import { clientOcrWorkerCount, detectBrowserOcrCapabilities } from "./capabilities";
import { BrowserTesseractPool } from "./tesseract-runtime";
import type { OcrToken } from "./types";

const MAX_SOLUTION_PDF_BYTES = 20 * 1024 * 1024;
const MAX_SOLUTION_PAGES = 100;
const MAX_CANVAS_PIXELS = 24_000_000;
const SOLUTION_DPI = 225;

export type LocalSolutionOcrResult = {
  entries: SolutionEntry[];
  confidence: number;
  issues: Array<{ row?: number; code: string; message: string }>;
};

function allowedGroups(examType: ExamType): number[][] {
  if (examType === "reading") {
    return Array.from({ length: 100 }, (_, index) => [101 + index]);
  }
  return [
    ...Array.from({ length: 31 }, (_, index) => [1 + index]),
    ...Array.from({ length: 13 }, (_, index) => {
      const start = 32 + index * 3;
      return [start, start + 1, start + 2];
    }),
    ...Array.from({ length: 10 }, (_, index) => {
      const start = 71 + index * 3;
      return [start, start + 1, start + 2];
    }),
  ];
}

function keyFor(numbers: number[]): string {
  return numbers.length === 1 ? `q-${numbers[0]}` : `q-${numbers[0]}-${numbers.at(-1)}`;
}

function parseAnchor(value: string): number[] | null {
  const normalized = value
    .normalize("NFKC")
    .replace(/[–—]/g, "-")
    .replace(/^[^0-9]*/, "")
    .replace(/[^0-9.-].*$/, "")
    .replace(/[.:]+$/, "");
  const match = normalized.match(/^(\d{1,3})(?:-(\d{1,3}))?$/);
  if (!match) return null;
  const start = Number(match[1]);
  const end = Number(match[2] || start);
  if (end < start || end - start > 2) return null;
  return Array.from({ length: end - start + 1 }, (_, index) => start + index);
}

function renderCanvas(page: PDFPageProxy): Promise<HTMLCanvasElement> {
  const initial = page.getViewport({ scale: SOLUTION_DPI / 72 });
  const boundedScale = initial.width * initial.height <= MAX_CANVAS_PIXELS
    ? SOLUTION_DPI / 72
    : (SOLUTION_DPI / 72) * Math.sqrt(MAX_CANVAS_PIXELS / (initial.width * initial.height));
  const viewport = page.getViewport({ scale: boundedScale });
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.floor(viewport.width));
  canvas.height = Math.max(1, Math.floor(viewport.height));
  const context = canvas.getContext("2d", { alpha: false });
  if (!context) throw new Error("Không tạo được canvas cho PDF lời giải.");
  context.fillStyle = "white";
  context.fillRect(0, 0, canvas.width, canvas.height);
  return page.render({ canvas, canvasContext: context, viewport }).promise.then(() => canvas);
}

function clearCanvas(canvas: HTMLCanvasElement): void {
  canvas.getContext("2d")?.clearRect(0, 0, canvas.width, canvas.height);
  canvas.width = 0;
  canvas.height = 0;
}

async function scannedPdf(document: PDFDocumentProxy): Promise<boolean> {
  const sampledPages = Math.min(document.numPages, 3);
  let characters = 0;
  for (let pageNumber = 1; pageNumber <= sampledPages; pageNumber += 1) {
    const page = await document.getPage(pageNumber);
    const content = await page.getTextContent();
    characters += content.items
      .filter((item): item is TextItem => "str" in item)
      .reduce((total, item) => total + item.str.replace(/\s/g, "").length, 0);
    page.cleanup();
  }
  return characters < sampledPages * 80;
}

function rowText(
  tokens: OcrToken[],
  startY: number,
  endY: number,
  column: "content" | "translation",
): string {
  const selected = tokens
    .filter((token) => {
      const centerY = (token.bbox[1] + token.bbox[3]) / 2;
      const centerX = (token.bbox[0] + token.bbox[2]) / 2;
      return centerY >= startY && centerY < endY && token.bbox[0] >= 0.12 &&
        (column === "content" ? centerX < 0.58 : centerX >= 0.58);
    })
    .sort((left, right) => left.bbox[1] - right.bbox[1] || left.bbox[0] - right.bbox[0]);
  const lines: OcrToken[][] = [];
  for (const token of selected) {
    const center = (token.bbox[1] + token.bbox[3]) / 2;
    const line = lines.find((candidate) => {
      const candidateCenter = candidate.reduce(
        (total, item) => total + (item.bbox[1] + item.bbox[3]) / 2,
        0,
      ) / candidate.length;
      return Math.abs(center - candidateCenter) <= 0.008;
    });
    if (line) line.push(token);
    else lines.push([token]);
  }
  return lines
    .map((line) => line.sort((left, right) => left.bbox[0] - right.bbox[0]).map((token) => token.text).join(" "))
    .join("\n")
    .replace(/[ \t]+/g, " ")
    .trim();
}

export async function recognizeScannedSolutionPdf(
  file: File,
  examType: ExamType,
  onProgress?: (page: number, pageCount: number) => void,
): Promise<LocalSolutionOcrResult | null> {
  if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) return null;
  if (file.size <= 0 || file.size > MAX_SOLUTION_PDF_BYTES) {
    throw new Error("PDF lời giải phải từ 1 byte đến 20 MiB.");
  }
  const capabilities = await detectBrowserOcrCapabilities(file.size);
  if (!capabilities.supported) throw new Error(capabilities.reasons.join(" "));
  const bytes = await file.arrayBuffer();
  if (new TextDecoder("ascii").decode(bytes.slice(0, 5)) !== "%PDF-") {
    throw new Error("File lời giải không có PDF magic hợp lệ.");
  }
  const pdfjs = await import("pdfjs-dist");
  pdfjs.GlobalWorkerOptions.workerSrc = "/ocr/pdfjs/pdf.worker.min.mjs";
  const document = await pdfjs.getDocument({ data: bytes }).promise;
  if (document.numPages > MAX_SOLUTION_PAGES) {
    await document.destroy();
    throw new Error(`PDF lời giải vượt quá ${MAX_SOLUTION_PAGES} trang.`);
  }
  if (!(await scannedPdf(document))) {
    await document.destroy();
    return null;
  }

  const allowed = new Set(allowedGroups(examType).map((numbers) => numbers.join("-")));
  const pool = new BrowserTesseractPool(clientOcrWorkerCount());
  const entries = new Map<string, SolutionEntry>();
  const issues: LocalSolutionOcrResult["issues"] = [];
  const confidences: number[] = [];
  try {
    for (let pageNumber = 1; pageNumber <= document.numPages; pageNumber += 1) {
      onProgress?.(pageNumber, document.numPages);
      const page = await document.getPage(pageNumber);
      const canvas = await renderCanvas(page);
      try {
        const result = await pool.recognize(canvas, pageNumber, "baseline", "11" as PSM);
        const anchors = result.tokens
          .map((token) => ({ token, numbers: parseAnchor(token.text) }))
          .filter(
            (item): item is { token: OcrToken; numbers: number[] } =>
              Boolean(item.numbers) && item.token.bbox[0] <= 0.16 && allowed.has(item.numbers?.join("-") || ""),
          )
          .sort((left, right) => left.token.bbox[1] - right.token.bbox[1]);
        for (let index = 0; index < anchors.length; index += 1) {
          const anchor = anchors[index];
          const next = anchors[index + 1];
          const startY = Math.max(0, anchor.token.bbox[1] - 0.006);
          const endY = next ? Math.max(startY + 0.012, next.token.bbox[1] - 0.006) : 0.98;
          const content = rowText(result.tokens, startY, endY, "content");
          const translation = rowText(result.tokens, startY, endY, "translation");
          const key = keyFor(anchor.numbers);
          if (!content && !translation) {
            issues.push({
              row: anchor.numbers[0],
              code: "empty_ocr_row",
              message: `Câu ${anchor.numbers.join("-")} không có nội dung sau OCR.`,
            });
            continue;
          }
          const entry: SolutionEntry = {
            key,
            question_numbers: anchor.numbers,
            transcript: examType === "listening" ? content || null : null,
            explanation: examType === "reading" ? content || null : null,
            translation,
          };
          const previous = entries.get(key);
          if (!previous || JSON.stringify(entry).length > JSON.stringify(previous).length) entries.set(key, entry);
          confidences.push(anchor.token.confidence);
        }
      } finally {
        clearCanvas(canvas);
        page.cleanup();
      }
    }
  } finally {
    await pool.terminate();
    await document.destroy();
  }
  if (!entries.size) {
    throw new Error("OCR local không nhận diện được hàng STT nào; hãy scan lại bảng lời giải rõ hơn.");
  }
  return {
    entries: [...entries.values()].sort((left, right) => left.question_numbers[0] - right.question_numbers[0]),
    confidence: confidences.length
      ? confidences.reduce((total, value) => total + value, 0) / confidences.length / 100
      : 0,
    issues,
  };
}
