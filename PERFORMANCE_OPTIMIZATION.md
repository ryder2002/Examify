# Báo cáo triển khai production-ready

## Monolith branding và runtime setup (2026-08-12)

- Frontend dùng thống nhất `frontend/public/logo.png` cho metadata, PWA,
  offline page, header, login và loader.
- Palette thương hiệu đã chuyển sang Navy `#0b1f3a` với dải `brand-*`; màu
  trạng thái nghiệp vụ (đỏ/xanh lá/vàng) và màu thương hiệu Zalo bên ngoài
  được giữ nguyên theo ngữ nghĩa.
- Compose bổ sung PostgreSQL và MinIO nội bộ, giữ các volume hiện có và
  trỏ Nginx/ứng dụng vào network nội bộ. OCR monolith dùng Tesseract local,
  không còn phụ thuộc PaddleOCR/ONNX hoặc OCR server ngoài.
- Đã build dependencies, migrate tới `0023_performance_hot_path`, tạo bucket
  MinIO và seed admin bằng script idempotent `backend/scripts/seed_admin.py`.
- Smoke runtime: toàn bộ container healthy; `/health`, `/health/ready`,
  frontend, logo PNG và admin login trả HTTP 200. Chưa coi đây là chứng nhận
  capacity 200 concurrent users; cần chạy load test trên server production.

### Tesseract migration and 16 CPU / 16 GB runtime (2026-08-12)

- Giữ nguyên pipeline layout/parser và contract `OCRResult`, chỉ thay engine
  nhận diện từ Rapid/PaddleOCR sang `pytesseract` + Tesseract `eng`.
- `image_to_data` vẫn cung cấp line/word boxes và confidence cho các bước phân
  cột, answer-key và parser hiện hữu; pool Tesseract được giới hạn tối đa 2
  subprocess, page workers của worker là 4, API pool là 1.
- Backend image cài `tesseract-ocr`, `tesseract-ocr-eng` và `tesseract-ocr-osd`;
  không cài `onnxruntime`/`onnxruntime-gpu`.
- Runtime Compose đã được cân lại cho host 16 CPU/16 GB: API 5 CPU/4 GiB,
  extraction worker 5 CPU/4 GiB, PostgreSQL 2 CPU/5 GiB; các service phụ vẫn
  có hard limit riêng. Smoke container xác nhận `tesseract:cpu` và readiness 200.

### Database and MinIO namespace migration (2026-08-12)

- PostgreSQL database đã đổi từ `smart_exam` thành `examify`; role runtime đã
  đổi từ `toeicdoc_app` thành `examify_app`. Schema và dữ liệu được giữ nguyên,
  Alembic vẫn ở head `0023_performance_hot_path`.
- MinIO buckets đã đổi thành `examify-sources`, `examify-assets`,
  `examify-audio`, `examify-answers` và `examify-guides`. Đã copy và đối chiếu
  23 source objects, 843 asset objects và 15 audio objects trước khi xóa bucket
  cũ; mọi object reference trong PostgreSQL đều được cập nhật.
- Readiness sau migration kiểm tra database `examify`, user `examify_app`, đủ
  năm bucket mới và admin login đều PASS.

## Hotfix scratch/upload và luồng Listening → Reading (2026-08-11)

- Root cause production đã được xác nhận từ log, không suy đoán theo thiết bị:
  API `/tmp` tmpfs 768 MiB đạt 94%, sau đó `audio_output.write()` trả
  `ENOSPC`. Uvicorn child chết và không thể spawn lại vì Python không tìm được
  temporary directory; Nginx vì vậy trả 400/502. Phiên Chrome/macOS liên quan
  đã finalize Listening 200, upload Reading 202 và OCR đủ 100 câu trước khi
  media review bắt đầu 404 do cùng sự cố scratch.
- API multipart giờ spool vào Docker volume `/scratch` trên SSD, không giữ bản
  upload lớn trong RAM tmpfs. File spool có sẵn được hash rồi stream thẳng lên
  MinIO; bỏ bản copy staging thứ hai. `/tmp` 1 GiB chỉ còn là fallback cho thư
  viện hard-code đường dẫn.
- Scratch readiness chặn nhận tải mới khi còn dưới 5 GiB hoặc 5%; health payload
  công bố total/free/percent để alert. Cache local mềm giới hạn 2 GiB và dọn
  staging orphan sau một giờ.
- Nginx nhận tối đa bốn upload tạo đề đồng thời: ba giáo viên và một slot dự
  phòng. Worker vẫn concurrency 2 nên lượt thứ ba xếp hàng an toàn thay vì tạo
  thêm OCR/FFmpeg process và làm cạn CPU/RAM.
- Asset, page và audio review ở server không materialize toàn bộ job vào API
  scratch nữa. API xác thực object rồi dùng signed `X-Accel-Redirect`; Nginx
  tải trực tiếp từ MinIO, gồm Range 206 cho audio. Save text draft cũng không
  tải toàn bộ media về local.
- Upload lỗi được rollback; cleanup MinIO chưa xong giữ row `failed` để retention
  task có thể thử lại. Tác vụ hourly xóa job standalone quá 24 giờ nhưng bảo vệ
  job đang edit và exam legacy còn tham chiếu.

Sau deploy, `/scratch` có 45 GiB trống (59%), ghi được bằng UID runtime;
`/tmp` 1 GiB dùng 1%. Smoke qua Nginx trả ảnh WebP 200 và audio Range 206 trong
khi `/scratch/smart-exam` vẫn 4 KiB/0 file. Bốn Python role dùng cùng image
digest và toàn bộ service đều healthy.

Verification: targeted backend/deploy **7 passed**; toàn bộ backend **203
passed, 6 skipped, 3 lỗi regression không thuộc hotfix**; frontend **74/74**,
TypeScript và Next.js production build pass. Ba lỗi full-suite còn lại là test
isolation/role-publication hiện hữu, không nằm trên upload/OCR/media path.

## Progress/dialog xử lý audio và OCR (2026-08-10, cập nhật 2026-08-11)

- Job contract tách `processing_phase` và `phase_progress` khỏi progress tổng.
  Khi hai nhánh chạy đồng thời, Audio chiếm trọng số 20% và OCR 80%; thanh
  tiến độ không còn nằm ở 1% hoặc nhảy lùi khi đổi phase.
- Bộ cắt FFmpeg phát mốc thật cho probe, silence detection, refinement,
  alignment và từng output đã encode; không nội suy bằng timer phía client.
- Màn tạo đề mở dialog riêng khi phase là `audio_ocr`, hiển thị phần trăm
  tổng cùng hai progress bar độc lập (`Audio` và `OCR`). Job chỉ mở Review
  sau khi cả hai nhánh hoàn tất.
- Progress audio chỉ persist khi tăng ít nhất 3%; PostgreSQL store có write path
  nhẹ không scan/upload MinIO media. 54 clip chỉ sync qua manifest hoàn chỉnh,
  tránh biến cải thiện UX thành write/upload storm.
- Regression: 15/15 test audio/cutter pass; dialog 2/2 pass; toàn bộ frontend
  69/69 test pass, TypeScript và Next.js production build pass. Backend schema
  test trên host chưa chạy do host không có `pydantic` và phiên này không có
  quyền Docker daemon; source đã qua `py_compile`.

## Sửa Full Test, Answer key và lối vào Giải chi tiết (2026-08-09)

- Hai đề Listening/Reading trung gian của web Full Test nay có scope staging
  cá nhân, không xuất hiện hoặc tranh unique title trong Kho đề chung. Chỉ sau
  khi API combine trả 2xx, một Exam `combined` 200 câu mới được công bố và hai
  component được soft-delete.
- Frontend không còn bỏ qua lỗi combine. Nếu ghép lỗi, draft Listening vẫn còn
  trong session để Teacher thử lại và lỗi thật được hiển thị.
- Kết quả personal attempt giữ immutable exam snapshot có answer key do submit
  response trả về; không còn ghi đè bằng payload đã sanitize trước lúc thi.
- Card và menu Teacher trong Kho đề có action `Import / sửa giải chi tiết`, mở
  thẳng tab Solutions của edit session. Parser DOCX/DOC/PDF và versioning hiện
  hữu được tái sử dụng.
- Không đổi PostgreSQL schema, không thêm query theo câu và không thêm service.
  Regression gate: backend **119 passed, 6 skipped**, frontend **52 passed**,
  TypeScript và Next.js production build đều pass.

Ngày cập nhật: 2026-08-08
Máy đích: Linux, Intel Core i5-12400F (6 core/12 thread), 32 GB RAM, SSD
512 GB, uplink 1 Gbps. Production gate là 300 học viên active đồng thời; 400
chỉ là stretch test.

Tài liệu này mô tả code/config đã triển khai. Capacity vẫn **chưa được chứng
nhận** vì chưa có ba lượt 300 VU và soak hai giờ trên staging tương đương.

## Sửa luồng media, mock audio, PWA và account isolation (2026-08-08)

- Media exam/classroom qua `X-Accel-Redirect` giờ giữ presigned SigV4 query.
  Nginx vẫn truyền Range/byte trực tiếp nhưng MinIO private không còn nhận
  anonymous request và trả 403.
- Test media kiểm tra chính `X-Accel-Redirect` có `X-Amz-Signature`, thay cho
  assertion cũ chỉ loại trừ 401 nhưng vô tình cho 403 pass.
- Mock exam Listening có control Play/Pause thật. Khi browser chặn autoplay,
  UI hiện thông báo và nút phát thay vì nuốt Promise rejection.
- Service worker `examify-pwa-v6` dùng network-first cho logo/icon/manifest;
  registration dùng `updateViaCache: none`, manifest và branding có header
  revalidate. Hashed `/_next/static` vẫn cache immutable.
- Nhãn product-facing còn sót đã đổi sang Examify. Namespace kỹ thuật cũ
  được giữ khi đổi có thể làm mất offline pack/metrics history.
