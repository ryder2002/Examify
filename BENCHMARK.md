# Benchmark — Examify

## Final handover cleanup — 2026-08-11

Theo yêu cầu bàn giao, không chạy thêm k6 hoặc tạo thêm 300 user. PostgreSQL đã
được làm sạch và chỉ giữ lại 1 admin; attempts/answers/devices đều bằng 0.
MinIO giữ 5 bucket rỗng, tổng object bằng 0; Redis cache đã flush. API đã khôi
phục về profile `server` và `/health/ready` trả HTTP 200.

Chi tiết xem tại [HANDOVER_REPORT.md](HANDOVER_REPORT.md). Các số liệu bên dưới
là lịch sử của những run trước cleanup, không phải chứng nhận capacity hiện tại.

## Trạng thái

Đã chạy controlled load test ngày `2026-08-11` trên production. Kết quả bên
dưới là benchmark thật của run `handover-msoho9lz`; đây **không phải** kết quả
đạt SLO.

## Post-implementation validation — 2026-08-11

Đã triển khai migration/index/API/cache/MinIO proxy/OCR throttling trên image
mới và recreate local stack. Kết quả smoke test sau triển khai:

| Kiểm tra | Kết quả |
| --- | --- |
| API live health | HTTP 200 |
| API readiness | HTTP 200 — PostgreSQL, MinIO, Redis và OCR đều ready |
| Requested PostgreSQL DSN | PASS qua `10.10.10.3:5432`; user `postgres`, database `toeic-doc` |
| Application PostgreSQL DSN | PASS qua `10.10.10.3:5432`; user `toeicdoc_app` |
| PostgreSQL effective limit | `max_connections=300`; `pg_stat_statements` loaded |
| PgBouncer backend pool | PASS; `toeic-doc` mapped to PostgreSQL direct `5433`, backend servers idle |
| PgBouncer safety limits | SCRAM, transaction pooling, max prepared statements `0`, DB cap `50` |
| MinIO readiness | PASS |
| Redis readiness | PASS |
| PaddleOCR readiness | PASS; app semaphore 1 request/job |
| Alembic | `0023_performance_hot_path` (head) |
| Runtime DB role | `toeicdoc_app` |
| Migration DB role | `toeicdoc_migrate` |
| API pool | 5 connections + 1 overflow |
| Manifest single-flight | 20 concurrent callers → 1 build |

Đã SSH vào ba VM và triển khai phần cấu hình cần thiết: PgBouncer trên
`10.10.10.3` được recreate với mapping backend port `5433`, SCRAM/auth-query,
transaction pooling và giới hạn pool; MinIO trên `10.10.10.2` được xác nhận
healthy, không bị Docker CPU/memory cap; PaddleOCR trên `10.10.10.4` trả HTTP
200, dùng engine 9-core và đã đặt restart policy `unless-stopped`.

Ma trận load test 50/100/150/200 bên dưới chưa được chạy lại sau lần sửa
PgBouncer. Các số liệu 300-VU ở phần lịch sử vẫn là run trước đó và không được
dùng làm kết quả nghiệm thu của cấu hình mới.

Target được kiểm tra là `https://exam.congnhat.online`.

## Cách chạy trực tiếp trên production

Trước khi chạy, production trả:

```json
{"status":"ready","profile":"server","postgres":true,"minio":true,"redis":true}
```

Harness hiện tại có ba safety gate cố ý chặn production:

- `backend/scripts/build_loadtest_fixture.py` chỉ tạo fixture khi
  `APP_PROFILE=loadtest`.
- `load-tests/k6.js` từ chối chạy nếu `/health/ready` không trả
  `profile=loadtest`.
- `load-tests/verify_answers.py` cũng từ chối xác minh ngoài profile
  `loadtest`.

Theo yêu cầu bàn giao, API được recreate tạm thời với `APP_PROFILE=loadtest` để
harness hiện hữu không bị bypass. Sau khi chạy xong API đã được recreate lại
với `APP_PROFILE=server`; readiness cuối cùng trả `profile=server` và các
dependency đều healthy. Mỗi lần recreate có khoảng hai probe HTTP 502 trong
thời gian API khởi động; không có 502 trong các request load-test.

## DB inventory (read-only)

