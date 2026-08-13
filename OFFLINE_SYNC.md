# Offline desktop and PWA synchronization

## Desktop teacher workflow

1. Activate/login once while online.
2. The app stores only a bounded identity snapshot for offline navigation. The
   refresh token stays in Windows Credential Manager or Apple Keychain; an
   authoritative online signed-out/revoked response removes offline access.
3. The desktop sidecar stores finalized exams and assets in SQLite/local managed storage.
4. The teacher's active classroom list is cached locally.
5. Selecting **Public cho lớp** for a local exam creates one durable intent per classroom. The request can be queued with no network.
6. The desktop coordinator runs at startup, on `online`, on visibility resume and every 30 seconds with single-flight protection.
7. It atomically leases due work, uploads the exam/assets, completes the remote exam, then calls the existing classroom publication endpoint once per cached classroom. A stale lease becomes eligible again after a crash/reload.

Exam sync and classroom publication are independent. A revoked class marks only that publication intent failed; the local exam remains preserved and synced.

## Integrity rules

- Server stores a canonical SHA-256 manifest hash.
- Reusing a `client_exam_id` with the same manifest returns the existing
  receipt. A changed manifest reconciles into the same remote exam; a concurrent
  in-progress completion returns retryable `409 Conflict` instead of racing.
- Asset uploads validate byte size and SHA-256 before MinIO write.
- Completion is serialized by a PostgreSQL row lock/lease. The unique
  `(owner_user_id, client_exam_id)` constraint recovers the same exam even if a
  worker stops after persisting but before updating the sync receipt.
- Repeated init/upload/complete/publication calls are safe to retry.
- PostgreSQL transactions do not remain open during MinIO or network calls.
- A finalized exam and its sync intent survive sidecar/app restart; native
  installer smoke tests verify this exact restart path.

## PWA workflow

Students press **Tải để làm offline** while online. The server reserves an authoritative attempt and returns the exam snapshot. IndexedDB stores the snapshot, attempt metadata and answer drafts; Cache Storage stores referenced media. The service worker caches only the shell/static/media paths, never private answer API responses.

When offline, the quiz uses the reserved attempt ID and local revisioned answers. On reconnect, existing batch answer and submit endpoints retry with the latest revision; the receipt remains local until the server confirms submission. OCR/PDF creation remains desktop-only.
