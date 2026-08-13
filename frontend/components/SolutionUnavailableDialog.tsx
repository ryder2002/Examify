"use client";

import { BookOpen, X } from "lucide-react";

export default function SolutionUnavailableDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/55 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="solution-unavailable-title"
    >
      <div className="w-full max-w-md rounded-3xl border border-brand-100 bg-white p-6 shadow-2xl">
        <div className="flex items-start justify-between gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-50 text-[#173a5c]">
            <BookOpen className="h-6 w-6" />
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Đóng thông báo"
            className="rounded-xl p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <h2 id="solution-unavailable-title" className="mt-5 text-xl font-extrabold text-slate-900">
          Giải chi tiết chưa khả dụng
        </h2>
        <p className="mt-3 leading-7 text-slate-600">
          Giáo viên chưa bổ sung nội dung giải chi tiết cho bài thi này. Vui lòng quay lại sau để xem phần giải thích và bản dịch đầy đủ.
        </p>
        <button
          type="button"
          onClick={onClose}
          className="mt-6 w-full rounded-xl bg-[#1f4e79] px-4 py-3 font-extrabold text-white shadow hover:bg-[#1a3657]"
        >
          Đã hiểu
        </button>
      </div>
    </div>
  );
}
