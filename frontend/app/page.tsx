"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useDropzone } from "react-dropzone";
import {
  BookOpen,
  CheckCircle2,
  Headphones,
  Loader2,
  ScanLine,
  Upload,
  UploadCloud,
} from "lucide-react";

import AudioUpload, { MAX_AUDIO_BYTES } from "@/components/AudioUpload";
import AudioProcessingDialog from "@/components/AudioProcessingDialog";
import Header from "@/components/Header";
import UploadZone, { MAX_UPLOAD_BYTES } from "@/components/UploadZone";
import Segmented from "@/components/Segmented";
import {
  abandonPendingListeningSession,
  apiFetch,
  desktopRuntime,
  getDesktopExamQuota,
  isDesktop,
} from "@/lib/api";
import type { ExamDraft, ExamType } from "@/lib/utils";

type AudioMode = "full" | "question_groups";
type AudioGroupDraft = { id: string; range: string; file: File | null };

const QUESTION_AUDIO_NUMBERS = Array.from({ length: 31 }, (_, index) => index + 1);
const MAX_TOTAL_AUDIO_BYTES = 300 * 1024 * 1024;

function defaultAudioGroups(): AudioGroupDraft[] {
  return Array.from({ length: 23 }, (_, index) => {
    const start = 32 + index * 3;
    const end = Math.min(start + 2, 100);
    return { id: `group-${start}-${end}`, range: `${start}-${end}`, file: null };
  });
}

function parseAudioRange(value: string): [number, number] | null {
  const match = value.trim().match(/^(\d{1,3})\s*[-–]\s*(\d{1,3})$/);
  if (!match) return null;
  const start = Number(match[1]);
  const end = Number(match[2]);
  return Number.isInteger(start) && Number.isInteger(end) && start <= end
    ? [start, end]
    : null;
}

const COUNT_OPTIONS = [
  { label: "10", value: "10" },
  { label: "20", value: "20" },
  { label: "25", value: "25" },
  { label: "50", value: "50" },
  { label: "100", value: "100" },
  { label: "Tất cả", value: "all" },
  { label: "Khác", value: "custom" },
];