| Hạng mục | Kết quả |
| --- | ---: |
| Admin | 1 |
| User thường | 1 |
| Teacher | 4 |
| Tổng exam | 27 |
| Teacher-shared exam ready, 200 câu/200 answer key | 8 |
| Student test account được tạo | 300 |
| Device được tạo | 300 |
| Load-test attempt được tạo | 300 |
| Answer row được tạo | 60.000 |
| Practice attempt được tạo | 300 |
| Practice answer row được ghi thêm | 600 |

Một dataset hợp lệ để clone sang staging là `TOEIC Full Test 10`, exam id
`f256ec3b-3f4d-41ef-9370-37dffcb38e2d`. Dataset này được dùng cho run
`handover-msoho9lz`.

Các account test có email dạng
`handover-msoho9lz-0001@loadtest.invalid` …
`handover-msoho9lz-0300@loadtest.invalid`. Fixture chứa token/password được
giữ local tại `/tmp/toeicdoc-loadtest-1786442389415/participants.json`, mode
`0600`, không commit vào repository.

## Production preflight baseline

Các số dưới đây là 5 request tuần tự cho mỗi endpoint, qua Cloudflare/Nginx;
chúng không đại diện cho concurrent capacity.

| Endpoint | p50 | p95* | HTTP |
| --- | ---: | ---: | --- |
| `/health` | 138 ms | 317 ms | 200/200 |
| `/health/ready` | 247 ms | 334 ms | 200/200 |
| `/` | 138 ms | 324 ms | 200/200 |

`p95*` với chỉ 5 mẫu lấy mẫu lớn nhất, chỉ dùng làm smoke baseline.

Readiness báo scratch còn khoảng `43.9 GB` (`54%`), PostgreSQL/MinIO/Redis
đều healthy. Host baseline: load average `0.16/0.10/0.12`, RAM available khoảng
`12 GiB / 15 GiB`, swap đã dùng `0`, root disk dùng `44%`.

Container baseline khi không có load đáng kể:

| Service | CPU | RAM |
| --- | ---: | ---: |
| API | 3.25% | 617 MiB / 4 GiB |
| Worker | 0.15% | 533 MiB / 2 GiB |
| Frontend | 3.25% | 46 MiB / 1 GiB |
| Redis | 3.77% | 15 MiB / 512 MiB |

## Capacity benchmark matrix

`NOT RUN` là trạng thái có chủ ý, không phải số 0.

| Concurrent Users | RPS | p50 | p95 | p99 | Error Rate | CPU | RAM |
| ---------------: | --: | --: | --: | --: | ---------: | --: | --: |
| 50 | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| 100 | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| 150 | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| 200 | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |

### Actual 300-VU runs

CPU là CPU của API container, có giới hạn 4 vCPU; RAM là memory API container.
RPS là tốc độ iterations/request của k6 trong scenario spike.

| Workload | Concurrent Users | RPS | p50 | p95 | p99 | Error Rate | API CPU max | API RAM max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Full 200-answer snapshot | 300 | 58.3 | 2.74 s | 3.52 s | 3.65 s | 0% | 406.83% | 626.8 MiB |
| Peak submit + grading | 300 | 41.8 | 4.75 s | 6.76 s | 6.93 s | 0% | 409.24% | 640.5 MiB |

`p50/p95/p99` của snapshot lấy từ
`http_req_duration{workload:api}`. Peak submit lấy từ metric chuyên biệt
`submit_duration`; HTTP response p95 của submit là `5.84 s`.

### Kết quả k6

| Scenario | Iterations | HTTP failures | Checks | Threshold |
| --- | ---: | ---: | ---: | --- |
| Full snapshot | 300/300 | 0/301 | 300/300 | FAIL latency SLO |
| Peak submit | 300/300 | 0/301 | 300/300 | FAIL submit latency SLO |

Full snapshot ghi 60.000 đáp án trong cùng thời điểm. Peak submit tạo 300
durable receipt; verifier DB xác nhận 60.000/60.000 answer rows, 300/300
receipt, mỗi attempt đủ 200 câu và `answer_mismatches=0`.

## Key, login và làm đề luyện tập — 2026-08-11

Đã tạo group key `keyrun-msoi6jx6` gồm đủ 300 key, bind vào 300 Student test
account và 300 web activation device. Tất cả key đang ở trạng thái `redeemed`
vì đã được dùng để bind account; plaintext key chỉ được giữ local trong file
mode `0600`, không commit vào repository. Một key đã được redeem trực tiếp qua
`https://exam.congnhat.online` và trả `next=login`, `registration_required=false`.