- Tauri local store, history, classroom cache, tags, asset và sync/publication
  queue được namespace tại `DESKTOP_DATA_DIR/users/<user_id>`. OCR job/cache
  cũng lưu owner và từ chối account khác. Coordinator không chạy khi chưa có
  active user; logout/account switch dọn quiz session cũ.
- Version web/Tauri/backend sync được tăng đồng bộ lên `0.1.5`; bundle identifier
  cũ `online.congnhat.exam` được thay bằng reverse-DNS `com.toeicdoc.app`.
  Release mới dọn đúng thư mục/credential legacy khi khởi động, rồi dùng data
  directory và credential namespace Examify mới.
- Release `0.1.5` không migrate root `DESKTOP_DATA_DIR/desktop.sqlite3` thiếu
  owner sang account mới. Theo yêu cầu reset toàn bộ, lần khởi động đầu tiên với
  identifier mới chỉ xóa đúng thư mục bundle legacy và credential cũ; không theo
  symlink và không đụng tới thư mục ngoài sibling identifier đã định trước.
  Script reset server vẫn không thể truy cập máy client đang offline.

Verification source hiện tại: backend `85 passed, 5 skipped`; frontend `51
passed`; TypeScript pass; Next web build và Tauri static export đều pass.
Compose config pass. Smoke 200/206 qua Nginx của image mới vẫn phải chạy sau
khi deploy lên staging; không dùng unit test để tuyên bố production đã đổi.
Hai bucket MinIO trong stack local hiện không có object nên smoke đọc object
local được ghi nhận `SKIP`, không được coi là bằng chứng cho dữ liệu production.

## Desktop Windows, offline sync và OCR local

- Desktop đã đăng nhập online có thể khởi động lại hoàn toàn offline. Identity
  snapshot chỉ mở chức năng local; mọi API web vẫn cần access/refresh token và
  phản hồi online hợp lệ có thể thu hồi snapshot.
- Refresh token mới chỉ được ghi vào Windows Credential Manager.
  Bản mới tự migrate rồi xóa `session.dat` plaintext của release cũ khi keyring
  hoạt động.
- SQLite outbox claim work bằng lease atomic; coordinator vẫn chạy startup,
  `online`, visibility resume và mỗi 30 giây. Lease stale tự phục hồi sau crash.
- Remote sync serialize `complete`, recheck init sau owner row-lock và dùng
  unique `(owner_user_id, client_exam_id)` để recovery không tạo duplicate hay
  tiêu thụ quota hai lần. Asset phải khớp chính xác size + SHA-256 trước MinIO.
- OCR lượt thường dùng ảnh 225 DPI/LANCZOS; chỉ tối đa sáu trang mất câu mới
  retry ở đủ 300 DPI. Native PDF text Reading chỉ được tin khi có header hợp lệ
  hoặc câu với ít nhất ba lựa chọn, tránh font-map rác thay thế OCR.
- Static Tauri build đã được sửa route động public-test; link public copy từ
  desktop luôn trỏ tới `https://exam.congnhat.online`, không còn `tauri.localhost`.
- Installer smoke contract hiện gồm OCR -> review -> finalize -> SQLite ->
  attempt -> restart sidecar -> exam/outbox vẫn tồn tại.

Giới hạn còn lại: host Linux không có MSVC `lib.exe`, nên artifact Windows
phải được build/smoke trên Windows runner trước khi phát hành.

## 1. Kiến trúc sau triển khai

```text
Browser / Next.js 16 / React 19
  ├─ localStorage + IndexedDB durable outbox
  ├─ delta sync 10 giây + jitter, một request/attempt
  └─ offline media pack, tối đa hai download song song
                  |
             Nginx HTTP/2 TLS
       JSON/body limits | X-Accel-Redirect + Range
                  |                  |
       FastAPI stateless (4 workers) |
         |          |          |     |
    PostgreSQL    Redis      Celery  MinIO private
   source of truth cache/limit  |    audio/image/PDF
                      OCR + maintenance queues riêng
```

Không rewrite framework, không thêm Kubernetes/microservice/PgBouncer. Redis
không bao giờ là nguồn sự thật của đáp án.

## 2. Các bottleneck chính và thay đổi đã làm

### Bảo toàn đáp án và submit

- Migration `0020_attempt_sync_projection.py` thêm projection bất biến
  `exam_version_questions`, ledger `attempt_sync_batches`, revision và durable
  submit receipt/idempotency. Backfill chạy idempotent cho cả database cũ.
- API sync v2 cho personal/student/class-session nhận tối đa 50 delta, kể cả
  `null` để xóa đáp án. Mỗi batch có UUID và `base_revision`; retry cùng UUID
  trả ACK cũ, UUID bị tái sử dụng với payload khác bị từ chối, stale revision
  trả HTTP 409 cùng canonical answers.
- Frontend ghi click vào localStorage và singleton IndexedDB trước network,
  giữ exact batch chưa ACK, chỉ cho một request đang bay, retry backoff và
  random jitter. `online`, `pagehide` và submit đều yêu cầu flush.
- `BroadcastChannel` phát hiện hai tab. Tab phụ cảnh báo và không ghi đè âm
  thầm; hòa giải revision giữ delta local.
- Submit yêu cầu `Idempotency-Key` với client mới. Snapshot cuối, upsert,
  grading, score, status và receipt nằm trong một transaction; không gọi
  Redis/MinIO/external service khi đang giữ transaction.
- Grading được ghi ngay trong bulk UPSERT. Trường hợp đáp án đã autosave cùng
  lựa chọn vẫn cập nhật `is_correct`; không cần SELECT lại toàn bộ answers.
- Auto-finalize lấy tối đa 25 attempt mỗi transaction bằng `FOR UPDATE SKIP
  LOCKED`, thay vì khóa cả đợt hết giờ.

### PostgreSQL và query path

- Projection nhỏ thay việc deep-copy/deserialise payload đề 200 câu trên hot
  autosave. Version mới bulk-insert projection tối đa 200 dòng.
- Hot context classroom được gom bằng joined query; query-count regression gate
  giữ sync không quá 5 round-trip và submit không quá 7 round-trip.
- `attempt_answers` dùng bulk upsert/delete và unique
  `(attempt_id, question_number)` bảo vệ integrity.
- Thêm index latest attempt
  `(class_assignment_id, class_member_id, started_at DESC)`; giữ partial
  deadline index. Không thêm index ngoài workload chưa được `EXPLAIN` chứng minh.
- Pool API là `4 + overflow 2` mỗi worker, timeout 3 giây; PostgreSQL
  `max_connections=80`, statement 10 giây, lock 2 giây, idle transaction 15
  giây. OCR chỉ có pool `2 + 1` và phải dừng trong giờ thi.
- PostgreSQL bật `pg_stat_statements`, `track_io_timing`, slow query 250 ms,
  lock-wait log và autovacuum riêng cho bảng ghi nóng.

### Giảm request/write amplification

- Presence nằm ở Redis TTL 75 giây; PostgreSQL chỉ checkpoint tối đa mỗi 60
  giây hoặc khi sync/submit. Monitoring có DB fallback khi Redis lỗi.
- Anti-cheat event có outbox, flush tối đa 20 event hoặc mỗi 5 giây; backend
  insert batch idempotent.
- Identity User–Device cache Redis TTL 30 giây, invalidation khi revoke/update;
  Redis lỗi quay về joined PostgreSQL query, không bỏ authorization.
- Compact `GET .../attempts/{id}/state` chỉ trả status, answers, revision và
  deadline. Full exam chỉ tải lại khi browser thiếu pack/content hash đổi.
- Monitoring tách live poll 10 giây khỏi history/export phân trang. Admin user
  aggregates được batch thay N+1; devices/members/list endpoints có giới hạn.

### Rate limiting

- Fixed window được thay bằng atomic Redis/Lua token bucket, có bounded local
  fallback khi Redis gián đoạn.
- Login: 600 request/IP/phút cho NAT nhưng chỉ 5 lần/email/phút.
- Sync: 12 request/attempt/phút, burst 4; 30/user/phút; 10.000/IP/phút.
- Submit: 10 request/attempt/phút, burst 3; media có quota riêng cao.
- Teacher có lane gấp bốn, không được miễn upload/CPU-heavy endpoint.
- Nginx giới hạn answer body 64 KiB; Pydantic giới hạn batch/snapshot và range.

### Audio, MinIO và frontend

- Directions không còn phụ thuộc Supabase; asset mặc định nằm trong static
  origin nội bộ. Audio web hợp lệ <=160 kbps được giữ; WAV/FLAC/high-bitrate
  được worker chuyển trước sang MP3 128 kbps và giữ original riêng.
- FastAPI chỉ authorize media; Nginx truyền Range trực tiếp từ MinIO private.
  Metadata authorization được cache tối đa 5 phút và invalidation được.
- Quiz dùng `preload="metadata"`, chỉ dựng audio hiện tại. Countdown tách khỏi
  component lớn; question/navigator được memo hóa; chuyển câu không gọi API.
- Offline pack là thao tác chủ động, kiểm tra browser quota, tối đa hai download
  song song và báo progress. Service Worker ưu tiên exact cached media URL,
  kể cả `/api/...`/Range, và không xóa offline cache khi nâng PWA version.
- CI bundle gate tính toàn bộ initial chunks của `/quiz`, giới hạn 250 KiB gzip
  và từ chối Tiptap/XLSX lọt vào route.

### Runtime, observability và recovery

- Compose có resource/CPU limit, read-only filesystem, non-root backend,
  `no-new-privileges`, bounded tmpfs và JSON log rotation.
- PostgreSQL baseline: `shared_buffers=4GB`, `effective_cache_size=18GB`,
  `work_mem=4MB`, `maintenance_work_mem=512MB`, WAL tối đa 8 GB.
- Nginx có cấu hình HTTP bootstrap và TLS 1.2/1.3 + HTTP/2; HSTS được để tắt
  cho tới khi HTTPS ổn định bảy ngày. Chỉ 80/443 cần public.
