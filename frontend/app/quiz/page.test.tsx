// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const routerPush = vi.fn();
const routerReplace = vi.fn();
const apiFetch = vi.fn();
const router = { push: routerPush, replace: routerReplace };

vi.mock("next/navigation", () => ({
  useRouter: () => router,
}));
vi.mock("@/lib/api", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  assetUrl: (value: string) => value,
  isDesktop: () => false,
}));
vi.mock("@/components/DictionaryPanel", () => ({ default: () => null }));
vi.mock("@/components/AudioWavePlayer", () => ({ default: () => null }));
vi.mock("@/components/HiddenExamAudio", () => ({ default: () => null }));
vi.mock("@/components/ExamifyLoader", () => ({
  default: () => <div>loading</div>,
}));
vi.mock("@/components/QuestionCard", () => ({
  default: ({
    question,
    selected,
    onSelect,
  }: {
    question: { number: number };
    selected: string | null;
    onSelect: (letter: string) => void;
  }) => (
    <div>
      <span>Đáp án hiện tại: {selected || "chưa chọn"}</span>
      <button
        type="button"
        onClick={() => onSelect("A")}
        aria-label={`Chọn A cho câu ${question.number}`}
      >
        A
      </button>
    </div>
  ),
}));

import QuizPage from "./page";

