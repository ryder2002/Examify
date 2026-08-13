import { describe, expect, it } from "vitest";

import { solutionGroupStatus, solutionQuestionStatus } from "@/lib/solution-status";
import type { Question } from "@/lib/utils";

function question(number: number, correct: string | null): Question {
  return {
    number,
    part: "Part 5 - Phần 5",
    text: "Question",
    options: { A: "One", B: "Two", C: "Three", D: "Four" },
    option_letters: ["A", "B", "C", "D"],
    correct,
    group_id: null,
    stimulus_id: null,
    confidence: 100,
    issues: [],
  };
}

describe("solution answer statuses", () => {
  it("distinguishes correct, wrong, unanswered and ungraded", () => {
    expect(solutionQuestionStatus(question(101, "B"), { "101": "B" })).toBe("correct");
    expect(solutionQuestionStatus(question(101, "B"), { "101": "A" })).toBe("wrong");
    expect(solutionQuestionStatus(question(101, "B"), {})).toBe("unanswered");
    expect(solutionQuestionStatus(question(101, null), { "101": "A" })).toBe("ungraded");
  });

  it("keeps a group red when any answer is wrong", () => {
    expect(
      solutionGroupStatus(
        [question(32, "A"), question(33, "B"), question(34, "C")],
        { "32": "A", "33": "D", "34": "C" },
      ),
    ).toBe("wrong");
  });
});