- `/internal/metrics` chỉ ở Docker network. Prometheus retention 7 ngày/5 GB;
  Grafana bind localhost. Alert bao phủ 5xx/latency, pool/connections/locks,
  transaction >5 giây, CPU/RAM/disk, queue age, WAL archive và network.
- App log là JSON có request ID, route template, status, latency và ID rút gọn;
  không log answer/token/cookie/signed URL.
- pgBackRest archive WAL với `archive_timeout=300s`, full tuần, differential
  ngày, incremental 6 giờ. Repo pgBackRest và MinIO được mirror liên tục sang
  S3/NAS versioned, không propagate delete.
- Systemd khởi động Compose sau reboot; watchdog chỉ restart api/frontend/nginx
  sau ba health failure liên tiếp. OCR không được tự bật trong restore flow.

## 3. Public API được thêm

- `PATCH /api/v1/attempts/{id}/sync`
- `PATCH /api/v1/student/attempts/{id}/sync`
- route class-session tương ứng
- `GET .../attempts/{id}/state`

Sync success trả `accepted_revision`, `accepted_batch_id`, `server_time`,
`deadline_at`, `status`. Conflict trả 409 với server revision và canonical
answers. Start-attempt trả content hash/deadline/revision. Submit trả durable
receipt; CORS cho phép `Idempotency-Key`. `/answers` và `/heartbeat` cũ còn được
giữ trong giai đoạn compatibility.

## 4. Bằng chứng kiểm chứng repository

| Gate | Kết quả hiện tại |
|---|---|
| Backend | `88 passed, 5 skipped` |
| Frontend Vitest | `45 passed` |
| TypeScript | pass |
| Next.js 16.3.0 production build | pass, 22 routes |
| `/quiz` initial JS | 215,4 KiB gzip / budget 250 KiB |
| Production dependency audit | `npm audit --omit=dev`: 0 vulnerability |
| Alembic database trắng | upgrade tới `0020` pass |
| Alembic từ legacy `0019` | backfill đúng 200 projection rows pass |
| Sync query gate | <=5 round-trip |
| Submit query gate | <=7 round-trip |
| Compose base/monitoring/backup/restore/TLS | pass |
| Nginx HTTP và TLS `nginx -t` | pass |
| Prometheus config | pass, 14 alert rules |
| Python/JS/Shell syntax + `git diff --check` | pass |
| PostgreSQL pgBackRest image | build pass, pgBackRest 2.58.0 |

Golden OCR fixtures bị skip có điều kiện; đó không phải load test. GitHub Actions
chỉ còn workflow release native Windows; migration PostgreSQL,
production config validation và image build phải chạy trong quy trình deploy
server riêng.
CI cũng fail khi production dependency audit phát hiện vulnerability mức high
trở lên. SheetJS được lấy từ distribution chính thức 0.20.3 thay cho bản npm
registry 0.18.5 không còn bản vá; lockfile giữ URL và integrity để build tái lập.

## 5. Phần còn phải làm trên staging

- Chạy `EXPLAIN (ANALYZE, BUFFERS)` cho login/start/state/sync/submit/result và
  chụp payload/query/pool/WAL/lock baseline với dữ liệu thật.
- Chạy 50/100/200/300 VU, ba lượt 300 VU, soak 300 VU hai giờ và 400 stretch
  từ máy phát tải độc lập. Sau mỗi lượt phải chạy SQL verifier; một mismatch là
  fail bất kể latency.
- Chạy Playwright browser/device matrix cho offline 5–30 giây, pagehide, crash,
  hai tab và media pack. Automated unit/integration tests không thay thế kiểm
  tra browser thật.
- Đo 300 Range audio streams trên uplink thật và thực hiện restore drill
  off-host. RPO <=5 phút chỉ đạt khi WAL mirror/alert đã được quan sát thực tế.

## 6. Capacity hiện tại

- Safe concurrent users: **chưa chứng nhận**.
- Maximum concurrent users được đo trên staging tương đương: **chưa chạy**.
- 300 là production gate thiết kế; 400 là stretch, không phải cam kết.
- Một SSD vẫn là single point of failure và cấu hình này không phải HA.

Không go-live chỉ dựa trên unit test/build. Điều kiện quyết định là zero lost
answer, đạt SLO ba lượt 300 VU và còn headroom như mô tả trong `LOAD_TEST.md`.

## 7. Docker startup incident fix — 2026-08-08

### Root cause

The API image runs with a read-only root filesystem. RapidOCR's provider-cache
initialization defaulted to `/app/.cache`, so every `/health/ready` probe
returned `503` even though PostgreSQL, Redis, MinIO, Poppler, and the pinned OCR
models were healthy. Because `frontend` depends on `api: service_healthy`, it
remained `Created` and Nginx returned `502` for the web root.

### Change made

The shared backend Compose environment now sets:

```yaml
XDG_CACHE_HOME: /tmp/.cache
```

All backend containers already have a bounded writable `/tmp` tmpfs, so the
runtime cache is writable and disposable without weakening the read-only root
filesystem. The same setting is inherited by API, OCR worker, maintenance
worker, scheduler, and migration services. Readiness still requires OCR to be
ready; it was not bypassed.

### Verification

- `/health/ready`: HTTP 200; PostgreSQL, Redis, MinIO and OCR all ready.
- API, frontend and Nginx: healthy/running.
- Nginx `/`: HTTP 200.
- Nginx `/health`: HTTP 200.
- Compose recreate completed without dependency failure.

This is a startup reliability fix, not evidence that the 200-user load target
has been certified. Load and failure testing remain pending on a staging
environment with representative exam/audio data.

## 8. OCR extraction verification — LC.pdf / RC.pdf

Pipeline dùng chính sách raster theo loại đề: Listening mặc định 240 DPI để
giảm kích thước input gần ngưỡng hiệu dụng 2000px của PP-OCRv4; Reading giữ
300 DPI vì RC có passage Part 6 dày. Có thể đặt `OCR_RENDER_DPI=300` nếu ưu
tiên fidelity tối đa.

| File | Trang | Kết quả | OCR duration |
|---|---:|---|---:|
| LC.pdf | 11 | 100 câu, mapping Part 1/graphic đúng, không có crop issue | 59.12s |
| RC.pdf | 28 | 101–200 đúng thứ tự, không có extraction issue | 165.07s |

LC trước sửa chạy 212.99s và nhận nhầm `content_start_page=4`, gây lệch crop
ảnh/graphic như ảnh chụp. Đây là số đo một job; chưa phải load test đồng thời.

## 9. Review crop thủ công và PDF scan in — 2026-08-08

### Crop từ trang PDF gốc

Trong lúc review, source page đã render được giữ cùng extraction job (và được
đồng bộ private lên MinIO ở server). Review UI nay có hai luồng:

- Crop đang có: có thể đổi **Trang PDF gốc** rồi chọn lại vùng crop; không còn
  bị khóa vào asset auto-crop ban đầu.
- **Thêm ảnh thủ công**: chọn một trang nguồn, kéo vùng cần lấy và gán cho một
  hoặc nhiều số câu. API `POST /api/extractions/{job_id}/manual-stimulus` cắt
  asset mới lossless, chuyển `stimulus_id` của các câu được chọn và bỏ mapping
  cũ chỉ với các câu đó. Input bị giới hạn 1–500 trang, tối đa 100 câu và bbox
  phải nằm trong trang.

Đây là thao tác review có chủ đích; source page vẫn theo TTL của extraction
job. Khi đã finalise đề, chỉ asset đã chọn được đưa vào đề, không giữ PDF nguồn
public vô thời hạn.

### Scan không phẳng / scan in

Hai fixture mới `Đề Listening (bản in).pdf` (11 trang) và `TEST 1 RC (1).pdf`
(29 trang) không có text layer hữu dụng. Chúng có bóng nền, độ cong nhẹ, chữ
nhỏ, watermark/quảng cáo và một số header số câu bị OCR bỏ qua dù phương án
A–D vẫn được đọc.

Pipeline `3.0.2-paddleocr-v4-scan-structure` thêm recovery theo cấu trúc:

- Giữ block phương án không có số câu OCR khi có ít nhất hai marker A–D.
- Chỉ gán số câu khi block nằm đúng giữa hai số câu tin cậy và số block đúng
  bằng khoảng cách số câu. Không đủ bằng chứng thì giữ `question_missing` /
  `options_missing` để giáo viên review, không dịch đáp án câu sau lên.
- Listening Part 1–2 được tạo deterministic theo audio (1–6 A–D, 7–31 A–C),
  nên marker in bị mất không bị báo nhầm là thiếu nội dung.
- Deskew vẫn áp dụng cho trang nguồn. Normalization nền ảnh được giữ như
  recovery có giới hạn, tắt mặc định vì thử nghiệm scan in làm job lâu hơn mà
  không cải thiện dữ liệu; chỉ bật khi có fixture chứng minh hiệu quả qua
  `OCR_SCAN_RETRY_PAGES=1..12`.

Lần đo có giới hạn 2 CPU/4 GB cho Listening scan in dùng hai OCR engine đạt
đỉnh khoảng 0.9 GB RAM. Một pass OCR đầy đủ mất 149.09 giây; retry toàn trang
làm tăng lên 215.10 giây mà không cải thiện dữ liệu, nên không bật mặc định.
Đây không phải load test concurrent.

## 10. OCR worker 6 core, skip prefix và Desktop packaging — 2026-08-08

### Phân bổ tài nguyên OCR

Worker OCR Docker nay có quota **6 CPU / 6 GB RAM**, thay cho 2 CPU / 4 GB.
Một worker Celery vẫn chỉ nhận **một job OCR** (`--concurrency=1`), nhưng job
đó chạy sáu page pipeline / sáu PaddleOCR CPU session song song, mỗi session
ONNX một thread. Cách này dùng đủ sáu core mà không để nhiều PDF scan cùng làm
cạn CPU, RAM hoặc I/O của PostgreSQL/MinIO.

