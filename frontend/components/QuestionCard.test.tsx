// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import QuestionCard from "./QuestionCard";


const question = {
  number: 101,
  part: "Part 5",
  text: "Choose the best answer.",
  options: { A: "annual contract", B: "monthly report" },
  option_letters: ["A", "B"],
  correct: "A",
  group_id: null,
  stimulus_id: null,
  confidence: 100,
  issues: [],
};


describe("QuestionCard dictionary selection", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("keeps A-D text selectable and does not change the answer while text is selected", () => {
    const onSelect = vi.fn();
    vi.spyOn(window, "getSelection").mockReturnValue({
      isCollapsed: false,
    } as Selection);
    render(
      <QuestionCard
        index={0}
        question={question}
        selected={null}
        showAnswer={false}
        onSelect={onSelect}
      />,
    );

    const option = screen.getByRole("button", { name: "A . annual contract" });
    expect(option.className).toContain("select-text");
    fireEvent.click(option);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("still selects an answer on a normal click", () => {
    const onSelect = vi.fn();
    vi.spyOn(window, "getSelection").mockReturnValue({
      isCollapsed: true,
    } as Selection);
    render(
      <QuestionCard
        index={0}
        question={question}
        selected={null}
        showAnswer={false}
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "B . monthly report" }));
    expect(onSelect).toHaveBeenCalledWith("B");
  });

  it("uses the durable quiz flag handler when the flag is controlled", () => {
    const onToggleFlag = vi.fn();
    render(
      <QuestionCard
        index={0}
        question={question}
        selected={null}
        showAnswer={false}
        onSelect={vi.fn()}
        flagged
        onToggleFlag={onToggleFlag}
      />,
    );

    const flag = screen.getByRole("button", { name: "Bỏ cờ câu 101" });
    expect(flag.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(flag);
    expect(onToggleFlag).toHaveBeenCalledOnce();
  });
});
