import { describe, expect, it } from "vitest";

import { parseToeicPages } from "./parser";
import type { OcrLine, PageLayoutEvidence } from "./types";

function pageWithText(texts: string[]): PageLayoutEvidence {
  const lines: OcrLine[] = texts.map((text, index) => ({
    text,
    confidence: 94,
    bbox: [0.08, 0.08 + index * 0.03, 0.45, 0.1 + index * 0.03],
    page: 1,
    source: "baseline",
    tokens: [],
  }));
  return {
    page: 1,
    width: 1000,
    height: 1400,
    lines,
    textLayerUsed: false,
    medianConfidence: 94,
    questionAnchors: [101],
    optionAnchorCount: 4,
  };
}

describe("TOEIC browser parser", () => {
  it("associates question and all options deterministically", () => {
    const result = parseToeicPages(
      [
        pageWithText([
          "101. The shipment will arrive ____ Friday.",
          "(A) at",
          "(B) in",
          "(C) by",
          "(D) from",
        ]),
      ],
      "reading",
      1,
    );
    expect(result.questions).toHaveLength(1);
    expect(result.questions[0]).toMatchObject({
      number: 101,
      part: "part5",
      text: "The shipment will arrive ____ Friday.",
      options: { A: "at", B: "in", C: "by", D: "from" },
      option_letters: ["A", "B", "C", "D"],
      issues: [],
    });
    expect(result.issues).toEqual([]);
  });

  it("surfaces missing questions and options instead of silently omitting them", () => {
    const result = parseToeicPages(
      [pageWithText(["101. Incomplete question", "(A) only one"])],
      "reading",
      2,
    );
    expect(result.questions.map((question) => question.number)).toEqual([101, 102]);
    expect(result.questions[0].issues).toContain("options_missing");
    expect(result.questions[1].issues).toContain("question_missing");
    expect(result.issues.map((issue) => issue.code)).toEqual([
      "options_missing",
      "question_missing",
      "options_missing",
    ]);
  });

  it("does not invent missing printed fields for Listening Part 1/2", () => {
    const result = parseToeicPages([], "listening", 31);
    expect(result.questions).toHaveLength(31);
    expect(result.questions[0]).toMatchObject({ option_letters: ["A", "B", "C", "D"], issues: [] });
    expect(result.questions[6]).toMatchObject({ option_letters: ["A", "B", "C"], issues: [] });
    expect(result.issues).toEqual([]);
  });

  it("allows Part 6 passage questions without a standalone question line", () => {
    const result = parseToeicPages(
      [pageWithText(["131.", "(A) grows", "(B) grow", "(C) growing", "(D) grown"])],
      "reading",
      31,
    );
    expect(result.questions.find((question) => question.number === 131)?.issues).toEqual([]);
  });

  it("recovers an answer whose faint watermark erased only the A marker", () => {
    const base: OcrLine = {
      text: "",
      confidence: 94,
      bbox: [0, 0, 0, 0],
      page: 1,
      source: "baseline",
      tokens: [],
    };
    const lines: OcrLine[] = [
      { ...base, text: "88. What does the speaker ask the listeners to do?", bbox: [0.08, 0.2, 0.45, 0.22] },
      { ...base, text: "Show their tickets", bbox: [0.12, 0.23, 0.45, 0.25] },
      { ...base, text: "(B) Put on protective clothing", bbox: [0.12, 0.26, 0.45, 0.28] },
      { ...base, text: "(C) Use some handrails", bbox: [0.12, 0.29, 0.45, 0.31] },
      { ...base, text: "(D) Speak quietly", bbox: [0.12, 0.32, 0.45, 0.34] },
    ];
    const result = parseToeicPages([{ ...pageWithText([]), lines }], "listening", 88);
    expect(result.questions[87]).toMatchObject({
      number: 88,
      options: {
        A: "Show their tickets",
        B: "Put on protective clothing",
        C: "Use some handrails",
        D: "Speak quietly",
      },
      issues: [],
    });
  });
});
