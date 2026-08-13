"use client";

import { useRef, useState } from "react";
import { Crop, X } from "lucide-react";

import { useAuthenticatedMediaUrl } from "@/components/AuthenticatedMedia";
import type { AssetRef } from "@/lib/utils";

type DragState = {
  mode: "move" | "resize";
  startX: number;
  startY: number;
  bbox: [number, number, number, number];
};

export type CropSelection = {
  page: number;
  bbox: [number, number, number, number];
  questionNumbers?: number[];
};

interface CropEditorProps {
  jobId: string;
  asset: AssetRef;
  label?: string;
  mode?: "edit" | "manual";
  pageCount?: number;
  availableQuestionNumbers?: number[];
  onCancel: () => void;
  onSave: (selection: CropSelection) => Promise<void>;
}

const clamp = (value: number, min = 0, max = 1) =>
  Math.max(min, Math.min(max, value));

export default function CropEditor({
  jobId,
  asset,
  label,
  mode = "edit",
  pageCount = 1,
  availableQuestionNumbers = [],
  onCancel,
  onSave,
}: CropEditorProps) {
  const [bbox, setBbox] = useState<[number, number, number, number]>(asset.bbox);
  const [page, setPage] = useState(asset.page);
  const [questionInput, setQuestionInput] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<DragState | null>(null);
  const sourcePage = useAuthenticatedMediaUrl(
    `/api/extractions/${jobId}/pages/${page}`,
  );
  const manual = mode === "manual";

  function startDrag(
    event: React.PointerEvent,
    mode: DragState["mode"],
  ) {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      mode,
      startX: event.clientX,
      startY: event.clientY,
      bbox: [...bbox],
    };
  }

  function moveDrag(event: React.PointerEvent) {
    const drag = dragRef.current;
    const container = containerRef.current;
    if (!drag || !container) return;
    const rect = container.getBoundingClientRect();
    const dx = (event.clientX - drag.startX) / rect.width;
    const dy = (event.clientY - drag.startY) / rect.height;
    const [left, top, right, bottom] = drag.bbox;
    if (drag.mode === "move") {
      const width = right - left;
      const height = bottom - top;
      const nextLeft = clamp(left + dx, 0, 1 - width);
      const nextTop = clamp(top + dy, 0, 1 - height);
      setBbox([nextLeft, nextTop, nextLeft + width, nextTop + height]);
    } else {
      setBbox([
        left,
        top,
        clamp(right + dx, left + 0.05, 1),
        clamp(bottom + dy, top + 0.05, 1),
      ]);
    }
  }

  function stopDrag() {
    dragRef.current = null;
  }

  function setCoordinate(index: number, value: number) {
    const next = [...bbox] as [number, number, number, number];
    next[index] = value;
    if (next[2] - next[0] < 0.05 || next[3] - next[1] < 0.05) return;
    setBbox(next);
  }

  async function save() {
    let questionNumbers: number[] | undefined;
    if (manual) {
      const rawValues = questionInput
        .split(/[\s,;]+/)
        .map((value) => value.trim())
        .filter(Boolean);
      const parsed = rawValues.map(Number);
      if (
        !rawValues.length ||
        parsed.some((number) => !Number.isInteger(number)) ||
        new Set(parsed).size !== parsed.length
      ) {
        setValidationError("Nhập số câu, ngăn cách bằng dấu phẩy (ví dụ: 62, 63, 64).");
        return;
      }
      const allowed = new Set(availableQuestionNumbers);
      const invalid = parsed.filter((number) => !allowed.has(number));
      if (invalid.length) {
        setValidationError(`Không có câu: ${invalid.join(", ")}.`);
        return;
      }
      questionNumbers = [...parsed].sort((a, b) => a - b);
      setValidationError(null);
    }
    setSaving(true);
    try {
      await onSave({
        page,
        bbox: bbox.map((value) => Number(value.toFixed(5))) as typeof bbox,
        questionNumbers,
      });
    } finally {
      setSaving(false);
    }
  }

  const labels = ["Trái", "Trên", "Phải", "Dưới"];

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/70 p-3 sm:p-6">
      <div className="flex max-h-[96vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
        <header className="flex items-center justify-between border-b px-5 py-3">
          <div className="flex items-center gap-2">
            <Crop className="h-5 w-5 text-[#1f4e79]" />
            <div>
              <h2 className="font-bold text-slate-900">Chỉnh vùng crop</h2>
              <p className="text-xs text-slate-500">
                {label ? `${label} · ` : ""}Trang {page}
              </p>
            </div>
          </div>
          <button onClick={onCancel} className="rounded-lg p-2 hover:bg-slate-100">
            <X className="h-5 w-5" />
          </button>
        </header>

        <div className="flex-1 overflow-auto bg-slate-100 p-4">
          <div
            ref={containerRef}
            className="relative mx-auto w-fit max-w-full select-none shadow-lg"
            onPointerMove={moveDrag}
            onPointerUp={stopDrag}
            onPointerCancel={stopDrag}
          >
            {sourcePage.url ? (
              <>
                {/* The source is a short-lived authenticated blob URL. */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={sourcePage.url}
                  alt={`Trang PDF ${asset.page}`}
                  draggable={false}
                  className="block max-h-[65vh] max-w-full"
                />
                <div className="pointer-events-none absolute inset-0 bg-slate-950/35" />
                <div
                  className="absolute cursor-move border-2 border-[#1f4e79] bg-[#1f4e79]/10 shadow-[0_0_0_9999px_rgba(15,23,42,0.12)]"
                  style={{
                    left: `${bbox[0] * 100}%`,
                    top: `${bbox[1] * 100}%`,
                    width: `${(bbox[2] - bbox[0]) * 100}%`,
                    height: `${(bbox[3] - bbox[1]) * 100}%`,
                  }}
                  onPointerDown={(event) => startDrag(event, "move")}
                >
                  <button
                    type="button"
                    aria-label="Thay đổi kích thước vùng crop"
                    className="absolute -bottom-2 -right-2 h-5 w-5 cursor-se-resize rounded-full border-2 border-white bg-[#1f4e79] shadow"
                    onPointerDown={(event) => {
                      event.stopPropagation();
                      startDrag(event, "resize");
                    }}
                  />
                </div>
              </>
            ) : (
              <div className="flex h-[55vh] w-[min(80vw,900px)] max-w-full items-center justify-center px-6 text-center text-sm text-slate-500">
                {sourcePage.error || "Đang tải trang PDF..."}
              </div>
            )}
          </div>
        </div>

        <footer className="border-t bg-white p-4">
          <div className="mb-4 grid gap-3 sm:grid-cols-2">
            <label className="text-xs font-medium text-slate-600">
              Trang PDF gốc
              <input
                type="number"
                min={1}
                max={Math.max(1, pageCount)}
                value={page}
                onChange={(event) => {
                  const nextPage = Number(event.target.value);
                  if (Number.isInteger(nextPage) && nextPage >= 1 && nextPage <= pageCount) {
                    setPage(nextPage);
                  }
                }}
                className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 outline-none focus:border-[#1f4e79]"
              />
            </label>
            {manual && (
              <label className="text-xs font-medium text-slate-600">
                Gán ảnh này cho câu
                <input
                  value={questionInput}
                  onChange={(event) => {
                    setQuestionInput(event.target.value);
                    setValidationError(null);
                  }}
                  placeholder="Ví dụ: 62, 63, 64"
                  className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 outline-none focus:border-[#1f4e79]"
                />
              </label>
            )}
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {bbox.map((value, index) => (
              <label key={labels[index]} className="text-xs font-medium text-slate-600">
                {labels[index]}: {(value * 100).toFixed(1)}%
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.005}
                  value={value}
                  onChange={(event) =>
                    setCoordinate(index, Number(event.target.value))
                  }
                  className="mt-1 w-full accent-[#1f4e79]"
                />
              </label>
            ))}
          </div>
          {validationError && (
            <p className="mt-3 text-xs font-medium text-red-600">{validationError}</p>
          )}
          <div className="mt-4 flex justify-end gap-2">
            <button
              type="button"
              onClick={onCancel}
              className="ui-btn-secondary px-4 py-2 text-sm"
            >
              Hủy
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={save}
              className="ui-btn-primary px-5 py-2 text-sm disabled:opacity-50"
            >
              {saving
                ? "Đang tạo lại ảnh..."
                : manual
                  ? "Tạo ảnh thủ công"
                  : "Lưu vùng crop"}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
