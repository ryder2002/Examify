import type { LoggerMessage, PSM, Worker as TesseractWorker } from "tesseract.js";
import type { NormalizedBbox, OcrLine, OcrToken } from "./types";
import { median, unionBbox } from "./layout";

type ProgressLogger = (message: LoggerMessage) => void;

type TesseractWord = {
  text?: string;
  confidence?: number;
  bbox?: { x0: number; y0: number; x1: number; y1: number };
};

type TesseractLine = {
  text?: string;
  confidence?: number;
  bbox?: { x0: number; y0: number; x1: number; y1: number };
  words?: TesseractWord[];
};

type TesseractParagraph = { lines?: TesseractLine[] };
type TesseractBlock = { paragraphs?: TesseractParagraph[] };

function normalizedBox(
  box: { x0: number; y0: number; x1: number; y1: number } | undefined,
  width: number,
  height: number,
): NormalizedBbox {
  if (!box) return [0, 0, 0, 0];
  return [box.x0 / width, box.y0 / height, box.x1 / width, box.y1 / height].map((value) =>
    Math.max(0, Math.min(1, value)),
  ) as NormalizedBbox;
}

export class BrowserTesseractPool {
  private workers: TesseractWorker[] = [];
  private queue: Array<() => void> = [];
  private available: TesseractWorker[] = [];
  private readonly parameterKeys = new WeakMap<TesseractWorker, string>();
  private initializePromise: Promise<void> | null = null;

  constructor(
    private readonly count: 1 | 2,
    private readonly logger?: ProgressLogger,
  ) {}

  async initialize(): Promise<void> {
    if (this.workers.length) return;
    if (this.initializePromise) return this.initializePromise;
    this.initializePromise = (async () => {
      const { createWorker, OEM } = await import("tesseract.js");
      this.workers = await Promise.all(
        Array.from({ length: this.count }, () =>
          createWorker("eng", OEM.LSTM_ONLY, {
            workerPath: "/ocr/tesseract/worker.min.js",
            // Pin the stable SIMD/LSTM core instead of directory auto-detection.
            // Chromium currently advertises relaxed-SIMD on some hosts where the
            // corresponding tesseract-core build aborts on DotProductSSE. Modern
            // supported desktop browsers all provide baseline WebAssembly SIMD.
            corePath: "/ocr/tesseract/core/tesseract-core-simd-lstm.wasm.js",
            langPath: "/ocr/tesseract/lang",
            workerBlobURL: false,
            logger: this.logger,
            errorHandler: (reason) => console.error("Browser Tesseract worker error", reason),
          }),
        ),
      );
      this.available = [...this.workers];
    })();
    try {
      await this.initializePromise;
    } catch (reason) {
      this.initializePromise = null;
      throw reason;
    }
  }

  private async acquire(): Promise<TesseractWorker> {
    const available = this.available.pop();
    if (available) return available;
    await new Promise<void>((resolve) => this.queue.push(resolve));
    return this.available.pop() as TesseractWorker;
  }

  private release(worker: TesseractWorker): void {
    this.available.push(worker);
    this.queue.shift()?.();
  }

  async recognize(
    image: HTMLCanvasElement,
    page: number,
    source: OcrToken["source"],
    psm: PSM,
    whitelist?: string,
  ): Promise<{ lines: OcrLine[]; tokens: OcrToken[]; medianConfidence: number }> {
    await this.initialize();
    const worker = await this.acquire();
    try {
      const parameterKey = `${psm}|${whitelist || ""}`;
      if (this.parameterKeys.get(worker) !== parameterKey) {
        await worker.setParameters({
          tessedit_pageseg_mode: psm,
          preserve_interword_spaces: "1",
          user_defined_dpi: "225",
          ...(whitelist ? { tessedit_char_whitelist: whitelist } : {}),
        });
        this.parameterKeys.set(worker, parameterKey);
      }
      // The parser consumes blocks -> paragraphs -> lines -> words. TSV is a
      // second serialized representation of the same geometry and is never
      // read, so requesting it only increases worker transfer/parse cost.
      const result = await worker.recognize(image, {}, { text: true, blocks: true });
      const width = image.width;
      const height = image.height;
      const blocks = ((result.data.blocks || []) as unknown as TesseractBlock[]);
      const lines: OcrLine[] = [];
      const tokens: OcrToken[] = [];
      for (const block of blocks) {
        for (const paragraph of block.paragraphs || []) {
          for (const line of paragraph.lines || []) {
            const lineTokens: OcrToken[] = [];
            for (const word of line.words || []) {
              const text = (word.text || "").trim();
              if (!text) continue;
              const token: OcrToken = {
                text,
                confidence: Number(word.confidence || 0),
                bbox: normalizedBox(word.bbox, width, height),
                page,
                source,
              };
              lineTokens.push(token);
              tokens.push(token);
            }
            const text = (line.text || lineTokens.map((token) => token.text).join(" ")).trim();
            if (!text) continue;
            lines.push({
              text,
              confidence: Number(line.confidence || median(lineTokens.map((token) => token.confidence))),
              bbox: line.bbox
                ? normalizedBox(line.bbox, width, height)
                : unionBbox(lineTokens.map((token) => token.bbox)),
              page,
              tokens: lineTokens,
              source,
            });
          }
        }
      }
      return { lines, tokens, medianConfidence: median(tokens.map((token) => token.confidence)) };
    } finally {
      this.release(worker);
    }
  }

  async terminate(): Promise<void> {
    const workers = this.workers;
    this.workers = [];
    this.available = [];
    this.queue = [];
    this.initializePromise = null;
    await Promise.all(workers.map((worker) => worker.terminate().catch(() => undefined)));
  }
}
