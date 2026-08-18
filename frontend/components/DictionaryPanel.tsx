"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  BookOpen,
  Loader2,
  Minus,
  Search,
  Volume2,
} from "lucide-react";

import { apiFetch } from "@/lib/api";
import {
  dictionaryErrorMessage,
  type DictionaryDirection,
  type DictionaryLookupRequest,
  type DictionaryResult,
} from "@/lib/dictionary";

type DictionaryPanelProps = {
  open: boolean;
  onMinimize: () => void;
  lookupRequest?: DictionaryLookupRequest | null;
};

export default function DictionaryPanel({
  open,
  onMinimize,
  lookupRequest,
}: DictionaryPanelProps) {
  const [source, setSource] = useState<DictionaryDirection>("en");
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<DictionaryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [audioLoading, setAudioLoading] = useState<number | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const audioUrlRef = useRef<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onMinimize();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onMinimize, open]);

  useEffect(
    () => () => {
      abortRef.current?.abort();
      audioRef.current?.pause();
      if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
      if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    },
    [],
  );

  const runLookup = useCallback(async (
    term: string,
    lookupSource: DictionaryDirection,
  ) => {
    const normalized = term.trim().replace(/\s+/g, " ");
    if (!normalized) {
      setError("Vui lòng nhập từ cần tra.");
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    const timeoutId = setTimeout(() => controller.abort("timeout"), 6000);
    try {
      const response = await apiFetch(
        `/api/v1/dictionary/lookup?q=${encodeURIComponent(normalized)}&source=${lookupSource}`,
        { cache: "no-store", signal: controller.signal },
      );
      clearTimeout(timeoutId);
      const payload = (await response.json().catch(() => ({}))) as {
        detail?: string;
      } & Partial<DictionaryResult>;
      if (!response.ok) {
        throw new Error(dictionaryErrorMessage(response.status, payload.detail));
      }
      setResult(payload as DictionaryResult);
    } catch (reason) {
      clearTimeout(timeoutId);
      if (controller.signal.aborted && controller.signal.reason !== "timeout") return;
      setError(
        controller.signal.reason === "timeout"
          ? "Thời gian phản hồi quá lâu. Vui lòng thử lại."
          : reason instanceof TypeError
          ? "Không thể kết nối dịch vụ từ điển. Hãy kiểm tra Internet."
          : reason instanceof Error && reason.message
          ? reason.message
          : "Không thể kết nối dịch vụ từ điển. Hãy kiểm tra Internet.",
      );
    } finally {
      clearTimeout(timeoutId);
      if (!controller.signal.aborted || controller.signal.reason === "timeout") setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!lookupRequest) return;
    setSource(lookupRequest.source);
    setQuery(lookupRequest.text);
    setResult(null);
    setError(null);
    void runLookup(lookupRequest.text, lookupRequest.source);
  }, [lookupRequest, runLookup]);

  function handleLookup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void runLookup(query, source);
  }

  function speakWithTts(word: string, accent: string | null = "US") {
    if (!("speechSynthesis" in window) || !("SpeechSynthesisUtterance" in window)) {
      setError("Thiết bị này không hỗ trợ giọng đọc dự phòng.");
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(word);
    utterance.lang = accent === "UK" ? "en-GB" : "en-US";
    const voice = window.speechSynthesis
      .getVoices()
      .find((item) => item.lang.toLowerCase().startsWith(utterance.lang.toLowerCase()));
    if (voice) utterance.voice = voice;
    window.speechSynthesis.speak(utterance);
  }

  async function playPronunciation(variant: number, accent: string | null) {
    if (!result) return;
    setAudioLoading(variant);
    setError(null);
    try {
      const response = await apiFetch(
        `/api/v1/dictionary/pronunciation?q=${encodeURIComponent(
          result.resolved_english_word,
        )}&variant=${variant}`,
        { cache: "force-cache" },
      );
      if (!response.ok) throw new Error("audio unavailable");
      const blob = await response.blob();
      audioRef.current?.pause();
      if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
      const objectUrl = URL.createObjectURL(blob);
      audioUrlRef.current = objectUrl;
      const audio = new Audio(objectUrl);
      audioRef.current = audio;
      await audio.play();
    } catch {
      speakWithTts(result.resolved_english_word, accent);
    } finally {
      setAudioLoading(null);
    }
  }

  if (!open) return null;

  const audioPhonetics = result?.phonetics.filter((item) => item.has_audio) || [];

  return (
    <aside
      role="dialog"
      aria-modal="false"
      aria-label="Từ điển Anh Việt"
      className="fixed bottom-[4.5rem] right-2 z-40 flex max-h-[70vh] w-[calc(100vw-16px)] flex-col overflow-hidden rounded-2xl border border-slate-300 bg-white shadow-[0_18px_50px_rgba(31,78,121,0.28)] sm:right-4 sm:w-[380px]"
    >
      <div className="flex items-center justify-between border-b border-slate-200 bg-[#1f4e79] px-4 py-3 text-white">
        <div className="flex items-center gap-2">
          <BookOpen className="h-5 w-5" />
          <div>
            <h2 className="text-sm font-extrabold">Dictionary</h2>
            <p className="text-[10px] text-white/70">Tra từ trong lúc luyện tập</p>
          </div>
        </div>
        <button
          type="button"
          onClick={onMinimize}
          aria-label="Thu nhỏ từ điển"
          title="Thu nhỏ"
          className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/30 bg-white/10 hover:bg-white/20"
        >
          <Minus className="h-4 w-4" />
        </button>
      </div>

      <div className="border-b border-slate-200 p-4">
        <div className="mb-3 grid grid-cols-2 rounded-lg bg-slate-100 p-1 text-xs font-bold">
          {(
            [
              ["en", "Anh → Việt"],
              ["vi", "Việt → Anh"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => {
                setSource(value);
                setResult(null);
                setError(null);
              }}
              className={`rounded-md px-3 py-2 transition ${
                source === value
                  ? "bg-white text-[#1f4e79] shadow-sm"
                  : "text-slate-500 hover:text-[#1f4e79]"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <form onSubmit={handleLookup} className="flex gap-2">
          <label htmlFor="dictionary-query" className="sr-only">
            Từ cần tra
          </label>
          <input
            ref={inputRef}
            id="dictionary-query"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            maxLength={80}
            placeholder={source === "en" ? "Nhập từ tiếng Anh…" : "Nhập từ tiếng Việt…"}
            className="min-w-0 flex-1 rounded-lg border border-slate-300 px-3 py-2.5 text-sm outline-none transition focus:border-[#1f4e79] focus:ring-2 focus:ring-[#1f4e79]/15"
          />
          <button
            type="submit"
            disabled={loading}
            aria-label="Tra từ"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-[#193e63] bg-[#1f4e79] text-white shadow-sm hover:bg-[#173a5c] disabled:opacity-50"
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Search className="h-4 w-4" />
            )}
          </button>
        </form>
        <p className="mt-2 text-[10px] text-slate-400">
          Bôi đen hoặc double-click từ trong đề để tra nhanh. Tối đa 5 từ.
        </p>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {error && (
          <div role="alert" className="flex gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {!result && !error && !loading && (
          <div className="py-10 text-center text-sm text-slate-400">
            <BookOpen className="mx-auto mb-3 h-8 w-8 text-slate-300" />
            Nhập một từ để xem nghĩa, ví dụ và phát âm.
          </div>
        )}

        {result && (
          <div className="space-y-5">
            <section>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-xl font-extrabold text-[#1f4e79]">
                    {result.resolved_english_word}
                  </h3>
                  <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate-500">
                    {result.phonetics
                      .filter((item) => item.text)
                      .map((item) => (
                        <span key={`${item.variant}-${item.text}`}>
                          {item.accent ? `${item.accent} ` : ""}
                          {item.text}
                        </span>
                      ))}
                  </div>
                </div>
                {audioPhonetics.length ? (
                  <div className="flex gap-1">
                    {audioPhonetics.slice(0, 2).map((item) => (
                      <button
                        key={item.variant}
                        type="button"
                        onClick={() => playPronunciation(item.variant, item.accent)}
                        aria-label={`Nghe phát âm ${item.accent || ""}`.trim()}
                        className="flex h-9 min-w-9 items-center justify-center gap-1 rounded-lg border border-slate-300 bg-white px-2 text-xs font-bold text-[#1f4e79] hover:border-[#1f4e79]"
                      >
                        {audioLoading === item.variant ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Volume2 className="h-4 w-4" />
                        )}
                        {item.accent}
                      </button>
                    ))}
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => speakWithTts(result.resolved_english_word)}
                    aria-label="Nghe phát âm bằng giọng đọc"
                    className="flex h-9 items-center gap-1 rounded-lg border border-slate-300 px-2 text-xs font-bold text-[#1f4e79] hover:border-[#1f4e79]"
                  >
                    <Volume2 className="h-4 w-4" /> TTS
                  </button>
                )}
              </div>

              {result.translations.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {result.translations.map((translation) => (
                    <span
                      key={translation}
                      className="rounded-full border border-[#1f4e79]/20 bg-[#1f4e79]/5 px-2.5 py-1 text-xs font-bold text-[#1f4e79]"
                    >
                      {translation}
                    </span>
                  ))}
                </div>
              )}
            </section>

            {result.meanings.map((meaning, meaningIndex) => (
              <details
                key={`${meaning.part_of_speech}-${meaningIndex}`}
                open={meaningIndex === 0}
                className="group rounded-xl border border-slate-200 bg-slate-50"
              >
                <summary className="cursor-pointer px-3 py-2 text-xs font-extrabold uppercase tracking-wide text-[#1f4e79]">
                  {meaning.part_of_speech}
                </summary>
                <ol className="space-y-3 border-t border-slate-200 bg-white p-3">
                  {meaning.senses.map((sense, senseIndex) => (
                    <li
                      key={`${sense.definition}-${senseIndex}`}
                      className="text-sm text-slate-700"
                    >
                      <div className="flex gap-2">
                        <span className="font-bold text-slate-400">{senseIndex + 1}.</span>
                        <div>
                          <p>{sense.definition}</p>
                          {sense.example && (
                            <p className="mt-1 border-l-2 border-[#c49a6c] pl-2 text-xs italic text-slate-500">
                              {sense.example}
                            </p>
                          )}
                          {sense.synonyms.length > 0 && (
                            <p className="mt-1 text-[11px] text-slate-500">
                              <strong>Synonyms:</strong> {sense.synonyms.join(", ")}
                            </p>
                          )}
                          {sense.antonyms.length > 0 && (
                            <p className="mt-1 text-[11px] text-slate-500">
                              <strong>Antonyms:</strong> {sense.antonyms.join(", ")}
                            </p>
                          )}
                        </div>
                      </div>
                    </li>
                  ))}
                </ol>
              </details>
            ))}

            {result.fallback_examples.length > 0 && (
              <section>
                <h4 className="mb-2 text-xs font-extrabold uppercase tracking-wide text-[#1f4e79]">
                  Ví dụ
                </h4>
                <div className="space-y-2">
                  {result.fallback_examples.map((example) => (
                    <blockquote
                      key={example.id}
                      className="rounded-lg border-l-2 border-[#c49a6c] bg-slate-50 px-3 py-2 text-xs italic text-slate-600"
                    >
                      “{example.text}”
                      <a
                        href={example.url}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-1 block text-[10px] not-italic text-slate-400 hover:text-[#1f4e79]"
                      >
                        {example.author} · {example.license}
                      </a>
                    </blockquote>
                  ))}
                </div>
              </section>
            )}

            {result.warnings.length > 0 && (
              <div className="rounded-lg bg-amber-50 p-2 text-[11px] text-amber-700">
                {result.warnings.join(" ")}
              </div>
            )}

            <footer className="border-t border-slate-200 pt-3 text-[10px] text-slate-400">
              Nguồn:{" "}
              {result.attribution.map((sourceItem, index) => (
                <span key={`${sourceItem.name}-${index}`}>
                  {index > 0 ? " · " : ""}
                  <a
                    href={sourceItem.url}
                    target="_blank"
                    rel="noreferrer"
                    className="underline hover:text-[#1f4e79]"
                  >
                    {sourceItem.name}
                  </a>{" "}
                  ({sourceItem.license})
                </span>
              ))}
            </footer>
          </div>
        )}
      </div>
    </aside>
  );
}
