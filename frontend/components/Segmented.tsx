"use client";

import { cn } from "@/lib/utils";

interface Option {
  label: string;
  value: string;
}

interface SegmentedProps {
  label: string;
  value: string;
  options: Option[];
  onChange: (value: string) => void;
}

export default function Segmented({
  label,
  value,
  options,
  onChange,
}: SegmentedProps) {
  return (
    <div>
      <p className="mb-2 text-sm font-bold text-slate-800">{label}</p>
      <div className="grid grid-cols-3 gap-2 sm:flex sm:flex-wrap">
        {options.map((opt) => {
          const active = opt.value === value;
          return (
            <button
              key={opt.value}
              type="button"
              onClick={() => onChange(opt.value)}
              className={cn(
                "rounded-lg border px-3 py-2 text-sm font-bold transition-all",
                active
                  ? "border-[#1f4e79] bg-[#1f4e79] text-white shadow-[0_2px_0_#173a5c]"
                  : "border-slate-300 bg-white text-slate-600 shadow-sm hover:border-[#1f4e79] hover:text-[#1f4e79]"
              )}
            >
              {opt.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
