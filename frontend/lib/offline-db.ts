"use client";

import type { AttemptDraft } from "@/lib/attempt-draft";
import type { FinalExam } from "@/lib/utils";

const DB_NAME = "examify-offline-v1";
const DB_VERSION = 1;
let databasePromise: Promise<IDBDatabase | null> | null = null;

export async function clearOfflineBusinessData(): Promise<void> {
  const database = await openDatabase();
  if (database) {
    await Promise.all(
      ["exam_packs", "attempt_drafts", "sync_queue"].map(
        (storeName) =>
          new Promise<void>((resolve) => {
            const transaction = database.transaction(storeName, "readwrite");
            transaction.objectStore(storeName).clear();
            transaction.oncomplete = () => resolve();
            transaction.onerror = () => resolve();
          }),
      ),
    );
  }
  if (typeof window !== "undefined" && "caches" in window) {
    await caches.delete("examify-offline-assets-v1");
  }
}

function openDatabase(): Promise<IDBDatabase | null> {
  if (typeof window === "undefined" || !window.indexedDB) return Promise.resolve(null);
  if (databasePromise) return databasePromise;
  databasePromise = new Promise<IDBDatabase | null>((resolve, reject) => {
    const request = window.indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains("exam_packs")) {
        database.createObjectStore("exam_packs", { keyPath: "key" });
      }
      if (!database.objectStoreNames.contains("attempt_drafts")) {
        database.createObjectStore("attempt_drafts", { keyPath: "attemptId" });
      }
      if (!database.objectStoreNames.contains("sync_queue")) {
        database.createObjectStore("sync_queue", { keyPath: "id" });
      }
    };
    request.onsuccess = () => {
      request.result.onversionchange = () => {
        request.result.close();
        databasePromise = null;
      };
      resolve(request.result);
    };
    request.onerror = () => reject(request.error || new Error("IndexedDB unavailable"));
  }).catch(() => {
    databasePromise = null;
    return null;
  });
  return databasePromise;
}

export async function putExamPack(
  key: string,
  exam: FinalExam,
  metadata: Record<string, unknown> = {},
): Promise<void> {
  const database = await openDatabase();
  if (!database) return;
  await new Promise<void>((resolve) => {
    const transaction = database.transaction("exam_packs", "readwrite");
    transaction.objectStore("exam_packs").put({
      key,
      exam,
      metadata,
      updatedAt: Date.now(),
    });
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => resolve();
  });
}

export async function getExamPack(key: string): Promise<{ exam: FinalExam; metadata: Record<string, unknown> } | null> {
  const database = await openDatabase();
  if (!database) return null;
  const value = await new Promise<{ exam: FinalExam; metadata: Record<string, unknown> } | null>((resolve) => {
    const request = database.transaction("exam_packs", "readonly").objectStore("exam_packs").get(key);
    request.onsuccess = () => resolve(request.result || null);
    request.onerror = () => resolve(null);
  });
  return value;
}

export async function putAttemptDraftIndexedDb(draft: AttemptDraft): Promise<void> {
  const database = await openDatabase();
  if (!database) return;
  await new Promise<void>((resolve) => {
    const transaction = database.transaction("attempt_drafts", "readwrite");
    transaction.objectStore("attempt_drafts").put(draft);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => resolve();
  });
}

export async function getAttemptDraftIndexedDb(attemptId: string): Promise<AttemptDraft | null> {
  const database = await openDatabase();
  if (!database) return null;
  const value = await new Promise<AttemptDraft | null>((resolve) => {
    const request = database.transaction("attempt_drafts", "readonly").objectStore("attempt_drafts").get(attemptId);
    request.onsuccess = () => resolve(request.result || null);
    request.onerror = () => resolve(null);
  });
  return value;
}

export async function removeAttemptDraftIndexedDb(attemptId: string): Promise<void> {
  const database = await openDatabase();
  if (!database) return;
  await new Promise<void>((resolve) => {
    const transaction = database.transaction("attempt_drafts", "readwrite");
    transaction.objectStore("attempt_drafts").delete(attemptId);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => resolve();
  });
}

export async function cacheExamAssets(
  exam: FinalExam,
  onProgress?: (completed: number, total: number) => void,
): Promise<void> {
  if (typeof window === "undefined" || !("caches" in window)) return;
  const urls = Array.from(new Set([
    ...(exam.audios || []).map((item) => item.url),
    ...(exam.audio ? [exam.audio.url] : []),
    ...(exam.stimuli || []).flatMap((stimulus) =>
      (stimulus.assets || []).map((asset) => asset.url),
    ),
  ].filter(Boolean)));
  if (!urls.length) return;
  if (navigator.storage?.estimate) {
    const estimate = await navigator.storage.estimate();
    const remaining = Math.max(0, (estimate.quota || 0) - (estimate.usage || 0));
    const audioBytesByUrl = new Map<string, number>();
    for (const audio of [
      ...(exam.audios || []),
      ...(exam.audio ? [exam.audio] : []),
    ]) {
      if (!audio.url) continue;
      audioBytesByUrl.set(
        audio.url,
        Math.max(audioBytesByUrl.get(audio.url) || 0, Number(audio.size || 0)),
      );
    }
    const knownAudioBytes = [...audioBytesByUrl.values()].reduce(
      (total, size) => total + Math.max(0, size),
      0,
    );
    const required = Math.max(50 * 1024 * 1024, Math.ceil(knownAudioBytes * 1.25));
    if (estimate.quota && remaining < required) {
      throw new Error("Trình duyệt không còn đủ dung lượng cho bộ đề offline.");
    }
  }
  const cache = await caches.open("examify-offline-assets-v1");
  const failures: string[] = [];
  let cursor = 0;
  let completed = 0;
  async function worker() {
    while (cursor < urls.length) {
      const index = cursor;
      cursor += 1;
      const url = urls[index];
      try {
        const response = await fetch(url, { credentials: "include" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        await cache.put(url, response.clone());
      } catch {
        failures.push(url);
      } finally {
        completed += 1;
        onProgress?.(completed, urls.length);
      }
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(2, urls.length) }, () => worker()),
  );
  if (failures.length) {
    throw new Error(
      `Chưa tải đủ ${failures.length}/${urls.length} media; hãy giữ mạng và thử lại.`,
    );
  }
}
