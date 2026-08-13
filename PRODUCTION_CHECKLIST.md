# Production checklist và runbook

## Monolith một máy chủ (PostgreSQL + MinIO nội bộ)

Stack local/server hiện chạy toàn bộ trên một Docker host: PostgreSQL, MinIO,
Redis, FastAPI, Celery, Next.js và Nginx. Cấu hình mẫu đặt trong `.env.example`;
copy sang `.env`, thay secret production và giữ file ở mode `600`.

```bash
cp .env.example .env
chmod 600 .env
docker compose config --quiet
docker compose build api frontend postgres
docker compose up -d api worker maintenance-worker scheduler frontend nginx
docker compose ps
curl -fsS http://127.0.0.1/health/ready
```

Lần đầu chạy sẽ tự migrate Alembic và khởi tạo bucket MinIO. Seed admin có thể
lặp an toàn bằng:

```bash
docker compose run --rm --no-deps api python scripts/seed_admin.py
```

Không chạy `docker compose down -v` trên máy production. Volume PostgreSQL và
MinIO chứa dữ liệu bài thi, đáp án và file assets; backup/restore phải theo
runbook bên dưới.

- `PENDING_COMPONENT_RETENTION_HOURS=24`: Hủy/Logout abandon ngay; tab crash
  được maintenance dọn sau TTL. Không đặt dưới 1 giờ để tránh xóa draft khi
  giáo viên vẫn đang chuẩn bị Reading.

## Hotfix scratch tạo đề — đã deploy 2026-08-11

- [x] API dùng named volume `api_scratch:/scratch`; `TMPDIR=/scratch` và
  `TOOL_TAO_DE_WORK_DIR=/scratch/smart-exam`.
- [x] `/tmp` API là fallback tmpfs 1 GiB; upload không spool vào đây.
- [x] `SCRATCH_MIN_FREE_BYTES=5368709120`,
  `SCRATCH_MIN_FREE_PERCENT=5`, cache mềm 2 GiB.
- [x] Nginx cho bốn upload đồng thời (ba giáo viên + một dự phòng); không tăng
  Celery worker quá concurrency 2.
- [x] Sau recreate: `/scratch` ghi được, 45 GiB trống; `/health/ready` báo
  `checks.scratch=true`; API/worker/scheduler/Nginx healthy.
- [x] Smoke object cũ của phiên desktop: asset 200 và audio Range 206; API scratch
  không cache lại media.
- [ ] Đặt alert khi readiness scratch fail, disk dùng trên 75%, job queue wait
  tăng hoặc có HTTP 507/502.
- [ ] Chạy ba create-exam workload thật trên staging để đo completion time;
  không chạy ba OCR job phá tải trên production chỉ để chứng minh config.

Kiểm tra nhanh sau mỗi deploy:

```bash
docker compose exec -T api sh -lc 'df -h /scratch /tmp; test -w /scratch'
curl -fsS -H 'Host: exam.congnhat.online' http://127.0.0.1/health/ready
docker compose logs --since=15m api worker nginx | \
  grep -Ei 'no space|507|upstream prematurely|traceback'
```

## Gate hotfix Full Test / Answer key / Giải chi tiết (2026-08-09)

- Rebuild đồng thời API và frontend; client cũ chưa gửi cờ staging component và
  vẫn có thể tái hiện hai đề tách rời.
- Smoke tạo mới một Full Test: sau Listening chưa được thấy card 100 câu trong
  Kho chung; sau Reading chỉ có đúng một card `Full Test`, 200 câu và 200 đáp án.
- Nộp một personal attempt rồi xác nhận Result hiện đáp án đúng; trước submit,
  response start vẫn tuyệt đối không chứa `correct`.
- Với Teacher, card Kho đề phải có `Import / sửa giải chi tiết`; chọn file
  DOCX/DOC/PDF, preview, Merge an toàn, finalize rồi xác nhận bộ đếm câu có giải.
