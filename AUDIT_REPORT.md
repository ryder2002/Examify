# Full System Audit Report

Ngày audit: 2026-08-07  
Phạm vi: repository hiện tại, cấu hình Compose/Nginx, backend FastAPI, frontend Next.js, Tauri và pipeline OCR.  
Mục tiêu: 300–400 tài khoản, khoảng 200 học viên active đồng thời trên máy Linux 8 CPU / 12–16 GB RAM / 100 GB SSD.

## 1. Kiến trúc hiện tại

- Backend: Python FastAPI, SQLAlchemy và PostgreSQL; Alembic là nơi sở hữu schema.
- Frontend: Next.js 16 / React 19, proxy API qua Next trong môi trường web.
- Storage: MinIO cho source PDF, exam assets, audio, answer import và guide media.
- Background: Redis + Celery cho OCR/maintenance khi `USE_CELERY=true`; OCR local có executor giới hạn.
- Desktop: Tauri 2, sidecar FastAPI local, SQLite store và đồng bộ lên API remote.
- Production: Docker Compose gồm PostgreSQL, Redis, MinIO, migration, API workers, OCR worker, maintenance worker, frontend và Nginx.
- Exam flow: payload đề được lưu durable; answer autosave bulk/upsert có revision; submit khóa attempt và trả receipt idempotent; frontend có draft IndexedDB/offline retry.

## 2. Phát hiện bảo mật và độ tin cậy

| ID | Mức độ | Phát hiện | Tác động | Hướng xử lý |
|---|---|---|---|---|
| SEC-01 | CRITICAL | Endpoint asset công khai mới có thể trả asset theo `exam_id/asset_id` mà không ràng buộc owner hoặc public share | Có thể lộ nội dung đề nếu biết ID/object key | Bắt buộc owner/admin hoặc active public share; giữ MinIO private |
| SEC-02 | HIGH | Public share/submission cho phép mọi `teacher` truy cập đề/kết quả của teacher khác | Vi phạm authorization/IDOR | Chỉ owner hoặc admin được create/list/delete |
| SEC-03 | HIGH | Public submission chỉ dùng UUID khi submit, thiếu proof/token gắn với lượt start | Người biết UUID có thể nộp thay/abuse endpoint | Trả signed submission token khi start và bắt buộc xác thực khi submit |
| SEC-04 | HIGH | Nginx `limit_req` không biết role, nên Teacher vẫn bị giới hạn trước app | Vi phạm yêu cầu miễn Teacher; burst giờ thi có thể bị 429 | Bỏ rate limit API ở Nginx; dùng limiter role-aware trong app |
| SEC-05 | HIGH | Request body/answer batch và public answer map cần giới hạn ở mọi endpoint | JSON lớn gây memory/CPU/query amplification | Giới hạn Pydantic, body/upload, batch và pagination |
| SEC-06 | MEDIUM | CORS đang cho phép wildcard methods/headers trong khi credentials bật | Mở rộng bề mặt browser request | Thu hẹp methods/headers cần thiết ở production |
| SEC-07 | MEDIUM | CSP Tauri còn `unsafe-inline`, shell/open external URL rộng | Tăng tác động nếu có XSS hoặc URL không mong muốn | Giữ tương thích Next nhưng bổ sung `object-src`, `base-uri`, `frame-ancestors`; rà soát allowlist |
| SEC-08 | MEDIUM | MinIO init hiện đặt examify-assets/audio anonymous download trong Compose | Nếu MinIO bị expose sẽ lộ object đoán được | Đặt private; media đi qua presigned/X-Accel route |
| SEC-09 | MEDIUM | Log sidecar chưa có rotation | Có thể làm đầy disk desktop | Rotation theo kích thước, giữ file hiện tại và một file backup |
| SEC-10 | LOW | macOS workflow build riêng Intel/ARM, chưa tạo universal/notarized artifact | Release thủ công/unsigned, không phải lỗi runtime | Bổ sung signing/notarization khi có Apple secrets; universal là follow-up |

Đã kiểm tra các nhóm SQL injection, path traversal, token leakage, authn/authz, file extension/size validation và MinIO key normalization. Không thấy query SQL nối chuỗi trực tiếp ở các hot path đã đọc; object key được chuẩn hóa; credential không được đưa vào frontend. Các mục trên vẫn cần hardening vì authorization phải được thực thi ở server, không dựa vào URL khó đoán.

