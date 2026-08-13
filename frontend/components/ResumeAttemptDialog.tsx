"use client";

import { ArrowLeft, RotateCcw } from "lucide-react";

export default function ResumeAttemptDialog({
  questionNumber,
  onContinue,
  onLeave,
}: {
  questionNumber: number;
  onContinue: () => void;
  onLeave: () => void;
}) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-100 p-4">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="resume-attempt-title"
        className="w-full max-w-lg rounded-3xl border border-brand-100 bg-white p-7 shadow-2xl sm:p-9"
      >
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-50 text-[#173a5c]">
          <RotateCcw className="h-7 w-7" />
        </div>
        <h1 id="resume-attempt-title" className="mt-5 text-center text-2xl font-extrabold text-slate-900">
          Tiếp tục bài thi đang làm?
        </h1>
        <p className="mt-3 text-center leading-7 text-slate-600">
          Hệ thống đã lưu tiến độ của bạn đến câu <strong className="text-slate-900">{questionNumber}</strong>. Các đáp án đã chọn sẽ được khôi phục đầy đủ.
        </p>
        <div className="mt-7 grid gap-3 sm:grid-cols-2">
          <button
            type="button"
            onClick={onLeave}
            className="inline-flex items-center justify-center gap-2 rounded-xl border border-slate-300 px-4 py-3 font-bold text-slate-700 hover:bg-slate-50"
          >
            <ArrowLeft className="h-4 w-4" /> Quay lại danh sách
          </button>
          <button
            type="button"
            onClick={onContinue}
            autoFocus
            className="rounded-xl bg-[#1f4e79] px-4 py-3 font-extrabold text-white shadow hover:bg-[#1a3657]"
          >
            Tiếp tục từ câu {questionNumber}
          </button>
        </div>
      </section>
    </main>
  );
}