```yaml
OCR_PAGE_WORKERS: 6
OCR_ENGINE_POOL_SIZE: 6
OCR_ONNX_INTRA_THREADS: 1
cpus: "6.0"
mem_limit: 6g
```

`rapid_ocr.py` chặn cứng pool CPU ở sáu session; giá trị môi trường lớn hơn
không thể tạo pool vô hạn. Kết quả quan sát lúc OCR fixture: gần 400% CPU và
khoảng 0.75 GB RSS, nằm dưới quota 6 GB. Cần benchmark lại end-to-end trên
server production trước khi công bố số giây cam kết, vì thời gian còn phụ thuộc
vào độ phân giải, số ảnh lớn và chất lượng scan của từng PDF.

### Rule skip bìa / hướng dẫn

Chỉ các **trang tiền tố** không có câu hỏi mới bị bỏ; header/footer nằm trên
trang có câu hỏi không làm trang đó bị skip. Detector quét tối đa 12 trang đầu:

- Listening ưu tiên trang có đồng thời marker `1.` và `2.`. Nếu trang cũng có
  heading `PART 1`, hai marker phải cách nhau theo chiều dọc để không nhầm một
  danh sách hướng dẫn đánh số.
- Nếu marker ảnh bị OCR bỏ sót, fallback Part 2 cũ vẫn được giữ.
- Reading bắt đầu ở marker hợp lệ `101`–`200`, kể cả PDF chỉ có hai trang.

OCR trực tiếp các file người dùng thêm xác nhận:

| File | Prefix bị skip | Trang bắt đầu | Bằng chứng OCR |
|---|---:|---:|---|
| `TEST 1 LC.pdf` | 1–2 | 3 | Trang 3 đọc được `1.` và `2.` |
| `TEST 1 RC .pdf` | 1 | 2 | Trang 2 đọc được `101.` |

`PIPELINE_CACHE_VERSION` được tăng lên `3.1.0-paddleocr-v4-reading-roi`, nên
upload lại cùng file sẽ không lấy draft crop cũ từ cache.

### Local OCR trong Tauri (Windows)

Không tải model tại thời điểm người dùng cài app. Build sidecar tải hai model
PP-OCRv4 đã pin SHA-256, đóng gói chúng cùng Poppler vào sidecar, và lúc chạy
`desktop_entry.py` kiểm tra checksum + probe `pdfinfo`/`pdftoppm` + warm-up OCR
trước khi app dùng local API. Thiếu/hỏng model cho lỗi hướng dẫn cài lại thay
vì fallback âm thầm ra Internet.

CI Windows smoke test sidecar và layout NSIS sau cài đặt, đồng thời kiểm tra
Tesseract, Poppler, FFmpeg và FFprobe trong resource path trước phát hành.

## 11. OCR ảnh answer key — 2026-08-08

Ảnh mẫu người dùng dán có bố cục 5 cột và chứa câu `101–200`, nhưng được dán
trong draft `TEST 1 LC.pdf` chỉ có câu `1–100`. Pipeline cũ đã lọc đúng phạm vi
để không làm sai đáp án, nhưng trả thông báo chung khiến lỗi giống như OCR
không đọc được.

Đã cập nhật:

- Tăng ngân sách OCR ảnh đáp án từ 15 lên 30 giây; mỗi pass tối đa 6 giây,
  giao diện chờ tối đa 45 giây.
- Recovery bảng 5 cột không được remap chữ cái theo vị trí nếu số câu OCR
  xác nhận thuộc phạm vi khác. Ảnh Reading không thể bị biến thành answer key
  Listening chỉ vì hai bảng có cùng hình dạng.
- Backend giữ bằng chứng OCR chưa lọc để báo rõ: ảnh là Reading `101–200`,
  draft hiện tại là Listening `1–100`. Đáp án ngoài phạm vi không bao giờ được
  ghi vào draft.
- Khi OCR trả về rỗng, giao diện không gọi `onApply` với map rỗng nên không
  tạo thêm lỗi “không tìm thấy answer key hợp lệ” chồng lên thông báo gốc.

Ảnh `101–200` cần được dán tại review của file RC/Reading tương ứng. Nếu dán
đúng loại ảnh trong đúng draft, recovery 5 cột vẫn dùng thứ tự hàng/cột và
kiểm tra số câu OCR trước khi gán đáp án.

## 12. Answer-key photo fast path — 2026-08-08

Ảnh 5 cột x 20 dòng có header màu được xử lý bằng vùng bảng đã cắt bỏ header
và một lượt PaddleOCR toàn vùng trước. Chỉ khi lượt này thiếu câu mới chạy
recovery từng cột. Ảnh được giới hạn ở khoảng 1000px chiều rộng; API dùng một
OCR session với bốn ONNX CPU threads, còn worker PDF vẫn dùng bốn session một
thread.

Smoke test bố cục tương đương ảnh `1–100`: nhận đúng `100/100`, không sai câu,
thời gian khoảng `5,8 giây` trên container CPU hiện tại; trước đó pipeline chạy
5 lượt cột tuần tự và mất khoảng `21,7 giây`. Đây là benchmark một ảnh, không
phải cam kết latency khi nhiều người cùng import.

## 13. Reading ROI pipeline — `TEST 1 RC .pdf`

`backend/pipeline.py` nay có kế hoạch hai giai đoạn cho Reading scan:

- `ReadingPagePlan` dùng locator OCR nhẹ để nhận diện Part 5/6/7, header nhóm
  câu, vùng câu hỏi và vùng passage theo tọa độ chuẩn hóa trên ảnh trang gốc.
- OCR chất lượng cao chỉ chạy trên `question_rois`; passage vẫn được cắt từ
  JPEG nguồn, không biến passage thành text và không OCR passage lần hai.
- ROI không chắc chắn hoặc kết quả thiếu câu/phương án sẽ fallback tối đa một
  lần theo từng trang. Đường mặc định còn có targeted recovery cho đúng các
  trang bị thiếu A–D, thay vì chạy locator cho toàn bộ tài liệu.
- `OCR_READING_ROI_ENABLED=1` bật toàn bộ pipeline hai giai đoạn; mặc định là
  `0` để rollback nhanh trong production cho tới khi benchmark trên đúng máy
  triển khai chứng minh được lợi ích thời gian. `OCR_READING_LOCATOR_DPI` và
  `OCR_READING_RECOVERY_PAGES` cho phép tinh chỉnh có giới hạn.

Golden fixture `TEST 1 RC .pdf` đã xác nhận 29 trang, bỏ bìa trang 1, đủ câu
101–200 với Part 5/6/7 = 30/16/54. Các nhóm cuối giữ đúng source crop: 172–175
ở trang 18; 176–180 ở 20; 181–185 ở 22; 186–190 dùng 24–25; 191–195 dùng
26–27; và 196–200 dùng 28–29. Unit suite hiện đạt 51 test, 6 golden test
được skip nếu chưa bật biến môi trường.

Trong phép đo worker 6 CPU/6 GB hiện tại, đường mặc định hoàn tất fixture với
100 câu, không có `question_missing/options_missing`; targeted recovery chạy
trang 3 và mất khoảng 18 giây trong tổng thời gian khoảng 183 giây. Vì vậy
không bật toàn bộ ROI mặc định chỉ dựa trên kỳ vọng; cần benchmark ba lượt
baseline/ROI trên máy production trước khi chuyển flag sang `1`.
## Native build reliability follow-up — 2026-08-09

Đã sửa regression làm Windows/macOS release dừng ở smoke finalize:

- Smoke Reading gửi đủ câu 101–200 với dữ liệu bounded hợp lệ; câu 101 vẫn là
  câu được OCR và answer-key kiểm tra. `count=1` giữ output smoke nhỏ.
- Không nới lỏng `ensure_question_coverage()` hoặc validation finalize, nên
  không đánh đổi tính toàn vẹn đề thi để làm CI xanh.
- Thêm regression test `scripts/test_smoke_sidecar.py` và chạy test này trong
  cả hai native workflow.
- Upload diagnostics được đánh dấu non-blocking để lỗi DNS GitHub không che
  root cause; job vẫn fail nếu smoke/build trước đó fail. macOS upload cả
  diagnostics trước đóng gói và diagnostics từ `.app` đã cài.

Verification trên Linux: backend `124 passed, 6 skipped`, frontend `51 passed`,
TypeScript/lint, desktop static build 23 routes, Rust `3 passed`, `cargo check`
và smoke sidecar OCR/finalize/SQLite/restart/outbox đều pass. Chưa có số liệu
load test hoặc native NSIS/DMG từ host này.

## macOS Intel OCR provider follow-up — 2026-08-09

Intel packaged smoke dùng CPU ONNX provider; Apple Silicon vẫn dùng CoreML.
Provider selection có test riêng, answer-key fixture được làm lớn và tương phản
hơn, và lỗi OCR in payload đầy đủ để phân biệt sai letter với lỗi resource.
Native Intel packaged smoke vẫn cần rerun trên GitHub Actions trước khi phát hành.

## 14. Kho đề thi chung, immutable version và Giải chi tiết — 2026-08-09

Đã triển khai migration `0021_shared_bank` theo kiểu expand-only. Đề Teacher mới
được gắn `teacher_shared`, title dùng khóa NFKC/casefold/trim-space duy nhất toàn
kho, Tag dùng khóa chuẩn hóa duy nhất và mọi Teacher có cùng quyền quản trị. API
list phân trang 20, tối đa 50 và chỉ trả metadata/contributor/coverage/thống kê
người gọi; payload câu hỏi, media và lời giải không đi cùng danh sách.

Mọi lượt tự làm, classroom assignment và public submission mới đều pin
`ExamVersion`. Projection `ExamVersionQuestion` là nguồn chấm điểm; payload trước
submit bỏ answer key/solutions. Việc rename, sửa, archive hoặc soft-delete không
đổi version/hash của attempt đã bắt đầu. Edit session copy-on-write hết hạn sau
2 giờ, finalize khóa Exam và so `base_revision`; conflict trả 409 nhưng giữ draft.
Source PDF được giữ trong MinIO, version assets được snapshot một lần theo
content hash và có fallback tái tạo page/crop/audio khi job OCR gốc hết hạn.