- Hai record tách rời đã tạo bởi release cũ không được tự ghép theo heuristic.
  Xác định đúng cặp/owner từ production DB và backup trước khi repair hoặc tạo
  lại; hotfix chỉ bảo đảm các lượt tạo mới không phát sinh split record.

Máy đích: Intel Core i5-12400F (6 core/12 thread), 32 GB RAM, SSD 512 GB,
Linux, 1 Gbps. Production gate 300 active users; 400 là stretch. Một host/một
SSD không phải HA.

## Hotfix media/PWA/Desktop account — gate triển khai

- Rebuild cả `api` và `frontend`; restart container cũ không cập nhật source.
- Xác nhận response asset có `X-Accel-Redirect` nội bộ chứa query SigV4 và
  request ngoài qua Nginx trả 200 hoặc 206, không phải 403. MinIO buckets vẫn
  phải private; không dùng `mc anonymous set download` để chữa cháy.
- Kiểm tra ảnh Part 1, audio Range và classroom asset trên web thật bằng owner,
  student hợp lệ và token sai/anonymous.
- Sau deploy, reload thường phải nhận logo mới. `/sw.js` và
  `/manifest.webmanifest` phải có revalidation/no-store. PWA đã cài từ manifest
  thương hiệu cũ có thể cần đóng toàn bộ cửa sổ rồi mở lại; nếu OS vẫn không
  refresh metadata thì gỡ/cài lại **một lần**.
- Phát hành Tauri `0.1.5` với bundle identifier `com.toeicdoc.app` và
  account-isolated local path. Gỡ bản identifier cũ trước khi cài nếu installer
  hệ điều hành hiển thị hai ứng dụng. Login hai user lần lượt:
  user B phải có kho/history local rỗng và không được sync item của A; login lại
  A phải thấy namespace A. Dữ liệu bundle legacy phải bị xóa, không được copy
  cho user A hay user B.
- `scripts/reset_system_data.sh` reset PostgreSQL, MinIO, Redis và API memory
  cache trên server; muốn
  dọn một máy Tauri phải backup rồi xử lý app-local-data trên đúng máy đó.

## 1. Chuẩn bị host

- Cài Docker Engine + Compose plugin, chrony, curl và firewall; đặt timezone
  hệ thống UTC và xác nhận NTP synchronized.
- Copy repository vào `/opt/examify`, checkout commit/tag đã duyệt. Không deploy
  trực tiếp một worktree đang sửa.
- Cài sysctl: `install -m 0644 deploy/examify-sysctl.conf
  /etc/sysctl.d/90-examify.conf && sysctl --system`.
- Đặt `nofile=65535` cho service; unit systemd trong repository đã có
  `LimitNOFILE=65535`.
- Tạo swap khẩn cấp 2 GB, permission `0600`, bật `vm.swappiness=1`. Swap chỉ là
  phanh an toàn, không được dùng để che thiếu RAM.
- Firewall chỉ public 80/443; SSH giới hạn IP/VPN. PostgreSQL, Redis, MinIO,
  MinIO console, Prometheus và Grafana không public. Grafana mặc định bind
  `127.0.0.1`.
- Chừa ít nhất 15% SSD. Cảnh báo disk 75%, critical 85%; ngân sách ban đầu:
  PostgreSQL/WAL 70 GiB, MinIO 250 GiB, log/metrics 20 GiB.

## 2. Secret và `.env`

```bash
cp .env.example .env
chmod 0600 .env
```

Điền giá trị ngẫu nhiên khác nhau, tối thiểu 24 ký tự, không chứa placeholder:

- `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `MINIO_ROOT_PASSWORD`
- `JWT_SECRET`, `TOKEN_EXPORT_SECRET`, `ADMIN_PASSWORD`
- `PGBACKREST_CIPHER_PASS` riêng biệt và được lưu trong secret manager/offsite
- `GRAFANA_ADMIN_PASSWORD`
- `PUBLIC_BASE_URL=https://exam.congnhat.online`
- credential S3/NAS off-host: `BACKUP_S3_*`

