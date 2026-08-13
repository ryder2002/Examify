# ROLE

Bạn là một Senior/Staff Full-Stack Engineer + Performance Engineer + DevOps Engineer chuyên tối ưu hệ thống web có lượng truy cập đồng thời cao.

Bạn đang làm việc trực tiếp trên repository hiện tại của tôi.

## MỤC TIÊU HỆ THỐNG

Đây là webapp cho học viên làm đề thi online.

Mục tiêu production:

* Tổng số tài khoản: khoảng 300–400 học viên
* Peak concurrent users: khoảng 200 người đang active/làm bài cùng lúc
* Backend sử dụng PostgreSQL
* Object/file storage sử dụng MinIO
* Server Linux
* Target server:

  * 8 CPU cores
  * 16 GB RAM
  * 100 GB SSD SATA
* Hệ thống phải hoạt động ổn định khi có khoảng 200 concurrent users.
* Không được thiết kế theo kiểu "chạy được khi ít user nhưng nghẽn khi tất cả cùng vào".
* Ưu tiên reliability, performance, data integrity và khả năng mở rộng.

# QUY TẮC QUAN TRỌNG

1. KHÔNG được đoán kiến trúc của project.
2. Trước tiên phải đọc và hiểu toàn bộ repository.
3. Không được tự ý thay đổi framework/language/database/storage nếu hiện tại chúng đã phù hợp.
4. Không rewrite toàn bộ project chỉ để "cho đẹp".
5. Ưu tiên sửa bottleneck thực tế.
6. Không tối ưu mù quáng.
7. Mọi thay đổi performance phải có lý do kỹ thuật rõ ràng.
8. Không được đánh đổi tính chính xác của dữ liệu bài thi để lấy performance.
9. Không được làm mất đáp án của học viên.
10. Không được làm thay đổi behavior nghiệp vụ hiện tại nếu không cần thiết.
11. Không được thêm Redis, Kafka, RabbitMQ, Kubernetes, microservices... chỉ vì "best practice" nếu chưa chứng minh là cần.
12. Kiến trúc phải phù hợp với một server 8 CPU / 16 GB RAM.
13. Không tạo ra quá nhiều background worker làm cạn RAM/CPU.
14. Không để một request có thể tạo ra số lượng database queries không giới hạn.
15. Không để client có thể spam API gây quá tải server.

---

# PHASE 1 — AUDIT TOÀN BỘ PROJECT

Trước khi sửa code, hãy inspect:

* project structure
* package.json / requirements / go.mod / composer.json hoặc tương ứng
* frontend architecture
* backend architecture
* API routes
* authentication
* authorization
* PostgreSQL schema
* migrations
* indexes
* ORM/query layer
* transaction handling
* connection pooling
* MinIO integration
* file upload/download
* caching
* logging
* error handling
* background jobs
* cron jobs
* Docker configuration nếu có
* Nginx/reverse proxy nếu có
* environment variables
* production configuration
* build configuration
* frontend bundle
* API payload sizes
* polling
* WebSocket/SSE nếu có
* autosave logic
* exam submission logic
* answer persistence
* result calculation
* image/audio loading
* PDF handling

Đặc biệt tìm:

* N+1 queries
* SELECT *
* query không có index
* query scan toàn bảng
* duplicate queries
* unnecessary joins
* unnecessary API calls
* API gọi lặp
* polling quá thường xuyên
* database write mỗi lần click
* synchronous operations không cần thiết
* transaction quá dài
* transaction không cần thiết
* connection leak
* connection pool quá lớn
* memory leak
* CPU-heavy operations
* synchronous file processing
* duplicate file downloads
* unnecessary MinIO requests
* large JSON responses
* huge frontend bundles
* unnecessary re-render
* unnecessary database round trips
* sequential API requests có thể chạy song song
* expensive serialization/deserialization
* excessive logging
* unbounded logs
* unbounded queues
* unbounded memory usage