Lời giải là plain text có mapping TOEIC chặt: 1–31 từng câu, 32–70 và 71–100
từng nhóm ba câu, Reading 101–200 từng câu. Backend chặn range sai, overlap,
field trên 12.000 ký tự và payload trên 2 MiB. DOCX/DOC/PDF import chạy queue OCR
có giới hạn một job/Teacher, 5 job/10 phút; file tạm hết hạn sau 24 giờ. Trang
solution lazy-load sau submit và media tiếp tục đi qua version-asset token/MinIO,
không đưa lời giải vào hot payload bắt đầu thi.

Autosave/submit hiện giữ batch revision, bulk upsert, row lock và receipt cũ.
Một integration test đã xác nhận Teacher B sửa đề Teacher A, stale revision 409,
Student không có quyền sửa, attempt pin version qua một lần sửa tiếp theo và chỉ
xem lời giải sau submit. Harness k6 có thêm `bank-list`, `bank-start`, `solutions`;
verifier kiểm tra exact answer map, receipt, version ID và content hash. Số đo
staging vẫn là TBD; không suy diễn capacity từ unit/integration test.

Verification cuối cho feature này trên Linux: backend `146 passed, 6 skipped`,
frontend `51 passed`, TypeScript/lint pass, Next.js production build đủ 25 route,
Python/JavaScript/shell static checks pass và database SQLite trắng nâng thành
công tới migration `0021_shared_bank`. Reset thật và load test vẫn chỉ được chạy
trong maintenance/staging theo checklist, không chạy trên workspace này.
## Kế hoạch chuyển dependency ra máy chủ ngoài (2026-08-10)

Thứ tự triển khai sau audit `EXT-*`:

1. Chuyển Compose backend environment sang `DATABASE_URL`, `MINIO_*` và
   `PADDLE_OCR_URL` do `.env` cung cấp; bỏ service/volume PostgreSQL và MinIO
   nội bộ cùng dependency graph liên quan.
2. Giữ Redis/Celery; thêm egress cho migrate/API/OCR worker/maintenance/beat và
   Nginx vì các service này phải truy cập dependency LAN.
3. Tạo năm bucket private idempotently trong container migration, trước khi API
   khởi động; không để lại một `minio-init` container riêng.
4. Thêm remote OCR adapter có connection/read timeout, response byte limit,
   schema validation, confidence/box normalization và concurrency ceiling.
   Desktop không cấu hình URL nên tiếp tục dùng model local.
5. Đổi readiness sang probe PaddleOCR thật khi remote URL được cấu hình, vẫn
   kiểm tra Poppler, PostgreSQL, Redis và bucket MinIO.
6. Đổi upstream private MinIO của Nginx sang endpoint LAN và giữ đúng signed
   Host để `X-Accel-Redirect`, Range audio và bucket private tiếp tục hoạt động.
7. Chạy unit/integration/lint/build; sau đó migration, bootstrap admin, tạo key
   qua API, OCR smoke và MinIO round-trip. Chỉ xóa container nội bộ sau khi đã
   xác định tên/volume và có quyền Docker; không xóa volume dữ liệu khi chưa
   được xác nhận backup.

Rollback: giữ database/schema không đổi; bỏ `PADDLE_OCR_URL` để quay về adapter
local, và có thể khôi phục endpoint MinIO/PostgreSQL cũ bằng biến môi trường.

### Kết quả implementation

- Compose mặc định không còn PostgreSQL, MinIO, MinIO console/init/mirror/fetch
  hoặc volume local tương ứng. Redis/Celery được giữ vì vẫn là dependency nghiệp
  vụ.
- Migrate/API/OCR worker/maintenance/beat/Nginx có egress; migration chạy đủ
  Alembic `0001` tới `0021` trên PostgreSQL ngoài và khởi tạo object storage.
- Năm bucket private đã được tạo. Upload/download 185,004 byte có SHA-256 khớp,
  anonymous GET trả 403 và object smoke đã được xóa.
- Remote adapter gọi đúng PaddleOCR `/ocr`, normalize box/confidence và giới hạn
  tối đa 6 request; logo smoke trả hai dòng đúng qua `remote:rapidocr`.
- Admin bootstrap, login, dashboard và tạo activation token đều trả HTTP 200.
- Worker OCR giảm từ 6 CPU/6 GB xuống 1.5 CPU/2 GB vì inference đã chuyển sang
  máy ngoài; PDF rendering/parser vẫn ở worker.

Giới hạn vận hành: user hệ điều hành hiện tại không có quyền Docker socket và
`sudo` cần mật khẩu, nên chưa thể chạy `compose down/up`, container readiness hay
xóa orphan thực tế. Không dùng `down -v`; volume PostgreSQL/MinIO cũ phải được
giữ đến khi backup/rollback được xác nhận.
## Kế hoạch sửa coordinate remote OCR cho LC/RC (2026-08-10)

1. Bổ sung bounded parser lấy width/height từ annotated data URI mà không decode
   toàn bộ ảnh chỉ để đọc header.
2. Scale polygon X/Y độc lập từ coordinate space của OCR server về đúng kích
   thước ảnh upload; giữ confidence và text nguyên vẹn.
3. Với ảnh vượt max-side của remote server, OCR các tile overlap; translate box
   về global coordinates và dùng core ownership để loại detection trùng.
4. Thêm unit test remote resize, tile coverage và two-column conversion.
5. Chạy lại LC 7–100 và RC 101–200, assert đủ số câu, text và số option theo
   Part; chỉ sau đó mới cân nhắc recovery OCR cho phần thực sự còn thiếu.

### Kết quả OCR end-to-end trên fixture thật

Do host hiện tại không có Poppler, test dùng `pypdfium2` chỉ để render đúng các
trang PDF; toàn bộ remote OCR, coordinate adapter, parser, crop và validation là
code production không mock.

| Fixture | Trang | Số câu | Thiếu số câu | Thiếu A–D | Thiếu text bắt buộc | Thời gian |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `LC.pdf` | 11 | 100/100 | 0 | 0 (câu 32–100) | 0 (câu 32–100) | 34,94 giây |
| `RC.pdf` | 28 | 100/100 | 0 | 0 | 0 | 146,93 giây |

Part 6 câu 131–146 có blank nằm trong passage nên `question.text` rỗng là đúng
mô hình dữ liệu; cả 16 câu vẫn đủ bốn lựa chọn. Câu 183 trước đây thiếu D nay
nhận đủ `Tour 5`. LC tạo 11 stimulus, không issue. RC tạo 17 stimulus; còn ba
`crop_review` về số tài liệu ảnh ở nhóm 172–175, 191–195 và 196–200, không phải
thiếu chữ câu hỏi/đáp án. Hai `number_inferred` (181, 191) vẫn tạo đúng số câu.

Unit regression cho adapter/parser pass 9/9. Full backend có 147 pass, 5 skip;
ba failure crop Reading đã tồn tại từ trước và một golden test không chạy được
vì host thiếu `pdfinfo`. Hai fixture thật đã được chạy end-to-end bằng renderer
thay thế như bảng trên.

## Kế hoạch sửa crop và latency Reading (2026-08-10)

1. Dùng chung grammar nhận question marker cho parser và spatial token; hỗ trợ
   cả token riêng `168.` lẫn cả line `168. What ...`.
2. Nhận `(A)` ở đầu token Part 6 để xác định biên passage/answer block.
3. Tạo semantic passage boundary theo token box trước khi chạy image trim/split;
   asset không được vượt qua boundary này.
4. Bỏ tiling toàn trang mặc định. Full-page OCR làm pass nhanh; chỉ page thiếu
   text/A-D mới OCR lại question ROI ở độ phân giải cao.
5. Chạy lại đủ `RC.pdf`; assert 101-200 đầy đủ, mọi stimulus có bottom nhỏ hơn
   first question/option top của nhóm, và inspect montage/cảnh báo split.
6. So sánh duration/request count với baseline 146,93 giây; không chấp nhận cải
   thiện tốc độ nếu câu 183 hoặc lựa chọn khác lại bị mất.

### Kết quả triển khai crop/latency

| Chỉ số `RC.pdf` | Trước | Sau |
| --- | ---: | ---: |
| Tổng thời gian | 146,93 giây | 78,89 giây |
| Full-page tile/request thông thường | 6 tile/trang | 1 request/trang |
| Targeted recovery | không merge được D câu 183 | 1 ROI, 1,427 giây |
| Câu đầy đủ | 100/100 | 100/100 |
| Câu thiếu A-D/text bắt buộc | 0 sau tiled run | 0 |
| Stimulus / asset | 17 / 22, còn crop review | 19 / 26, đúng single/double/triple |
| Crop lấn câu hỏi | có | 0 |
| Issues | 5 | 0 |

Timeline run sau: render kết thúc 7,38s; full-page OCR 63,34s; recovery/parse tới
64,79s; crop lossless baseline tới 78,89s. Sau run này Reading asset đã chuyển
sang WebP quality 95/method 0. Benchmark đúng 26 crop giảm payload 63,4%
(19.988.962 → 7.310.038 byte) và encode trong 3,288s. Không báo duration end-to-end
mới cho thay đổi encode cho tới lần deploy/run kế tiếp; 78,89s là số đo bảo thủ.

Regression: 16 targeted crop/OCR/parser tests pass; backend không cần golden
Poppler trên host đạt 154 pass, 5 skip, 1 golden deselected. Golden tương đương
đã chạy end-to-end bằng `pypdfium2` do host thiếu `pdfinfo`.

## Kế hoạch Part 6 và Listening asset crop (2026-08-10)

1. Thêm spatial grammar nhận `131.(A)...` đến `146.(A)...`; không dùng blank
   number nằm trong passage làm bottom boundary.
