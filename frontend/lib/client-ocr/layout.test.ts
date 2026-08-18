import { describe, expect, it } from "vitest";

import {
  bboxIoU,
  detectRecurringRegions,
  mergeRecoveryTokens,
  suppressRepeatedMarginLines,
} from "./layout";
import type { OcrLine, OcrToken, PageLayoutEvidence } from "./types";

function token(text: string, bbox: OcrToken["bbox"], page = 1, confidence = 90): OcrToken {
  return { text, bbox, page, confidence, source: "baseline" };
}

function line(text: string, bbox: OcrLine["bbox"], page: number, confidence = 90): OcrLine {
  const item = token(text, bbox, page, confidence);
  return { text, bbox, page, confidence, tokens: [item], source: "baseline" };
}

function page(pageNumber: number, lines: OcrLine[]): PageLayoutEvidence {
  return {
    page: pageNumber,
    width: 1000,
    height: 1400,
    lines,
    textLayerUsed: false,
    medianConfidence: 90,
    questionAnchors: [],
    optionAnchorCount: 0,
  };
}

describe("client OCR recurring layout", () => {
  it("requires repeated geometry and only suppresses margin content", () => {
    const pages = [1, 2, 3, 4].map((number) =>
      page(number, [
        line("Examify TOEIC", [0.1, 0.02, 0.35, 0.05], number),
        line(String(number), [0.48, 0.95, 0.52, 0.98], number),
        line(`Question body ${number}`, [0.1, 0.3, 0.7, 0.34], number),
      ]),
    );
    const regions = detectRecurringRegions(pages);
    expect(regions.some((region) => region.kind === "header")).toBe(true);
    expect(regions.some((region) => region.kind === "page-number")).toBe(true);
    const cleaned = suppressRepeatedMarginLines(pages[0], regions);
    expect(cleaned.lines.map((item) => item.text)).toEqual(["Question body 1"]);
  });

  it("does not classify a one-page TEST/PART label as removable", () => {
    const pages = [
      page(1, [line("TEST 1", [0.1, 0.03, 0.2, 0.06], 1)]),
      page(2, [line("PART 3", [0.1, 0.03, 0.2, 0.06], 2)]),
      page(3, [line("Directions", [0.1, 0.03, 0.25, 0.06], 3)]),
    ];
    expect(detectRecurringRegions(pages)).toEqual([]);
  });

  it("detects diagonal/tiled central marks but never suppresses body evidence", () => {
    const pages = [1, 2, 3, 4, 5, 6].map((number) =>
      page(number, [
        line("SAMPLE COPY", [0.18, 0.28, 0.76, 0.58], number, 42),
        line("SAMPLE COPY", [0.22, 0.62, 0.8, 0.9], number, 38),
        line(`145. Body text ${number}`, [0.08, 0.44, 0.44, 0.48], number, 91),
      ]),
    );
    const regions = detectRecurringRegions(pages);
    expect(regions.filter((region) => region.kind === "watermark")).toHaveLength(2);
    expect(suppressRepeatedMarginLines(pages[0], regions).lines).toEqual(pages[0].lines);
  });
});

describe("client OCR non-destructive recovery", () => {
  it("keeps original evidence and only adds aligned missing tokens", () => {
    const original = [
      token("The", [0.1, 0.3, 0.16, 0.33]),
      token("report", [0.25, 0.3, 0.34, 0.33]),
    ];
    const recovery = [
      { ...token("annual", [0.17, 0.3, 0.24, 0.33], 1, 70), source: "recovery" as const },
      { ...token("wrong", [0.25, 0.3, 0.34, 0.33], 1, 99), source: "recovery" as const },
      { ...token("noise", [0.5, 0.8, 0.6, 0.83], 1, 99), source: "recovery" as const },
    ];
    const result = mergeRecoveryTokens(original, recovery);
    expect(result.tokens.map((item) => item.text)).toEqual(["The", "annual", "report"]);
    expect(result.conflicts.map((item) => item.text)).toEqual(["wrong"]);
  });

  it("computes overlap with normalized boxes", () => {
    expect(bboxIoU([0, 0, 1, 1], [0, 0, 1, 1])).toBe(1);
    expect(bboxIoU([0, 0, 0.4, 0.4], [0.6, 0.6, 1, 1])).toBe(0);
  });

  it("never deletes dark baseline words when watermark recovery overlaps text", () => {
    const baseline = [
      token("watermark", [0.1, 0.3, 0.2, 0.34], 1, 61),
      token("covered", [0.21, 0.3, 0.3, 0.34], 1, 58),
      token("sentence", [0.31, 0.3, 0.42, 0.34], 1, 63),
    ];
    const recovery = [
      { ...token("altered", [0.21, 0.3, 0.3, 0.34], 1, 97), source: "recovery" as const },
      { ...token("is", [0.195, 0.3, 0.208, 0.34], 1, 82), source: "recovery" as const },
    ];
    const result = mergeRecoveryTokens(baseline, recovery);
    expect(result.tokens.map((item) => item.text)).toEqual(["watermark", "is", "covered", "sentence"]);
    expect(result.conflicts.map((item) => item.text)).toEqual(["altered"]);
  });
});
