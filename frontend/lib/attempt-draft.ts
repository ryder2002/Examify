import {
  getAttemptDraftIndexedDb,
  putAttemptDraftIndexedDb,
  removeAttemptDraftIndexedDb,
} from "@/lib/offline-db";

export type AttemptDraft = {
  attemptId: string;
  revision: number;
  acceptedRevision: number;
  answers: Record<number, string>;
  flaggedQuestions: number[];
  timeLeftSeconds: number;
  currentQuestionNumber?: number | null;
  updatedAt: number;
  pendingChanges?: Record<number, string | null>;
  pendingBatch?: {
    batchId: string;
    baseRevision: number;
    changes: Record<number, string | null>;
  };
};

const DRAFT_PREFIX = "smart-exam-attempt-draft-";
const ANSWER_LETTERS = new Set(["A", "B", "C", "D"]);
const MAX_DRAFT_AGE_MS = 7 * 24 * 60 * 60 * 1000;

export function attemptDraftKey(attemptId: string): string {
  return `${DRAFT_PREFIX}${attemptId}`;
}

function normalizedAnswers(value: unknown): Record<number, string> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const result: Record<number, string> = {};
  for (const [rawNumber, rawLetter] of Object.entries(value).slice(0, 200)) {
    const number = Number(rawNumber);
    const letter = String(rawLetter).toUpperCase();
    if (Number.isInteger(number) && number > 0 && ANSWER_LETTERS.has(letter)) {
      result[number] = letter;
    }
  }
  return result;
}

function normalizedChanges(value: unknown): Record<number, string | null> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const result: Record<number, string | null> = {};
  for (const [rawNumber, rawLetter] of Object.entries(value).slice(0, 200)) {
    const number = Number(rawNumber);
    if (!Number.isInteger(number) || number <= 0) continue;
    if (rawLetter === null) {
      result[number] = null;
      continue;
    }
    const letter = String(rawLetter).toUpperCase();
    if (ANSWER_LETTERS.has(letter)) result[number] = letter;
  }
  return result;
}

function normalizedFlaggedQuestions(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  return Array.from(
    new Set(
      value
        .slice(0, 200)
        .map(Number)
        .filter((number) => Number.isInteger(number) && number > 0),
    ),
  ).sort((left, right) => left - right);
}

export function loadAttemptDraft(attemptId: string): AttemptDraft | null {
  if (typeof window === "undefined") return null;
  try {
    const value = JSON.parse(
      localStorage.getItem(attemptDraftKey(attemptId)) || "null",
    ) as Partial<AttemptDraft> | null;
    if (!value || value.attemptId !== attemptId) return null;
    const revision = Number(value.revision);
    const acceptedRevision = Number(value.acceptedRevision);
    const timeLeftSeconds = Number(value.timeLeftSeconds);
    const updatedAt = Number(value.updatedAt) || 0;
    const currentQuestionNumber = Number(value.currentQuestionNumber);
    if (
      !Number.isSafeInteger(revision) ||
      revision < 0 ||
      !Number.isSafeInteger(acceptedRevision) ||
      acceptedRevision < 0 ||
      !Number.isFinite(timeLeftSeconds)
    ) {
      return null;
    }
    if (updatedAt > 0 && Date.now() - updatedAt > MAX_DRAFT_AGE_MS) {
      localStorage.removeItem(attemptDraftKey(attemptId));
      return null;
    }
    const pendingChanges = normalizedChanges(value.pendingChanges);
    if (
      Object.keys(pendingChanges).length === 0
      && revision > acceptedRevision
    ) {
      Object.assign(pendingChanges, normalizedAnswers(value.answers));
    }
    const rawBatch = value.pendingBatch;
    const batchChanges = normalizedChanges(rawBatch?.changes);
    const pendingBatch =
      rawBatch
      && typeof rawBatch.batchId === "string"
      && rawBatch.batchId.length >= 16
      && Number.isSafeInteger(Number(rawBatch.baseRevision))
      && Object.keys(batchChanges).length > 0
        ? {
            batchId: rawBatch.batchId,
            baseRevision: Number(rawBatch.baseRevision),
            changes: batchChanges,
          }
        : undefined;
    return {
      attemptId,
      revision,
      acceptedRevision: Math.min(acceptedRevision, revision),
      answers: normalizedAnswers(value.answers),
      flaggedQuestions: normalizedFlaggedQuestions(value.flaggedQuestions),
      timeLeftSeconds: Math.max(0, Math.floor(timeLeftSeconds)),
      ...(Number.isInteger(currentQuestionNumber) && currentQuestionNumber > 0
        ? { currentQuestionNumber }
        : {}),
      updatedAt,
      ...(Object.keys(pendingChanges).length > 0 ? { pendingChanges } : {}),
      ...(pendingBatch ? { pendingBatch } : {}),
    };
  } catch {
    return null;
  }
}

export function saveAttemptDraft(draft: AttemptDraft): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(attemptDraftKey(draft.attemptId), JSON.stringify(draft));
  } catch {
    // The in-memory/sessionStorage copies still remain available if storage is full.
  }
  void putAttemptDraftIndexedDb(draft);
}

export async function loadAttemptDraftAsync(attemptId: string): Promise<AttemptDraft | null> {
  const local = loadAttemptDraft(attemptId);
  const indexed = await getAttemptDraftIndexedDb(attemptId);
  if (!indexed) return local;
  if (!local || indexed.revision > local.revision || indexed.updatedAt > local.updatedAt) {
    return indexed;
  }
  return local;
}

export function removeAttemptDraft(attemptId: string): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(attemptDraftKey(attemptId));
  void removeAttemptDraftIndexedDb(attemptId);
}

export function reconcileAttemptDraft(
  attemptId: string,
  serverAnswers: Record<number, string>,
  serverRevision: number,
  serverTimeLeftSeconds: number,
  localDraft: AttemptDraft | null,
  serverCurrentQuestionNumber?: number | null,
): AttemptDraft {
  const pendingChanges = localDraft?.pendingChanges || {};
  if (localDraft && Object.keys(pendingChanges).length > 0) {
    const mergedAnswers = { ...normalizedAnswers(serverAnswers) };
    for (const [rawNumber, letter] of Object.entries(pendingChanges)) {
      const number = Number(rawNumber);
      if (letter === null) delete mergedAnswers[number];
      else mergedAnswers[number] = letter;
    }
    return {
      ...localDraft,
      answers: mergedAnswers,
      flaggedQuestions: normalizedFlaggedQuestions(
        localDraft.flaggedQuestions,
      ),
      acceptedRevision: serverRevision,
      timeLeftSeconds: Math.min(
        localDraft.timeLeftSeconds,
        serverTimeLeftSeconds,
      ),
      currentQuestionNumber:
        localDraft.currentQuestionNumber || serverCurrentQuestionNumber || null,
    };
  }
  return {
    attemptId,
    revision: serverRevision,
    acceptedRevision: serverRevision,
    answers: normalizedAnswers(serverAnswers),
    // Flags are local navigation metadata, so a server answer snapshot must not
    // erase them when a learner refreshes or reconnects.
    flaggedQuestions: normalizedFlaggedQuestions(
      localDraft?.flaggedQuestions,
    ),
    timeLeftSeconds: Math.max(0, Math.floor(serverTimeLeftSeconds)),
    currentQuestionNumber:
      serverCurrentQuestionNumber || localDraft?.currentQuestionNumber || null,
    updatedAt: Date.now(),
  };
}