`production-config-check` cố ý chặn startup khi thiếu/placeholder. Giữ
`TOKEN_EXPORT_SECRET` ổn định; rotate nó sẽ làm token code cũ không export lại
được. Trong production dùng `APP_PROFILE=server`; `loadtest` chỉ được dùng ở
staging cách ly.

## 3. Ngân sách tài nguyên và connection

| Service | CPU quota | RAM limit | DB pool tối đa |
|---|---:|---:|---:|
| PostgreSQL | 2.5 | 7 GB | `max_connections=80` |
| FastAPI, 4 workers | 4.0 | 4 GB | 4 + overflow 2/worker = 24 |
| MinIO | 1.0 | 2 GB | — |
| Redis AOF/noeviction | 0.5 | 512 MB | — |
| Next.js | 0.75 | 1 GB | — |
| Nginx | 0.5 | 256 MB | — |
| Maintenance worker | 0.5 | 768 MB | 2 + overflow 1 |
| Scheduler | 0.1 | 256 MB | 1 + overflow 1 |
| Monitoring/exporters | bounded | khoảng 2 GB | exporter nhỏ |
| OCR ngoài giờ thi | 2.0 | 4 GB | 2 + overflow 1 |
| OCR trong giờ thi | 0 | 0 | 0 |

API + maintenance + beat có trần lý thuyết 29 connection; có OCR là 32, chưa
tính migration/admin/exporter/backup nhưng vẫn giữ headroom dưới 80. Không tăng
worker/pool riêng lẻ; phải tính lại tổng và load test.

PostgreSQL giữ `fsync`, `synchronous_commit`, `full_page_writes` bật. Baseline
Compose: shared buffers 4 GB, effective cache 18 GB, work mem 4 MB, maintenance
work mem 512 MB, max WAL 8 GB, statement 10 giây, lock 2 giây, idle transaction
15 giây. Chỉ đổi sau khi có `EXPLAIN`/metrics từ staging.

## 4. Validation trước deploy

```bash
docker compose config --quiet
docker compose --profile monitoring config --quiet
docker compose --profile backup config --quiet
docker compose --profile restore config --quiet
docker compose -f compose.yaml -f compose.tls.yaml config --quiet
bash -n deploy/*.sh
git diff --check
```

CI Linux phải xanh: backend, migration database trắng, frontend test/typecheck,
production dependency audit (không có high vulnerability), production build,
`/quiz` <=250 KiB gzip, Docker builds, Nginx và Prometheus.
Ghi lại commit SHA và image digest dùng khi deploy.

### Gate riêng cho Desktop release

- `npm --prefix frontend run build:desktop` phải pass; route export phải có
  `/public-test/desktop-placeholder` và không được còn lỗi dynamic route.
- Windows runner phải build sidecar native, smoke trước cài, build NSIS, cài
  silent vào thư mục sạch rồi smoke lại chính sidecar/resource đã cài.
- Artifact Windows x86_64 phải build sidecar native, smoke, tạo NSIS `.exe`,
  verify Poppler/Tesseract/FFmpeg và smoke restart persistence.
- Artifact tải từ GitHub Actions phải được stage phẳng: giải nén thấy trực tiếp
  `.exe` + checksum, không chứa cây `src-tauri/target/...`.
- Smoke bắt buộc xác nhận: `/health/ready.ocr_ready`, OCR PDF, finalize exam,
  lưu attempt, restart sidecar với cùng data-dir, exam và sync intent vẫn còn.
- Không phát hành chỉ dựa trên `cargo check` Linux. Linux thiếu MSVC linker
  nên không thể chứng nhận artifact Windows.
- Kiểm tra link public copy từ desktop có origin `https://exam.congnhat.online`, không
  phải `tauri.localhost`.

## 5. TLS bootstrap lần đầu

DNS `exam.congnhat.online`/`www.exam.congnhat.online` phải trỏ đúng host và port 80 mở.