2. Truyền expected group numbers vào Part 6 boundary ở cả crop pipeline và ROI
   planning, rồi assert asset bottom nhỏ hơn answer-block top.
3. Thay OpenCV-only Part 1 fit bằng NumPy dense-rectangle projection; chọn run
   lớn nhất theo ink mass × span để bỏ number label và ảnh kế tiếp.
4. Giữ năm coarse safe zone LC đã đối chiếu, nhưng fit từng graphic bằng
   whitespace/ink trimming một mảnh trước khi lưu.
5. Chạy lại 4 crop Part 6, 6 photo và 5 graphic; inspect ảnh thật và chạy full
   regression. Không OCR lại toàn PDF nếu chỉ thay crop algorithm.

### Kết quả Part 6 và Listening assets

| Fixture | Gate | Kết quả |
| --- | --- | --- |
| RC page 4–7 | Part 6 asset trước answer block | PASS 4/4 |
| RC page 4–7 | Crop có A-D | PASS, 0 |
| LC page 1–3 | Part 1 photo không có number/ảnh kế | PASS 6/6 |
| LC page 7,8,11 | Graphic chỉ chứa bảng/sơ đồ | PASS 5/5 |
| LC | Stimulus mapping | PASS, 11/11 |

Part 6 dùng raw `number.(A)` boundary theo từng trang, không dùng fixed ratio.
Part 1 dùng dense row/column projection và largest ink-mass span; graphic dùng
`_trim_and_split_bboxes(..., pieces=1)` trong coarse zone đã đối chiếu. Backend
regression cuối: 156 pass, 5 skip, 1 Poppler golden deselected; compile, Compose
config và diff checks pass.

## Kế hoạch PDF bản in/scan xấu (2026-08-10)

1. Chặn crash khi ROI recovery không có option fragment; không tạo dữ liệu giả
   và không ghi đè kết quả OCR lượt đầu.
2. Đo baseline đầy đủ của cả hai fixture khi recovery tắt để lập bản đồ
   page/question lỗi và thời gian fast path.
3. Bổ sung scan normalization Pillow/NumPy khi OpenCV không khả dụng; chỉ gọi
   full-resolution retry cho trang có question/option thiếu, với cap chặt.
4. Merge recovery theo field còn thiếu thay vì nối candidate rồi để sequence
   resolver có thể chọn nhầm dữ liệu bleed-through.
5. Thay bbox graphic Test 1 bằng semantic safe zone: nửa cột tương ứng, từ đầu
   trang tới ngay trước question marker của group, sau đó trim theo ink.
6. Chạy E2E hai scan xấu; assert đủ 100 câu, text/option bắt buộc và crop không
   chạm câu hỏi. Chạy lại `LC.pdf`/`RC.pdf` để bảo vệ fast path đã đúng.

### Kết quả triển khai scan xấu

- Giữ fast path PDF đẹp; scan recovery chỉ kích hoạt sau completeness check và
  giới hạn bởi `OCR_SCAN_RETRY_PAGES=12`.
- Ghép fragment cùng hàng khi Paddle tách số câu khỏi câu hỏi; recovery theo
  block A–D và chỉ điền field thiếu. Choice từ threshold variant phải có nguồn
  hoàn chỉnh hoặc đồng thuận hai lần để tránh dịch lựa chọn sang câu kế bên.
- Part 6/7 dùng header bố cục dự phòng để giữ đủ 19 group; crop không mở rộng
  qua vùng câu hỏi. Bleed-through được loại khỏi phép dò whitespace bằng ngưỡng
  dark-ink 210.
- Part 1 dùng ngưỡng dark-ink 200 để tìm đúng biên ảnh trên giấy xám nhưng lưu
  nguyên pixel nguồn. Part 3/4 lấy safe zone theo question marker rồi trim ảnh.

| Fixture | Thời gian | Câu | Cần duyệt | Stimulus / asset |
| --- | ---: | ---: | ---: | ---: |
| `Đề Listening (bản in).pdf` | 47,03 s | 100/100 | 4 | 11 / 11 |
| `Đề Reading (BẢN IN).pdf` | 101,95 s | 100/100 | 5 | 19 / 25 |
| `LC.pdf` | 20,99 s | 100/100 | 0 | 11 / 11 |
| `RC.pdf` | 66,04 s | 100/100 | 0 | 19 / 26 |

Không coi “đủ 100 số câu” là “OCR đủ chữ”. Những câu không có bằng chứng OCR
đáng tin vẫn mang issue để UI yêu cầu duyệt tay; đặc biệt Listening 50 bị cắt
vật lý ở mép ảnh và Reading 181 không được Paddle phát hiện qua các biến thể.

## Kế hoạch finalize/Full Test/ACL và Part 5 (2026-08-10)

1. Thêm lifecycle `component_pending`; list và attempt API không công bố đề
   thành phần, combine mới chuyển thành một đề `combined` sẵn sàng.
2. Làm combine an toàn khi client retry sau timeout; cùng cặp component/job
   không được tạo thêm Full Test.
3. Sau finalize, resolve role rồi hard-navigate tới My Exams hoặc Kho đề thi;
   giữ nút disabled xuyên suốt toàn bộ transaction frontend.
4. Đồng bộ quyền UI với API: User owner và Teacher đều có menu quản lý; action
   dùng đúng personal/shared endpoint.
5. Cho User tạo category cá nhân mà không ghi vào taxonomy Kho chung; trả lỗi
   Tag về modal thay vì bỏ qua.
6. Chuẩn hóa blank Part 5 thành `_____`, retry text có glued-token, áp dụng
   lexical split có giới hạn và regression trên chính mẫu câu 101–130.
7. Chạy backend unit/integration, frontend test/typecheck/build và kiểm chứng
   API bằng tài khoản User/Teacher.

### Kết quả triển khai finalize/Full Test/ACL và Part 5

- Reading/Listening trung gian dùng `component_pending`, bị loại khỏi My Exams,
  không mở trực tiếp và không tạo attempt. Sau combine chúng được soft-delete
  với `combined_component`; retry cùng cặp job trả lại Full Test đã có.
- Điều hướng hoàn tất dùng full navigation sau khi toàn bộ persist/combine hoàn
  tất: Normal User tới `/my-exams`, Teacher/Admin tới `/exam-bank`; lỗi tạo Tag
  hoặc combine được hiển thị thay vì bị bỏ qua.
- Normal User owner đã có menu ba chấm để sửa, nhập giải và xóa đề cá nhân.
  Action archive/public vẫn chỉ dành cho Teacher và tiếp tục dùng API Kho chung.
- User có thể tạo Tag/category cá nhân; Teacher/Admin tiếp tục quản lý taxonomy
  Tag dùng chung. GET Tag hợp nhất Tag chung với category cá nhân của đúng owner.
- Part 5 canonicalize đúng một chỗ trống thành `_____`, sửa punctuation spacing,
  CamelCase và glued-token có confidence từ word-frequency segmentation. Logic
  chỉ chạy cho Reading 101–130, không sửa đáp án, passage hoặc các Part khác.
- Dữ liệu production cũ đã được sửa theo revision mới: 30/30 câu Part 5 của
  Full Test hiện tại có một blank; revision cũ vẫn được giữ cho attempt đã tạo.
  Hai component đã ghép được soft-delete; một Listening đang chờ Reading chỉ bị
  ẩn, không bị xóa nên người dùng vẫn có thể tiếp tục quy trình Full Test.

Verification: backend `162 passed, 2 skipped, 4 golden deselected`; frontend
`52 passed`, TypeScript pass và Next production build pass. Golden PDF trên host
không chạy vì host thiếu `pdfinfo`; Docker image có Poppler,
API/frontend/worker sau rebuild đều healthy và remote OCR health trả HTTP 200.

## Kế hoạch tối ưu OCR Desktop local (2026-08-10)

1. Thêm profile runtime thuần, xác định từ OS/architecture/CPU trước khi import
   OCR: Intel/CPU 4 core trở lên dùng 2 engine × 2 thread; CoreML/DirectML giữ
   một accelerator session; mọi biến môi trường do vận hành đặt rõ vẫn được ưu
   tiên và không bị ghi đè.
2. Bật CPU memory arena có giới hạn cho Desktop, đồng bộ cấu hình CoreML sang
   `MLProgram`/`FastPrediction`, giữ model cache ngoài bundle và CPU fallback.
3. Mở rộng readiness/log để chứng minh `LOCAL_EDGE`, model checksum, execution
   provider, architecture, engine pool và page workers của đúng artifact đang
   chạy; packaged smoke phải fail nếu sidecar báo remote OCR.
4. Sửa benchmark runner tạo đủ job layout, xuất timing/runtime JSON và hỗ trợ
   fail theo ngân sách thời gian để chạy lại cùng fixture trên Intel Mac, M1 và
   Windows release runner.
5. Thêm regression unit cho profile CPU/M1/Windows, pool/provider params và
   contract readiness; chạy lại LC/RC bảo vệ 100 câu và scan fixtures bảo vệ
   completeness trước khi cân nhắc locator bỏ trang bìa.
6. Chỉ triển khai prefix locator nếu benchmark xác nhận lợi ích và test chứng
   minh không bỏ nhầm trang Part 1. Không hạ độ phân giải OCR scan xấu.

### Kết quả triển khai Desktop 0.1.6

- Profile phần cứng, CPU pool 2×2, CPU memory arena, CoreML MLProgram cache và
  telemetry local/provider đã được triển khai.
- Benchmark tool tạo đúng `pages/assets`, hỗ trợ `--max-seconds` và
  `--min-questions`, đồng thời xuất missing numbers, runtime và metadata để
  không thể báo nhanh nhưng bỏ câu.
- Installer smoke xác minh toàn bộ local-route contract và OCR PDF 8 trang với
  budget 90 giây trên cả `.app` arm64/x64 và layout NSIS đã cài.
- Fix content routing scan Listening loại false positive table 1/2; kết quả
  cuối: PDF đẹp 48,05 s/144,74 s và scan 99,80 s/196,77 s, cả bốn fixture đủ
  100 số câu. Không hạ render DPI/max-side.