export default function HomePage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [audioMode, setAudioMode] = useState<AudioMode>("full");
  const [fullAudioFile, setFullAudioFile] = useState<File | null>(null);
  const [questionAudioFiles, setQuestionAudioFiles] = useState<Record<number, File | null>>(
    () => Object.fromEntries(QUESTION_AUDIO_NUMBERS.map((number) => [number, null])),
  );
  const [audioGroups, setAudioGroups] = useState<AudioGroupDraft[]>(defaultAudioGroups);
  const [audioError, setAudioError] = useState<string | null>(null);
  const [examType, setExamType] = useState<ExamType | null>(null);
  const [countChoice, setCountChoice] = useState("all");
  const [customCount, setCustomCount] = useState("");
  const [loading, setLoading] = useState(false);
  const [cancelingPending, setCancelingPending] = useState(false);
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState("");
  const [processingPhase, setProcessingPhase] = useState<ExamDraft["processing_phase"]>(
    "queued",
  );
  const [phaseProgress, setPhaseProgress] = useState(0);
  const [audioProgress, setAudioProgress] = useState(0);
  const [ocrProgress, setOcrProgress] = useState(0);
  const [audioStage, setAudioStage] = useState("");
  const [ocrStage, setOcrStage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [noCache, setNoCache] = useState(true);
  const [hasPendingListening, setHasPendingListening] = useState(false);
  const cancelled = useRef(false);

  function resetAudioSelection() {
    setFullAudioFile(null);
    setQuestionAudioFiles(
      Object.fromEntries(QUESTION_AUDIO_NUMBERS.map((number) => [number, null])),
    );
    setAudioGroups(defaultAudioGroups());
    setAudioError(null);
  }

  const handleBulkAudioDrop = useCallback((acceptedFiles: File[]) => {
    let matchedCount = 0;
    setQuestionAudioFiles((currentQuestions) => {
      const nextQuestions = { ...currentQuestions };
      setAudioGroups((currentGroups) => {
        const updatedGroups = [...currentGroups];
        for (const file of acceptedFiles) {
            const extension = file.name.substring(file.name.lastIndexOf(".")).toLowerCase();
            if (
              ![".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".webm", ".flac"].includes(
                extension,
              )
            ) {
              continue;
            }
            const nameWithoutExt =
              file.name.substring(0, file.name.lastIndexOf(".")) || file.name;

            // Check group range first e.g., 32-34 or 32_34
            const groupMatch = nameWithoutExt.match(
              /(?:câu|group|q)?\s*(\d{2,3})\s*[-_–\s]\s*(\d{2,3})/i,
            );
            if (groupMatch) {
              const start = Number(groupMatch[1]);
              const end = Number(groupMatch[2]);
              if (start >= 32 && end <= 100 && start <= end) {
                const rangeStr = `${start}-${end}`;
                const existingIdx = updatedGroups.findIndex(
                  (g) =>
                    parseAudioRange(g.range)?.[0] === start &&
                    parseAudioRange(g.range)?.[1] === end,
                );
                if (existingIdx !== -1) {
                  updatedGroups[existingIdx] = { ...updatedGroups[existingIdx], file };
                } else {
                  updatedGroups.push({
                    id: `group-auto-${start}-${end}-${Date.now()}`,
                    range: rangeStr,
                    file,
                  });
                }
                matchedCount += 1;
                continue;
              }
            }

            // Check question number 1..31 e.g., 1.mp3, 01.mp3, q1.mp3, cau 1.mp3
            const qMatch = nameWithoutExt.match(
              /(?:^|[^0-9])(?:câu|cau|q)?\s*0*([1-9]|[12][0-9]|3[01])(?:[^0-9]|$)/i,
            );
            if (qMatch) {
              const num = Number(qMatch[1]);
              if (num >= 1 && num <= 31) {
                nextQuestions[num] = file;
                matchedCount += 1;
                continue;
              }
            }
        }
        return updatedGroups.sort((a, b) => {
          const rA = parseAudioRange(a.range)?.[0] ?? 999;
          const rB = parseAudioRange(b.range)?.[0] ?? 999;
          return rA - rB;
        });
      });
      return nextQuestions;
    });
    if (matchedCount > 0) {
      setAudioError(null);
    }
  }, []);

  const bulkDropzone = useDropzone({
    accept: {
      "audio/*": [".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".webm", ".flac"],
    },
    noClick: false,
    onDrop: handleBulkAudioDrop,
  });

  useEffect(() => {
    let unlistenFn: (() => void) | undefined;
    const setupTauriDropListener = async () => {
      if (typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) return;
      try {
        const { getCurrentWindow } = await import("@tauri-apps/api/window");
        const { convertFileSrc } = await import("@tauri-apps/api/core");

        const convertPathToFile = async (filePath: string): Promise<File> => {
          const assetUrl = convertFileSrc(filePath);
          const response = await fetch(assetUrl);
          const blob = await response.blob();
          const fileName = filePath.split(/[/\\]/).pop() || "file";
          const ext = fileName.substring(fileName.lastIndexOf(".")).toLowerCase();
          const mimeTypes: Record<string, string> = {
            ".pdf": "application/pdf",
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".m4a": "audio/m4a",
            ".aac": "audio/aac",
            ".ogg": "audio/ogg",
            ".webm": "audio/webm",
            ".flac": "audio/flac",
          };
          const type = blob.type || mimeTypes[ext] || "application/octet-stream";
          return new File([blob], fileName, { type });
        };

        unlistenFn = await getCurrentWindow().onDragDropEvent(async (event) => {
          if (event.payload.type === "drop") {
            const paths = event.payload.paths;
            if (!paths || !paths.length) return;

            const droppedFiles: File[] = [];
            for (const path of paths) {
              try {
                const f = await convertPathToFile(path);
                droppedFiles.push(f);
              } catch (err) {
                console.error("Failed to convert Tauri file path:", path, err);
              }
            }
            if (!droppedFiles.length) return;

            const pos = event.payload.position;
            let targetEl: Element | null = null;
            if (pos) {
              const dpr = window.devicePixelRatio || 1;
              targetEl =
                document.elementFromPoint(pos.x, pos.y) ||
                document.elementFromPoint(pos.x / dpr, pos.y / dpr);
            }

            const dropzoneEl = targetEl?.closest("[data-dropzone-id]");
            const dropzoneId = dropzoneEl?.getAttribute("data-dropzone-id");

            if (dropzoneId === "pdf-uploadzone") {
              const pdfFile = droppedFiles.find((f) => f.name.toLowerCase().endsWith(".pdf"));
              if (pdfFile) setFile(pdfFile);
            } else if (dropzoneId === "audio-full") {
              const audioFile = droppedFiles.find((f) =>
                /\.(mp3|wav|m4a|aac|ogg|webm|flac)$/i.test(f.name),
              );
              if (audioFile) {
                setFullAudioFile(audioFile);
                setAudioError(null);
              }
            } else if (dropzoneId && dropzoneId.startsWith("audio-question-")) {
              const qNum = Number(dropzoneId.replace("audio-question-", ""));
              const audioFile = droppedFiles.find((f) =>
                /\.(mp3|wav|m4a|aac|ogg|webm|flac)$/i.test(f.name),
              );
              if (audioFile && qNum >= 1 && qNum <= 31) {
                setQuestionAudioFiles((current) => ({ ...current, [qNum]: audioFile }));
                setAudioError(null);
              }
            } else if (dropzoneId && dropzoneId.startsWith("audio-group-")) {
              const groupId = dropzoneId.replace("audio-group-", "");
              const audioFile = droppedFiles.find((f) =>
                /\.(mp3|wav|m4a|aac|ogg|webm|flac)$/i.test(f.name),
              );
              if (audioFile) {
                setAudioGroups((current) =>
                  current.map((g) => (g.id === groupId ? { ...g, file: audioFile } : g)),
                );
                setAudioError(null);
              }
            } else {
              const audioFilesOnly = droppedFiles.filter((f) =>
                /\.(mp3|wav|m4a|aac|ogg|webm|flac)$/i.test(f.name),
              );
              const pdfFile = droppedFiles.find((f) => f.name.toLowerCase().endsWith(".pdf"));

              if (pdfFile) {
                setFile(pdfFile);
              }

              if (audioFilesOnly.length) {
                if (audioMode === "full" && audioFilesOnly[0]) {
                  setFullAudioFile(audioFilesOnly[0]);
                  setAudioError(null);
                } else if (audioMode === "question_groups") {
                  handleBulkAudioDrop(audioFilesOnly);
                }
              }
            }
          }
        });
      } catch (err) {
        console.error("Failed to setup Tauri drag-drop listener:", err);
      }
    };

    void setupTauriDropListener();
    return () => {
      if (unlistenFn) unlistenFn();
    };
  }, [audioMode, handleBulkAudioDrop]);

  const searchParams = useSearchParams();

  useEffect(() => {
    cancelled.current = false;
    void desktopRuntime();
    const pending = Boolean(sessionStorage.getItem("pending-listening-exam"));
    const next = searchParams ? searchParams.get("next") : new URLSearchParams(window.location.search).get("next");
    setHasPendingListening(pending);
    if (pending) {
      setExamType("reading");
    }
    return () => {
      cancelled.current = true;
    };
  }, [searchParams]);

  const requestedCount = (() => {
    if (countChoice === "all") return null;
    const parsed = Number.parseInt(
      countChoice === "custom" ? customCount || "10" : countChoice,
      10,
    );
    return Number.isFinite(parsed) ? Math.min(100, Math.max(1, parsed)) : 10;
  })();

  async function pollJob(jobId: string) {
    let attempts = 0;
    const maxAttempts = 400;
    while (!cancelled.current && attempts < maxAttempts) {
      attempts += 1;
      const response = await apiFetch(`/api/extractions/${jobId}`, {
        cache: "no-store",
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "Không đọc được trạng thái xử lý");
      }
      const draft = (await response.json()) as ExamDraft;
      setProgress(draft.progress);
      setStage(draft.stage);
      setProcessingPhase(draft.processing_phase || "queued");
      setPhaseProgress(draft.phase_progress ?? 0);
      setAudioProgress(draft.audio_progress ?? 0);
      setOcrProgress(draft.ocr_progress ?? 0);
      setAudioStage(draft.audio_stage || "");
      setOcrStage(draft.ocr_stage || "");
      if (draft.status === "failed") {
        throw new Error(draft.error || "Xử lý tài liệu thất bại");
      }
      if (draft.status === "review" || draft.status === "ready") {
        sessionStorage.setItem("extraction-job", jobId);
        sessionStorage.setItem(
          "quiz-preferences",
          JSON.stringify({ count: requestedCount, shuffle: false }),
        );
        router.push(`/review?job=${jobId}`);
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1200));
    }
    if (attempts >= maxAttempts) {
      throw new Error("Quá thời gian xử lý tài liệu (8 phút). Hãy kiểm tra file PDF hoặc khởi động lại ứng dụng.");
    }
  }

  async function handleSubmit() {
    if (!file) {
      setError("Vui lòng chọn file PDF.");
      return;
    }
    if (!examType) {
      setError("Vui lòng chọn Listening hoặc Reading.");
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setError("File vượt quá giới hạn 50 MB.");
      return;
    }
    const selectedAudioFiles =
      audioMode === "question_groups"
        ? [
            ...Object.values(questionAudioFiles),
            ...audioGroups.map((group) => group.file),
          ].filter((item): item is File => Boolean(item))
        : fullAudioFile
          ? [fullAudioFile]
          : [];
    if (examType === "listening" && audioMode === "question_groups") {
      if (selectedAudioFiles.length === 0) {
        setError("Vui lòng chọn ít nhất 1 file audio.");
        return;
      }
      const expectedGroupRanges = new Set(
        defaultAudioGroups().map((group) => group.range),
      );
      const coveredQuestions = new Set<number>();
      for (const group of audioGroups) {
        if (!group.file) continue;
        const range = parseAudioRange(group.range);
        if (!range || range[0] < 32 || range[1] > 100) {
          setError(`Nhóm audio "${group.range || "chưa đặt số câu"}" không hợp lệ. Dùng dạng 32-34.`);
          return;
        }
        if (!expectedGroupRanges.has(`${range[0]}-${range[1]}`)) {
          setError(
            `Nhóm ${group.range} không đúng nhóm câu TOEIC. Hãy dùng các nhóm 32-34, 35-37, …, 98-100.`,
          );
          return;
        }
        for (let number = range[0]; number <= range[1]; number += 1) {
          if (coveredQuestions.has(number)) {
            setError(`Câu ${number} đang được gán cho nhiều file audio.`);
            return;
          }
          coveredQuestions.add(number);
        }
      }
      for (const [rawNumber, audio] of Object.entries(questionAudioFiles)) {
        if (!audio) continue;
        const number = Number(rawNumber);
        if (coveredQuestions.has(number)) {
          setError(`Câu ${number} đang được gán cho nhiều file audio.`);
          return;
        }
        coveredQuestions.add(number);
      }
    }
    if (examType === "listening" && audioMode === "full" && !fullAudioFile) {
      setError("Vui lòng chọn Audio Full hoặc chuyển sang Audio theo câu / nhóm.");
      return;
    }
    if (selectedAudioFiles.some((item) => item.size > MAX_AUDIO_BYTES)) {
      setError("Audio vượt quá giới hạn 50 MB.");
      return;
    }
    if (
      selectedAudioFiles.reduce((total, item) => total + item.size, 0) >
      MAX_TOTAL_AUDIO_BYTES
    ) {
      setError("Tổng dung lượng audio vượt quá giới hạn 300 MB.");
      return;
    }
    if (isDesktop() && !hasPendingListening) {
      const quota = getDesktopExamQuota();
      if (quota?.limit != null && quota.used >= quota.limit) {
        setError(
          `Bạn đã dùng hết hạn mức ${quota.limit} đề thi. Vui lòng liên hệ quản trị viên để tăng hạn mức.`,
        );
        return;
      }
    }
    setError(null);
    if (isDesktop()) {
      try {
        const readiness = await apiFetch("/health/ready", { cache: "no-store" });
        const status = await readiness.json().catch(() => ({}));
        if (!readiness.ok || !status.ocr_ready) {
          setError(
            "Thành phần OCR của ứng dụng chưa sẵn sàng. Hãy cài lại bản Examify Desktop mới nhất.",
          );
          return;
        }
      } catch {
        setError("Không thể kết nối bộ OCR cục bộ. Hãy khởi động lại hoặc cài lại Examify Desktop.");
        return;
      }
    }
    setLoading(true);
    setProgress(0);
    setProcessingPhase("queued");
    setPhaseProgress(0);
    setAudioProgress(0);
    setOcrProgress(0);
    setAudioStage("");
    setOcrStage("");
    setStage("Đang tải PDF...");
    cancelled.current = false;

    const formData = new FormData();
    formData.append("file", file);
    formData.append("exam_type", examType);
    if (noCache) {
      formData.append("no_cache", "true");
    }
    formData.append(
      "audio_mode",
      examType !== "listening" ? "none" : audioMode,
    );
    if (requestedCount !== null) {
      formData.append("requested_count", String(requestedCount));
    }
    if (examType === "listening") {
      if (audioMode === "full" && fullAudioFile) {
        formData.append("audio_full", fullAudioFile);
      }
      if (audioMode === "question_groups") {
        const manifest: Array<{
          id: string;
          scope: "question" | "group";
          question_numbers: number[];
          file_index: number;
        }> = [];
        let fileIndex = 0;
        for (const number of QUESTION_AUDIO_NUMBERS) {
          const audio = questionAudioFiles[number];
          if (!audio) continue;
          formData.append("audio_files", audio);
          manifest.push({
            id: `question-${number}`,
            scope: "question",
            question_numbers: [number],
            file_index: fileIndex,
          });
          fileIndex += 1;
        }
        for (const group of audioGroups) {
          const range = parseAudioRange(group.range);
          if (!range || !group.file) continue;
          formData.append("audio_files", group.file);
          manifest.push({
            id: group.id,
            scope: "group",
            question_numbers: Array.from(
              { length: range[1] - range[0] + 1 },
              (_, index) => range[0] + index,
            ),
            file_index: fileIndex,
          });
          fileIndex += 1;
        }
        formData.append("audio_manifest", JSON.stringify(manifest));
      }
    }

    try {
      const response = await apiFetch("/api/extractions", {
        method: "POST",
        body: formData,
      });
      const responseText = await response.text();
      let payload: {
        detail?: string;
        job_id?: string;
        processing_location?: string;
      } = {};
      try {
        payload = responseText ? JSON.parse(responseText) : {};
      } catch {
        payload = {};
      }
      if (!response.ok) {
        const fallback =
          response.status === 413
            ? "File vượt quá giới hạn 50 MB."
            : response.status === 507
              ? "Máy chủ tạm hết dung lượng xử lý upload. Vui lòng đợi ít phút rồi thử lại."
              : response.status === 503
                ? "Máy chủ đang xử lý nhiều lượt tải lên. Vui lòng đợi ít phút rồi thử lại."
                : response.status === 429
                  ? "Bạn thao tác tải lên quá nhanh. Vui lòng đợi rồi thử lại."
            : response.status >= 500
              ? "Máy chủ hoặc proxy đang gián đoạn. Hãy kiểm tra backend rồi thử lại."
              : `Không thể tải PDF lên (HTTP ${response.status}).`;
        throw new Error(payload.detail || fallback);
      }
      if (!payload.job_id) {
        throw new Error("Máy chủ không trả về mã xử lý.");
      }
      console.info(
        `[OCR_ROUTE] backend_confirmed=${
          payload.processing_location || (isDesktop() ? "LOCAL_EDGE" : "REMOTE_SERVER")
        } job=${payload.job_id}`,
      );
      await pollJob(payload.job_id);
    } catch (err) {
      const message =
        err instanceof TypeError
          ? "Không kết nối được tới máy chủ. Hãy kiểm tra backend."
          : err instanceof Error
            ? err.message
            : "Có lỗi xảy ra";
      setError(message);
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen bg-slate-50">
      <Header />
      {loading &&
        (processingPhase === "audio" || processingPhase === "audio_ocr") && (
        <AudioProcessingDialog
          mode={audioMode}
          progress={phaseProgress}
          stage={stage}
          parallel={processingPhase === "audio_ocr"}
          audioProgress={audioProgress}
          ocrProgress={ocrProgress}
          audioStage={audioStage}
          ocrStage={ocrStage}
        />
      )}
      <div className="mx-auto grid max-w-[1440px] gap-7 px-5 py-8 sm:px-8 lg:grid-cols-[minmax(0,1fr)_360px]">
        <section className="ui-card p-6 sm:p-8 lg:p-10">
          <div className="flex items-start gap-3 border-b border-slate-200 pb-5">
            <span className="rounded-xl border border-slate-200 bg-slate-50 p-2.5 text-[#1f4e79]">
              <ScanLine className="h-6 w-6" />
            </span>
            <div>
              <h2 className="text-xl font-extrabold text-[#1f4e79]">
                Tạo đề mới
              </h2>
              <p className="mt-1 text-sm text-slate-500">
                Chọn loại đề, tải tài liệu và kiểm tra kết quả trước khi làm bài.
              </p>
            </div>
          </div>

          {hasPendingListening && (
            <div className="mt-5 flex items-center justify-between gap-3 rounded-xl border border-[#1f4e79]/25 bg-slate-50 px-4 py-3">
              <div>
                <p className="text-sm font-bold text-[#1f4e79]">
                  Listening đã sẵn sàng
                </p>
                <p className="text-xs text-slate-500">
                  Tiếp tục tải Reading để hoàn thiện đề 200 câu.
                </p>
              </div>
              <button
                type="button"
                disabled={cancelingPending}
                onClick={async () => {
                  setCancelingPending(true);
                  setError(null);
                  try {
                    await abandonPendingListeningSession();
                    setHasPendingListening(false);
                    setExamType(null);
                    router.replace("/");
                  } catch (reason) {
                    setError(
                      reason instanceof Error
                        ? reason.message
                        : "Không thể hủy phiên Full Test tạm",
                    );
                  } finally {
                    setCancelingPending(false);
                  }
                }}
                className="ui-btn-secondary shrink-0 px-3 py-2 text-xs disabled:cursor-not-allowed disabled:opacity-60"
              >
                {cancelingPending ? "Đang hủy..." : "Hủy ghép"}
              </button>
            </div>
          )}

          <div className="mt-6 space-y-7">
            <section>
              <div className="mb-3 flex items-center gap-2">
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-[#1f4e79] text-xs font-bold text-white">
                  1
                </span>
                <h3 className="text-sm font-bold text-slate-900">Loại đề</h3>
              </div>
              <div className="grid grid-cols-2 gap-3">
                {(
                  [
                    ["listening", "Listening", Headphones],
                    ["reading", "Reading", BookOpen],
                  ] as const
                ).map(([value, label, Icon]) => (
                  <button
                    key={value}
                    type="button"
                    disabled={
                      loading || (hasPendingListening && value === "listening")
                    }
                    onClick={() => {
                      setExamType(value);
                      if (value === "reading") resetAudioSelection();
                    }}
                    className={`flex items-center justify-center gap-2 rounded-lg border px-4 py-3 text-sm font-bold shadow-sm transition ${
                      examType === value
                        ? "border-[#1f4e79] bg-[#1f4e79] text-white shadow-[0_3px_0_#173a5c]"
                        : "border-slate-300 bg-white text-slate-700 hover:border-[#1f4e79] hover:text-[#1f4e79]"
                    }`}
                  >
                    <Icon className="h-5 w-5" />
                    {label}
                  </button>
                ))}
              </div>
            </section>

            <section>
              <div className="mb-3 flex items-center gap-2">
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-[#1f4e79] text-xs font-bold text-white">
                  2
                </span>
                <h3 className="text-sm font-bold text-slate-900">Tài liệu nguồn</h3>
              </div>
              <UploadZone file={file} onFileChange={setFile} />
              <div className="mt-3 flex items-center gap-2 text-xs text-slate-600">
                <input
                  type="checkbox"
                  id="no_cache"
                  checked={noCache}
                  onChange={(e) => setNoCache(e.target.checked)}
                  className="h-4 w-4 rounded border-slate-300 text-[#1f4e79] focus:ring-[#1f4e79]"
                />
                <label htmlFor="no_cache" className="cursor-pointer font-medium select-none text-slate-700">
                  Quét mới từ đầu (Bỏ qua cache bài làm cũ)
                </label>
              </div>
              {examType === "listening" && (
                <div className="mt-4 rounded-xl border border-slate-300 bg-slate-50 p-4">
                  <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <h4 className="text-sm font-extrabold text-[#1f4e79]">
                        Audio Listening
                      </h4>
                      <p className="mt-1 text-xs text-slate-500">
                        Chọn một audio toàn bài hoặc bộ audio riêng theo câu / nhóm.
                      </p>
                    </div>
                    <div className="flex rounded-lg border border-slate-300 bg-white p-1 shadow-sm">
                      {(["full", "question_groups"] as const).map((mode) => (
                        <button
                          key={mode}
                          type="button"
                          onClick={() => {
                            if (mode === audioMode) return;
                            setAudioMode(mode);
                            resetAudioSelection();
                          }}
                          className={`rounded-md px-4 py-2 text-xs font-bold transition ${
                            audioMode === mode
                              ? "bg-[#1f4e79] text-white shadow-sm"
                              : "bg-white text-slate-600 hover:text-[#1f4e79]"
                          }`}
                        >
                          {mode === "full"
                            ? "Audio Full"
                            : "Theo câu / nhóm"}
                        </button>
                      ))}
                    </div>
                  </div>
                  {audioMode === "full" ? (
                    <AudioUpload
                      dropzoneId="audio-full"
                      label="Audio Full"
                      file={fullAudioFile}
                      onFileChange={setFullAudioFile}
                      onError={setAudioError}
                    />
                  ) : (
                    <div className="space-y-5">
                      <div className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-3 text-xs leading-5 text-blue-900">
                        Chỉ cần tải audio cho câu/nhóm muốn tạo. Part 1–2 dùng audio từng câu; Part 3–4 dùng nhóm liên tiếp như 32–34. Không cần điền đủ 100 câu. Mỗi file tối đa 50 MB, tổng bộ audio tối đa 300 MB.
                      </div>
                      <div
                        {...bulkDropzone.getRootProps()}
                        data-dropzone-id="audio-bulk-dropzone"
                        className={`cursor-pointer rounded-xl border-2 border-dashed p-4 text-center transition ${
                          bulkDropzone.isDragActive
                            ? "border-[#1f4e79] bg-blue-50/80 ring-4 ring-[#1f4e79]/10"
                            : "border-slate-300 bg-white hover:border-[#1f4e79] hover:bg-slate-50"
                        }`}
                      >
                        <input {...bulkDropzone.getInputProps()} />
                        <div className="flex flex-col items-center justify-center gap-1.5">
                          <UploadCloud className="h-7 w-7 text-[#1f4e79]" />
                          <p className="text-xs font-bold text-slate-800 sm:text-sm">
                            {bulkDropzone.isDragActive
                              ? "Thả tất cả file audio vào đây..."
                              : "Kéo & thả bộ file audio (1.mp3, 2.mp3, 32-34.mp3...) vào đây để tự động khớp câu"}
                          </p>
                          <p className="text-[11px] text-slate-500">
                            Tự động nhận diện tên file (VD: 1, 01, q1, 32-34...) hoặc kéo thả vào từng ô bên dưới
                          </p>
                        </div>
                      </div>
                      <div>
                        <h5 className="mb-2 text-xs font-extrabold uppercase tracking-wide text-slate-500">Audio theo câu · Part 1–2</h5>
                        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                          {QUESTION_AUDIO_NUMBERS.map((number) => (
                            <AudioUpload
                              key={number}
                              dropzoneId={`audio-question-${number}`}
                              compact
                              label={`Câu ${number} · Part ${number <= 6 ? 1 : 2}`}
                              file={questionAudioFiles[number]}
                              onFileChange={(next) =>
                                setQuestionAudioFiles((current) => ({ ...current, [number]: next }))
                              }
                              onError={setAudioError}
                            />
                          ))}
                        </div>
                      </div>
                      <div>
                        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                          <h5 className="text-xs font-extrabold uppercase tracking-wide text-slate-500">Audio theo nhóm · Part 3–4</h5>
                          <button
                            type="button"
                            onClick={() => setAudioGroups((current) => [...current, { id: `group-new-${Date.now()}`, range: "", file: null }])}
                            className="ui-btn-secondary px-3 py-1.5 text-xs"
                          >
                            + Thêm nhóm câu
                          </button>
                        </div>
                        <div className="space-y-2">
                          {audioGroups.map((group, index) => (
                            <div key={group.id} className="grid gap-2 rounded-lg border border-slate-200 bg-white p-2 md:grid-cols-[140px_minmax(0,1fr)_auto] md:items-center">
                              <label className="text-xs font-bold text-slate-600">
                                Câu nhóm
                                <input
                                  value={group.range}
                                  onChange={(event) => setAudioGroups((current) => current.map((item) => item.id === group.id ? { ...item, range: event.target.value } : item))}
                                  placeholder="32-34"
                                  aria-label={`Phạm vi nhóm audio ${index + 1}`}
                                  className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-2 text-sm font-bold outline-none focus:border-[#1f4e79] focus:ring-2 focus:ring-[#1f4e79]/10"
                                />
                              </label>
                              <AudioUpload
                                dropzoneId={`audio-group-${group.id}`}
                                compact
                                label={parseAudioRange(group.range) ? `Audio nhóm ${group.range}` : "Audio nhóm câu"}
                                file={group.file}
                                onFileChange={(next) => setAudioGroups((current) => current.map((item) => item.id === group.id ? { ...item, file: next } : item))}
                                onError={setAudioError}
                              />
                              <button
                                type="button"
                                onClick={() => setAudioGroups((current) => current.length > 1 ? current.filter((item) => item.id !== group.id) : current)}
                                className="rounded-lg border border-red-200 px-3 py-2 text-xs font-bold text-red-700 hover:bg-red-50"
                                aria-label={`Xóa nhóm audio ${index + 1}`}
                              >
                                Xóa
                              </button>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  )}
                  {audioError && (
                    <p className="mt-2 text-xs font-semibold text-red-700">
                      {audioError}
                    </p>
                  )}
                </div>
              )}
            </section>

            <section>
              <div className="mb-3 flex items-center gap-2">
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-[#1f4e79] text-xs font-bold text-white">
                  3
                </span>
                <h3 className="text-sm font-bold text-slate-900">Cấu hình bài thi</h3>
              </div>
              <Segmented
                label="Số câu sử dụng"
                value={countChoice}
                options={COUNT_OPTIONS}
                onChange={setCountChoice}
              />
              {countChoice === "custom" && (
                <input
                  type="number"
                  min={1}
                  value={customCount}
                  onChange={(event) => setCustomCount(event.target.value)}
                  placeholder="Nhập số câu"
                  className="mt-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm shadow-inner outline-none focus:border-[#1f4e79] focus:ring-2 focus:ring-[#1f4e79]/10"
                />
              )}
              <p className="mt-2 text-xs leading-5 text-slate-500">
                Hệ thống tự nhận diện dải câu trong PDF (ví dụ Part 2 là 7–31,
                Reading Part 5 là 101–130). Số câu chọn sẽ được áp dụng khi tạo
                đề; passage/graphic dùng chung vẫn được giữ nguyên nhóm.
              </p>
            </section>
          </div>

          {loading && (
            <div className="mt-6 rounded-xl border border-slate-300 bg-slate-50 p-4">
              <div className="flex items-center justify-between text-sm">
                <span className="font-semibold text-[#1f4e79]">{stage}</span>
                <span className="font-extrabold text-[#1f4e79]">{progress}%</span>
              </div>
              <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-200">
                <div
                  className="h-full rounded-full bg-[#1f4e79] transition-all duration-500"
                  style={{ width: `${progress}%` }}
                />
              </div>
            </div>
          )}

          {error && (
            <div className="mt-5 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm font-medium text-red-800">
              {error}
            </div>
          )}

          <button
            type="button"
            onClick={handleSubmit}
            disabled={loading || !file || !examType}
            className="ui-btn-primary mt-6 w-full py-3.5"
          >
            {loading ? (
              <Loader2 className="h-5 w-5 animate-spin" />
            ) : (
              <Upload className="h-5 w-5" />
            )}
            {loading ? "Đang xử lý..." : "Tải lên và bắt đầu tạo đề"}
          </button>
        </section>

        <aside className="space-y-4">
          <section className="ui-card p-5">
            <h3 className="font-extrabold text-[#1f4e79]">Quy trình tạo đề</h3>
            <ol className="mt-4 space-y-4">
              {[
                "Nhận diện bố cục tài liệu",
                "Kiểm tra câu hỏi, hình và đáp án",
                "Tạo bài thi và làm bài",
              ].map((item, index) => (
                <li key={item} className="flex gap-3">
                  <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-[#1f4e79]" />
                  <div>
                    <p className="text-sm font-bold text-slate-800">
                      {index + 1}. {item}
                    </p>
                    <p className="mt-0.5 text-xs leading-5 text-slate-500">
                      {index === 0
                        ? "PDF scan được xử lý trực tiếp trên hệ thống."
                        : index === 1
                          ? "Có thể sửa crop và import answer key từ ảnh."
                          : "Listening phát audio trực tiếp trong màn thi."}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          </section>
          <section className="rounded-xl border border-[#1f4e79] bg-[#1f4e79] p-5 text-white shadow-[0_4px_0_#173a5c]">
            <p className="text-sm font-bold">Giới hạn tệp</p>
            <p className="mt-2 text-xs leading-5 text-slate-200">
              PDF tối đa 50 MB. Mỗi audio Listening tối đa 50 MB. Không gửi dữ
              liệu tới dịch vụ xử lý bên ngoài.
            </p>
          </section>
        </aside>
      </div>
    </main>
  );
}