```bash
# Khởi động HTTP để phục vụ ACME webroot
sudo ./deploy/rebuild.sh
curl -f http://127.0.0.1/health

# Cấp certificate vào named volume dùng chung
docker compose -f compose.yaml -f compose.tls.yaml --profile tls run --rm \
  certbot certonly --webroot --webroot-path /var/www/certbot \
  -d exam.congnhat.online -d www.exam.congnhat.online \
  --email admin@example.com --agree-tos --no-eff-email

# Chuyển sang overlay TLS
docker compose -f compose.yaml -f compose.tls.yaml up -d --force-recreate nginx
curl -f https://exam.congnhat.online/health
```

Chỉ bỏ comment HSTS trong `deploy/nginx-tls.conf` sau ít nhất bảy ngày HTTPS ổn
định cho toàn bộ domain/subdomain. Enable renew timer sau khi test
`deploy/certbot-renew.sh` thành công.

## 6. Deploy release

Trong maintenance window:

1. Dừng OCR trước giờ thi ít nhất 15 phút: `docker compose stop worker`; kiểm
   tra queue không có job đang chạy.
2. Chạy/kiểm tra backup mới nhất và off-host mirror.
3. Checkout release đã duyệt, chạy validation ở mục 4, rồi `docker compose
   build`.
4. Chạy expand migration: `docker compose run --rm migrate`. Nếu fail, không
   start app mới và không tự downgrade dữ liệu.
5. Khởi động stack TLS: `docker compose -f compose.yaml -f compose.tls.yaml up
   -d`.
6. Kiểm tra `docker compose ps`, `/health`, `/health/ready`, login,
   start/state/sync/reload/submit/duplicate submit, teacher monitoring và audio
   Range 206.
7. Quan sát ít nhất 30 phút: 5xx, p95/p99, pool wait, active connection, lock,
   WAL archive, queue age, CPU/RAM/I/O/network/disk.

Roll back application bằng image/commit cũ nếu cần; migration `0020` là
expand-only nên app cũ không cần downgrade schema. Không rollback schema có dữ
liệu trong lúc sự cố.

## 7. Systemd và reboot

```bash
install -m 0644 deploy/systemd/*.service deploy/systemd/*.timer \
  /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now examify-tls.service
systemctl enable --now \
  examify-certbot-renew.timer \
  examify-watchdog.timer \
  examify-backup-full.timer \
  examify-backup-diff.timer \
  examify-backup-incr.timer
```

Chỉ enable một trong `examify.service` (HTTP bootstrap) và
`examify-tls.service`; hai unit có `Conflicts`. Watchdog chỉ recycle service
stateless sau ba readiness failure, không restart mù PostgreSQL/Redis/MinIO.

## 8. Monitoring và alert

```bash
docker compose --profile monitoring up -d
```

- `/internal/metrics` chỉ nằm trong Docker network; Grafana qua SSH tunnel/VPN.
- Prometheus giữ 7 ngày/5 GB.
- Alert: 5xx >0,5%; hot p95 >300 ms; DB pool >80%; connection >60;
  transaction >5 giây; CPU >70%/10 phút; RAM >80%; disk 75/85%; WAL archive
  age/failure; queue age và outbound network.
- Log JSON có request ID/route/status/latency/ID rút gọn. Không log answers,
  password, token, cookie hoặc signed media URL. Docker log rotation là
  10 MB x 5 file/container.

Trước mỗi kỳ thi, xác nhận không có alert firing, disk còn >15%, đồng hồ đúng,
WAL archive mới hơn 5 phút, Redis AOF/MinIO/Postgres healthy và OCR đã dừng.

### Backend read-only cache preflight

Backend services use a read-only root filesystem. Before starting a release,
verify the rendered Compose configuration contains `XDG_CACHE_HOME=/tmp/.cache`
for `api` and every Celery worker. `/tmp` must remain a writable bounded tmpfs.
Then verify the internal API endpoint with
`docker compose exec -T api curl -fsS http://127.0.0.1:8000/health/ready` and
confirm it reports `ocr_ready=true`; the public Nginx surface intentionally
exposes only the lightweight liveness endpoint. Do not bypass readiness,
because the same cache path is required when an OCR job starts later.