## 3. Phát hiện performance/reliability

| ID | Mức độ | Phát hiện | Nguyên nhân | Expected impact sau xử lý |
|---|---|---|---|---|
| PERF-01 | CRITICAL | Rate limit hiện ở proxy không role-aware | Nginx chặn trước FastAPI | Teacher không bị limiter; user khác vẫn được bảo vệ |
| PERF-02 | HIGH | Public submissions trả toàn bộ danh sách và answers không pagination | Query/JSON tăng tuyến tính | Bounded DB work và payload |
| PERF-03 | HIGH | PDF chưa có page-count guard trước render | PDF nhiều trang có thể chiếm CPU/disk | Chặn input gây OCR amplification |
| PERF-04 | HIGH | Một số public path/auth path chưa có limiter phân tách | Login/upload/submit có thể bị spam | Redis fixed-window + fallback bounded |
| PERF-05 | MEDIUM | OCR log có tổng thời gian nhưng thiếu stage metrics chuẩn | Khó xác định render/preprocess/OCR bottleneck | Có baseline stage trong runbook; benchmark cần dataset thật |
| PERF-06 | MEDIUM | Guide image kiểm tra bytes nhưng cần chặn ảnh giải nén cực lớn | Decompression bomb/memory spike | Giới hạn megapixel và xử lý exception |
| PERF-07 | MEDIUM | Static/asset delivery đã có X-Accel/Range cho classroom nhưng public exam route cần kiểm tra quyền | Có nguy cơ vừa chậm vừa lộ dữ liệu | Private object + authorized redirect |
| PERF-08 | MEDIUM | Chưa có load evidence mới trong repository | Không thể tuyên bố capacity 200 users | Có k6 scenarios parameterized; phải chạy trên staging tương đương |

Các điểm đã có nền tảng tốt: engine pool có timeout/pre-ping; answer save là bulk upsert; attempt submit có row lock/idempotency; frontend có IndexedDB draft/retry; history có pagination; OCR worker/concurrency bị giới hạn; Nginx có body-size/timeout/asset caching; Alembic có migration và backup/restore scripts.

## 4. Responsive audit

- Web có mobile navigation, `overflow-x-auto` cho bảng cần thiết, safe-area footer cho quiz, minimum touch target và responsive grid.
- Quiz chuyển câu ở client sau khi payload đã tải; không gọi API cho mỗi lần đổi câu.
- Audio player dùng wrapper `w-full/min-w-0`, nhưng cần kiểm thử thực tế trên viewport 320/375/768/1024/1440 và các browser Safari/Chromium.
- Các bảng admin/classroom có thể cuộn ngang có chủ đích; không coi đây là lỗi nếu không làm vỡ layout.
- Tauri desktop đặt minimum width 1024; đây là chủ đích cho desktop, không phải mobile target.

## 5. Exam integrity audit

- Practice và Mock/Exam đã tách bằng `quizMode`; dictionary chỉ được bật cho Practice.
- Mock không hiển thị nút/panel dictionary và handler lookup phải kiểm tra lại mode ở runtime.
- Answer state có durable draft, revision và retry; submit failure giữ attempt để không mất đáp án.
- Cần regression cho click, double-click, selection, right-click, keyboard, touch/long-press và event bubbling ở Mock; không chỉ kiểm tra nút UI.
- Server vẫn là nơi quyết định score và uniqueness; frontend không được coi submit thành công nếu chưa có receipt.

## 6. Tauri/macOS audit

- Tauri 2 + Rust sidecar, loopback secret theo launch, keyring/fallback refresh token, version gate và smoke test đã có.
- Workflow đã build `x86_64-apple-darwin` và `aarch64-apple-darwin` riêng. Chưa có universal binary, Developer ID signing hoặc notarization.
- Windows NSIS và Linux build có trong workflow/config; cần chạy artifact smoke test trong CI trước release.
- Cần rotation log sidecar và allowlist URL external trước khi phát hành rộng.

## 7. OCR audit

