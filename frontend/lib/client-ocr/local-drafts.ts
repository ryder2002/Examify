import type { ClientOcrDraft } from "./types";

const DATABASE_NAME = "examify-client-ocr-v1";
const DATABASE_VERSION = 1;
const DRAFT_STORE = "drafts";
const BLOB_STORE = "blobs";
const MAX_DRAFTS = 20;
const MAX_LOCAL_BYTES = 2 * 1024 * 1024 * 1024;
const COMMITTED_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const ABANDONED_TTL_MS = 30 * 24 * 60 * 60 * 1000;

type StoredBlob = { key: string; blob: Blob; size: number; updatedAt: string };

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("IndexedDB request failed"));
  });
}

async function openDatabase(): Promise<IDBDatabase> {
  if (typeof indexedDB === "undefined") throw new Error("IndexedDB không khả dụng.");
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(DRAFT_STORE)) {
        const store = database.createObjectStore(DRAFT_STORE, { keyPath: "key" });
        store.createIndex("updatedAt", "updatedAt");
        store.createIndex("status", "status");
      }
      if (!database.objectStoreNames.contains(BLOB_STORE)) {
        const store = database.createObjectStore(BLOB_STORE, { keyPath: "key" });
        store.createIndex("updatedAt", "updatedAt");
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("Không mở được kho OCR local."));
  });
}

async function withStore<T>(
  names: string | string[],
  mode: IDBTransactionMode,
  callback: (transaction: IDBTransaction) => Promise<T>,
): Promise<T> {
  const database = await openDatabase();
  try {
    const transaction = database.transaction(names, mode);
    const result = await callback(transaction);
    await new Promise<void>((resolve, reject) => {
      transaction.oncomplete = () => resolve();
      transaction.onabort = () => reject(transaction.error || new Error("Giao dịch local bị hủy."));
      transaction.onerror = () => reject(transaction.error || new Error("Giao dịch local thất bại."));
    });
    return result;
  } finally {
    database.close();
  }
}

export function clientOcrDraftKey(sourceSha256: string, pipelineVersion: string): string {
  return `${sourceSha256}:${pipelineVersion}`;
}

export async function getClientOcrDraft(key: string): Promise<ClientOcrDraft | null> {
  return withStore(DRAFT_STORE, "readonly", async (transaction) => {
    const result = await requestResult<ClientOcrDraft | undefined>(
      transaction.objectStore(DRAFT_STORE).get(key),
    );
    return result || null;
  });
}

export async function listClientOcrDrafts(): Promise<ClientOcrDraft[]> {
  return withStore(DRAFT_STORE, "readonly", async (transaction) => {
    const result = await requestResult<ClientOcrDraft[]>(
      transaction.objectStore(DRAFT_STORE).getAll(),
    );
    return result.sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
  });
}

export async function putClientOcrDraft(
  draft: ClientOcrDraft,
  options: { checkQuota?: boolean } = {},
): Promise<void> {
  // Blob writes and draft creation already enforce the quota. Per-page
  // checkpoints must not rescan every blob/draft in IndexedDB, otherwise the
  // local persistence cost grows with the OCR session itself.
  if (options.checkQuota !== false) await enforceClientOcrQuota(draft.key);
  await withStore(DRAFT_STORE, "readwrite", async (transaction) => {
    transaction.objectStore(DRAFT_STORE).put(draft);
  });
}

export async function putClientOcrBlob(key: string, blob: Blob): Promise<void> {
  // Blob keys are namespaced by the source draft (``sha256:asset:id``).  A
  // draft at the 20-item limit must still be able to checkpoint/replace its
  // own source or crop; quota enforcement must not mistake that write for a
  // new draft.
  const activeKey = key.includes(":") ? key.slice(0, key.indexOf(":")) : undefined;
  const previousSize = await withStore(BLOB_STORE, "readonly", async (transaction) => {
    const existing = await requestResult<StoredBlob | undefined>(
      transaction.objectStore(BLOB_STORE).get(key),
    );
    return existing?.size || 0;
  });
  await enforceClientOcrQuota(activeKey, Math.max(0, blob.size - previousSize));
  const item: StoredBlob = { key, blob, size: blob.size, updatedAt: new Date().toISOString() };
  await withStore(BLOB_STORE, "readwrite", async (transaction) => {
    transaction.objectStore(BLOB_STORE).put(item);
  });
}