## 9. Backup và off-host mirror

Khởi tạo stanza một lần rồi kiểm tra:

```bash
deploy/pgbackrest-init.sh
docker compose exec -T -u postgres postgres sh -ec \
  'PGHOST=127.0.0.1 PGUSER="$POSTGRES_USER" PGPASSWORD="$POSTGRES_PASSWORD" pgbackrest --stanza=examify info'
```

Lịch systemd:

- full Chủ nhật;
- differential các ngày còn lại;
- incremental mỗi 6 giờ;
- WAL archive liên tục, `archive_timeout=300s`.

Bật mirror versioned không propagate delete:

```bash
docker compose --profile backup up -d minio-mirror
```

Mirror bao gồm các bucket MinIO và repo pgBackRest. Volume trên cùng SSD chỉ là
bản local, không được tính là backup. RPO PostgreSQL mục tiêu <=5 phút chỉ được
xác nhận khi WAL archive và mirror off-host được monitor thực tế.

Chạy backup thủ công trước deploy:

```bash
deploy/pgbackrest-backup.sh incr
docker compose exec -T -u postgres postgres sh -ec \
  'PGHOST=127.0.0.1 PGUSER="$POSTGRES_USER" PGPASSWORD="$POSTGRES_PASSWORD" pgbackrest --stanza=examify check'
```

## 10. Restore drill

Restore là destructive, chỉ chạy trong môi trường diễn tập/maintenance đã được
xác nhận. Nếu SSD local mất, tải repository và objects từ off-host trước:

```bash
CONFIRM_FETCH_BACKUP=YES deploy/fetch-offhost-backups.sh
CONFIRM_RESTORE=YES deploy/pgbackrest-restore.sh

# PITR tùy chọn
CONFIRM_RESTORE=YES TARGET_TIME='2026-08-08 06:30:00+00' \
  deploy/pgbackrest-restore.sh
```

Restore script cố ý để OCR worker dừng. Sau restore:

- kiểm tra random users/exams/attempts/answers và object checksum;
- so sánh row/object counts và receipt;
- chạy start/sync/submit/audio smoke test;
- ghi RPO/RTO thực tế và chỉ sau đó mới `docker compose start worker`.

Diễn tập tối thiểu hàng quý. RTO <=30 phút chỉ áp dụng crash container/OS/reboot
đã đo; hỏng SSD vật lý có thể mất nhiều giờ và single-host không đảm bảo HA.

## 11. Go-live gate

- Windows release workflow, migration và production smoke đều xanh.
- Load test k6/300 VU đã được archive theo yêu cầu bàn giao; không chạy trong
  maintenance này. Số liệu lịch sử nằm trong `BENCHMARK.md`.
- Không dùng số liệu lịch sử để tuyên bố capacity mới; benchmark lại chỉ khi có
  yêu cầu và staging/load generator riêng.
- Offline/reload/pagehide/two-tab/duplicate submit không mất answer.
- Restore drill off-host gần nhất pass; WAL archive/mirror có alert.
- CPU/RAM/DB pool/disk/network còn headroom, không lock/runaway query.
- Có người trực, rollback/communication runbook và không chạy OCR trong peak.

Nếu thiếu bất kỳ điều kiện dữ liệu nào ở trên, không go-live dù latency trung
bình đẹp.

## 12. Rollout Kho chung / Giải chi tiết / data epoch

- Build image có migration `0021_shared_bank`, `python-docx`, LibreOffice Writer
  và worker queue `solutions.process_import`; không tăng OCR concurrency trong
  giờ thi.
- Trước maintenance: chạy pgBackRest backup + `check`, mirror MinIO hoàn tất,
  restore drill trên staging và ghi `KEEP_ADMIN_ID`. Không dùng snapshot chưa
  verify làm lý do để chạy reset.
- Chạy `alembic upgrade head` trước reset. Migration xử lý hash snapshot legacy
  trùng mà không xóa assignment đang tham chiếu.
- Chạy kiểm tra không ghi dữ liệu:

```bash
KEEP_ADMIN_ID='<ADMIN_UUID_IF_NEEDED>' scripts/reset_system_data.sh --dry-run
```

- Khi số Admin/policy/object đúng và maintenance window đã bắt đầu, chạy đúng
  một lần:

```bash
BACKUP_VERIFIED=YES CONFIRM_RESET=YES \
KEEP_ADMIN_ID='<ADMIN_UUID_IF_NEEDED>' \
scripts/reset_system_data.sh --execute
```

Script dừng worker, giữ nguyên Admin ID/email/password hash và chỉ terms/privacy,
xóa business rows/MinIO/Redis, rotate `data_epoch`, restart API rồi mới bật lại
worker. Tuyệt đối không gắn script vào container startup. Nếu reset dở dang,
giữ maintenance và điều tra; rollback dữ liệu là restore PostgreSQL + MinIO từ
backup đã verify, không chạy `alembic downgrade` để giả lập khôi phục dữ liệu.

- Sau reset: Admin login lại (device/session mới), tạo một Teacher và Student
  smoke; Teacher tạo Tag/đề, Teacher thứ hai sửa/gán lớp, Student list/start/save/
  submit/xem solution; kiểm tra anonymous public không có solution.
- Kiểm tra Desktop epoch cũ được chuyển vào `quarantine/` read-only và sync cũ
  nhận 409; browser xóa exam pack/draft/sync queue/media cache nhưng vẫn login
  được. Không xóa static cache không liên quan.
- Kiểm tra source PDF/edit session sau khi job OCR gốc bị purge; Range audio trả
  206 (hoặc 200 object nhỏ) trực tiếp qua MinIO/Nginx, API không proxy body.
- Chỉ mở hệ thống sau khi backend/frontend/build và smoke readiness xanh. Ma trận
  k6 đã được archive, không còn harness active trong repository.
## All-in-one Compose deployment — 2026-08-12

Compose hiện chạy PostgreSQL, MinIO, Redis, migrate, API, extraction worker,
maintenance worker, scheduler, frontend và Nginx trên cùng một Docker network.
Tesseract OCR chạy local trong backend image; không cấu hình PaddleOCR/OCR host
ngoài.

Các biến cần kiểm tra trong `.env`:

```dotenv
DATABASE_URL=postgresql+psycopg://examify_app:PASSWORD@postgres:5432/examify
MIGRATION_DATABASE_URL=postgresql+psycopg://examify_migrate:PASSWORD@postgres:5432/examify
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=...
MINIO_SECRET_KEY=...
MINIO_SECURE=false
OCR_ENABLED=true
TESSERACT_LANG=eng
TESSERACT_TIMEOUT_SECONDS=90
OCR_ENGINE_POOL_SIZE=2
ADMIN_EMAIL=...
ADMIN_PASSWORD=...
```

Quy trình trên host có quyền Docker:

```bash
cd /home/toeicdoc/EXAMIFY
sudo docker compose config --quiet
sudo ./deploy/rebuild.sh
sudo docker compose ps
sudo docker compose logs --tail=100 migrate api worker nginx
curl -fsS http://127.0.0.1/health
```

Script không chạy `down` và không xóa volume. Nó build một named backend image,
gắn revision, kiểm tra import audio trước/sau deploy và từ chối thành công nếu
API/worker không dùng cùng image ID. `migrate` tự chạy Alembic rồi bootstrap
object storage; thao tác này idempotent. Sau deploy phải xác nhận trong container:

```bash
sudo docker compose exec api curl -fsS http://127.0.0.1:8000/health/ready
sudo docker compose exec api curl -fsS http://10.10.10.4:8000/health
```

Nginx đang ký/phát private MinIO bằng host LAN cụ thể. Nếu đổi IP/port MinIO,
phải đổi cả `MINIO_ENDPOINT` và upstream/`Host` trong hai file Nginx; nếu lệch,
SigV4 media URL sẽ trả 403.

Backup PostgreSQL/MinIO local trong Compose không còn bảo vệ dependency ngoài.
Phải cấu hình backup/retention/restore drill tại chính hai máy ngoài trước khi
xóa volume rollback cũ.

