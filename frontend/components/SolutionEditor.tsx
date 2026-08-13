"use client";

import { useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, FileUp, Loader2 } from "lucide-react";

import { apiFetch } from "@/lib/api";
import type { ExamType, SolutionEntry } from "@/lib/utils";

type Group = { key: string; numbers: number[]; part: number };

function groupsFor(examType: ExamType): Group[] {
  if (examType === "reading") {
    return Array.from({ length: 100 }, (_, index) => {
      const number = 101 + index;
      return {
        key: `q-${number}`,
        numbers: [number],
        part: number <= 130 ? 5 : number <= 146 ? 6 : 7,
      };
    });
  }
  const singles = Array.from({ length: 31 }, (_, index) => {
    const number = index + 1;
    return { key: `q-${number}`, numbers: [number], part: number <= 6 ? 1 : 2 };
  });
  const grouped = [
    ...Array.from({ length: 13 }, (_, index) => 32 + index * 3),
    ...Array.from({ length: 10 }, (_, index) => 71 + index * 3),
  ].map((start) => ({
    key: `q-${start}-${start + 2}`,
    numbers: [start, start + 1, start + 2],
    part: start <= 70 ? 3 : 4,
  }));
  return [...singles, ...grouped];
}

function labelFor(numbers: number[]): string {
  return numbers.length === 1 ? String(numbers[0]) : `${numbers[0]}–${numbers.at(-1)}`;
}

type ImportPreview = {
  entries: SolutionEntry[];
  issues: Array<{ row?: number; message?: string; code?: string }>;
  missing_keys: string[];
  mode: string;
  ocr_confidence?: number | null;
};

