// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from "vitest";

import {
  attemptDraftKey,
  loadAttemptDraft,
  reconcileAttemptDraft,
  removeAttemptDraft,
  saveAttemptDraft,
} from "./attempt-draft";

describe("durable attempt drafts", () => {
  beforeEach(() => localStorage.clear());

  it("round-trips the latest acknowledged and pending revision", () => {
    const updatedAt = Date.now();
    saveAttemptDraft({
      attemptId: "attempt-1",
      revision: 4,
      acceptedRevision: 3,
      answers: { 101: "A", 102: "B" },
      pendingChanges: { 102: "B" },
      pendingBatch: {
        batchId: "03a28a7e-8577-4bba-b43f-a649a33df721",
        baseRevision: 3,
        changes: { 102: "B" },
      },
      flaggedQuestions: [102],
      timeLeftSeconds: 120,
      updatedAt,
    });

    expect(loadAttemptDraft("attempt-1")).toEqual({
      attemptId: "attempt-1",
      revision: 4,
      acceptedRevision: 3,
      answers: { 101: "A", 102: "B" },
      pendingChanges: { 102: "B" },
      pendingBatch: {
        batchId: "03a28a7e-8577-4bba-b43f-a649a33df721",
        baseRevision: 3,
        changes: { 102: "B" },
      },
      flaggedQuestions: [102],
      timeLeftSeconds: 120,
      updatedAt,
    });
    removeAttemptDraft("attempt-1");
    expect(localStorage.getItem(attemptDraftKey("attempt-1"))).toBeNull();
  });

  it("keeps a newer local revision instead of overwriting it on reload", () => {
    const reconciled = reconcileAttemptDraft(
      "attempt-2",
      { 101: "A" },
      6,
      300,
      {
        attemptId: "attempt-2",
        revision: 7,
        acceptedRevision: 5,
        answers: { 101: "D", 102: "C" },
        pendingChanges: { 101: "D", 102: "C" },
        flaggedQuestions: [102],
        timeLeftSeconds: 280,
        updatedAt: 456,
      },
    );

    expect(reconciled.answers).toEqual({ 101: "D", 102: "C" });
    expect(reconciled.revision).toBe(7);
    expect(reconciled.acceptedRevision).toBe(6);
    expect(reconciled.flaggedQuestions).toEqual([102]);
    expect(reconciled.timeLeftSeconds).toBe(280);
  });

  it("uses the server snapshot when it has acknowledged the local revision", () => {
    const reconciled = reconcileAttemptDraft(
      "attempt-3",
      { 101: "B" },
      8,
      240,
      {
        attemptId: "attempt-3",
        revision: 8,
        acceptedRevision: 7,
        answers: { 101: "A" },
        pendingChanges: {},
        flaggedQuestions: [101],
        timeLeftSeconds: 250,
        updatedAt: 789,
      },
    );

    expect(reconciled.answers).toEqual({ 101: "B" });
    expect(reconciled.acceptedRevision).toBe(8);
    expect(reconciled.flaggedQuestions).toEqual([101]);
    expect(reconciled.timeLeftSeconds).toBe(240);
  });

  it("loads old drafts without flags and sanitizes invalid flag values", () => {
    localStorage.setItem(
      attemptDraftKey("attempt-4"),
      JSON.stringify({
        attemptId: "attempt-4",
        revision: 1,
        acceptedRevision: 0,
        answers: { 101: "A" },
        flaggedQuestions: [102, "101", 102, -1, "bad"],
        timeLeftSeconds: 100,
        updatedAt: Date.now(),
      }),
    );

    expect(loadAttemptDraft("attempt-4")?.flaggedQuestions).toEqual([
      101, 102,
    ]);
  });
});