## PgBouncer/PostgreSQL recovery và pooling — 2026-08-11

Ứng dụng chỉ kết nối qua `10.10.10.3:5432`; PostgreSQL direct dùng port `5433`
cho migration/diagnostic. Không đổi `DATABASE_URL` của API sang port direct để
che giấu lỗi pooler.

Trên VM PostgreSQL/PgBouncer phải bảo đảm database mapping là:

```ini
examify = host=10.10.10.3 port=5433 dbname=examify user=examify_app pool_size=30 reserve_pool_size=5 max_db_connections=50 pool_mode=transaction
```

PgBouncer phải dùng `auth_type=scram-sha-256`, `max_prepared_statements=0`,
`max_client_conn=600`, `query_wait_timeout=5`, `transaction_timeout=15` và
`idle_transaction_timeout=60`. File `userlist.txt` phải chứa đúng SCRAM
verifier của role `examify_app`; không ghi password thật vào repository.

Sau khi đặt file với quyền `0600`, reload an toàn:

```bash
psql -h 10.10.10.3 -p 5432 -U pgbouncer_admin -d pgbouncer -c 'RELOAD'
psql -h 10.10.10.3 -p 5432 -U pgbouncer_admin -d pgbouncer -c 'RECONNECT "examify"'
psql -h 10.10.10.3 -p 5432 -U pgbouncer_admin -d pgbouncer -c 'SHOW POOLS'
```

PostgreSQL đã được chuẩn bị với `max_connections=300` và
`shared_preload_libraries=pg_stat_statements`; cả hai cần restart PostgreSQL
để hết `pending_restart=true`. Sau restart xác nhận:

```bash
psql -h 10.10.10.3 -p 5433 -U postgres -d examify -c \
  "select current_setting('max_connections'), current_setting('shared_preload_libraries')"
curl -fsS https://exam.congnhat.online/health/ready
```

Không mở 300 connection backend thực tế: `300` là trần PostgreSQL, còn
PgBouncer giữ backend pool tối đa `50` để bảo vệ CPU/RAM.

## Desktop OCR 0.1.6 release gate

- [ ] Build đúng native target Windows x86_64 cho `.exe`/NSIS; không phát hành
  binary cross-arch.
- [ ] Packaged `/health/ready` phải có `profile=desktop`,
  `processing_location=LOCAL_EDGE`, `ocr_local=true`, `ocr_remote=false` và
  provider không chứa `remote`.
- [ ] Windows phải báo OCR local với bundled Tesseract và có FFmpeg/FFprobe
  trước khi sidecar chuyển sang trạng thái ready.
- [ ] Chạy `scripts/smoke-sidecar.py --pdf-pages 8 --max-ocr-seconds 90` trên
  sidecar bên trong app/installer, không chỉ binary staging.
- [ ] Trên máy test thật chạy `scripts/benchmark_ocr.py <pdf> --exam-type ...
  --max-seconds 300 --min-questions 100` và lưu JSON cùng `sidecar.log`.
- [ ] Không đặt `OCR_PAGE_WORKERS=1` trong môi trường runtime release. Với máy
  từ 4 logical CPU, readiness phải báo page worker 2 và CPU pool 2 nếu provider
  là CPU.
# FFmpeg auto-cut Audio Full

- [ ] Image `api`/`worker` có cả `ffmpeg` và `ffprobe`; kiểm tra bằng
  `docker compose exec -T worker sh -lc 'ffmpeg -version && ffprobe -version'`.
- [ ] Upload một Audio Full chuẩn, Review hiển thị 54 audio câu/nhóm (+ direction
  Part 1) và metadata `audio_autocut.status=ready`.
- [ ] Xác minh thủ công ít nhất nhóm 32–34 và 71–73: audio kết thúc sau khi đọc
  đủ ba prompt, sau đó Quiz mới chuyển nhóm.
