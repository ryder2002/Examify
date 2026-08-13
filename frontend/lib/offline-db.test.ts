// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FinalExam } from "./utils";
import { cacheExamAssets } from "./offline-db";

function exam(overrides: Partial<FinalExam> = {}): FinalExam {
  return {
    schema_version: 2,
    job_id: "offline-test",
    exam_type: "combined",
    requested_count: 200,
    returned_count: 200,
    total: 200,
    questions: [],
    stimuli: [],
    audio: null,
    audios: [],
    ...overrides,
  };
}

describe("offline exam asset pack", () => {
  const cachePut = vi.fn().mockResolvedValue(undefined);
  const cacheStorage = {
    open: vi.fn().mockResolvedValue({ put: cachePut }),
  };

  beforeEach(() => {
    cachePut.mockReset().mockResolvedValue(undefined);
    cacheStorage.open.mockReset().mockResolvedValue({ put: cachePut });
    vi.stubGlobal("caches", cacheStorage);
    Object.defineProperty(navigator, "storage", {
      configurable: true,
      value: {
        estimate: vi.fn().mockResolvedValue({
          quota: 1024 * 1024 * 1024,
          usage: 0,
        }),
      },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("downloads at most two assets concurrently and reports progress", async () => {
    let active = 0;
    let maximumActive = 0;
    const fetchMock = vi.fn(async () => {
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      await new Promise((resolve) => window.setTimeout(resolve, 5));
      active -= 1;
      return { ok: true, clone: () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);
    const progress: Array<[number, number]> = [];

    await cacheExamAssets(
      exam({
        audios: [1, 2, 3].map((number) => ({
          id: `audio-${number}`,
          url: `/media/audio-${number}.mp3`,
          filename: `audio-${number}.mp3`,
          part: `part_${number}`,
        })),
        stimuli: [
          {
            id: "stimulus",
            kind: "image",
            title: "",
            assets: [1, 2].map((number) => ({
              id: `image-${number}`,
              url: `/media/image-${number}.webp`,
              page: number,
              bbox: [0, 0, 1, 1],
              width: 100,
              height: 100,
            })),
            question_numbers: [1],
            page_numbers: [1],
            confidence: 1,
            issues: [],
          },
        ],
      }),
      (completed, total) => progress.push([completed, total]),
    );

    expect(fetchMock).toHaveBeenCalledTimes(5);
    expect(cachePut).toHaveBeenCalledTimes(5);
    expect(maximumActive).toBe(2);
    expect(progress.at(-1)).toEqual([5, 5]);
  });

  it("does not count the same main audio twice during quota validation", async () => {
    const size = 60 * 1024 * 1024;
    Object.defineProperty(navigator, "storage", {
      configurable: true,
      value: {
        estimate: vi.fn().mockResolvedValue({
          quota: 100 * 1024 * 1024,
          usage: 20 * 1024 * 1024,
        }),
      },
    });
    const mainAudio = {
      id: "main",
      url: "/media/main.mp3",
      filename: "main.mp3",
      part: "full",
      size,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, clone: () => ({}) }),
    );

    await expect(
      cacheExamAssets(exam({ audio: mainAudio, audios: [mainAudio] })),
    ).resolves.toBeUndefined();
    expect(cachePut).toHaveBeenCalledTimes(1);
  });

  it("refuses a pack before downloading when browser quota is insufficient", async () => {
    Object.defineProperty(navigator, "storage", {
      configurable: true,
      value: {
        estimate: vi.fn().mockResolvedValue({ quota: 64, usage: 63 }),
      },
    });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      cacheExamAssets(
        exam({
          audios: [
            {
              id: "audio",
              url: "/media/audio.mp3",
              filename: "audio.mp3",
              part: "full",
            },
          ],
        }),
      ),
    ).rejects.toThrow("không còn đủ dung lượng");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