- Verification: backend 170 pass, 2 skip, 4 golden deselected; targeted Desktop
  21 pass; frontend 52 pass, typecheck và production Desktop build pass. Host
  hiện không có Rust toolchain nên `cargo check` phải chạy trong native release
  CI (workflow đã có bước này cho từng target).

## Kế hoạch import lời giải và xây lại Result/Solutions (2026-08-10)

1. Chuẩn hóa header/number Unicode, whitespace và xuống dòng; thêm regression
   `1 0 1`, `1\n0\n1`, `Câu 101`, `Question 101` và range hợp lệ.
2. Tách duplicate consolidation khỏi overlap validation: candidate cùng key từ
   PDF lặp được dedupe/chọn bản giàu nội dung; overlap giữa hai group thật vẫn
   bị chặn. Issue phải được aggregate, không trả hàng nghìn dòng.
3. Tối ưu PDF text fast path và dừng sau khi đủ coverage với lookahead an toàn;
   giữ pdfplumber/OCR fallback cho format không có table.
4. Phát hiện component mismatch Listening/Reading trước merge và đánh dấu nội
   dung cuối file có dấu hiệu bị cắt để bắt buộc preview/sửa tay.
5. Đồng bộ solution permission/payload cho Teacher, Normal User, Student và
   Desktop local; giữ ownership check và chỉ cho xem attempt đã submit.
6. Xây Result score hero căn giữa; Listening/Reading bên dưới; giữ score release
   policy từ API và tách thống kê bài làm thành hàng riêng.
7. Xây Solutions rộng/dễ đọc hơn; điều hướng dùng trạng thái đáp án thật với
   legend đúng/sai/chưa làm/chưa chấm, không dùng màu xanh “có lời giải”.
8. Chạy parser thật hai PDF, backend tests, frontend tests/typecheck/build và
   kiểm tra luồng Result → Solutions bằng cả payload remote và Desktop local.

### Kết quả triển khai import lời giải và Result/Solutions

- Parser PDF table ưu tiên table text, dừng sau coverage + một trang lookahead,
  gom duplicate theo fingerprint và chọn candidate đầy đủ nhất theo bằng chứng
  answer/options/ending/độ dài.
- Component mismatch và phần cuối có dấu hiệu bị cắt được đưa thành warning có
  nghĩa nghiệp vụ; importer không tự suy diễn nội dung không có trong nguồn.
- Thời gian hai đường hợp lệ giảm từ 47,07/71,36 giây xuống 19,48/17,75 giây,
  vẫn đủ 100/100 câu Reading. Nhánh chọn nhầm Listening mất 19,87 giây và trả
  duy nhất `exam_type_mismatch`.
- Result dùng score hero mới; Solutions tăng chiều rộng, kích thước ảnh/chữ và
  chỉ tách hai cột ở màn hình rất rộng. Navigation/status không còn suy luận
  “có lời giải = đúng”.
- Verification: parser/classroom/platform backend 41 pass; frontend 54 pass; TypeScript,
  Next production build và Desktop build đều pass.

## Kế hoạch bảo vệ mapping Full Script Listening Test 1 (2026-08-10)

1. Giữ PDF text fast path; không đưa file rõ này qua OCR.
2. Dedupe row có fingerprint giống hệt một cách im lặng; chỉ cảnh báo khi cùng
   key thực sự có nhiều phiên bản nội dung và parser phải chọn một bản.
3. Thêm regression tạo đủ 54 group theo contract Listening, gồm STT dọc và row
   Quartz lặp, rồi assert 100/100 câu, không thiếu key và không có issue giả.
4. Chạy lại file thật, parser/backend tests và frontend build để bảo vệ luồng
   Listening → ghép Full Test → Result → Solutions.

### Kết quả Full Script Listening

- File thật parse trong 11,23 giây, đúng 54 entry/100 câu và không còn warning
  giả từ 232 row Quartz giống hệt.
- Part counts giữ đúng `6 / 25 / 13 / 10`; toàn bộ key và thứ tự entry trùng
  contract `allowed_solution_groups("listening")`.
- Regression parser gồm full contract STT dọc pass 22/22; merge vẫn không đụng
  tới answer key và vẫn bắt buộc preview trước khi lưu vào version đề.

## Kết quả sửa login qua IP LAN

- Cookie session không còn phụ thuộc cứng vào `PUBLIC_BASE_URL`; scheme được
  lấy từ request/proxy.
- `http://10.10.10.5` login/state/me đã xác minh thành công sau khi rebuild.
- HTTPS canonical domain vẫn nhận cookie `Secure`; không hạ bảo mật production.
# Auto-cut Audio Full TOEIC bằng FFmpeg (2026-08-10)

## Thay đổi đã triển khai

- Port thuật toán dò im lặng MIT từ `jinjor/wave-cutter-for-toeic` revision
  `4e4ce393864d2d7aa8944c5efa0c9350ea5ea8c6` sang một lượt FFmpeg
  `silencedetect`. Ngưỡng giữ đúng `-40 dBFS` và `60.000 / sample_rate` giây,
  không thêm Whisper/ASR/model hoặc service mới.
- Thêm bộ giải dynamic programming cho cấu trúc TOEIC `All+`: 31 vai trò câu
  1–31 và 23 × (passage + ba question prompt), tổng 123 vai trò. Bộ giải cho
  phép bỏ direction/“go on to the next page”, gộp raw split nội bộ và sử dụng
  prior năm opening waves cho signature 132–136 nhưng không ép index cố định.
- Nếu một vai trò câu/prompt dài bất thường (dấu hiệu hai câu bị nhập do khoảng
  nghỉ dưới 1,25–1,36 giây), FFmpeg chạy thêm một pass 0,45 giây. Chỉ boundary
  nằm trong coarse wave đáng ngờ được thêm rồi alignment chạy lại; passage và
  toàn audio không bị băm nhỏ theo ngưỡng ngắn.
- Sau khi alignment đạt confidence, worker chỉ encode output cuối: 31 audio câu,
  23 audio nhóm và một direction Part 1 nếu có. Không tạo/upload 123 raw files.
- Audio nhóm 32–34 … 98–100 luôn bắt đầu ở passage và kết thúc sau prompt thứ
  ba. `HiddenExamAudio` vì vậy chỉ phát `ended` khi cả nhóm đã nghe xong.
- Direction dài tại biên Part 2/3/4 được gắn vào audio đầu Part tương ứng; raw
  transition ngắn độc lập bị loại. Full source vẫn nằm trong job để audit/retry
  nhưng không được đưa vào FinalExam khi 54 clip đã sẵn sàng.
- Alignment confidence thấp, raw wave ngoài giới hạn hoặc lỗi xử lý sẽ giữ Audio
  Full, lưu diagnostic và hiện warning ở Review. Hệ thống không publish các clip
  gán sai chỉ để đạt đủ số lượng.
- Review đổi audio sang `preload=none`, tránh 55 metadata request khi vừa mở
  draft. Public-test ưu tiên `question_numbers`, nên group audio không bị chọn
  nhầm theo filename/Part.

## Tài nguyên và hiệu năng

Phân tích dùng một process FFmpeg chuẩn và tối đa một process refinement có
điều kiện. Encode chạy tuần tự 54 output, mỗi process giới hạn một thread,
trong chính OCR worker có concurrency/prefetch bounded;
không tăng worker và không giữ transaction PostgreSQL lúc xử lý file. Docker
image backend đã có cả `ffmpeg` và `ffprobe`. Celery vẫn có hard limit 25 phút.

## Regression coverage

- 134 raw waves → 123 role, bỏ đúng 11 extra trên fixture cấu trúc.
- `silence_end` là biên mới và 44,1 kHz tạo `d=1.360544218`.
- 54 quiz assets; nhóm 32–34 kết thúc đúng tại prompt câu 34.
- Pattern duration không hợp lệ có confidence dưới ngưỡng và không publish.
- Integration normalize/auto-cut thay Full; fallback giữ nguyên Full.

## Kết quả end-to-end với `File_TEST/Test 01.mp3`

- Tạo job thật `5ca68e90-6fc7-4207-9b11-59b21ccfba68` cùng
  `File_TEST/TEST 1 LC.pdf`, chạy qua MinIO, Celery worker, OCR server và
  trang Review; không dùng fixture giả lập cho kết quả này.
- Audio nguồn dài 2.763,832 giây (46 phút 04 giây), 44,1 kHz. Pass
  thô phát hiện 132 wave; refinement có mục tiêu tạo 140 candidate và
  alignment đạt confidence `0.9881` với cost `10.564`.
- Sinh 55 object audio: một direction Part 1, 6 file Part 1, 25 file Part 2,
  13 nhóm Part 3 và 10 nhóm Part 4. Có đúng 54 quiz asset và phủ mỗi câu
  1–100 đúng một lần. Cả 55 object đều tải ngược từ MinIO thành file MP3
  44,1 kHz không rỗng.
- Các mốc kiểm tra trọng yếu: câu 1 `26,593 s`, câu 2 `25,704 s`, câu 6
  `27,167 s`, câu 7 `54,282 s` (gồm transition/direction Part 2), câu 31
  `23,014 s`; nhóm 32–34 `102,713 s`, 35–37 `75,651 s`, 71–73
  `102,922 s` và 98–100 `83,566 s`. Nhóm Part 3/4 chỉ phát `ended` sau
  prompt thứ ba.
- Toàn job hoàn tất trong `98,76 s`: auto-cut và upload chiếm khoảng
  64 giây, OCR 13 trang chiếm `33,91 s`; kết quả Review có đủ
  100/100 câu, `issues=[]`.
## Desktop OCR, đồng bộ revision và parallel audio/OCR (2026-08-11)