export async function getClientOcrBlob(key: string): Promise<Blob | null> {
  return withStore(BLOB_STORE, "readonly", async (transaction) => {
    const item = await requestResult<StoredBlob | undefined>(transaction.objectStore(BLOB_STORE).get(key));
    return item?.blob || null;
  });
}

export async function deleteClientOcrDraft(key: string): Promise<void> {
  await withStore([DRAFT_STORE, BLOB_STORE], "readwrite", async (transaction) => {
    transaction.objectStore(DRAFT_STORE).delete(key);
    const blobStore = transaction.objectStore(BLOB_STORE);
    const blobs = await requestResult<StoredBlob[]>(blobStore.getAll());
    for (const item of blobs) {
      if (item.key === key || item.key.startsWith(`${key}:`)) blobStore.delete(item.key);
    }
  });
  const storage = typeof navigator !== "undefined" ? navigator.storage : undefined;
  if (storage && typeof storage.getDirectory === "function") {
    try {
      const root = (await storage.getDirectory()) as unknown as OpfsDirectory;
      await root.removeEntry(`ocr-${key}.pdf`);
    } catch (reason) {
      if (!(reason instanceof DOMException && reason.name === "NotFoundError")) throw reason;
    }
  }
}

export async function cleanupClientOcrDrafts(now = Date.now()): Promise<number> {
  const drafts = await listClientOcrDrafts();
  const expired = drafts.filter((draft) => {
    if (draft.active) return false;
    const timestamp = Date.parse(draft.committedAt || draft.updatedAt);
    const age = now - timestamp;
    return draft.status === "committed" ? age >= COMMITTED_TTL_MS : age >= ABANDONED_TTL_MS;
  });
  for (const draft of expired) await deleteClientOcrDraft(draft.key);
  return expired.length;
}

async function enforceClientOcrQuota(activeKey?: string, incomingBytes = 0): Promise<void> {
  await cleanupClientOcrDrafts();
  const drafts = await listClientOcrDrafts();
  if (drafts.length >= MAX_DRAFTS && !drafts.some((draft) => draft.key === activeKey)) {
    throw new Error(`Đã đạt giới hạn ${MAX_DRAFTS} bản OCR local. Hãy dọn bản nháp cũ trước.`);
  }
  let storedBytes = 0;
  await withStore(BLOB_STORE, "readonly", async (transaction) => {
    const blobs = await requestResult<StoredBlob[]>(transaction.objectStore(BLOB_STORE).getAll());
    storedBytes = blobs.reduce((total, item) => total + item.size, 0);
  });
  if (storedBytes + incomingBytes > MAX_LOCAL_BYTES) {
    throw new Error("Kho OCR local đã đạt 2 GiB. Bản đang mở không bị xóa; hãy dọn bản cũ trước.");
  }
}

type OpfsDirectory = {
  getFileHandle(name: string, options?: { create?: boolean }): Promise<{
    getFile(): Promise<File>;
    createWritable(): Promise<{ write(data: Blob): Promise<void>; close(): Promise<void> }>;
  }>;
  removeEntry(name: string): Promise<void>;
};

export async function persistClientOcrSource(key: string, file: File): Promise<"opfs" | "indexeddb"> {
  const storage = typeof navigator !== "undefined" ? navigator.storage : undefined;
  if (storage && typeof storage.getDirectory === "function") {
    try {
      const root = (await storage.getDirectory()) as unknown as OpfsDirectory;
      const handle = await root.getFileHandle(`ocr-${key}.pdf`, { create: true });
      const writable = await handle.createWritable();
      await writable.write(file);
      await writable.close();
      return "opfs";
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "QuotaExceededError") throw reason;
      // Private modes may expose OPFS but reject writes. IndexedDB is the
      // explicit fallback and never sends the source to the server.
    }
  }
  await putClientOcrBlob(`${key}:source`, file);
  return "indexeddb";
}

export async function loadClientOcrSource(key: string): Promise<Blob | null> {
  const storage = typeof navigator !== "undefined" ? navigator.storage : undefined;
  if (storage && typeof storage.getDirectory === "function") {
    try {
      const root = (await storage.getDirectory()) as unknown as OpfsDirectory;
      const handle = await root.getFileHandle(`ocr-${key}.pdf`);
      return await handle.getFile();
    } catch {
      // Try the IndexedDB fallback below.
    }
  }
  return getClientOcrBlob(`${key}:source`);
}
