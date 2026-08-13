import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Generate a client-only identifier in both secure and plain HTTP contexts.
 *
 * `crypto.randomUUID()` is restricted to secure contexts in some browsers,
 * while the web app is also intentionally reachable over a private LAN IP.
 * Keep quiz tab/batch/event IDs working there without weakening server-side
 * authentication (these IDs are only idempotency/correlation values).
 */
export function createClientId(): string {
  const cryptoApi =
    typeof globalThis !== "undefined" ? globalThis.crypto : undefined;
  if (typeof cryptoApi?.randomUUID === "function") {
    return cryptoApi.randomUUID();
  }
  if (typeof cryptoApi?.getRandomValues === "function") {
    try {
      const bytes = new Uint8Array(16);
      cryptoApi.getRandomValues(bytes);
      // RFC 4122-compatible version/variant bits make diagnostics easier while
      // retaining the full entropy available from getRandomValues.
      bytes[6] = (bytes[6] & 0x0f) | 0x40;
      bytes[8] = (bytes[8] & 0x3f) | 0x80;
      const hex = Array.from(bytes, (byte) =>
        byte.toString(16).padStart(2, "0"),
      ).join("");
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    } catch {
      // Fall through for restricted/old browser implementations.
    }
  }
  return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}-${Math.random().toString(16).slice(2)}`;
}

export type ExamType = "listening" | "reading";
export type QuizExamType = ExamType | "combined";

export type Issue = {
  code: string;
  message: string;
  page?: number | null;
  question_number?: number | null;
  severity: "info" | "warning" | "error";
};

export type AssetRef = {
  id: string;
  url: string;
  page: number;
  bbox: [number, number, number, number];
  width: number;
  height: number;
};

export type AudioRef = {
  id: string;
  url: string;
  filename: string;
  content_type?: string;
  size?: number;
  part: string;
  scope?: "full" | "part" | "question" | "group";
  question_numbers?: number[];
  group_id?: string | null;
};

export type SolutionEntry = {
  key: string;
  question_numbers: number[];
  transcript: string | null;
  explanation: string | null;
  translation: string;
};

export type Stimulus = {
  id: string;
  kind: "image";
  title: string;
  assets: AssetRef[];
  question_numbers: number[];
  page_numbers: number[];
  confidence: number;
  issues: string[];
};

export type Question = {
  number: number;
  part: string;
  text: string;
  options: Record<string, string>;
  option_letters: string[];
  correct: string | null;
  group_id: string | null;
  stimulus_id: string | null;
  confidence: number;
  issues: string[];
};

export type ExamDraft = {
  schema_version: 2;
  job_id: string;
  exam_type: ExamType;
  status: "queued" | "processing" | "review" | "ready" | "failed";
  stage: string;
  progress: number;
  processing_phase?: "queued" | "audio" | "audio_ocr" | "ocr" | "review";
  phase_progress?: number;
  audio_progress?: number;
  ocr_progress?: number;
  audio_stage?: string;
  ocr_stage?: string;
  filename: string;
  requested_count: number | null;
  returned_count: number;
  questions: Question[];
  stimuli: Stimulus[];
  issues: Issue[];
  error: string | null;
  cached: boolean;
  metadata: Record<string, unknown>;
  audio: AudioRef | null;
  audios: AudioRef[];
  solutions?: SolutionEntry[];
};

export type FinalExam = {
  schema_version: 2;
  job_id: string;
  exam_type: QuizExamType;
  requested_count: number;
  returned_count: number;
  total: number;
  questions: Question[];
  stimuli: Stimulus[];
  audio: AudioRef | null;
  audios: AudioRef[];
  solutions?: SolutionEntry[];
  exam_id?: string | null;
  slug?: string | null;
  title?: string | null;
  category?: string | null;
  client_exam_id?: string | null;
  sync_status?: string | null;
  component_job_ids?: {
    listening: string;
    reading: string;
  };
};

export type ExamSummary = {
  id: string;
  slug?: string | null;
  client_exam_id?: string | null;
  job_id?: string | null;
  title: string;
  category?: string | null;
  exam_type: QuizExamType;
  status: string;
  sync_status?: string | null;
  sync_error?: string | null;
  remote_exam_id?: string | null;
  question_count: number;
  answer_key_count: number;
  duration_minutes: number;
  created_at: string;
  updated_at: string;
  attempt_count: number;
  last_attempt_at?: string | null;
  revision?: number;
  current_version_id?: string | null;
  solution_entry_count?: number;
  solution_question_count?: number;
  solution_coverage_percent?: number;
  contributor?: { id: string; display_name: string } | null;
  local_payload?: FinalExam;
};

export type QuizResult = {
  schema_version: 2;
  exam: FinalExam;
  answers: Record<number, string>;
  duration_seconds: number;
  time_left_seconds: number;
  submitted_at: string;
  attempt_id?: string;
  has_solutions?: boolean;
  receipt_id?: string;
  status?: string;
  score_released?: boolean;
  answers_released?: boolean;
  scores?: {
    toeic: number;
    listening: number;
    reading: number;
    correct: number;
    graded: number;
  };
};

export function assetUrl(url: string) {
  return url;
}

export function parseAnswerKeyText(text: string) {
  const answers: Record<number, string> = {};
  const duplicates: string[] = [];
  const pattern =
    /(?:^|[\s,;])(\d{1,3})\s*(?:[:.\-]\s*)?[\(\[\{]?\s*([A-D])\s*[\)\]\}]?/gi;
  for (const match of text.matchAll(pattern)) {
    const number = Number(match[1]);
    const letter = match[2].toUpperCase();
    if (answers[number] && answers[number] !== letter) {
      duplicates.push(`${number}${letter}`);
      continue;
    }
    answers[number] = letter;
  }
  return { answers, duplicates };
}

/**
 * Reference question/audio structure for a standard TOEIC Listening test.
 * The full recording is estimated in two forms:
 * - >= 45 minutes: includes the opening and Part 2/3/4 directions.
 * - < 45 minutes: question audio only (normally around 44 minutes).
 *
 * Direction time stays fixed because those scripts are standardized. The
 * remaining real media duration is distributed across question/group audio,
 * so 46-, 47- and 48-minute recordings end exactly with question 100.
 */
export type ToeicFullAudioProfile = "with_directions" | "without_directions";

export const TOEIC_FULL_AUDIO_PROFILE_THRESHOLD_SECONDS = 45 * 60;
export const TOEIC_FULL_AUDIO_REFERENCE_SECONDS = 2740;
export const TOEIC_FULL_AUDIO_QUESTION_REFERENCE_SECONDS = 2551;

const TOEIC_DIRECTION_SECONDS = {
  opening: 95,
  part2: 34,
  part3: 30,
  part4: 30,
} as const;

const TOEIC_TOTAL_DIRECTION_SECONDS =
  TOEIC_DIRECTION_SECONDS.opening +
  TOEIC_DIRECTION_SECONDS.part2 +
  TOEIC_DIRECTION_SECONDS.part3 +
  TOEIC_DIRECTION_SECONDS.part4;

export function inferToeicFullAudioProfile(
  durationSeconds: number,
): ToeicFullAudioProfile {
  const duration =
    Number.isFinite(durationSeconds) && durationSeconds > 0
      ? durationSeconds
      : TOEIC_FULL_AUDIO_REFERENCE_SECONDS;
  return duration >= TOEIC_FULL_AUDIO_PROFILE_THRESHOLD_SECONDS
    ? "with_directions"
    : "without_directions";
}

export function getToeicFullAudioIntroEndTime(durationSeconds: number): number {
  return inferToeicFullAudioProfile(durationSeconds) === "with_directions"
    ? TOEIC_DIRECTION_SECONDS.opening
    : 0;
}

function getToeicQuestionAudioElapsed(questionNumber: number): number {
  if (questionNumber <= 6) return questionNumber * 27.5;
  if (questionNumber <= 31) return 6 * 27.5 + (questionNumber - 6) * 20.5;
  if (questionNumber <= 70) {
    return 6 * 27.5 + 25 * 20.5 + Math.ceil((questionNumber - 31) / 3) * 76.5;
  }
  return (
    6 * 27.5 +
    25 * 20.5 +
    13 * 76.5 +
    Math.ceil((questionNumber - 70) / 3) * 87.9
  );
}

function getToeicDirectionElapsed(questionNumber: number): number {
  if (questionNumber <= 6) return TOEIC_DIRECTION_SECONDS.opening;
  if (questionNumber <= 31) {
    return TOEIC_DIRECTION_SECONDS.opening + TOEIC_DIRECTION_SECONDS.part2;
  }
  if (questionNumber <= 70) {
    return (
      TOEIC_DIRECTION_SECONDS.opening +
      TOEIC_DIRECTION_SECONDS.part2 +
      TOEIC_DIRECTION_SECONDS.part3
    );
  }
  return TOEIC_TOTAL_DIRECTION_SECONDS;
}

export function getToeicFullAudioQuestionEndTime(
  questionNumber: number,
  durationSeconds: number,
): number {
  if (questionNumber < 1 || questionNumber > 100) return Infinity;
  const duration =
    Number.isFinite(durationSeconds) && durationSeconds > 0
      ? durationSeconds
      : TOEIC_FULL_AUDIO_REFERENCE_SECONDS;
  if (questionNumber === 100) return duration;
  const profile = inferToeicFullAudioProfile(duration);
  const questionDuration =
    profile === "with_directions"
      ? Math.max(duration - TOEIC_TOTAL_DIRECTION_SECONDS, 1)
      : duration;
  const questionScale =
    questionDuration / TOEIC_FULL_AUDIO_QUESTION_REFERENCE_SECONDS;
  const directionsElapsed =
    profile === "with_directions"
      ? getToeicDirectionElapsed(questionNumber)
      : 0;
  return Math.min(
    duration,
    directionsElapsed +
      getToeicQuestionAudioElapsed(questionNumber) * questionScale,
  );
}

/**
 * Legacy/reference timings for TOEIC Listening Questions (45:40 / 2740 seconds):
 * - Opening Intro: 95s (1:35) -> Q1 starts at 01:35
 * - Part 1 (Q1-6): 6 Qs * 27.5s = 165s -> Ends at 260s (04:20)
 * - Part 2 Direction: 34s -> Q7 starts at 04:54
 * - Part 2 (Q7-31): 25 Qs * 20.5s = 512.5s -> Ends at 806.5s (13:26.5)
 * - Part 3 Direction: 30s -> Q32 starts at 13:56.5
 * - Part 3 (Q32-70): 13 Clusters * 76.5s = 994.5s -> Ends at 1831s (30:31)
 * - Part 4 Direction: 30s -> Q71 starts at 31:01
 * - Part 4 (Q71-100): 10 Clusters * 87.9s = 879s -> Ends at 2740s (45:40)
 */
export function getToeicListeningQuestionEndTime(
  questionNumber: number,
  audioMode: "full" | "part" = "full"
): number {
  if (questionNumber < 1 || questionNumber > 100) return Infinity;

  const part1Intro = 95; // 1m35s
  const part2Direction = 34; // 34s
  const part3Direction = 30; // 30s
  const part4Direction = 30; // 30s

  if (audioMode === "part") {
    if (questionNumber <= 6) {
      return part1Intro + questionNumber * 27.5;
    } else if (questionNumber <= 31) {
      const qInPart = questionNumber - 6;
      return part2Direction + qInPart * 20.5;
    } else if (questionNumber <= 70) {
      const qInPart = questionNumber - 31;
      const clusterIndex = Math.ceil(qInPart / 3);
      return part3Direction + clusterIndex * 76.5;
    } else {
      const qInPart = questionNumber - 70;
      const clusterIndex = Math.ceil(qInPart / 3);
      return part4Direction + clusterIndex * 87.9;
    }
  }

  // Audio Full (Single audio file, 45:40 total duration)
  if (questionNumber <= 6) {
    return part1Intro + questionNumber * 27.5;
  }
  const part1EndTime = part1Intro + 6 * 27.5; // 260s (04:20)

  if (questionNumber <= 31) {
    const qInPart2 = questionNumber - 6;
    return part1EndTime + part2Direction + qInPart2 * 20.5;
  }
  const part2EndTime = part1EndTime + part2Direction + 25 * 20.5; // 806.5s (13:26.5)

  if (questionNumber <= 70) {
    const qInPart3 = questionNumber - 31;
    const clusterIndex = Math.ceil(qInPart3 / 3);
    return part2EndTime + part3Direction + clusterIndex * 76.5;
  }
  const part3EndTime = part2EndTime + part3Direction + 13 * 76.5; // 1831s (30:31)

  const qInPart4 = questionNumber - 70;
  const clusterIndex = Math.ceil(qInPart4 / 3);
  return part3EndTime + part4Direction + clusterIndex * 87.9; // 2740s (45:40)
}
