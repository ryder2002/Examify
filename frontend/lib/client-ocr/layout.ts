import type {
  NormalizedBbox,
  OcrLine,
  OcrToken,
  PageLayoutEvidence,
  RecurringRegion,
  RecurringRegionKind,
} from "./types";

const HEADER_FOOTER_RATIO = 0.12;
const RECURRING_IOU = 0.7;

export function bboxIoU(left: NormalizedBbox, right: NormalizedBbox): number {
  const x0 = Math.max(left[0], right[0]);
  const y0 = Math.max(left[1], right[1]);
  const x1 = Math.min(left[2], right[2]);
  const y1 = Math.min(left[3], right[3]);
  const intersection = Math.max(0, x1 - x0) * Math.max(0, y1 - y0);
  const leftArea = Math.max(0, left[2] - left[0]) * Math.max(0, left[3] - left[1]);
  const rightArea = Math.max(0, right[2] - right[0]) * Math.max(0, right[3] - right[1]);
  const union = leftArea + rightArea - intersection;
  return union > 0 ? intersection / union : 0;
}

export function unionBbox(boxes: NormalizedBbox[]): NormalizedBbox {
  if (!boxes.length) return [0, 0, 0, 0];
  return [
    Math.min(...boxes.map((box) => box[0])),
    Math.min(...boxes.map((box) => box[1])),
    Math.max(...boxes.map((box) => box[2])),
    Math.max(...boxes.map((box) => box[3])),
  ];
}

