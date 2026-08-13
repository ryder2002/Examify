"use client";

import { Headphones } from "lucide-react";

type AudioProcessingDialogProps = {
  mode: "full" | "question_groups";
  progress: number;
  stage: string;
  parallel?: boolean;
  audioProgress?: number;
  ocrProgress?: number;
  audioStage?: string;
  ocrStage?: string;
};

export default function AudioProcessingDialog({
  mode,
  progress,
  stage,
  parallel = false,
  audioProgress = progress,
  ocrProgress = 0,
  audioStage = stage,
  ocrStage = "Đang chờ OCR",
}: AudioProcessingDialogProps) {
  const boundedProgress = Math.max(0, Math.min(100, Math.round(progress)));
  const boundedAudio = Math.max(0, Math.min(100, Math.round(audioProgress)));
  const boundedOcr = Math.max(0, Math.min(100, Math.round(ocrProgress)));
  const title = parallel
    ? "Đang xử lý Audio và OCR"
    : mode === "full"
      ? "Đang xử lý Audio Full"
      : "Đang chuẩn hóa bộ audio";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 px-4 backdrop-blur-[2px]">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="audio-processing-title"
        aria-describedby="audio-processing-description"
        className="w-full max-w-lg rounded-2xl border border-[#1f4e79]/30 bg-white p-6 shadow-2xl sm:p-8"
      >
        <div className="flex items-start gap-4">
          <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-[#1f4e79]/10 text-[#1f4e79]">
            <Headphones className="h-6 w-6" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-xs font-extrabold uppercase tracking-[0.16em] text-[#1f4e79]">
              {parallel ? "Hai tác vụ song song" : "Bước 1/2"}
            </p>
            <h2
              id="audio-processing-title"
              className="mt-1 text-xl font-extrabold text-slate-900"
            >
              {title}
            </h2>
            <p
              id="audio-processing-description"
              className="mt-2 text-sm leading-6 text-slate-600"
            >
              {parallel
                ? "Audio đang được chuẩn hóa/cắt trong khi OCR server đọc tài liệu. Hệ thống chỉ mở màn hình review sau khi cả hai tác vụ hoàn tất."
                : <>
                    {mode === "full"
                      ? "Hệ thống đang phân tích và cắt audio theo cấu trúc TOEIC. "
                      : "Hệ thống đang kiểm tra và chuẩn hóa bộ audio theo câu/nhóm. "}
                    OCR tài liệu sẽ tự bắt đầu ngay sau khi audio hoàn tất.
                  </>}
            </p>
          </div>
        </div>

        <div className="mt-6 rounded-xl border border-slate-200 bg-slate-50 p-4">
          <div className="flex items-center justify-between gap-4 text-sm">
            <span className="font-semibold text-slate-700">{stage}</span>
            <span className="shrink-0 text-lg font-extrabold text-[#1f4e79]">
              {boundedProgress}%
            </span>
          </div>
          <div className="mt-3 h-2.5 overflow-hidden rounded-full bg-slate-200">
            <div
              aria-label={`Tiến độ xử lý audio ${boundedProgress}%`}
              className="h-full rounded-full bg-[#1f4e79] transition-[width] duration-500"
              style={{ width: `${boundedProgress}%` }}
            />
          </div>
        </div>

        {parallel ? (
          <div className="mt-5 grid gap-3 text-xs font-bold sm:grid-cols-2">
            {[
              ["Audio", boundedAudio, audioStage],
              ["OCR tài liệu", boundedOcr, ocrStage],
            ].map(([label, value, detail]) => (
              <div key={String(label)} className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                <div className="flex items-center justify-between gap-2 text-slate-700">
                  <span>{label}</span>
                  <span className="text-[#173254]">{value}%</span>
                </div>
                <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200">
                  <div
                    aria-label={`Tiến độ ${label} ${value}%`}
                    className="h-full rounded-full bg-[#1f4e79] transition-[width] duration-500"
                    style={{ width: `${value}%` }}
                  />
                </div>
                <p className="mt-2 line-clamp-2 font-medium leading-4 text-slate-500">
                  {detail}
                </p>
              </div>
            ))}
          </div>
        ) : (
          <div className="mt-5 grid grid-cols-2 gap-3 text-xs font-bold">
            <div className="rounded-lg border border-[#1f4e79]/30 bg-[#1f4e79]/5 px-3 py-2.5 text-[#173254]">
              <span className="mr-2 inline-block h-2 w-2 animate-pulse rounded-full bg-[#1f4e79]" />
              Audio đang xử lý
            </div>
            <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-slate-500">
              OCR · bước tiếp theo
            </div>
          </div>
        )}
        <p className="mt-4 text-center text-xs leading-5 text-slate-500">
          Vui lòng giữ trang này mở. Thời gian phụ thuộc độ dài file audio.
        </p>
      </div>
    </div>
  );
}
