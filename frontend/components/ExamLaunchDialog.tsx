"use client";

import { useEffect, useMemo, useState } from "react";
import { BookOpen, FileQuestion, Loader2, X } from "lucide-react";

const TIME_OPTIONS = [30, 45, 60, 90, 120];

export type ExamLaunchConfiguration = {
  launchMode: "practice" | "mock_exam";
  partNumbers: number[];
  durationSeconds: number;
};

type Props = {
  title: string;
  questionCount: number;
  durationMinutes: number;
  availablePartNumbers: number[];
  loading?: boolean;
  onClose: () => void;
  onStart: (configuration: ExamLaunchConfiguration) => void | Promise<void>;
};

export default function ExamLaunchDialog({
  title,
  questionCount,
  durationMinutes,
  availablePartNumbers,
  loading = false,
  onClose,
  onStart,
}: Props) {
  const parts = useMemo(
    () => Array.from(new Set(availablePartNumbers)).sort((a, b) => a - b),
    [availablePartNumbers],
  );
  const [mode, setMode] = useState<"practice" | "mock_exam">("practice");
  const [selectedParts, setSelectedParts] = useState<number[]>(parts);
  const [selectedDuration, setSelectedDuration] = useState(60);
  const [customDuration, setCustomDuration] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setSelectedParts(parts), [parts]);
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !loading) onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [loading, onClose]);

  function togglePart(part: number) {
    setSelectedParts((current) =>
      current.includes(part)
        ? current.length > 1
          ? current.filter((item) => item !== part)
          : current
        : [...current, part].sort((a, b) => a - b),
    );
  }

  async function submit() {
    const practiceMinutes = customDuration.trim()
      ? Number(customDuration)
      : selectedDuration;
    if (
      mode === "practice" &&
      (!Number.isInteger(practiceMinutes) || practiceMinutes < 1 || practiceMinutes > 300)
    ) {
      setError("Thời gian phải là số nguyên từ 1 đến 300 phút.");
      return;
    }
    setError(null);
    await onStart({
      launchMode: mode,
      partNumbers: mode === "mock_exam" ? parts : selectedParts,
      durationSeconds:
        (mode === "mock_exam" ? Math.max(1, durationMinutes) : practiceMinutes) * 60,
    });
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={`Cấu hình ${title}`}
    >
      <section className="flex max-h-[90dvh] w-full max-w-xl flex-col overflow-hidden rounded-2xl border border-slate-300 bg-white shadow-2xl sm:max-h-[80vh]">
        <div className="flex items-center justify-between bg-[#1e4b85] px-5 py-3 text-white">
          <h2 className="min-w-0 truncate text-lg font-extrabold">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            disabled={loading}
            className="rounded-lg p-1.5 hover:bg-white/10 disabled:opacity-50"
            aria-label="Đóng"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="space-y-4 overflow-y-auto p-5">
          <div>
            <p className="text-xs font-bold uppercase tracking-wider text-slate-500">
              Chọn chế độ
            </p>
            <div className="mt-2 grid gap-3 sm:grid-cols-2">
              <button
                type="button"
                onClick={() => setMode("practice")}
                className={`flex items-center gap-3 rounded-xl border p-3 text-left transition ${
                  mode === "practice"
                    ? "border-[#c49a6c] bg-[#fcf8f3] ring-2 ring-[#c49a6c]/20"
                    : "border-slate-300 bg-white hover:border-slate-400"
                }`}
              >
                <span className="rounded-lg bg-[#b58855] p-2 text-white">
                  <BookOpen className="h-5 w-5" />
                </span>
                <span>
                  <span className="block font-extrabold text-[#966b3b]">Luyện tập</span>
                  <span className="text-xs text-slate-500">Tự chọn Part và thời gian</span>
                </span>
              </button>
              <button
                type="button"
                onClick={() => setMode("mock_exam")}
                className={`flex items-center gap-3 rounded-xl border p-3 text-left transition ${
                  mode === "mock_exam"
                    ? "border-[#1f4e79] bg-blue-50 ring-2 ring-[#1f4e79]/20"
                    : "border-slate-300 bg-white hover:border-slate-400"
                }`}
              >
                <span className="rounded-lg bg-slate-200 p-2 text-slate-700">
                  <FileQuestion className="h-5 w-5" />
                </span>
                <span>
                  <span className="block font-extrabold text-slate-900">Thi thử</span>
                  <span className="text-xs text-slate-500">
                    {questionCount} câu · {durationMinutes} phút
                  </span>
                </span>
              </button>
            </div>
          </div>

          {mode === "practice" && (
            <>
              <div>
                <p className="text-xs font-bold uppercase tracking-wider text-slate-500">
                  Chọn phần thi
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {parts.map((part) => (
                    <button
                      key={part}
                      type="button"
                      onClick={() => togglePart(part)}
                      className={`flex h-9 w-14 items-center justify-center rounded-lg border text-sm font-bold ${
                        selectedParts.includes(part)
                          ? "border-[#c49a6c] bg-[#fbf5ed] text-[#b58855]"
                          : "border-slate-300 text-slate-600"
                      }`}
                    >
                      P{part}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <p className="text-xs font-bold uppercase tracking-wider text-slate-500">
                  Thời gian
                </p>
                <div className="mt-2 grid grid-cols-3 gap-2 sm:grid-cols-5">
                  {TIME_OPTIONS.map((time) => (
                    <button
                      key={time}
                      type="button"
                      onClick={() => {
                        setSelectedDuration(time);
                        setCustomDuration("");
                        setError(null);
                      }}
                      className={`rounded-lg border py-2 text-sm font-bold ${
                        selectedDuration === time && !customDuration
                          ? "border-[#c49a6c] bg-[#fbf5ed] text-[#b58855]"
                          : "border-slate-300 text-slate-700"
                      }`}
                    >
                      {time} phút
                    </button>
                  ))}
                </div>
                <input
                  type="number"
                  min={1}
                  max={300}
                  step={1}
                  value={customDuration}
                  onChange={(event) => {
                    setCustomDuration(event.target.value);
                    setError(null);
                  }}
                  placeholder="Hoặc nhập số phút"
                  className="mt-2 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-[#b58855]"
                />
                {error && <p className="mt-2 text-sm font-semibold text-red-600">{error}</p>}
              </div>
            </>
          )}
        </div>

        <div className="flex items-center justify-between border-t border-slate-200 bg-white px-5 py-3">
          <button type="button" onClick={onClose} disabled={loading} className="ui-btn-secondary">
            Hủy
          </button>
          <button type="button" onClick={() => void submit()} disabled={loading} className="ui-btn-primary">
            {loading && <Loader2 className="h-4 w-4 animate-spin" />}
            {mode === "practice" ? "Luyện tập ngay" : "Bắt đầu thi thử"}
          </button>
        </div>
      </section>
    </div>
  );
}
