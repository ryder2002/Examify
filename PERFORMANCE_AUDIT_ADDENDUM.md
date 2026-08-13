# Desktop, offline và đa nền tảng — audit addendum

## Findings

| Severity | Finding | Remediation | Expected impact |
| --- | --- | --- | --- |
| CRITICAL | Windows smoke polling let a transient socket timeout abort CI and did not preserve sidecar diagnostics. | Bounded retry/deadline handling, process logs and failure artifact collection. | CI failures distinguish transport flakiness from a real OCR hang. |
| HIGH | Desktop queue had no automatic online worker or per-class publication intent. | Single-flight coordinator, SQLite publication queue and idempotent remote publication calls. | Offline-created exams sync and publish without manual navigation. |
| HIGH | Existing desktop manifests could be silently reused with different content. | Canonical SHA-256 manifest hash and HTTP 409 conflict. | Prevents silent overwrite and preserves teacher data integrity. |
| HIGH | Uploaded audio could be mapped by original filename while payload URLs use asset IDs. | Resolve payload asset ID to database filename and support both URL forms. | Prevents missing audio after desktop sync. |
| MEDIUM | Mobile browsers had no installable shell or durable IndexedDB exam/answer store. | PWA shell, IndexedDB packs/drafts and reconnect retry. | Refresh/offline exam work survives browser storage limitations better. |
| MEDIUM | Quiz/review headers and modals were desktop-first. | Dynamic viewport/safe-area styles, mobile menu, bottom-sheet dialogs and touch targets. | Better usability on iPhone/iPad without changing desktop workflows. |
| LOW | macOS packaging needed native sidecar, Keychain/device branches and an explicit signing policy. | Native build script/workflow, platform-specific Tauri code, ad-hoc signing (`signingIdentity: "-"`) and post-package verification. | Reproducible ARM/Intel artifacts without Apple credentials; testers have a documented `xattr` escape hatch for Gatekeeper quarantine. |

## Deliberately unchanged

- No Redis/Kafka/Kubernetes/microservice rewrite.
- No unbounded OCR worker increase or database connection expansion.
- Existing classroom publication semantics remain `study_resource`/practice and stay idempotent.
- Server remains authoritative for permissions, attempts, answer revisions and submitted results.

## Verification status

Linux checks pass locally: frontend type-check/tests/static build, backend desktop/platform/classroom tests and packaged sidecar smoke. Native Windows/macOS builds require their hosted runners; the workflows now perform those checks and upload diagnostics on failure.
