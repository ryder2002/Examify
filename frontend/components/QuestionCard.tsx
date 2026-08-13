"use client";

import { useState } from "react";
import { Bookmark, Check, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Question } from "@/lib/utils";

interface QuestionCardProps {
  index: number;
  question: Question;
  selected: string | null;
  showAnswer: boolean;
  onSelect: (letter: string) => void;
  flagged?: boolean;
  onToggleFlag?: () => void;
}

export default function QuestionCard({
  index,
  question,
  selected,
  showAnswer,
  onSelect,
  flagged,
  onToggleFlag,
}: QuestionCardProps) {
  const [localFlagged, setLocalFlagged] = useState(false);
  const isFlagged = flagged ?? localFlagged;

  return (
    <article className="rounded-xl border border-slate-300 bg-white p-6 shadow-[0_4px_16px_rgba(31,78,121,0.08)] sm:p-7">
      <header className="mb-4 flex items-start justify-between gap-4">
        <h3 className="text-lg font-bold leading-relaxed text-slate-900">
          {question.text
            ? `${question.number}. ${question.text}`
            : `${question.number}. Chọn đáp án đúng`}
        </h3>
        <button
          type="button"
          onClick={() => {
            if (onToggleFlag) onToggleFlag();
            else setLocalFlagged(!localFlagged);
          }}
          className={cn(
            "rounded-md border p-1.5 transition-colors shrink-0",
            isFlagged
              ? "border-amber-400 bg-amber-50 text-amber-600"
              : "border-slate-300 bg-white text-slate-400 hover:border-slate-400 hover:text-slate-600"
          )}
          title={
            isFlagged
              ? `Bỏ cờ câu ${question.number}`
              : `Gắn cờ câu ${question.number}`
          }
          aria-label={
            isFlagged
              ? `Bỏ cờ câu ${question.number}`
              : `Gắn cờ câu ${question.number}`
          }
          aria-pressed={isFlagged}
        >
          <Bookmark className={cn("h-4 w-4", isFlagged && "fill-amber-500")} />
        </button>
      </header>

      <div className="space-y-2.5">
        {question.option_letters.map((letter) => {
          const optionText = question.options[letter];

          const isSelected = selected === letter;
          const isCorrect = question.correct === letter;
          const showAsCorrect = showAnswer && isCorrect;
          const showAsWrong =
            showAnswer && Boolean(question.correct) && isSelected && !isCorrect;

          return (
            <button
              key={letter}
              type="button"
              onClick={() => {
                const selection =
                  typeof window === "undefined" ? null : window.getSelection();
                // Double-clicking/dragging answer text is a dictionary action,
                // not an answer change.
                if (selection && !selection.isCollapsed) return;
                if (!showAnswer) onSelect(letter);
              }}
              disabled={showAnswer}
              className={cn(
                "flex w-full select-text items-center gap-4 rounded-lg border px-5 py-4 text-left text-base transition-all",
                "disabled:cursor-default",
                showAsCorrect
                  ? "border-emerald-500 bg-emerald-50/80 text-emerald-900 font-medium"
                  : showAsWrong
                  ? "border-rose-500 bg-rose-50/80 text-rose-900 font-medium"
                  : isSelected
                  ? "border-[#1f4e79] bg-slate-50 text-[#1f4e79] font-medium shadow-sm"
                  : "border-slate-300 bg-white text-slate-700 hover:border-[#1f4e79] hover:bg-slate-50"
              )}
            >
              <div
                className={cn(
                  "flex h-5 w-5 shrink-0 items-center justify-center rounded-full border transition-all",
                  showAsCorrect
                    ? "border-emerald-600 bg-emerald-600 text-white"
                    : showAsWrong
                    ? "border-rose-600 bg-rose-600 text-white"
                    : isSelected
                    ? "border-[#1f4e79] bg-[#1f4e79] text-white"
                    : "border-slate-300 bg-white"
                )}
              >
                {showAsCorrect ? (
                  <Check className="h-3 w-3 stroke-[3]" />
                ) : showAsWrong ? (
                  <X className="h-3 w-3 stroke-[3]" />
                ) : isSelected ? (
                  <div className="h-2 w-2 rounded-full bg-white" />
                ) : null}
              </div>
              <span className="flex-1 text-base">
                <span className="font-semibold">{letter}</span>
                {optionText ? <span>. {optionText}</span> : null}
              </span>
            </button>
          );
        })}
      </div>
    </article>
  );
}
