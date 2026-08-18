import type { PSM } from "tesseract.js";

import { parseAnswerKeyText, type ExamType } from "@/lib/utils";
import { preprocessForOcr, terminatePreprocessWorker } from "./preprocess";
import { BrowserTesseractPool } from "./tesseract-runtime";

const MAX_ANSWER_KEY_BYTES = 15 * 1024 * 1024;
const MAX_IMAGE_PIXELS = 20_000_000;
const ANSWER_KEY_WHITELIST = "0123456789ABCD().:- ";

export type LocalAnswerKeyResult = {
  answer_key: Record<number, string>;
  recognized_count: number;
  ignored: string[];
  missing: number[];
  raw_text: string;
  duration_ms: number;
};

async function imageCanvas(file: File): Promise<HTMLCanvasElement> {
  let source: CanvasImageSource;
  let release: (() => void) | undefined;
  if (typeof createImageBitmap === "function") {
    const bitmap = await createImageBitmap(file);
    source = bitmap;
    release = () => bitmap.close();
  } else {
    const url = URL.createObjectURL(file);
    const image = new Image();
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error("Không giải mã được ảnh đáp án."));
      image.src = url;
    });
    source = image;
    release = () => URL.revokeObjectURL(url);
  }
  const sourceWidth = "naturalWidth" in source ? source.naturalWidth : source.width;
  const sourceHeight = "naturalHeight" in source ? source.naturalHeight : source.height;
  const scale = sourceWidth * sourceHeight > MAX_IMAGE_PIXELS
    ? Math.sqrt(MAX_IMAGE_PIXELS / (sourceWidth * sourceHeight))
    : 1;
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.floor(sourceWidth * scale));
  canvas.height = Math.max(1, Math.floor(sourceHeight * scale));
  const context = canvas.getContext("2d", { alpha: false, willReadFrequently: true });
  if (!context) throw new Error("Không tạo được canvas cho ảnh đáp án.");
  context.fillStyle = "white";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(source, 0, 0, canvas.width, canvas.height);
  release?.();
  return canvas;
}

export async function recognizeAnswerKeyImage(
  file: File,
  examType: ExamType,
): Promise<LocalAnswerKeyResult> {
  if (!file.type.startsWith("image/")) throw new Error("Vui lòng chọn một file ảnh.");
  if (file.size > MAX_ANSWER_KEY_BYTES) throw new Error("Ảnh đáp án vượt giới hạn 15 MiB.");
  const startedAt = performance.now();
  const canvas = await imageCanvas(file);
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) throw new Error("Không đọc được pixel ảnh đáp án.");
  const original = context.getImageData(0, 0, canvas.width, canvas.height);
  const pool = new BrowserTesseractPool(1);
  try {
    const baselineImage = await preprocessForOcr(
      new ImageData(new Uint8ClampedArray(original.data), original.width, original.height),
      "baseline",
    );
    const baselineCanvas = document.createElement("canvas");
    baselineCanvas.width = baselineImage.width;
    baselineCanvas.height = baselineImage.height;
    baselineCanvas.getContext("2d")?.putImageData(baselineImage, 0, 0);
    const baseline = await pool.recognize(
      baselineCanvas,
      1,
      "baseline",
      "6" as PSM,
      ANSWER_KEY_WHITELIST,
    );
    let rawText = baseline.lines.map((line) => line.text).join("\n");
    let parsed = parseAnswerKeyText(rawText);
    const start = examType === "listening" ? 1 : 101;
    const end = examType === "listening" ? 100 : 200;
    const validBaseline = Object.fromEntries(
      Object.entries(parsed.answers).filter(([number]) => Number(number) >= start && Number(number) <= end),
    );
    if (Object.keys(validBaseline).length < 90 || baseline.medianConfidence < 70) {
      const recoveryImage = await preprocessForOcr(
        new ImageData(new Uint8ClampedArray(original.data), original.width, original.height),
        "recovery",
      );
      const recoveryCanvas = document.createElement("canvas");
      recoveryCanvas.width = recoveryImage.width;
      recoveryCanvas.height = recoveryImage.height;
      recoveryCanvas.getContext("2d")?.putImageData(recoveryImage, 0, 0);
      const recovery = await pool.recognize(
        recoveryCanvas,
        1,
        "recovery",
        "11" as PSM,
        ANSWER_KEY_WHITELIST,
      );
      const recoveryText = recovery.lines.map((line) => line.text).join("\n");
      const recoveryParsed = parseAnswerKeyText(recoveryText);
      // Original pass wins on disagreement. Recovery only fills missing rows.
      parsed = {
        answers: { ...recoveryParsed.answers, ...parsed.answers },
        duplicates: [...parsed.duplicates, ...recoveryParsed.duplicates],
      };
      rawText = `${rawText}\n${recoveryText}`;
      recoveryCanvas.width = 0;
      recoveryCanvas.height = 0;
    }
    const answerKey = Object.fromEntries(
      Object.entries(parsed.answers)
        .map(([number, letter]) => [Number(number), letter] as const)
        .filter(([number]) => number >= start && number <= end),
    );
    const missing = Array.from({ length: end - start + 1 }, (_, index) => start + index).filter(
      (number) => !answerKey[number],
    );
    baselineCanvas.width = 0;
    baselineCanvas.height = 0;
    return {
      answer_key: answerKey,
      recognized_count: Object.keys(answerKey).length,
      ignored: parsed.duplicates,
      missing,
      raw_text: rawText,
      duration_ms: Math.round(performance.now() - startedAt),
    };
  } finally {
    canvas.width = 0;
    canvas.height = 0;
    await pool.terminate();
    terminatePreprocessWorker();
  }
}

