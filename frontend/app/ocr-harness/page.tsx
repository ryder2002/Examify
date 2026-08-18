"use client";

import { useState } from "react";

import type { ExamType } from "@/lib/utils";

declare global {
  interface Window {
    __OCR_HARNESS_RESULT__?: unknown;
  }
}

export default function OcrHarnessPage() {
  const [file, setFile] = useState<File | null>(null);
  const [examType, setExamType] = useState<ExamType>("listening");
  const [status, setStatus] = useState("idle");

  if (process.env.NEXT_PUBLIC_OCR_REGRESSION_HARNESS !== "1") {
    return <main className="p-8">OCR regression harness is disabled.</main>;
  }

  async function run() {
    if (!file) return;
    setStatus("running");
    try {
      const { runClientOcr } = await import("@/lib/client-ocr");
      const manifest = await runClientOcr({
        file,
        examType,
        requestedCount: 100,
        onProgress: (progress) => setStatus(`${progress.phase}:${progress.progress}`),
      });
      window.__OCR_HARNESS_RESULT__ = manifest;
      setStatus("complete");
    } catch (reason) {
      window.__OCR_HARNESS_RESULT__ = {
        error: reason instanceof Error ? reason.message : String(reason),
      };
      setStatus("failed");
    }
  }

  return (
    <main className="mx-auto max-w-xl space-y-4 p-8">
      <h1 className="text-xl font-semibold">Client OCR regression harness</h1>
      <select
        aria-label="Exam type"
        value={examType}
        onChange={(event) => setExamType(event.target.value as ExamType)}
      >
        <option value="listening">Listening</option>
        <option value="reading">Reading</option>
      </select>
      <input
        aria-label="PDF fixture"
        type="file"
        accept="application/pdf"
        onChange={(event) => setFile(event.target.files?.[0] || null)}
      />
      <button type="button" disabled={!file || status === "running"} onClick={() => void run()}>
        Run local OCR
      </button>
      <output data-testid="ocr-status">{status}</output>
    </main>
  );
}
