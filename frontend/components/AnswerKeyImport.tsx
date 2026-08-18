"use client";

import { useRef, useState } from "react";
import {
  ClipboardPaste,
  ImageUp,
  KeyRound,
  Loader2,
  ScanText,
} from "lucide-react";

import type { ExamType } from "@/lib/utils";
import { parseAnswerKeyText } from "@/lib/utils";

type AnswerKeyImportProps = {
  jobId: string;
  examType: ExamType;
  value: string;
  onChange: (value: string) => void;
  onApply: (answers: Record<number, string>, source: string) => void;
};

type ImageImportResponse = {
  answer_key: Record<string, string>;
  recognized_count: number;
  ignored: string[];
  missing: number[];
  raw_text: string;
  duration_ms?: number;
  detail?: string;
};

export function missingAnswerMessage(missing: number[]): string | null {
  if (!missing.length) return null;
  return `Chưa có đáp án câu: ${missing.join(", ")}.`;
}

export function completedOcrMessage(
  recognizedCount: number,
  durationMs?: number,
): string | null {
  if (recognizedCount <= 0) return null;
  const duration =
    typeof durationMs === "number"
      ? ` trong ${(durationMs / 1000).toFixed(1).replace(".", ",")} giây`
      : "";
  return `Đã đọc ${recognizedCount} đáp án${duration}.`;
}