Sau khi audit, tạo một file:

`PERFORMANCE_AUDIT.md`

File này phải ghi rõ:

1. Kiến trúc hiện tại
2. Các bottleneck phát hiện được
3. Mức độ nghiêm trọng: CRITICAL / HIGH / MEDIUM / LOW
4. Nguyên nhân
5. Cách sửa
6. Expected impact
7. Những phần không cần sửa

KHÔNG sửa code trước khi hoàn thành audit.

---

# PHASE 2 — DATABASE PERFORMANCE

PostgreSQL là thành phần cực kỳ quan trọng.

## Connection Pool

Thiết kế connection pooling phù hợp với server 16 GB RAM.

Không được tạo một PostgreSQL connection cho mỗi user.

Phải đảm bảo:

200 concurrent users

→ application connection pool

→ PostgreSQL với số connection hợp lý.

Nếu project phù hợp, cân nhắc PgBouncer.

Không đặt `max_connections` quá cao chỉ để phục vụ concurrent users.

Phải kiểm tra:

* pool size
* max pool size
* idle timeout
* connection timeout
* connection leak
* transaction timeout

## Query Optimization

Audit toàn bộ query quan trọng.

Đặc biệt:

* login
* lấy exam
* lấy questions
* lấy answer
* save answer
* submit exam
* calculate result
* history
* dashboard
* admin pages

Không dùng:

`SELECT *`

nếu không cần thiết.

Chỉ select các column cần thiết.

Kiểm tra EXPLAIN / EXPLAIN ANALYZE cho các query quan trọng.

Tạo index đúng theo workload.

Đặc biệt xem xét index cho:

* user_id
* exam_id
* question_id
* attempt_id
* created_at
* updated_at
* status
* composite indexes thường xuyên dùng trong WHERE + ORDER BY

Nhưng KHÔNG tạo index tràn lan.

Mỗi index phải có lý do.

## N+1

Tìm và loại bỏ toàn bộ N+1 queries.

Ví dụ:

Không được:

request → lấy 100 questions → mỗi question query thêm 1 lần.

Phải batch/eager load/join hợp lý.

## Transactions

Transaction phải:

* ngắn
* rõ ràng
* atomic khi cần
* không giữ transaction trong lúc gọi MinIO hoặc external service
* không giữ transaction trong lúc xử lý file
* không chứa các operation chậm không cần thiết

---

# PHASE 3 — EXAM ANSWER SYSTEM

Đây là phần QUAN TRỌNG NHẤT.

Hệ thống phải chịu được trường hợp:

200 học viên

→ cùng lúc làm bài

→ cùng lúc chuyển câu

→ cùng lúc autosave

→ cuối giờ cùng lúc submit.

Không được thiết kế autosave theo kiểu:

Mỗi click:

Browser
→ API
→ PostgreSQL UPDATE
→ response

nếu điều đó tạo ra quá nhiều database writes.

Thiết kế autosave hiệu quả hơn.

Frontend có thể giữ answer state trong memory/state management.

Autosave theo batch/debounce/throttle hợp lý.

Ví dụ:

* debounce save
* periodic save
* save khi chuyển section
* save trước khi submit
* batch nhiều answers trong một request

Nhưng PHẢI đảm bảo:

* không mất đáp án
* refresh browser không làm mất dữ liệu đã lưu
* mạng chập chờn không làm mất answer
* request duplicate không làm duplicate answer
* request retry phải idempotent
* submit nhiều lần không làm corrupt result

## Idempotency

Các API như:

* save answer
* submit exam
* finish attempt

phải có cơ chế chống duplicate request.

Ví dụ:

* attempt ID
* answer/question unique constraint
* idempotency key nếu phù hợp
* transaction

Không được dựa vào frontend để đảm bảo uniqueness.

Database phải bảo vệ integrity.

---

# PHASE 4 — CONCURRENT EXAM SUBMISSION

Hãy đặc biệt test tình huống:

