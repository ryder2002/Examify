"use client";

import { FileAudio, Music2, X } from "lucide-react";
import { useDropzone } from "react-dropzone";

export const MAX_AUDIO_BYTES = 50 * 1024 * 1024;

type AudioUploadProps = {
  file: File | null;
  onFileChange: (file: File | null) => void;
  onError: (message: string | null) => void;
  label?: string;
  compact?: boolean;
  dropzoneId?: string;
};

export default function AudioUpload({
  file,
  onFileChange,
  onError,
  label = "Audio Full",
  compact = false,
  dropzoneId,
}: AudioUploadProps) {
  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: {
      "audio/*": [".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".webm", ".flac"],
    },
    maxFiles: 1,
    maxSize: MAX_AUDIO_BYTES,
    onDrop: (accepted) => {
      const next = accepted[0];
      if (next) {
        onError(null);
        onFileChange(next);
      }
    },
    onDropRejected: (rejections) => {
      const tooLarge = rejections.some((rejection) =>
        rejection.errors.some((error) => error.code === "file-too-large"),
      );
      onError(
        tooLarge
          ? "Audio vượt quá giới hạn 50 MB."
          : "Định dạng audio không được hỗ trợ.",
      );
      onFileChange(null);
    },
  });

  return (
    <div
      {...getRootProps()}
      data-dropzone-id={dropzoneId}
      className={`cursor-pointer rounded-xl border bg-white shadow-[0_2px_8px_rgba(31,78,121,0.08)] transition ${
        compact ? "p-3" : "p-4"
      } ${
        isDragActive
          ? "border-[#1f4e79] ring-2 ring-[#1f4e79]/10"
          : "border-slate-300 hover:border-[#1f4e79]"
      }`}
    >
      <input {...getInputProps()} />
      {file ? (
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <span className="rounded-lg border border-slate-200 bg-slate-50 p-2 text-[#1f4e79]">
              <FileAudio className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-slate-900">
                {label}: {file.name}
              </p>
              <p className="text-xs text-slate-500">
                {(file.size / 1024 / 1024).toFixed(2)} MB
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onFileChange(null);
              onError(null);
            }}
            className="rounded-lg border border-slate-300 bg-white p-2 text-slate-500 shadow-sm hover:border-slate-400 hover:text-slate-900"
            aria-label="Xóa audio"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-3">
          <span className="rounded-lg border border-slate-200 bg-slate-50 p-2.5 text-[#1f4e79]">
            <Music2 className="h-5 w-5" />
          </span>
          <div>
            <p className="text-sm font-semibold text-slate-900">
              {isDragActive ? "Thả audio tại đây" : `Chọn ${label}`}
            </p>
            <p className="mt-0.5 text-xs text-slate-500">
              MP3, WAV, M4A, AAC, OGG, WebM hoặc FLAC · tối đa 50 MB
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