/** Render and OCR a scanned answer-key PDF locally; no source bytes leave the browser. */
export async function recognizeAnswerKeyPdf(
  file: File,
  examType: ExamType,
): Promise<LocalAnswerKeyResult> {
  if (file.size > MAX_ANSWER_KEY_BYTES) throw new Error("PDF đáp án vượt giới hạn 15 MiB.");
  const bytes = await file.arrayBuffer();
  if (new TextDecoder("ascii").decode(bytes.slice(0, 5)) !== "%PDF-") {
    throw new Error("File đáp án không có PDF magic hợp lệ.");
  }
  const pdfjs = await import("pdfjs-dist");
  pdfjs.GlobalWorkerOptions.workerSrc = "/ocr/pdfjs/pdf.worker.min.mjs";
  const pdfDocument = await pdfjs.getDocument({ data: bytes }).promise;
  if (pdfDocument.numPages > 100) {
    await pdfDocument.destroy();
    throw new Error("PDF đáp án vượt quá 100 trang.");
  }
  const startedAt = performance.now();
  const pool = new BrowserTesseractPool(1);
  const rawPages: string[] = [];
  const duplicates: string[] = [];
  const mergedAnswers: Record<number, string> = {};
  const start = examType === "listening" ? 1 : 101;
  const end = examType === "listening" ? 100 : 200;
  try {
    for (let pageNumber = 1; pageNumber <= pdfDocument.numPages; pageNumber += 1) {
      const page = await pdfDocument.getPage(pageNumber);
      const initial = page.getViewport({ scale: 225 / 72 });
      const ratio = initial.width * initial.height > MAX_IMAGE_PIXELS
        ? Math.sqrt(MAX_IMAGE_PIXELS / (initial.width * initial.height))
        : 1;
      const viewport = page.getViewport({ scale: (225 / 72) * ratio });
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.ceil(viewport.width));
      canvas.height = Math.max(1, Math.ceil(viewport.height));
      const context = canvas.getContext("2d", { alpha: false, willReadFrequently: true });
      if (!context) throw new Error("Không tạo được canvas PDF đáp án.");
      context.fillStyle = "white";
      context.fillRect(0, 0, canvas.width, canvas.height);
      await page.render({ canvas, canvasContext: context, viewport }).promise;
      const original = context.getImageData(0, 0, canvas.width, canvas.height);
      try {
        const baselineImage = await preprocessForOcr(
          new ImageData(new Uint8ClampedArray(original.data), original.width, original.height),
          "baseline",
        );
        const baselineCanvas = document.createElement("canvas");
        baselineCanvas.width = baselineImage.width;
        baselineCanvas.height = baselineImage.height;
        baselineCanvas.getContext("2d")?.putImageData(baselineImage, 0, 0);
        const baseline = await pool.recognize(
          baselineCanvas, pageNumber, "baseline", "6" as PSM, ANSWER_KEY_WHITELIST,
        );
        let pageText = baseline.lines.map((line) => line.text).join("\n");
        let parsed = parseAnswerKeyText(pageText);
        if (Object.keys(parsed.answers).length < 90 || baseline.medianConfidence < 70) {
          const recoveryImage = await preprocessForOcr(
            new ImageData(new Uint8ClampedArray(original.data), original.width, original.height),
            "recovery",
          );
          const recoveryCanvas = document.createElement("canvas");
          recoveryCanvas.width = recoveryImage.width;
          recoveryCanvas.height = recoveryImage.height;
          recoveryCanvas.getContext("2d")?.putImageData(recoveryImage, 0, 0);
          const recovery = await pool.recognize(
            recoveryCanvas, pageNumber, "recovery", "11" as PSM, ANSWER_KEY_WHITELIST,
          );
          const recoveryText = recovery.lines.map((line) => line.text).join("\n");
          const recoveryParsed = parseAnswerKeyText(recoveryText);
          parsed = {
            answers: { ...recoveryParsed.answers, ...parsed.answers },
            duplicates: [...parsed.duplicates, ...recoveryParsed.duplicates],
          };
          pageText += `\n${recoveryText}`;
          recoveryCanvas.width = 0;
          recoveryCanvas.height = 0;
        }
        rawPages.push(pageText);
        duplicates.push(...parsed.duplicates);
        for (const [rawNumber, letter] of Object.entries(parsed.answers)) {
          const number = Number(rawNumber);
          if (number < start || number > end) continue;
          if (mergedAnswers[number] && mergedAnswers[number] !== letter) {
            duplicates.push(`${number}(${mergedAnswers[number]}/${letter})`);
            continue;
          }
          mergedAnswers[number] = letter;
        }
        baselineCanvas.width = 0;
        baselineCanvas.height = 0;
      } finally {
        canvas.width = 0;
        canvas.height = 0;
        page.cleanup();
      }
    }
  } finally {
    await pool.terminate();
    terminatePreprocessWorker();
    await pdfDocument.destroy();
  }
  const missing = Array.from({ length: end - start + 1 }, (_, index) => start + index)
    .filter((number) => !mergedAnswers[number]);
  return {
    answer_key: mergedAnswers,
    recognized_count: Object.keys(mergedAnswers).length,
    ignored: duplicates,
    missing,
    raw_text: rawPages.join("\n"),
    duration_ms: Math.round(performance.now() - startedAt),
  };
}
