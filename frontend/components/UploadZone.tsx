"use client";

import { useState } from "react";
import { useDropzone } from "react-dropzone";
import { UploadCloud, FileText, X } from "lucide-react";
import { cn } from "@/lib/utils";

interface UploadZoneProps {
  file: File | null;
  onFileChange: (file: File | null) => void;
}

export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

export default function UploadZone({ file, onFileChange }: UploadZoneProps) {
  const [fileError, setFileError] = useState<string | null>(null);
  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: { "application/pdf": [".pdf"] },
    maxFiles: 1,
    maxSize: MAX_UPLOAD_BYTES,
    onDrop: (accepted) => {
      const next = accepted[0];
      if (next) {
        setFileError(null);
        onFileChange(next);
      }
    },
    onDropRejected: (rejections) => {
      const tooLarge = rejections.some((rejection) =>
        rejection.errors.some((error) => error.code === "file-too-large"),
      );
      setFileError(
        tooLarge
          ? "File vượt quá giới hạn 50 MB."
          : "Chỉ chấp nhận một file PDF hợp lệ.",
      );
      onFileChange(null);
    },
  });

  return (
    <div
      {...getRootProps()}
      data-dropzone-id="pdf-uploadzone"
      className={cn(
        "group relative cursor-pointer rounded-xl border bg-white p-7 text-center shadow-[0_2px_8px_rgba(31,78,121,0.08)] transition-all",
        isDragActive
          ? "border-[#1f4e79] ring-2 ring-[#1f4e79]/10"
          : "border-slate-300 hover:border-[#1f4e79]"
      )}
    >
      <input {...getInputProps()} />
      {file ? (
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 text-left">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-slate-200 bg-slate-50 text-[#1f4e79]">
              <FileText className="h-6 w-6" />
            </div>
            <div>
              <p className="font-medium text-slate-900">{file.name}</p>
              <p className="text-xs text-slate-500">
                {(file.size / 1024 / 1024).toFixed(2)} MB
              </p>
            </div>
          </div>
          <button
            type="button"
              onClick={(e) => {
                e.stopPropagation();
                setFileError(null);
                onFileChange(null);
            }}
            className="rounded-full p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
            aria-label="Xóa file"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
      ) : (
        <div className="flex flex-col items-center gap-3">
          <div className="flex h-14 w-14 items-center justify-center rounded-xl border border-slate-200 bg-slate-50 text-[#1f4e79] shadow-sm transition-transform group-hover:-translate-y-0.5">
            <UploadCloud className="h-7 w-7" />
          </div>
          <div>
            <p className="text-base font-medium text-slate-900">
              {isDragActive
                ? "Thả file PDF vào đây..."
                : "Kéo thả file PDF hoặc bấm để chọn"}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              Hỗ trợ PDF có text và PDF scan. Tối đa 50 MB.
            </p>
            {fileError && (
              <p className="mt-2 text-xs font-semibold text-red-600">
                {fileError}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
