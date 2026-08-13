// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import ExamLaunchDialog from "./ExamLaunchDialog";

const baseProps = {
  title: "ETS 2026 Test 1",
  questionCount: 200,
  durationMinutes: 120,
  availablePartNumbers: [5, 6, 7],
  onClose: vi.fn(),
};

describe("ExamLaunchDialog", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("returns the learner-selected Parts and custom practice duration", async () => {
    const user = userEvent.setup();
    const onStart = vi.fn();
    render(<ExamLaunchDialog {...baseProps} onStart={onStart} />);

    await user.click(screen.getByRole("button", { name: "P7" }));
    const customTime = screen.getByPlaceholderText("Hoặc nhập số phút");
    await user.type(customTime, "75");
    await user.click(screen.getByRole("button", { name: "Luyện tập ngay" }));

    expect(onStart).toHaveBeenCalledWith({
      launchMode: "practice",
      partNumbers: [5, 6],
      durationSeconds: 75 * 60,
    });
  });

  it("uses every available Part and the standard duration for a mock exam", async () => {
    const user = userEvent.setup();
    const onStart = vi.fn();
    render(<ExamLaunchDialog {...baseProps} onStart={onStart} />);

    await user.click(screen.getByRole("button", { name: /Thi thử/ }));
    await user.click(screen.getByRole("button", { name: "Bắt đầu thi thử" }));

    expect(onStart).toHaveBeenCalledWith({
      launchMode: "mock_exam",
      partNumbers: [5, 6, 7],
      durationSeconds: 120 * 60,
    });
  });

  it("closes with Escape when it is not starting", () => {
    const onClose = vi.fn();
    render(<ExamLaunchDialog {...baseProps} onClose={onClose} onStart={vi.fn()} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });
});
