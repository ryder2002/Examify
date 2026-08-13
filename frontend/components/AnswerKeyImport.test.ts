import { describe, expect, it } from "vitest";

import { completedOcrMessage, missingAnswerMessage } from "./AnswerKeyImport";

describe("missingAnswerMessage", () => {
  it("only lists questions without an answer", () => {
    expect(missingAnswerMessage([67, 72, 100])).toBe(
      "Chưa có đáp án câu: 67, 72, 100.",
    );
  });

  it("hides the notice when all answers are present", () => {
    expect(missingAnswerMessage([])).toBeNull();
  });
});

describe("completedOcrMessage", () => {
  it("shows recognized answers and local OCR duration", () => {
    expect(completedOcrMessage(100, 1612)).toBe(
      "Đã đọc 100 đáp án trong 1,6 giây.",
    );
  });

  it("hides the success notice when no answer was recognized", () => {
    expect(completedOcrMessage(0, 1000)).toBeNull();
  });
});
