// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import DictionaryPanel from "./DictionaryPanel";
import {
  dictionarySourceForText,
  isDictionaryAvailable,
  normalizeSelectedDictionaryText,
} from "@/lib/dictionary";

const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock("@/lib/api", () => ({ apiFetch }));

const result = {
  query: "example",
  direction: "en-vi" as const,
  resolved_english_word: "example",
  translations: ["ví dụ", "thí dụ"],
  phonetics: [
    {
      text: "/ɪɡˈzɑːmpəl/",
      accent: "US",
      has_audio: false,
      variant: 0,
    },
  ],
  meanings: [
    {
      part_of_speech: "noun",
      senses: [
        {
          definition: "Something representative of a group.",
          example: "This is an example.",
          example_source: "dictionary" as const,
          synonyms: ["instance"],
          antonyms: [],
        },
      ],
    },
  ],
  fallback_examples: [],
  attribution: [
    {
      name: "Wiktionary",
      url: "https://en.wiktionary.org/wiki/example",
      license: "CC BY-SA 3.0",
    },
  ],
  warnings: [],
  cached: false,
};

function Harness() {
  const [open, setOpen] = useState(true);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Reopen
      </button>
      <DictionaryPanel open={open} onMinimize={() => setOpen(false)} />
    </>
  );
}

describe("DictionaryPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiFetch.mockResolvedValue(
      new Response(JSON.stringify(result), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  });

  afterEach(() => cleanup());

  it("looks up and renders bilingual meanings, examples and attribution", async () => {
    const user = userEvent.setup();
    render(<DictionaryPanel open onMinimize={vi.fn()} />);

    await user.type(screen.getByLabelText("Từ cần tra"), "example");
    await user.click(screen.getByLabelText("Tra từ"));

    expect(await screen.findByText("Something representative of a group.")).toBeTruthy();
    expect(screen.getByText("ví dụ")).toBeTruthy();
    expect(screen.getByText("This is an example.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Wiktionary" })).toBeTruthy();
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/dictionary/lookup?q=example&source=en",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("automatically looks up text selected in the quiz", async () => {
    render(
      <DictionaryPanel
        open
        onMinimize={vi.fn()}
        lookupRequest={{ id: 1, text: "contract", source: "en" }}
      />,
    );

    expect(screen.getByDisplayValue("contract")).toBeTruthy();
    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith(
        "/api/v1/dictionary/lookup?q=contract&source=en",
        expect.objectContaining({ cache: "no-store" }),
      ),
    );
    expect(await screen.findByText("Something representative of a group.")).toBeTruthy();
  });

  it("minimizes without losing the query or result", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.type(screen.getByLabelText("Từ cần tra"), "example");
    await user.click(screen.getByLabelText("Tra từ"));
    await screen.findByText("ví dụ");
    await user.click(screen.getByLabelText("Thu nhỏ từ điển"));

    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(screen.getByText("Reopen"));
    expect(screen.getByDisplayValue("example")).toBeTruthy();
    expect(screen.getByText("ví dụ")).toBeTruthy();
  });

  it("supports Vietnamese to English and maps API errors", async () => {
    const user = userEvent.setup();
    render(<DictionaryPanel open onMinimize={vi.fn()} />);

    await user.click(screen.getByText("Việt → Anh"));
    await user.type(screen.getByLabelText("Từ cần tra"), "ví dụ");
    await user.click(screen.getByLabelText("Tra từ"));
    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith(
        expect.stringContaining("source=vi"),
        expect.any(Object),
      ),
    );

    apiFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Không tìm thấy từ phù hợp" }), {
        status: 404,
      }),
    );
    await user.clear(screen.getByLabelText("Từ cần tra"));
    await user.type(screen.getByLabelText("Từ cần tra"), "không có");
    await user.click(screen.getByLabelText("Tra từ"));
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Không tìm thấy từ phù hợp",
    );
  });

  it("uses speech synthesis when natural audio is unavailable", async () => {
    const speak = vi.fn();
    const cancel = vi.fn();
    Object.defineProperty(window, "speechSynthesis", {
      configurable: true,
      value: { speak, cancel, getVoices: () => [] },
    });
    class Utterance {
      lang = "";
      voice: SpeechSynthesisVoice | null = null;
      constructor(public text: string) {}
    }
    vi.stubGlobal("SpeechSynthesisUtterance", Utterance);
    const user = userEvent.setup();
    render(<DictionaryPanel open onMinimize={vi.fn()} />);

    await user.type(screen.getByLabelText("Từ cần tra"), "example");
    await user.click(screen.getByLabelText("Tra từ"));
    await screen.findByText("ví dụ");
    await user.click(screen.getByLabelText("Nghe phát âm bằng giọng đọc"));

    expect(speak).toHaveBeenCalledTimes(1);
    expect(speak.mock.calls[0][0].text).toBe("example");
    vi.unstubAllGlobals();
  });

  it("plays natural audio through a blob URL and revokes it on cleanup", async () => {
    const audioResult = {
      ...result,
      phonetics: [{ ...result.phonetics[0], has_audio: true }],
    };
    apiFetch
      .mockResolvedValueOnce(
        new Response(JSON.stringify(audioResult), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(new Blob(["audio"]), {
          status: 200,
          headers: { "Content-Type": "audio/mpeg" },
        }),
      );
    const createObjectURL = vi.fn(() => "blob:dictionary-audio");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revokeObjectURL,
    });
    const play = vi.fn().mockResolvedValue(undefined);
    const pause = vi.fn();
    vi.stubGlobal(
      "Audio",
      class {
        play = play;
        pause = pause;
      },
    );
    const user = userEvent.setup();
    const rendered = render(<DictionaryPanel open onMinimize={vi.fn()} />);

    await user.type(screen.getByLabelText("Từ cần tra"), "example");
    await user.click(screen.getByLabelText("Tra từ"));
    await user.click(await screen.findByLabelText("Nghe phát âm US"));
    await waitFor(() => expect(play).toHaveBeenCalledTimes(1));
    expect(createObjectURL).toHaveBeenCalledTimes(1);

    rendered.unmount();
    expect(pause).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:dictionary-audio");
    vi.unstubAllGlobals();
  });
});

describe("dictionary practice visibility", () => {
  it("is available only in practice mode", () => {
    expect(isDictionaryAvailable("practice")).toBe(true);
    expect(isDictionaryAvailable("exam")).toBe(false);
  });

  it("normalizes selected text and detects its lookup direction", () => {
    expect(normalizeSelectedDictionaryText('  “contract,”  ')).toBe("contract");
    expect(normalizeSelectedDictionaryText("hợp\nđồng")).toBe("hợp đồng");
    expect(normalizeSelectedDictionaryText("one two three four five six")).toBeNull();
    expect(dictionarySourceForText("contract")).toBe("en");
    expect(dictionarySourceForText("hợp đồng")).toBe("vi");
  });
});
