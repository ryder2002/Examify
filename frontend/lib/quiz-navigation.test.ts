import { describe, expect, it } from "vitest";

import { moveQuizCursor } from "./quiz-navigation";

describe("moveQuizCursor", () => {
  const groups = [
    { questions: [32, 33, 34] },
    { questions: [35, 36, 37] },
    { questions: [38] },
  ];

  it("moves through every question in a Part 3 group before changing group", () => {
    expect(moveQuizCursor(groups, { groupIndex: 0, questionIndex: 0 }, 1)).toEqual({
      groupIndex: 0,
      questionIndex: 1,
    });
    expect(moveQuizCursor(groups, { groupIndex: 0, questionIndex: 1 }, 1)).toEqual({
      groupIndex: 0,
      questionIndex: 2,
    });
    expect(moveQuizCursor(groups, { groupIndex: 0, questionIndex: 2 }, 1)).toEqual({
      groupIndex: 1,
      questionIndex: 0,
    });
  });

  it("moves left to the final question of the previous group", () => {
    expect(moveQuizCursor(groups, { groupIndex: 1, questionIndex: 0 }, -1)).toEqual({
      groupIndex: 0,
      questionIndex: 2,
    });
  });
});
