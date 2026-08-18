export { assertClientOcrAssets, detectBrowserOcrCapabilities, clientOcrWorkerCount } from "./capabilities";
export { recognizeAnswerKeyImage, recognizeAnswerKeyPdf } from "./answer-key";
export { commitClientOcrDraft } from "./client-api";
export { clientOcrAssetBlob, createClientOcrCrop, renderClientOcrPage } from "./crop";
export {
  cleanupClientOcrDrafts,
  clientOcrDraftKey,
  deleteClientOcrDraft,
  getClientOcrDraft,
  getClientOcrBlob,
  listClientOcrDrafts,
  loadClientOcrSource,
  putClientOcrBlob,
  putClientOcrDraft,
} from "./local-drafts";
export { runClientOcr } from "./runtime";
export { recognizeScannedSolutionPdf } from "./solution-import";
export type { LocalSolutionOcrResult } from "./solution-import";
export type {
  BrowserOcrCapabilities,
  ClientOcrDraft,
  ClientOcrManifestV1,
  ClientOcrProgress,
  ClientOcrRunOptions,
  NormalizedBbox,
  OcrLine,
  OcrToken,
  PageLayoutEvidence,
  RecurringRegion,
} from "./types";
export { CLIENT_OCR_MODEL_VERSION } from "./types";