| Scenario | VUs | Kết quả | p50 | p95 | p99 | Error rate |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| Login đồng thời, không spread | 300 | 130/300 thành công; pool timeout | 14.60 s | 19.83 s | 20.67 s | 56.47% |
| Login spread trong 60 s | 300 | 300/300 thành công | 260.57 ms | 885.76 ms | 1.05 s | 0% |
| Start practice attempt đồng thời | 300 | 300/300 thành công | 5.13 s | 7.10 s | 7.34 s | 0% |
| Practice next + autosave đồng thời | 300 | 300/300; 1.201 request | 1.08 s | 3.46 s | 3.71 s | 0% |

Luồng practice đã thực sự chạy qua `GET exam → sync câu 1 → sync câu 2 →
GET state` cho toàn bộ 300 account, nên có thao tác next câu và ghi đáp án đồng
thời. Có 600/600 sync write thành công, `practice_data_mismatch=0`; DB xác nhận
300 attempt ở trạng thái `in_progress`, revision `2`, mỗi attempt có đúng 2
answer row.

Login 300 request cùng một thời điểm làm lộ bottleneck connection pool:
`QueuePool limit of size 4 overflow 2 reached`, timeout 3 giây. Đây là lỗi
capacity thật của hệ thống, không phải lỗi key hoặc device. Khi login được spread
trong 60 giây thì không có lỗi, nhưng p95/p99 vẫn vượt target API.

### Peak submission

| Scenario | VUs | Spread | Result |
| --- | ---: | ---: | --- |
| Submit đồng thời | 200 | 0–1 s | NOT RUN |
| Submit đồng thời | 200 | 0–10 s | NOT RUN |
| Autosave delta spike | 200 | gần đồng thời | NOT RUN |
| Listening Range | 200 | steady | NOT RUN |

Không có số liệu p95/p99, error rate, CPU/RAM, pool wait hoặc database lock để
báo cáo cho các scenario trên.

## Cách chạy certification hợp lệ

1. Clone PostgreSQL/MinIO và image production sang staging riêng, giữ cùng
   dataset 200 câu và media thật.
2. Đặt `APP_PROFILE=loadtest`, `ACCESS_TOKEN_MINUTES>=180`, bật monitoring,
   dùng load generator riêng host.
3. Tạo fixture bằng `backend/scripts/build_loadtest_fixture.py` với prefix mới
   cho từng scenario; fixture không được commit.
4. Chạy matrix trong `LOAD_TEST.md`: 50/100/150/200, peak submit, autosave,
   listening và mixed; sau mỗi lượt chạy `load-tests/verify_answers.py`.
5. Chỉ sau khi 200 VU đạt zero mismatch và còn headroom mới chạy 300 VU; 400 VU
   là stretch test.

Lệnh mẫu:

```bash
docker compose exec -T api python scripts/build_loadtest_fixture.py \
  --exam-id 'f256ec3b-3f4d-41ef-9370-37dffcb38e2d' \
  --users 300 --prefix 'run-<unique>' \
  --output /tmp/participants.json

cd load-tests
k6 run -e BASE_URL='https://<staging-host>' \
  -e FIXTURE_PATH='./participants.json' \
  -e MODE=mixed -e VUS=200 k6.js
```

## Kết luận hiện tại

`exam.congnhat.online` đã xử lý được 300 request full-snapshot và 300 submit đồng thời
với error rate 0% và không mất/sai đáp án trong run này. Tuy nhiên hệ thống
không đạt latency target hiện tại: autosave full snapshot khoảng 2,7–3,5 giây
và peak submit khoảng 4,8–6,9 giây; API container chạm gần 4 vCPU.

Do chưa chạy các mốc 50/100/150/200 nên chưa thể nội suy safe capacity ở các
mốc đó. 300 VU là mức đã test thành công về data integrity, bao gồm practice
next đồng thời, nhưng chưa đạt SLO về latency; login đồng thời còn bị pool
exhaustion. 300 key, 300 account, 300 practice attempt và dữ liệu answer vẫn
được giữ lại để kiểm tra tiếp; cần xóa theo đúng prefix/group sau khi đối tác
xác nhận không cần dữ liệu nữa.