- [ ] Nếu `audio_autocut_fallback` xuất hiện, giữ file Full và xem
  `raw_wave_count`, `alignment_confidence`, `skipped_waves`; không bỏ ngưỡng
  confidence ở production để ép kết quả.
## Desktop Tesseract OCR và đồng bộ web/app

- [ ] Windows native workflow pass sidecar với bundled Tesseract, Poppler và
  FFmpeg/FFprobe; provider readiness phải là local, không phải remote.
- [ ] Kiểm tra `/health/ready` trên máy thật: `ocr_local=true`, `ocr_remote=false`,
  provider/pool/thread đúng kiến trúc.
- [ ] Không đóng gói `onnxruntime`, `onnxruntime-gpu` hoặc PaddleOCR trong
  artifact Tesseract.
- [ ] Test offline create -> online sync -> web thấy; web edit/delete -> Desktop
  online reconcile không hiện cache cũ.
- [ ] Test hai phía cùng sửa: API trả `exam_revision_conflict`, local hiển thị
  conflict và không tự retry upload.
- [ ] Backup/quarantine Desktop được giữ tại app-data; không dọn tự động trước
  khi chính sách retention/recovery được xác nhận.

## Parallel audio và local OCR

- [ ] Desktop/local Tesseract OCR chạy song song với audio trong cùng job
  (không quay lại luồng audio -> OCR tuần tự).
- [ ] Job Listening hiển thị `processing_phase=audio_ocr` cùng hai progress bar.
- [ ] Xác nhận Celery concurrency, `OCR_PAGE_WORKERS` và
  `OCR_ENGINE_POOL_SIZE`; không tăng các ceiling này khi chưa benchmark host.
- [ ] Failure test: audio fail/OCR fail đều đưa job về `failed`, không kẹt ở
  `queued`, `audio` hoặc `audio_ocr`.
- [ ] Sau mỗi image deploy, chạy trong worker:
  `python -c 'import audio_processing, toeic_audio_cutter, pytesseract'`, xác nhận
  `PYTHONPATH=/app`, command dùng `python -m celery`, và log không còn
  `No module named audio_processing`.
- [ ] Chạy một job thật bằng PDF + Audio Full, poll đến `review`/`failed`; HTTP
  `202` chỉ chứng minh job đã được xếp hàng.

## Release gate: OCR LC/RC và Kho đề theo Teacher (2026-08-13)

- [ ] Đã backup PostgreSQL trước release. Không chạy downgrade `0024` sau khi
  production đã cho phép hai Teacher dùng cùng title.
- [ ] Với `.env` production hợp lệ, build và migrate theo thứ tự:

  ```bash
  docker compose up -d postgres minio redis
  docker compose build api frontend nginx
  docker compose run --rm migrate
  docker compose up -d --force-recreate api worker frontend nginx
  ```

- [ ] Xác nhận migration head là `0024_teacher_scoped_exam_bank` và health
  readiness trả 200:

  ```bash
  docker compose exec -T api alembic current
  curl -fsS https://YOUR_HOST/health/ready
  ```

- [ ] Kiểm thử quyền bằng tài khoản thật/staging: Student chưa join không thấy
  kho của Teacher A; join một trong các lớp A (500+/600+/800+) thì thấy tất cả
  đề A; join lớp B không làm lộ đề Teacher khác. Teacher B không sửa/xóa/publish
  đề A. Manual **Giao bài cho lớp** vẫn hoạt động riêng.
- [ ] Chạy fixture `LC.pdf` và `RC.pdf` bằng worker/host production hoặc môi
  trường staging tương đương; kiểm tra Part 3/4 đủ text/A--D, job không có
  warning Review và theo dõi CPU, scratch disk, duration. Không hạ
  `OCR_LISTENING_PAGE_SCALE` dưới `1.0` khi chưa có golden thay thế.
- [ ] Sau deploy, giám sát Celery queue, `ocr_progress`, free scratch disk và
  PostgreSQL error/latency trong giờ đầu. Không tăng worker/page-worker pool chỉ
  vì OCR job dài hơn trên fixture chất lượng cao.
