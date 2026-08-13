// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import HiddenExamAudio from "./HiddenExamAudio";

describe("HiddenExamAudio exam playback", () => {
  beforeEach(() => {
    Object.defineProperty(HTMLMediaElement.prototype, "play", {
      configurable: true,
      value: vi.fn().mockRejectedValue(new DOMException("autoplay blocked")),
    });
    Object.defineProperty(HTMLMediaElement.prototype, "pause", {
      configurable: true,
      value: vi.fn(),
    });
  });

  afterEach(() => cleanup());

  it("never exposes play or pause controls when browser autoplay is blocked", async () => {
    render(
      <HiddenExamAudio
        audios={[
          {
            id: "full-audio",
            url: "/api/v1/exams/exam-1/assets/full.mp3?token=signed",
            filename: "full.mp3",
            part: "full",
          },
        ]}
        active
        currentQuestionNumber={1}
      />,
    );

    expect(screen.queryByRole("button")).toBeNull();
    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toContain(
        "chặn audio tự động",
      ),
    );
  });

  it("selects the audio matching the current question and advances when its duration ends", () => {
    const onAutoAdvance = vi.fn();
    const { container, rerender } = render(
      <HiddenExamAudio
        audios={[
          {
            id: "question-1",
            url: "/audio/1.mp3",
            filename: "1.mp3",
            part: "part_1",
            scope: "question",
            question_numbers: [1],
          },
          {
            id: "question-2",
            url: "/audio/2.mp3",
            filename: "2.mp3",
            part: "part_1",
            scope: "question",
            question_numbers: [2],
          },
        ]}
        active
        currentQuestionNumber={1}
        currentQuestionNumbers={[1]}
        showingDirections={false}
        onAutoAdvance={onAutoAdvance}
      />,
    );

    let audio = container.querySelector('audio[src="/audio/1.mp3"]');
    expect(audio).toBeTruthy();
    fireEvent.ended(audio as HTMLAudioElement);
    expect(onAutoAdvance).toHaveBeenCalledTimes(1);

    rerender(
      <HiddenExamAudio
        audios={[
          {
            id: "question-1",
            url: "/audio/1.mp3",
            filename: "1.mp3",
            part: "part_1",
            scope: "question",
            question_numbers: [1],
          },
          {
            id: "question-2",
            url: "/audio/2.mp3",
            filename: "2.mp3",
            part: "part_1",
            scope: "question",
            question_numbers: [2],
          },
        ]}
        active
        currentQuestionNumber={2}
        currentQuestionNumbers={[2]}
        showingDirections={false}
        onAutoAdvance={onAutoAdvance}
      />,
    );

    audio = container.querySelector('audio[src="/audio/2.mp3"]');
    expect(audio).toBeTruthy();
    fireEvent.ended(audio as HTMLAudioElement);
    expect(onAutoAdvance).toHaveBeenCalledTimes(2);
  });

  it("advances a Part 3 group only after its combined passage and three prompts end", () => {
    const onAutoAdvance = vi.fn();
    const { container } = render(
      <HiddenExamAudio
        audios={[
          {
            id: "group-32-34",
            url: "/audio/32-34.mp3",
            filename: "32-34.mp3",
            part: "part_3",
            scope: "group",
            question_numbers: [32, 33, 34],
            group_id: "listening-32-34",
          },
        ]}
        active
        currentQuestionNumber={32}
        currentQuestionNumbers={[32, 33, 34]}
        showingDirections={false}
        onAutoAdvance={onAutoAdvance}
      />,
    );

    const audio = container.querySelector(
      'audio[src="/audio/32-34.mp3"]',
    ) as HTMLAudioElement;
    expect(audio).toBeTruthy();
    fireEvent.timeUpdate(audio);
    expect(onAutoAdvance).not.toHaveBeenCalled();
    fireEvent.ended(audio);
    expect(onAutoAdvance).toHaveBeenCalledTimes(1);
  });

  it("uses the no-directions timeline for a 44-minute full audio", () => {
    const onAutoAdvance = vi.fn();
    const { container } = render(
      <HiddenExamAudio
        audios={[
          {
            id: "full-audio",
            url: "/audio/full.mp3",
            filename: "full.mp3",
            part: "full",
            scope: "full",
          },
        ]}
        active
        currentQuestionNumber={1}
        currentQuestionNumbers={[1]}
        showingDirections={false}
        onAutoAdvance={onAutoAdvance}
      />,
    );
    const audio = container.querySelector("audio") as HTMLAudioElement;
    Object.defineProperty(audio, "duration", { configurable: true, value: 2640 });
    Object.defineProperty(audio, "currentTime", {
      configurable: true,
      writable: true,
      value: 28.4,
    });

    fireEvent.timeUpdate(audio);
    expect(onAutoAdvance).not.toHaveBeenCalled();

    audio.currentTime = 28.5;
    fireEvent.timeUpdate(audio);

    expect(onAutoAdvance).toHaveBeenCalledTimes(1);
  });
});