export function normalizeLayoutText(text: string): string {
  return text
    .normalize("NFKC")
    .toUpperCase()
    .replace(/\d+/g, "#")
    .replace(/[^A-Z#]+/g, " ")
    .trim()
    .replace(/\s+/g, " ");
}

function regionKind(line: OcrLine, pageNumberSequence: boolean): RecurringRegionKind {
  const [, y0, , y1] = line.bbox;
  const normalized = normalizeLayoutText(line.text);
  if (pageNumberSequence && y0 >= 1 - HEADER_FOOTER_RATIO) return "page-number";
  if (y1 <= HEADER_FOOTER_RATIO) return "header";
  if (y0 >= 1 - HEADER_FOOTER_RATIO) return "footer";
  if (normalized || line.bbox[2] - line.bbox[0] >= 0.2) return "watermark";
  return "watermark";
}

function isSequentialPageNumber(group: OcrLine[]): boolean {
  if (group.length < 3) return false;
  const values = group
    .map((line) => Number(line.text.match(/\b\d{1,4}\b/)?.[0]))
    .filter(Number.isFinite);
  if (values.length < 3) return false;
  let increasing = 0;
  for (let index = 1; index < values.length; index += 1) {
    if (values[index] === values[index - 1] + 1) increasing += 1;
  }
  return increasing >= values.length - 2;
}

/**
 * Detect only geometry corroborated by multiple pages. No single-page regex is
 * allowed to suppress content because TEST/PART/Directions are valid TOEIC text.
 */
export function detectRecurringRegions(
  pages: PageLayoutEvidence[],
): RecurringRegion[] {
  if (pages.length < 3) return [];
  const minimumOccurrences = Math.max(3, Math.ceil(pages.length * 0.5));
  const candidates = pages.flatMap((page) => page.lines.map((line) => ({ page, line })));
  const consumed = new Set<string>();
  const regions: RecurringRegion[] = [];

  for (const candidate of candidates) {
    const key = `${candidate.page.page}:${candidate.line.bbox.join(":")}:${candidate.line.text}`;
    if (consumed.has(key)) continue;
    const normalizedText = normalizeLayoutText(candidate.line.text);
    const matches = candidates.filter(({ page, line }) => {
      if (page.page === candidate.page.page) return line === candidate.line;
      const otherText = normalizeLayoutText(line.text);
      const sameText = normalizedText.length >= 2 && normalizedText === otherText;
      const pageNumberLike = /^\s*\d{1,4}\s*$/.test(candidate.line.text) &&
        /^\s*\d{1,4}\s*$/.test(line.text);
      return (sameText || pageNumberLike) && bboxIoU(candidate.line.bbox, line.bbox) >= RECURRING_IOU;
    });
    const uniquePages = new Map(matches.map((match) => [match.page.page, match.line]));
    if (uniquePages.size < minimumOccurrences) continue;
    const group = [...uniquePages.values()].sort((left, right) => left.page - right.page);
    const sequential = isSequentialPageNumber(group);
    const kind = regionKind(candidate.line, sequential);
    // Repeated body text is not enough on its own. Central watermark evidence
    // must be low-confidence or occupy a visibly large/diagonal-like region.
    const width = candidate.line.bbox[2] - candidate.line.bbox[0];
    const height = candidate.line.bbox[3] - candidate.line.bbox[1];
    if (kind === "watermark") {
      const lowConfidenceLargeComponent =
        candidate.line.confidence < 80 && (width >= 0.2 || height >= 0.05);
      const largeDiagonalLikeComponent = width >= 0.45 && height >= 0.1;
      if (!lowConfidenceLargeComponent && !largeDiagonalLikeComponent) continue;
    }
    for (const match of matches) {
      consumed.add(`${match.page.page}:${match.line.bbox.join(":")}:${match.line.text}`);
    }
    regions.push({
      kind,
      bbox: unionBbox(group.map((line) => line.bbox)),
      pageNumbers: [...uniquePages.keys()].sort((left, right) => left - right),
      occurrenceRatio: uniquePages.size / pages.length,
      normalizedText,
    });
  }
  return regions;
}

function overlapRatio(left: NormalizedBbox, right: NormalizedBbox): number {
  const x0 = Math.max(left[0], right[0]);
  const y0 = Math.max(left[1], right[1]);
  const x1 = Math.min(left[2], right[2]);
  const y1 = Math.min(left[3], right[3]);
  const overlap = Math.max(0, x1 - x0) * Math.max(0, y1 - y0);
  const area = Math.max(0, left[2] - left[0]) * Math.max(0, left[3] - left[1]);
  return area > 0 ? overlap / area : 0;
}

export function suppressRepeatedMarginLines(
  page: PageLayoutEvidence,
  regions: RecurringRegion[],
): PageLayoutEvidence {
  const suppressible = regions.filter(
    (region) =>
      region.pageNumbers.includes(page.page) &&
      (region.kind === "header" || region.kind === "footer" || region.kind === "page-number"),
  );
  if (!suppressible.length) return page;
  return {
    ...page,
    lines: page.lines.flatMap((line) => {
      if (suppressible.some((region) => overlapRatio(line.bbox, region.bbox) >= 0.8)) return [];
      // Tesseract PSM 6 can attach a bottom page number to the last answer
      // line. Filter only the token whose geometry is in the corroborated
      // recurring margin region, retaining all other evidence in the line.
      const tokens = line.tokens.filter(
        (token) => !suppressible.some((region) => overlapRatio(token.bbox, region.bbox) >= 0.8),
      );
      if (!tokens.length) return [];
      if (tokens.length === line.tokens.length) return [line];
      return [{
        ...line,
        text: tokens.map((token) => token.text).join(" "),
        bbox: unionBbox(tokens.map((token) => token.bbox)),
        tokens,
      }];
    }),
  };
}

function sameLine(left: NormalizedBbox, right: NormalizedBbox): boolean {
  const leftCenter = (left[1] + left[3]) / 2;
  const rightCenter = (right[1] + right[3]) / 2;
  const tolerance = Math.max(left[3] - left[1], right[3] - right[1]) * 0.7;
  return Math.abs(leftCenter - rightCenter) <= tolerance;
}

/** Merge recovery as additive evidence. Baseline tokens are never deleted. */
export function mergeRecoveryTokens(
  baseline: OcrToken[],
  recovery: OcrToken[],
): { tokens: OcrToken[]; conflicts: OcrToken[] } {
  const tokens = [...baseline];
  const conflicts: OcrToken[] = [];
  for (const candidate of recovery) {
    if (candidate.confidence < 55 || !candidate.text.trim()) continue;
    const overlaps = baseline.filter((token) => bboxIoU(token.bbox, candidate.bbox) >= 0.45);
    if (overlaps.some((token) => normalizeLayoutText(token.text) === normalizeLayoutText(candidate.text))) {
      continue;
    }
    if (overlaps.length) {
      conflicts.push(candidate);
      continue;
    }
    if (!baseline.some((token) => sameLine(token.bbox, candidate.bbox))) continue;
    tokens.push(candidate);
  }
  tokens.sort((left, right) => left.bbox[1] - right.bbox[1] || left.bbox[0] - right.bbox[0]);
  return { tokens, conflicts };
}

export function median(values: number[]): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

export function extractAnchors(lines: OcrLine[]): {
  questionAnchors: number[];
  optionAnchorCount: number;
} {
  const questions = new Set<number>();
  let options = 0;
  for (const line of lines) {
    const question = line.text.match(/^\s*(\d{1,3})\s*[.)]?\s+/);
    if (question) questions.add(Number(question[1]));
    if (/^\s*[([]?[A-D][)\].:]\s*/i.test(line.text)) options += 1;
  }
  return { questionAnchors: [...questions].sort((a, b) => a - b), optionAnchorCount: options };
}
