"use client";

import Image from "next/image";
import { Volume2, ChevronRight, Sparkles } from "lucide-react";

interface Part1DirectionsViewProps {
  onContinue: () => void;
  isPlaying?: boolean;
}

export default function Part1DirectionsView({
  onContinue,
  isPlaying = true,
}: Part1DirectionsViewProps) {
  return (
    <div className="flex w-full max-w-5xl flex-col gap-6 p-3 sm:p-6 animate-in fade-in duration-300">
      {/* Top Card Box */}
      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xl">
        {/* Card Header Bar */}
        <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50/80 px-6 py-4">
          <h2 className="text-xl font-extrabold tracking-wide text-slate-800">
            PART 1
          </h2>
          <div className="flex items-center gap-2 rounded-full bg-brand-50 px-3.5 py-1.5 text-sm font-semibold text-[#1f4e79]">
            <Volume2 className={`h-4 w-4 ${isPlaying ? "animate-pulse" : ""}`} />
            <span>{isPlaying ? "Playing Direction..." : "Direction Audio"}</span>
          </div>
        </div>

        {/* Card Content Area */}
        <div className="p-6 sm:p-8 space-y-6">
          {/* Instructions Paragraph */}
          <div className="rounded-xl bg-slate-50 border border-slate-200/80 p-4 text-sm leading-relaxed text-black font-semibold">
            <p>
              <strong className="text-[#1f4e79]">Directions:</strong> For each
              question in this part, you will hear four statements about a
              picture in your test book. When you hear the statements, you must
              select the one statement that best describes what you see in the
              picture. Then find the number of the question on your answer
              sheet and mark your answer. The statements will not be printed in
              your test book and will be spoken only one time.
            </p>
          </div>

          {/* Main Visual & Demo Section */}
          <div className="grid grid-cols-1 md:grid-cols-12 gap-6 items-center">
            {/* Left: Sample Image */}
            <div className="md:col-span-8 overflow-hidden rounded-xl border border-slate-200 bg-slate-100 shadow-sm transition hover:shadow-md">
              <Image
                src="/directions_part1_sample.jpg"
                alt="TOEIC Part 1 Directions Sample Image"
                width={800}
                height={500}
                className="w-full h-auto object-cover object-center"
                priority
              />
            </div>

            {/* Right: Answer Sheet Sample Card */}
            <div className="md:col-span-4 flex flex-col rounded-xl border border-slate-200 bg-white overflow-hidden shadow-sm">
              <div className="bg-[#1f4e79] px-4 py-2.5 text-center text-sm font-bold text-white shadow-sm">
                Choose the correct answer.
              </div>
              <div className="p-6 flex flex-col items-center justify-center gap-4 bg-slate-50/50">
                <span className="text-base font-extrabold text-slate-700">1.</span>
                <div className="flex flex-col gap-3">
                  {["A", "B", "C", "D"].map((letter) => {
                    const isSelected = letter === "C";
                    return (
                      <div
                        key={letter}
                        className={`flex h-9 w-9 items-center justify-center rounded-full border-2 text-sm font-extrabold transition ${
                          isSelected
                            ? "border-[#1f4e79] bg-[#1f4e79] text-white shadow-md scale-105"
                            : "border-slate-300 bg-white text-slate-500"
                        }`}
                      >
                        {letter}
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>

          {/* Statement Example Callout */}
          <div className="rounded-xl border-l-4 border-[#1f4e79] bg-brand-50/60 p-4 text-sm font-medium text-slate-800 shadow-sm">
            <p className="italic">
              Statement (C), <span className="font-bold text-black">&quot;They&apos;re sitting at a table,&quot;</span> is the best description of the picture, so you should select answer (C) and mark it on your answer sheet.
            </p>
          </div>
        </div>
      </div>

      {/* Bottom Action Footer */}
      <div className="flex justify-end pt-2">
        <button
          onClick={onContinue}
          className="group flex items-center gap-2.5 rounded-xl bg-[#1f4e79] px-8 py-3.5 text-base font-bold text-white shadow-lg transition hover:bg-[#1e3a5f] hover:shadow-xl active:scale-[0.99]"
        >
          <span>Continue</span>
          <ChevronRight className="h-5 w-5 transition-transform group-hover:translate-x-1" />
        </button>
      </div>
    </div>
  );
}