export default function SolutionEditor({
  examType,
  value,
  onChange,
}: {
  examType: ExamType;
  value: SolutionEntry[];
  onChange: (value: SolutionEntry[]) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const groups = useMemo(() => groupsFor(examType), [examType]);
  const byKey = useMemo(
    () => new Map(value.map((entry) => [entry.key, entry])),
    [value],
  );
  const [part, setPart] = useState(groups[0]?.part || 1);
  const [importing, setImporting] = useState(false);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [importError, setImportError] = useState<string | null>(null);

  const mappedQuestions = new Set(
    value.flatMap((entry) => entry.question_numbers),
  ).size;

  function update(group: Group, field: "transcript" | "explanation" | "translation", text: string) {
    const current = byKey.get(group.key) || {
      key: group.key,
      question_numbers: group.numbers,
      transcript: null,
      explanation: null,
      translation: "",
    };
    const next = {
      ...current,
      [field]: text,
      transcript: field === "transcript" ? text : current.transcript,
      explanation: field === "explanation" ? text : current.explanation,
      translation: field === "translation" ? text : current.translation,
    };
    const merged = new Map(byKey);
    if (!next.transcript?.trim() && !next.explanation?.trim() && !next.translation.trim()) {
      merged.delete(group.key);
    } else {
      merged.set(group.key, next);
    }
    onChange(
      [...merged.values()].sort(
        (left, right) => left.question_numbers[0] - right.question_numbers[0],
      ),
    );
  }

  async function importFile(file: File) {
    setImporting(true);
    setImportError(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("exam_type", examType);
      const response = await apiFetch("/api/v1/solution-imports", {
        method: "POST",
        body: formData,
      });
      const started = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(started.detail || "Không tải được file lời giải");
      for (let attempt = 0; attempt < 120; attempt += 1) {
        const statusResponse = await apiFetch(`/api/v1/solution-imports/${started.id}`, {
          cache: "no-store",
        });
        const status = await statusResponse.json().catch(() => ({}));
        if (!statusResponse.ok) throw new Error(status.detail || "Không đọc được kết quả import");
        if (status.status === "completed") {
          setPreview(status.result as ImportPreview);
          return;
        }
        if (status.status === "failed") {
          throw new Error(status.error || "Worker không đọc được file lời giải");
        }
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
      }
      throw new Error("Import quá thời gian chờ; có thể kiểm tra lại sau.");
    } catch (reason) {
      setImportError(reason instanceof Error ? reason.message : "Import thất bại");
    } finally {
      setImporting(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  function applyPreview(replaceAll: boolean) {
    if (!preview) return;
    if (replaceAll && !window.confirm("Thay toàn bộ sẽ xóa các entry không có trong file. Tiếp tục?")) {
      return;
    }
    const merged = new Map<string, SolutionEntry>();
    if (!replaceAll) value.forEach((entry) => merged.set(entry.key, entry));
    preview.entries.forEach((entry) => merged.set(entry.key, entry));
    onChange(
      [...merged.values()].sort(
        (left, right) => left.question_numbers[0] - right.question_numbers[0],
      ),
    );
    setPreview(null);
  }

  const availableParts = [...new Set(groups.map((group) => group.part))];
  return (
    <section className="space-y-4">
      <div className="rounded-xl border border-slate-300 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-extrabold text-slate-900">Giải chi tiết</h2>
            <p className="mt-1 text-xs text-slate-500">
              Plain text, giữ Unicode và xuống dòng · {value.length} entry · {mappedQuestions} câu đã có giải
            </p>
          </div>
          <label className="ui-btn-secondary cursor-pointer px-3 py-2 text-xs">
            {importing ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileUp className="h-4 w-4" />}
            Import DOCX/DOC/PDF
            <input
              ref={inputRef}
              type="file"
              accept=".docx,.doc,.pdf"
              disabled={importing}
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void importFile(file);
              }}
            />
          </label>
        </div>
        {importError && (
          <p className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-xs font-semibold text-red-700">
            {importError}
          </p>
        )}
        <div className="mt-4 flex flex-wrap gap-2">
          {availableParts.map((number) => (
            <button
              key={number}
              type="button"
              onClick={() => setPart(number)}
              className={`rounded-lg border px-3 py-2 text-xs font-bold ${
                part === number
                  ? "border-[#1f4e79] bg-[#1f4e79] text-white"
                  : "border-slate-300 bg-white text-slate-600"
              }`}
            >
              Part {number}
            </button>
          ))}
        </div>
      </div>

      {groups.filter((group) => group.part === part).map((group) => {
        const entry = byKey.get(group.key);
        return (
          <details key={group.key} className="rounded-xl border border-slate-300 bg-white" open={Boolean(entry)}>
            <summary className="flex cursor-pointer list-none items-center justify-between p-4">
              <span className="font-bold text-slate-800">Câu {labelFor(group.numbers)}</span>
              {entry ? (
                <span className="inline-flex items-center gap-1 text-xs font-bold text-emerald-700"><CheckCircle2 className="h-4 w-4" /> Đã có giải</span>
              ) : (
                <span className="text-xs font-semibold text-slate-400">Chưa có giải chi tiết</span>
              )}
            </summary>
            <div className="grid gap-4 border-t border-slate-200 p-4 lg:grid-cols-2">
              <label className="text-xs font-bold text-slate-600">
                {examType === "listening" ? "Transcript" : "Giải thích / Nội dung đề"}
                <textarea
                  rows={8}
                  maxLength={12_000}
                  value={(examType === "listening" ? entry?.transcript : entry?.explanation) || ""}
                  onChange={(event) => update(group, examType === "listening" ? "transcript" : "explanation", event.target.value)}
                  className="mt-2 w-full rounded-lg border border-slate-300 p-3 text-sm leading-6 outline-none focus:border-[#1f4e79]"
                />
              </label>
              <label className="text-xs font-bold text-slate-600">
                Dịch
                <textarea
                  rows={8}
                  maxLength={12_000}
                  value={entry?.translation || ""}
                  onChange={(event) => update(group, "translation", event.target.value)}
                  className="mt-2 w-full rounded-lg border border-slate-300 p-3 text-sm leading-6 outline-none focus:border-[#1f4e79]"
                />
              </label>
            </div>
          </details>
        );
      })}

      {preview && (
        <div className="fixed inset-0 z-[90] flex items-center justify-center bg-slate-950/50 p-4">
          <section className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-2xl bg-white p-6 shadow-2xl">
            <h3 className="text-xl font-extrabold text-[#1f4e79]">Preview import lời giải</h3>
            <p className="mt-2 text-sm text-slate-600">
              {preview.entries.length} hàng hợp lệ · {preview.issues.length} lỗi · {preview.missing_keys.length} entry còn thiếu · chế độ {preview.mode}
              {preview.ocr_confidence ? ` · OCR confidence ${Math.round(preview.ocr_confidence * 100)}%` : ""}
            </p>
            {preview.issues.length > 0 && (
              <div className="mt-4 max-h-48 overflow-y-auto rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
                {preview.issues.map((issue, index) => (
                  <p key={`${issue.row || 0}-${issue.code || index}`} className="flex gap-2 py-1">
                    <AlertTriangle className="h-4 w-4 shrink-0" />
                    Hàng {issue.row || "?"}: {issue.message || issue.code}
                  </p>
                ))}
              </div>
            )}
            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              {preview.entries.slice(0, 20).map((entry) => (
                <div key={entry.key} className="rounded-lg border border-slate-200 p-3 text-xs">
                  <strong>Câu {labelFor(entry.question_numbers)}</strong>
                  <p className="mt-1 line-clamp-3 whitespace-pre-line text-slate-600">
                    {entry.transcript || entry.explanation || entry.translation}
                  </p>
                </div>
              ))}
            </div>
            <div className="mt-6 flex flex-wrap justify-end gap-3">
              <button type="button" onClick={() => setPreview(null)} className="ui-btn-secondary">Hủy</button>
              <button type="button" onClick={() => applyPreview(true)} className="ui-btn-secondary">Thay toàn bộ</button>
              <button type="button" onClick={() => applyPreview(false)} className="ui-btn-primary">Merge an toàn</button>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