const exam = {
  schema_version: 2 as const,
  job_id: "job-1",
  exam_type: "reading" as const,
  requested_count: 1,
  returned_count: 1,
  total: 1,
  questions: [
    {
      number: 101,
      part: "Part 5",
      text: "Question",
      options: { A: "One", B: "Two", C: "Three", D: "Four" },
      option_letters: ["A", "B", "C", "D"],
      correct: "A",
      group_id: null,
      stimulus_id: null,
      confidence: 100,
      issues: [],
    },
  ],
  stimuli: [],
  audio: null,
  audios: [],
};

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("quiz submission acknowledgement", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
    sessionStorage.setItem("quiz-data", JSON.stringify(exam));
    sessionStorage.setItem("quiz-duration", "600");
    sessionStorage.setItem("quiz-attempt-id", "attempt-1");
    sessionStorage.setItem("quiz-initial-answers", JSON.stringify({}));
    apiFetch.mockImplementation((input: string, init?: RequestInit) => {
      if (init?.method === "POST" && input.endsWith("/submit")) {
        throw new TypeError("network down");
      }
      return Promise.resolve(
        jsonResponse({
          id: "attempt-1",
          status: "in_progress",
          exam,
          answers: {},
          duration_seconds: 600,
          time_left_seconds: 600,
          accepted_revision: 0,
        }),
      );
    });
  });

  afterEach(() => cleanup());

  it("keeps the active attempt when the server does not acknowledge submit", async () => {
    const user = userEvent.setup();
    render(<QuizPage />);

    await user.click(await screen.findByRole("button", { name: "Submit" }));

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Đáp án vẫn được giữ trên máy",
    );
    expect(sessionStorage.getItem("quiz-attempt-id")).toBe("attempt-1");
    expect(sessionStorage.getItem("quiz-result")).toBeNull();
    expect(routerPush).not.toHaveBeenCalled();
    expect(routerReplace).not.toHaveBeenCalled();
  });

  it("cleans the draft only after receiving a server receipt", async () => {
    apiFetch.mockImplementation((input: string, init?: RequestInit) => {
      if (init?.method === "POST" && input.endsWith("/submit")) {
        return Promise.resolve(
          jsonResponse({
            attempt_id: "attempt-1",
            schema_version: 2,
            status: "submitted",
            receipt_id: "7c337301-6781-479b-a5f7-e253c6a2abc0",
            answers: { 101: "A" },
            duration_seconds: 600,
            time_left_seconds: 590,
            submitted_at: "2026-08-04T10:00:00Z",
            accepted_revision: 1,
          }),
        );
      }
      return Promise.resolve(
        jsonResponse({
          id: "attempt-1",
          status: "in_progress",
          exam,
          answers: {},
          duration_seconds: 600,
          time_left_seconds: 600,
          accepted_revision: 0,
        }),
      );
    });
    const user = userEvent.setup();
    render(<QuizPage />);

    await user.click(await screen.findByRole("button", { name: "Submit" }));

    await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/result"));
    expect(sessionStorage.getItem("quiz-attempt-id")).toBeNull();
    expect(sessionStorage.getItem("quiz-result")).not.toBeNull();
  });

  it("keeps the revealed immutable answer key returned after submit", async () => {
    const sanitizedExam = {
      ...exam,
      questions: exam.questions.map((question) => ({
        ...question,
        correct: null,
      })),
    };
    sessionStorage.setItem("quiz-data", JSON.stringify(sanitizedExam));
    apiFetch.mockImplementation((input: string, init?: RequestInit) => {
      if (init?.method === "POST" && input.endsWith("/submit")) {
        return Promise.resolve(
          jsonResponse({
            attempt_id: "attempt-1",
            schema_version: 2,
            status: "submitted",
            receipt_id: "7c337301-6781-479b-a5f7-e253c6a2abc1",
            exam,
            answers: { 101: "A" },
            duration_seconds: 600,
            time_left_seconds: 590,
            submitted_at: "2026-08-04T10:00:00Z",
            accepted_revision: 1,
          }),
        );
      }
      return Promise.resolve(
        jsonResponse({
          id: "attempt-1",
          status: "in_progress",
          exam: sanitizedExam,
          answers: {},
          duration_seconds: 600,
          time_left_seconds: 600,
          accepted_revision: 0,
        }),
      );
    });
    const user = userEvent.setup();
    render(<QuizPage />);

    await user.click(await screen.findByRole("button", { name: "Submit" }));

    await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/result"));
    const stored = JSON.parse(sessionStorage.getItem("quiz-result") || "null");
    expect(stored.exam.questions[0].correct).toBe("A");
  });

  it("persists an answer immediately and restores it after refresh while offline", async () => {
    const user = userEvent.setup();
    const firstRender = render(<QuizPage />);

    await user.click(
      await screen.findByRole("button", { name: "Chọn A cho câu 101" }),
    );

    const stored = JSON.parse(
      localStorage.getItem("smart-exam-attempt-draft-attempt-1") || "null",
    );
    expect(stored.answers).toEqual({ 101: "A" });
    expect(stored.revision).toBe(1);
    expect(sessionStorage.getItem("quiz-initial-answers")).toBe(
      JSON.stringify({ 101: "A" }),
    );

    // Simulate a refresh whose attempt refresh request cannot reach the server.
    firstRender.unmount();
    apiFetch.mockRejectedValue(new TypeError("offline"));
    render(<QuizPage />);

    expect(await screen.findByRole("dialog")).toBeTruthy();
    await user.click(
      screen.getByRole("button", { name: "Tiếp tục từ câu 101" }),
    );
    expect(await screen.findByText("Đáp án hiện tại: A")).toBeTruthy();
    expect(screen.getByText("Đã lưu trên máy · chờ đồng bộ")).toBeTruthy();
  });

  it("shows answered questions in green and flagged questions in yellow", async () => {
    const user = userEvent.setup();
    render(<QuizPage />);

    await user.click(
      await screen.findByRole("button", { name: "Chọn A cho câu 101" }),
    );
    await user.click(screen.getByRole("button", { name: "Gắn cờ câu 101" }));
    await user.click(screen.getByTitle("Danh sách câu hỏi"));

    const flaggedNavigatorItem = screen.getByRole("button", {
      name: "Câu 101, đã trả lời, đã gắn cờ, câu hiện tại",
    });
    expect(flaggedNavigatorItem.className).toContain("bg-amber-400");

    await user.click(screen.getByRole("button", { name: "Bỏ cờ câu 101" }));
    const answeredNavigatorItem = screen.getByRole("button", {
      name: "Câu 101, đã trả lời, câu hiện tại",
    });
    expect(answeredNavigatorItem.className).toContain("bg-emerald-600");
  });

  it("restores a yellow question flag from the durable draft", async () => {
    const user = userEvent.setup();
    const firstRender = render(<QuizPage />);

    await user.click(
      await screen.findByRole("button", { name: "Gắn cờ câu 101" }),
    );
    firstRender.unmount();
    apiFetch.mockRejectedValue(new TypeError("offline"));
    render(<QuizPage />);

    expect(
      await screen.findByRole("button", { name: "Bỏ cờ câu 101" }),
    ).toBeTruthy();
  });

  it("keeps question navigation hidden on the Listening directions screen", async () => {
    const listeningExam = {
      ...exam,
      exam_type: "listening" as const,
      questions: [{ ...exam.questions[0], number: 1 }],
    };
    sessionStorage.setItem("quiz-data", JSON.stringify(listeningExam));
    sessionStorage.removeItem("quiz-attempt-id");
    const user = userEvent.setup();

    render(<QuizPage />);

    expect(await screen.findByRole("button", { name: "Continue" })).toBeTruthy();
    expect(screen.queryByTitle("Danh sách câu hỏi")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByTitle("Danh sách câu hỏi")).toBeTruthy();
  });

  it("locks every manual Listening path in mock mode and never goes back from Reading", async () => {
    const combinedExam = {
      ...exam,
      exam_type: "combined" as const,
      requested_count: 2,
      returned_count: 2,
      total: 2,
      questions: [
        {
          ...exam.questions[0],
          number: 1,
          part: "Part 1 - Phần 1",
        },
        exam.questions[0],
      ],
    };
    sessionStorage.setItem("quiz-data", JSON.stringify(combinedExam));
    sessionStorage.setItem("quiz-mode", "exam");
    sessionStorage.removeItem("quiz-attempt-id");
    const user = userEvent.setup();

    const firstRender = render(<QuizPage />);
    await user.click(await screen.findByRole("button", { name: "Continue" }));

    expect((screen.getByTitle("Câu tiếp theo") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTitle(/Danh sách câu bị khóa/) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByText(/Listening: Q\. 1/)).toBeTruthy();

    firstRender.unmount();
    sessionStorage.setItem("quiz-question-number", "101");
    render(<QuizPage />);

    expect(await screen.findByText(/Reading: Q\. 101/)).toBeTruthy();
    expect((screen.getByTitle("Câu trước") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(screen.getByText(/Reading: Q\. 101/)).toBeTruthy();
  });
});