export default function AnswerKeyImport({
  jobId,
  examType,
  value,
  onChange,
  onApply,
}: AnswerKeyImportProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [scanning, setScanning] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  function applyText() {
    const { answers } = parseAnswerKeyText(value);
    if (!Object.keys(answers).length) {
      setMessage("Không tìm thấy đáp án theo định dạng 1(A), 1A hoặc 1 A.");
      return;
    }
    onApply(answers, "text");
    const start = examType === "listening" ? 1 : 101;
    const end = examType === "listening" ? 100 : 200;
    const missing = Array.from(
      { length: end - start + 1 },
      (_, index) => start + index,
    ).filter((number) => !answers[number]);
    setMessage(missingAnswerMessage(missing));
  }

  async function scanImage(file: File) {
    const isPdf = file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
    if (!file.type.startsWith("image/") && !isPdf) {
      setMessage("Vui lòng chọn hoặc dán một file ảnh hoặc PDF.");
      return;
    }
    setScanning(true);
    setMessage("Đang đọc bảng đáp án…");
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 45000);
    try {
      const { recognizeAnswerKeyImage, recognizeAnswerKeyPdf } = await import("@/lib/client-ocr");
      if (controller.signal.aborted) throw new DOMException("OCR đã bị hủy.", "AbortError");
      const payload = (await (isPdf
        ? recognizeAnswerKeyPdf(file, examType)
        : recognizeAnswerKeyImage(file, examType))) as ImageImportResponse;
      const answers = Object.fromEntries(
        Object.entries(payload.answer_key).map(([number, letter]) => [
          Number(number),
          letter,
        ]),
      );
      // Format answers into text string for textarea
      const formattedText = Object.entries(answers)
        .sort((a, b) => Number(a[0]) - Number(b[0]))
        .map(([num, letter]) => `${num}(${letter})`)
        .join(" ");
      if (formattedText) {
        onChange(formattedText);
      }
      if (Object.keys(answers).length) {
        onApply(answers, isPdf ? "local-pdf" : "local-image");
      }
      const detailWarning = payload.detail || null;
      const timeoutWarning = (payload.ignored || []).find((item) =>
        item.startsWith("OCR đã dừng"),
      );
      const missingWarning = detailWarning
        ? null
        : missingAnswerMessage(payload.missing || []);
      const completedMessage =
        !detailWarning && !timeoutWarning && !missingWarning
          ? completedOcrMessage(payload.recognized_count, payload.duration_ms)
          : null;
      setMessage(
        [detailWarning, timeoutWarning, missingWarning, completedMessage]
          .filter(Boolean)
          .join(" ") || null,
      );
    } catch (reason) {
      setMessage(
        reason instanceof DOMException && reason.name === "AbortError"
          ? "OCR đã dừng vì vượt quá 45 giây. Hãy cắt sát vùng đáp án hoặc dùng ảnh rõ hơn."
          : reason instanceof Error
            ? reason.message
            : "Không đọc được ảnh đáp án",
      );
    } finally {
      window.clearTimeout(timeoutId);
      setScanning(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  function handlePaste(event: React.ClipboardEvent<HTMLElement>) {
    if (scanning) return;
    const imageItem = [...event.clipboardData.items].find((item) =>
      item.type.startsWith("image/"),
    );
    if (!imageItem) return;
    const image = imageItem.getAsFile();
    if (image) {
      event.preventDefault();
      void scanImage(image);
    }
  }

  return (
    <section className="rounded-xl border border-slate-300 bg-white p-4 shadow-[0_3px_12px_rgba(31,78,121,0.08)]">
      <div className="flex items-start gap-3">
        <span className="rounded-lg border border-slate-200 bg-slate-50 p-2 text-[#1f4e79]">
          <KeyRound className="h-5 w-5" />
        </span>
        <div>
          <h2 className="text-sm font-bold text-slate-950">
            Đáp án {examType === "listening" ? "Listening" : "Reading"}
          </h2>
          <p className="mt-0.5 text-xs leading-5 text-slate-500">
            Dán text dạng <strong>1(D) 2(A)</strong>, chọn ảnh hoặc nhấn Ctrl+V
            để đọc ảnh đáp án; PDF scan cũng được nhận dạng trực tiếp trên máy.
          </p>
        </div>
      </div>

      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onPaste={handlePaste}
        rows={5}
        className="mt-4 w-full resize-y rounded-lg border border-slate-300 bg-white p-3 text-sm text-slate-900 shadow-inner outline-none placeholder:text-slate-400 focus:border-[#1f4e79] focus:ring-2 focus:ring-[#1f4e79]/10"
        placeholder={
          examType === "listening"
            ? "1(D) 2(A) 3(B) ... 100(D)"
            : "101(B) 102(A) 103(A) ... 200(A)"
        }
      />

      <div
        tabIndex={0}
        onPaste={handlePaste}
        className="mt-3 rounded-lg border border-dashed border-slate-300 bg-slate-50 px-3 py-3 outline-none focus:border-[#1f4e79] focus:ring-2 focus:ring-[#1f4e79]/10"
      >
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={applyText}
            className="inline-flex items-center gap-2 rounded-lg border border-[#1f4e79] bg-[#1f4e79] px-3 py-2 text-xs font-bold text-white shadow-[0_3px_0_#173a5c] transition hover:-translate-y-0.5 hover:shadow-[0_5px_0_#173a5c] active:translate-y-0 active:shadow-[0_2px_0_#173a5c]"
          >
            <ScanText className="h-4 w-4" /> Áp dụng text
          </button>
          <button
            type="button"
            disabled={scanning}
            onClick={() => inputRef.current?.click()}
            className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-bold text-[#1f4e79] shadow-[0_2px_5px_rgba(31,78,121,0.12)] transition hover:border-[#1f4e79] disabled:opacity-50"
          >
            {scanning ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <ImageUp className="h-4 w-4" />
            )}
            Chọn ảnh đáp án
          </button>
          <span className="inline-flex items-center gap-1.5 px-2 py-2 text-xs font-medium text-slate-500">
            <ClipboardPaste className="h-4 w-4" /> Ctrl+V ảnh vào khu vực này
          </span>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,image/bmp,image/tiff,application/pdf"
          className="hidden"
          onChange={(event) => {
            const image = event.target.files?.[0];
            if (image) void scanImage(image);
          }}
        />
      </div>

      {message && (
        <p className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-medium text-slate-700">
          {message}
        </p>
      )}
    </section>
  );
}
