import { describe, expect, it } from "vitest";

import {
  getToeicFullAudioIntroEndTime,
  getToeicFullAudioQuestionEndTime,
  inferToeicFullAudioProfile,
  TOEIC_FULL_AUDIO_QUESTION_REFERENCE_SECONDS,
} from "./utils";

describe("TOEIC full-audio timestamp estimation", () => {
  it("uses the question-only profile for an approximately 44-minute audio", () => {
    const duration = 44 * 60;
    const scale = duration / TOEIC_FULL_AUDIO_QUESTION_REFERENCE_SECONDS;

    expect(inferToeicFullAudioProfile(duration)).toBe("without_directions");
    expect(getToeicFullAudioIntroEndTime(duration)).toBe(0);
    expect(getToeicFullAudioQuestionEndTime(1, duration)).toBeCloseTo(
      27.5 * scale,
      5,
    );
    expect(getToeicFullAudioQuestionEndTime(31, duration)).toBeCloseTo(
      (6 * 27.5 + 25 * 20.5) * scale,
      5,
    );
    expect(getToeicFullAudioQuestionEndTime(100, duration)).toBe(duration);
  });

  it.each([46, 47, 48])(
    "keeps fixed directions and fills a %i-minute audio through question 100",
    (minutes) => {
      const duration = minutes * 60;
      const questionScale =
        (duration - 189) / TOEIC_FULL_AUDIO_QUESTION_REFERENCE_SECONDS;

      expect(inferToeicFullAudioProfile(duration)).toBe("with_directions");
      expect(getToeicFullAudioIntroEndTime(duration)).toBe(95);
      expect(getToeicFullAudioQuestionEndTime(1, duration)).toBeCloseTo(
        95 + 27.5 * questionScale,
        5,
      );
      expect(getToeicFullAudioQuestionEndTime(32, duration)).toBe(
        getToeicFullAudioQuestionEndTime(34, duration),
      );
      expect(getToeicFullAudioQuestionEndTime(100, duration)).toBe(duration);
    },
  );
});