200 users submit trong khoảng 1–10 giây.

Không được để:

200 requests

→ 200 heavy result calculations

→ 200 expensive database queries

→ database nghẽn.

Tối ưu result calculation.

Nếu có thể:

* batch query
* prefetch dữ liệu cần thiết
* giảm round trips
* tính toán ở application layer nếu phù hợp
* tránh query từng câu
* tránh query từng answer
* tránh transaction quá dài

Nhưng tuyệt đối không làm sai điểm.

Điểm thi phải deterministic và chính xác.

---

# PHASE 5 — API PERFORMANCE

Audit tất cả API.

Mỗi endpoint phải xem xét:

* authentication cost
* authorization cost
* DB query count
* response size
* serialization
* caching possibility
* rate limiting
* timeout
* error handling

Tránh:

Frontend → API A → API B → API C → API D

nếu có thể gom thành API phù hợp.

Nhưng cũng không tạo một API trả về toàn bộ database.

## Pagination

Các endpoint list phải có pagination.

Không được trả về hàng nghìn record nếu frontend chỉ cần 20–50 record.

## Payload

Giảm:

* JSON size
* duplicate data
* unnecessary fields
* unnecessary nested objects

---

# PHASE 6 — MINIO / FILE STORAGE

MinIO dùng cho:

* audio
* image
* PDF
* exam assets
* uploaded files

Không lưu binary lớn trong PostgreSQL.

Database chỉ nên giữ metadata/object key.

Thiết kế file access hiệu quả.

Nếu phù hợp:

* presigned URLs
* browser direct download từ MinIO
* HTTP caching
* Cache-Control
* ETag
* Range requests cho audio/video
* tránh proxy file qua application server nếu không cần

Không để:

Browser
→ Backend
→ Backend tải file từ MinIO
→ Backend trả file lại Browser

nếu có thể cho browser truy cập object storage trực tiếp một cách an toàn.

Đặc biệt chú ý Listening.

200 users có thể cùng tải audio.

---

# PHASE 7 — FRONTEND PERFORMANCE

Audit frontend.

Tối ưu:

* initial bundle
* code splitting
* lazy loading
* route splitting
* image optimization
* audio loading
* caching
* unnecessary React/Vue/etc re-render
* unnecessary API requests
* duplicate requests
* request waterfalls
* debounce/throttle
* state management
* local state
* browser cache

Exam page phải cực kỳ nhẹ.

Khi học viên chuyển câu:

KHÔNG được gọi API không cần thiết.

Nếu question data đã tải:

chuyển câu phải gần như instantaneous ở client.

Không tải lại toàn bộ exam mỗi lần chuyển câu.

---

# PHASE 8 — CACHING

Xác định dữ liệu nào có thể cache.

Ví dụ:

* exam metadata
* question content
* static assets
* images
* audio
* configuration

Không cache dữ liệu cá nhân/answer một cách sai lệch.

Nếu chưa cần Redis thì không được thêm Redis chỉ để cache.

Có thể sử dụng:

* browser cache
* HTTP Cache-Control
* Nginx cache
* application memory cache

nhưng phải đảm bảo memory không tăng không giới hạn.

---

# PHASE 9 — SECURITY + LOAD PROTECTION

Không chỉ tối ưu performance.

Phải bảo vệ server khỏi request abuse.

Implement hợp lý:

* rate limiting
* request body limits
* upload limits
* timeout
* authentication throttling
* login protection
* pagination limits
* maximum query range
* maximum batch answer size

Không để user gửi:

10 MB JSON answer

hoặc

100.000 questions

trong một request.

API phải validate input.

Không expose:

* database credentials
* MinIO credentials
* internal paths
* sensitive environment variables

Frontend chỉ được nhận public configuration cần thiết.

---

# PHASE 10 — NGINX / LINUX / PRODUCTION

Nếu repository có Docker/Nginx deployment, audit toàn bộ.

Tối ưu:

* gzip/brotli nếu phù hợp
* keepalive
* connection limits
* request timeout
* upload limits
* static file caching
* proxy buffering
* proxy timeout
* compression
* security headers

Không cấu hình quá mức gây tốn RAM.

Application process phải có giới hạn phù hợp.

Không để một process ngốn toàn bộ 16 GB RAM.

---

# PHASE 11 — LOGGING & MONITORING

Production phải có monitoring đủ để biết server nghẽn ở đâu.

Theo dõi tối thiểu:

* CPU
* RAM
* disk usage
* disk I/O
* network
* load average
* API latency
* request rate
* error rate
* PostgreSQL connections
* slow queries
* connection pool usage
* MinIO performance

Health endpoints:

`/health`

và nếu phù hợp:

`/ready`

Health check KHÔNG được thực hiện query nặng.

Log phải có:

* timestamp
* request ID
* user/attempt ID nếu phù hợp
* endpoint
* latency
* status
* error

Không log password, token hoặc sensitive data.

Có log rotation.

Không để log làm đầy 100 GB disk.

---

# PHASE 12 — DATABASE BACKUP

Thiết kế backup cho PostgreSQL.

Phải có:

* automated backup
* retention
* backup verification
* restore procedure

Backup không được làm database production bị lock lâu hoặc gây downtime.

MinIO cũng phải có chiến lược backup/object replication phù hợp.

---

# PHASE 13 — LOAD TESTING

Đây là PHASE BẮT BUỘC.

Không được nói:

"Code này có thể chịu 200 users"

nếu chưa test.

Tạo load test bằng công cụ phù hợp, ví dụ:

* k6
* Artillery
* Locust

Ưu tiên k6 nếu project phù hợp.

Mô phỏng realistic workload:

## Scenario A

50 concurrent users.

## Scenario B

100 concurrent users.

## Scenario C

150 concurrent users.

## Scenario D

200 concurrent users.

## Scenario E — Peak submission

200 users submit trong khoảng 1–10 giây.

## Scenario F — Autosave spike

200 users autosave gần cùng thời điểm.

## Scenario G — Listening

200 users cùng request audio assets.

## Scenario H — Mixed workload

Ví dụ:

* 70% đang làm bài
* 15% load exam
* 10% autosave
* 5% submit

Không chỉ benchmark một endpoint.

---

# PERFORMANCE TARGETS

Mục tiêu production:

### API

Thông thường:

* p50 < 100 ms
* p95 < 300 ms
* p99 < 500 ms

Đối với endpoint nặng có thể cao hơn nếu có lý do rõ ràng.

### Error rate

Mục tiêu:

`< 0.1%`

trong load test bình thường.

### Database

Không được:

* connection exhaustion
* lock contention nghiêm trọng
* runaway queries
* sequential scan lớn ở hot path

### Server

Ở 200 concurrent users:

Mục tiêu ban đầu:

* CPU sustained < 70%
* RAM < 75–80%
* disk usage < 80%
* không swap thrashing
* không OOM
* không connection exhaustion

Không cần đạt các con số trên bằng mọi giá nếu workload thực tế khác, nhưng phải giải thích rõ bottleneck.

---

# PHASE 14 — FAILURE TESTING

Test các tình huống:

1. User refresh giữa bài.
2. User mất mạng 5–30 giây.
3. User reconnect.
4. User click Submit 2 lần.
5. Browser gửi duplicate save.
6. API timeout.
7. PostgreSQL connection tạm thời fail.
8. MinIO tạm thời unavailable.
9. 200 users submit cùng lúc.
10. Server restart.
11. Browser mở nhiều tab cùng attempt.

Đảm bảo không mất answer.

---

# PHASE 15 — IMPLEMENTATION

Sau khi audit:

1. Tạo implementation plan.
2. Ưu tiên CRITICAL → HIGH → MEDIUM.
3. Sửa từng nhóm.
4. Chạy tests sau mỗi nhóm thay đổi.
5. Không phá behavior hiện tại.
6. Chạy lint.
7. Chạy type check.
8. Chạy unit tests.
9. Chạy integration tests.
10. Chạy build production.
11. Chạy load test.
12. Phân tích kết quả.
13. Tiếp tục tối ưu nếu bottleneck vẫn còn.

---

# PHASE 16 — CODE QUALITY

Không được tạo code hack chỉ để benchmark đẹp.

Code phải:

* readable
* maintainable
* typed nếu project hỗ trợ
* có error handling
* có timeout
* có validation
* có transaction boundary rõ ràng
* không duplicate logic
* không tạo abstraction không cần thiết

Performance optimization phải có comment/documentation khi logic phức tạp.

---

# PHASE 17 — DELIVERABLES

Sau khi hoàn thành phải tạo/cập nhật:

`PERFORMANCE_AUDIT.md`

`PERFORMANCE_OPTIMIZATION.md`

`LOAD_TEST.md`

`PRODUCTION_CHECKLIST.md`

Nếu có load test:

`load-tests/`

Nếu có database migration:

`migrations/`

Nếu có deployment config:

`docker-compose.production.yml`

hoặc file tương ứng với stack hiện tại.

---

# FINAL REPORT

Cuối cùng phải báo cáo:

## 1. Architecture

Kiến trúc cuối cùng.

## 2. Major bottlenecks found

Liệt kê bottleneck trước khi sửa.

## 3. Changes made

Liệt kê chính xác từng thay đổi.

## 4. Database

* indexes added
* queries optimized
* connection pool
* transactions
* N+1 fixes

## 5. API

* latency
* request reduction
* payload reduction
* rate limiting

## 6. Frontend

* bundle size
* API requests
* rendering
* caching

## 7. MinIO

* access strategy
* caching
* direct/presigned access
* bandwidth considerations

## 8. Load test results

Bắt buộc đưa bảng:

| Concurrent Users | RPS | p50 | p95 | p99 | Error Rate | CPU | RAM |
| ---------------: | --: | --: | --: | --: | ---------: | --: | --: |
|               50 |     |     |     |     |            |     |     |
|              100 |     |     |     |     |            |     |     |
|              150 |     |     |     |     |            |     |     |
|              200 |     |     |     |     |            |     |     |

Đặc biệt báo cáo riêng:

**200 users submitting simultaneously**

## 9. Remaining bottlenecks

Nếu còn bottleneck phải nói rõ.

Không được nói "fully optimized" nếu vẫn còn bottleneck nghiêm trọng.

## 10. Capacity estimate

Ước lượng:

* safe concurrent users
* maximum tested concurrent users
* expected RPS
* CPU utilization
* RAM utilization
* database utilization

## 11. Production deployment instructions

Đưa ra chính xác các bước:

* build
* migration
* environment variables
* database configuration
* MinIO configuration
* Nginx
* systemd/Docker
* backup
* monitoring

---

# QUAN TRỌNG NHẤT

Đừng chỉ tối ưu để đạt benchmark.

Hãy tối ưu cho workload thực tế:

**300–400 registered students**

**~200 concurrent active exam users**

**có autosave**

**có Listening/audio**

**có PostgreSQL**

**có MinIO**

**có peak submit**

**chạy trên Linux**

**8 CPU cores / 12 GB RAM / 100 GB SSD SATA**

Mục tiêu là:

> 200 người cùng làm bài vẫn mượt, không mất đáp án, PostgreSQL không nghẽn connection, API không bị request storm, MinIO không làm application server quá tải và server vẫn còn headroom.

Nếu phát hiện kiến trúc hiện tại không đáp ứng được mục tiêu này, hãy sửa kiến trúc ở mức cần thiết thay vì chỉ tối ưu vài dòng code.

BẮT ĐẦU BẰNG AUDIT REPOSITORY.
KHÔNG SỬA CODE NGAY.
Sau audit mới lập kế hoạch và triển khai.
