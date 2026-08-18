import type { AudioRef, ExamType, Issue, Question, SolutionEntry, Stimulus } from "@/lib/utils";

export const CLIENT_OCR_PIPELINE_VERSION = "client-tesseract-v1";
// The self-hosted language asset follows Tesseract.js' pinned LSTM model
// family; keeping the model version in the manifest makes cache invalidation
// explicit when the traineddata file is upgraded.
export const CLIENT_OCR_MODEL_VERSION = "eng-4.0.0_best_int";
export const CLIENT_OCR_SCHEMA_VERSION = 1 as const;

export type NormalizedBbox = [number, number, number, number];

export type OcrToken = {
  text: string;
  confidence: number;
  bbox: NormalizedBbox;
  page: number;
  source: "text-layer" | "baseline" | "recovery";
};

export type OcrLine = {
  text: string;
  confidence: number;
  bbox: NormalizedBbox;
  page: number;
  tokens: OcrToken[];
  source: OcrToken["source"];
};

export type PageLayoutEvidence = {
  page: number;
  width: number;
  height: number;
  lines: OcrLine[];
  textLayerUsed: boolean;
  medianConfidence: number;
  questionAnchors: number[];
  optionAnchorCount: number;
};

export type RecurringRegionKind =
  | "header"
  | "footer"
  | "page-number"
  | "watermark";

export type RecurringRegion = {
  kind: RecurringRegionKind;
  bbox: NormalizedBbox;
  pageNumbers: number[];
  occurrenceRatio: number;
  normalizedText: string;
};

export type ClientOcrAsset = {
  id: string;
  page: number;
  bbox: NormalizedBbox;
  width: number;
  height: number;
  contentType: "image/webp";
  size: number;
  localBlobKey: string;
  objectKey?: string;
};

export type ClientOcrMedia = Omit<AudioRef, "url"> & {
  localBlobKey: string;
  uploadId: string;
};

export type ClientOcrMediaInput = Omit<ClientOcrMedia, "localBlobKey"> & {
  file: File;
};

export type ClientOcrManifestV1 = {
  schema_version: typeof CLIENT_OCR_SCHEMA_VERSION;
  pipeline_version: typeof CLIENT_OCR_PIPELINE_VERSION;
  source_sha256: string;
  source_filename: string;
  source_size: number;
  page_count: number;
  exam_type: ExamType;
  requested_count: number | null;
  questions: Question[];
  stimuli: Stimulus[];
  assets: ClientOcrAsset[];
  media: ClientOcrMedia[];
  solutions: SolutionEntry[];
  issues: Issue[];
  answer_key: Record<string, string>;
  metadata: {
    ocr_model: typeof CLIENT_OCR_MODEL_VERSION;
    text_layer_pages: number;
    ocr_pages: number;
    recovery_pages: number;
    duration_ms: number;
    browser: string;
  };
};

export type ClientOcrDraftStatus =
  | "active"
  | "review"
  | "committing"
  | "committed"
  | "failed";

export type ClientOcrDraft = {
  key: string;
  sourceSha256: string;
  pipelineVersion: typeof CLIENT_OCR_PIPELINE_VERSION;
  sourceName: string;
  sourceSize: number;
  examType: ExamType;
  requestedCount: number | null;
  pageCount: number;
  completedPages: number[];
  pages: PageLayoutEvidence[];
  media: ClientOcrMedia[];
  status: ClientOcrDraftStatus;
  active: boolean;
  createdAt: string;
  updatedAt: string;
  committedAt?: string;
  manifest?: ClientOcrManifestV1;
  serverSessionId?: string;
  clientRequestId?: string;
  error?: string;
};

export type ClientOcrProgress = {
  phase:
    | "capability"
    | "hashing"
    | "loading-pdf"
    | "layout"
    | "ocr"
    | "parsing"
    | "checkpoint"
    | "review";
  page?: number;
  pageCount?: number;
  progress: number;
  message: string;
};

export type ClientOcrRunOptions = {
  file: File;
  examType: ExamType;
  requestedCount: number | null;
  forceRestart?: boolean;
  signal?: AbortSignal;
  onProgress?: (progress: ClientOcrProgress) => void;
  media?: ClientOcrMediaInput[];
};

export type BrowserOcrCapabilities = {
  supported: boolean;
  webAssembly: boolean;
  worker: boolean;
  indexedDb: boolean;
  opfs: boolean;
  availableBytes: number | null;
  requiredBytes: number;
  reasons: string[];
};