- Provider Desktop dùng policy `auto`: Windows chọn DirectML trước CPU, Apple
  Silicon chọn CoreML, Intel Mac giữ CPU pool ổn định; có override vận hành
  `OCR_PROVIDER=cpu|dml|coreml|cuda`. Override không tồn tại trong artifact sẽ
  fail-fast thay vì âm thầm báo GPU giả.
- Native smoke nhận `--expected-provider`. Windows release bắt buộc
  `DmlExecutionProvider`; macOS ARM bắt buộc `CoreMLExecutionProvider`; macOS
  Intel bắt buộc `CPUExecutionProvider`. Tham số `EnableNvidiaGpu` không có tác
  dụng và `GPU_OCR_ENABLED=false` gây hiểu nhầm đã được bỏ.
- SQLite lưu `remote_revision`; manifest Desktop gửi `base_revision`. API kiểm
  tra revision khi nhận manifest và lần nữa trong transaction finalize. HTTP
  409 chuyển local queue sang `conflict`, không retry đè server.
- Coordinator gọi reconcile trước khi claim upload. Cache sạch nhưng server đã
  sửa/xóa được chuyển vào quarantine; local đang có edit được giữ nguyên và
  đánh dấu conflict. `client_exam_id` chỉ được trả cho đúng owner để loại card
  trùng mà không lộ mapping của Teacher khác.
- Đề local chưa sync có route xóa riêng và được quarantine recoverable. Đề đã
  sync phải xóa trên server; reconcile áp tombstone về app ngay sau thao tác.
- Khi web dùng `PADDLE_OCR_URL`, audio FFmpeg và OCR HTTP chạy song song. Worker
  chỉ thêm đúng một audio future/job; OCR page worker và HTTP connection vẫn
  giữ ceiling hiện tại. Desktop/local OCR cũng dùng cùng orchestration hai
  nhánh, nhưng vẫn giới hạn đúng một audio future để tránh tạo worker không
  kiểm soát.
- Job state có `audio_progress`, `ocr_progress`, stage riêng và aggregate
  20%/80%. PostgreSQL dùng `SELECT ... FOR UPDATE` cho progress/media merge;
  SQLite dùng `RLock`, ngăn hai thread ghi đè metadata/progress của nhau.

Kỳ vọng: job Listening web/Desktop không còn mất tổng thời gian `audio + OCR` mà gần
với nhánh chậm hơn; số liệu wall-clock thật cần đo trên OCR server/fixture
production. Thay đổi này không tăng `PADDLE_OCR_MAX_CONNECTIONS` và không tuyên
bố kết quả benchmark chưa chạy.

## Hotfix worker thiếu module audio (2026-08-11)

- API vẫn trả `202 Accepted` vì upload/job creation thành công; lỗi xảy ra ở
  worker bất đồng bộ khi `_run_extraction_job` import module audio.
- `api`, `migrate`, OCR worker, maintenance worker và scheduler dùng chung một
  named backend image thay vì năm implicit image độc lập. Rebuild một backend
  artifact vì vậy không thể để worker giữ filesystem cũ.
- Backend image có OCI revision label và chạy import contract ngay trong bước
  build. `deploy/rebuild.sh` tiếp tục kiểm tra import trước deploy, recreate
  stack, rồi so image ID thật của toàn bộ container Python.
- Worker startup nay preflight `import audio_processing, toeic_audio_cutter`;
  image cũ sẽ fail ngay khi khởi động với log rõ ràng thay vì nhận job rồi trả
  `No module named 'audio_processing'`.
- Root cause runtime đã được xác nhận trên host: module có trong image nhưng
  dynamic import chạy sau task dispatch dưới console script `celery` không luôn
  có `/app` trong `sys.path`. `main.py` giờ import audio ở startup, Docker đặt
  `PYTHONPATH=/app`, và mọi Celery role chạy bằng `python -m celery`.
- Windows/macOS PyInstaller spec đã bundle hai hidden import này; sidecar smoke
  test kiểm tra contract để build audio không thiếu module.

Verification source hiện tại: audio/cutter **15/15**, deployment contract
**3/3**, frontend **70/70**, TypeScript và Next.js production build đều pass;
`docker compose config` xác nhận năm backend role resolve cùng
`examify-backend:local`. Host `10.10.10.5` đã rebuild/recreate thành công và
E2E với `LC.pdf` + `Test 01.mp3` đạt `review`: 100 câu, 55 audio, 0 issue,
Celery `succeeded` trong 74,9 giây.

## Hotfix Full Test staging conflict (2026-08-11)

- Log thật từ iPad/macOS xác nhận draft save trả `200`, nhưng finalize Listening
  trả `409` vì đã có một record staging tên `Listening Component`; đây không
  phải lỗi cache trình duyệt hay hết temp.
- Finalize nay không áp uniqueness của tên đề cho `is_full_test_component` và
  Listening staging legacy. Đề chính thức/shared vẫn giữ nguyên kiểm tra trùng.
- Nhiều job component khác nhau được phép tồn tại để không phá luồng nhiều tab
  hoặc nhiều máy; retry cùng `job_id` tiếp tục cập nhật đúng record cũ.
- Regression test gọi endpoint với một component cũ, finalize component mới hai
  lần và xác nhận `200`, cùng `exam_id`; toàn bộ `backend/test_platform.py` pass
  **18/18**.
- Backend production đã rebuild/recreate riêng API; `/health/ready` trả ready với
  PostgreSQL, MinIO, Redis và scratch đều `true`, pool `checked_out=0`.

## Lifecycle cleanup cho Full Test tạm (2026-08-11)

- `DELETE /api/v1/full-test-components/{exam_id}` chỉ cho owner hủy component
  chưa publish, hỗ trợ retry idempotent.
- Logout web abandon toàn bộ component pending của tài khoản trước khi xóa
  cookie; frontend cũng xóa reference local để lần đăng nhập sau không giữ state
  chết.
- Reading component đã giữ quota được hoàn lại đúng một lần bằng row lock;
  Listening component vốn không trừ quota nên không được cộng/trừ nhầm.
- Celery maintenance chạy mỗi 10 phút: abandon orphan quá 24 giờ, xóa source,
  job prefixes, snapshot assets rồi hard-delete DB theo batch tối đa 100.
- Không gọi MinIO bên trong transaction đánh dấu abandon; lỗi storage giữ record
  cho lần retry maintenance sau thay vì để DB báo đã sạch giả.
- Regression: platform **20/20**, frontend **75/75**, API frontend **12/12**,
  TypeScript và Next.js production build đều pass.

## OCR LC/RC và Kho đề theo Teacher — 2026-08-13

### OCR correctness hotfix

- Listening render ở 300 DPI và OCR toàn trang tại 100% resolution; JPEG render
  dùng quality 100. Normal pass Listening giữ nét glyph sạch thay vì median
  filter làm mòn ký tự nhỏ. Reading vẫn giữ scale/các guardrail cũ để tránh
  tăng CPU không cần thiết trên toàn bộ workload.
- Parser không còn phụ thuộc line-id do Tesseract tự gán: word box được nhóm
  theo vị trí thực trong từng cột. Với Part 3/4, crop phục hồi được neo bằng số
  câu, nên vẫn khôi phục được block khi marker A/D bị mất; marker chỉ là
  fallback. Một recovery Reading chỉ thay dấu câu cuối khi crop có cùng chuỗi
  từ và bằng chứng dấu câu, không tự "đoán" nội dung.
- Regression thực đo: `LC.pdf` PASS trong 34,062 giây (1--100, Part 3/4 không
  thiếu question/options); `RC.pdf` PASS trong 68,100 giây (101--200, các
  assertion text/options/stimulus/crop chọn lọc đều đúng). Đây là bằng chứng
  cho hai fixture mẫu, không phải tuyên bố OCR hoàn hảo với mọi định dạng PDF.
- OCR tiếp tục chạy bất đồng bộ/bounded qua Celery. Chất lượng cao hơn sẽ dùng
  nhiều CPU/disk tạm hơn cho Listening; không tăng concurrency worker hoặc
  connection pool. Theo dõi `ocr_progress`, scratch disk và thời gian job sau
  deploy trước khi thay các ceiling hiện hữu.

### Teacher-owned bank access

- Teacher vẫn tạo/OCR đề như ví dụ Teacher A tạo 10 đề. Thay đổi là bỏ hiển thị
  global cho Student và bỏ yêu cầu publish từng đề vào từng lớp chỉ để *nhìn*
  Kho đề: chỉ cần Student join một lớp do A sở hữu (500+, 600+ hoặc 800+) là
  toàn bộ bank của A hiện ra. `ClassAssignment` được giữ riêng cho luồng **Giao
  bài** có giới hạn attempt, lịch mở/đóng, giám sát và công bố kết quả.
- Authorization được áp dụng cho list, tag, start attempt và mọi mutation.
  Teacher không thể truy cập đề Teacher khác; Admin vẫn quản trị toàn hệ thống.
  `shared_title_key` đã namespace theo owner để hai Teacher dùng cùng title an
  toàn.
- Migration `0024_teacher_scoped_exam_bank` backfill key hiện có và thêm index
  `(user_id, status, classroom_id)` cho lookup membership. Nó không xóa dữ liệu
  và downgrade cố ý không bỏ prefix vì sau migrate title trùng giữa Teacher là
  hợp lệ.
- Đã sửa thêm idempotency cho **Giao bài cho lớp**: lần publish trùng bây giờ
  nhận đúng immutable snapshot đã có và trả `already_published`, thay vì cố
  insert lại `publication_key` duy nhất.

### Verification run locally

- `LC.pdf` golden: PASS (34,062 s); `RC.pdf` golden opt-in: PASS (68,100 s).
- Backend routing/OCR regression: 39 tests PASS; classroom integration: 4
  tests PASS; `python -m compileall -q .` và `alembic heads` PASS, head là
  `0024_teacher_scoped_exam_bank`.
- Frontend `npm run lint` và `npm run build` PASS.
- Không có benchmark 50--200 concurrent hoặc tải production trong thay đổi này;
  không dùng các kết quả correctness ở trên để xác nhận capacity.