- RapidOCR engine được cache singleton; page workers giới hạn tối đa 4; Celery prefetch=1; OCR queue tách maintenance.
- Pipeline xử lý render/preprocess/deskew/crop/OCR/parse theo page, đóng ảnh sau khi dùng và dùng thư mục job tạm.
- Chưa có dataset chuẩn trong repository nên không được bịa số accuracy/latency. Benchmark phải chạy bằng PDF đại diện production và ghi p50/p95, RSS peak, stage duration, missing-page/error rate.
- Có script `scripts/benchmark_ocr.py` để chạy một PDF đại diện; chạy nhiều lần
  với `OCR_PAGE_WORKERS=1..4`, lưu output ngoài source tree và đối chiếu answer
  key thủ công/ground truth để tính accuracy.
- Cần page-count guard, megapixel guard cho media/answer image, và stage metrics khi chạy production.

## 8. Kế hoạch triển khai theo ưu tiên

1. CRITICAL: app rate limiter phân lớp, miễn Teacher theo role; bỏ `limit_req` API ở Nginx; harden public asset/share/submission authorization.
2. HIGH: signed token cho public submission, pagination/caps, PDF page limit, private MinIO bucket.
3. MEDIUM: Mock dictionary defense-in-depth, guide image pixel cap, Tauri log rotation/CSP, load-test artifacts và runbook.
4. Verification: compile/unit/integration/frontend/Tauri/Compose/Nginx checks; load test chỉ chạy khi có staging credentials và k6.

## 9. Kết luận trước triển khai

Repository có kiến trúc phù hợp để tiếp tục tối ưu incremental cho mục tiêu 200 active users; chưa có bằng chứng để gọi là đã chứng nhận 200 users production. Không được ghi capacity p50/p95/p99, CPU hoặc RAM nếu chưa chạy load test trên host tương đương. Các thay đổi tiếp theo phải giữ answer integrity, idempotent submit và pool budget hiện tại.

## 10. Verification trong lượt audit này

| Kiểm tra | Kết quả |
|---|---|
| Backend full pytest | 80 passed, 5 skipped |
| Frontend Vitest | 42 passed |
| Frontend TypeScript | Passed |
| Next production build | Passed, 22 routes |
| Tauri `cargo check` | Passed |
| Tauri `cargo fmt --check` | Passed |
| `docker compose config --quiet` | Passed |
| Python compile / k6 syntax / JSON checks | Passed |
| Nginx syntax | Chưa chạy: binary không có trong môi trường audit |
| k6 runtime | Chưa chạy: `k6` không có staging/runner trong môi trường audit |

## 11. Load-test results

Đây là bảng chờ số liệu staging, không phải số liệu suy diễn:

| Concurrent Users | RPS | p50 | p95 | p99 | Error Rate | CPU | RAM |
| ---------------: | --: | --: | --: | --: | ---------: | --: | --: |
| 50 | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| 100 | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| 150 | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| 200 | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

Peak `200 users submitting simultaneously`: chưa chạy; cần ghi thêm submit p50/p95/p99, PostgreSQL lock wait, pool checked-out/overflow, active connections, CPU/RAM và kết quả retry/duplicate.

## 12. Remaining risks và capacity

- Safe concurrent users: chưa chứng nhận.
- Maximum tested concurrent users trong lượt này: chưa chạy.
- CPU/RAM/RPS/database utilization: TBD trên host tương đương; không dùng số lịch sử.
- macOS universal binary, Developer ID signing và notarization chưa hoàn tất.
- OCR accuracy/performance chưa benchmark vì thiếu PDF dataset đại diện.
- Nginx syntax phải kiểm tra trong image/deployment thật; TLS/HSTS/firewall/DNS là trách nhiệm hạ tầng.

## 13. Production handoff

1. Điền `.env` với PostgreSQL/Redis/MinIO/JWT/token-export/admin secrets, `MAX_PDF_PAGES` và `CORS_ALLOWED_ORIGINS`; đặt permission `0600`.
2. Không expose PostgreSQL, Redis, MinIO hoặc console; cấu hình TLS ở reverse proxy và giữ API body/timeouts của Nginx.
3. Chạy `docker compose config --quiet`, `docker compose build`, `docker compose run --rm migrate`, rồi `docker compose up -d`.
4. Kiểm tra `/health`, `/health/ready`, login, Teacher request không nhận rate-limit, tạo đề, start/save/reload/submit/duplicate submit, public test token và audio Range.
5. Chạy backup/restore drill; theo dõi pool, locks, queue, CPU/RAM/disk/log growth.
6. Chạy các scenario trong `LOAD_TEST.md` trên staging tương đương trước khi chấp nhận mục tiêu 200 concurrent.
