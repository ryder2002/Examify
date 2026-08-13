# Báo cáo bàn giao — 2026-08-11

## Trạng thái cuối

Hệ thống đã được khôi phục về profile `server` và readiness đang HTTP `200`.
PostgreSQL database `examify`, MinIO buckets `examify-*`, Redis và Tesseract OCR đều đang hoạt động trong Compose
monolith. PgBouncer/PaddleOCR là thông tin lịch sử của môi trường cũ.

## Runtime update — 2026-08-12

Backend image đã chuyển engine OCR sang Tesseract CPU, giữ nguyên contract và
layout/parser pipeline. Compose đã được build/recreate lại trên một host với
PostgreSQL, MinIO, Redis, Celery, API, frontend và Nginx; `/health/ready`, OCR
smoke và admin login đều pass.

## Các việc đã triển khai

- PostgreSQL direct port `5433` và PgBouncer port `5432` đã được đồng bộ.
- PgBouncer dùng SCRAM, transaction pooling, `max_prepared_statements=0`,
  `max_db_connections=50`, query wait 5 giây.
- PostgreSQL có `max_connections=300` và `pg_stat_statements` active.
- Tạo role runtime `toeicdoc_app` và role migration `toeicdoc_migrate`.
- Áp dụng migration/index hot path `0023_performance_hot_path`.
- API dùng pool nhỏ, batch autosave, cache manifest single-flight và OCR request
  concurrency giới hạn.
- MinIO giữ bucket private, proxy keepalive và bucket structure.
- PaddleOCR chạy engine 9-core, restart policy `unless-stopped`.

## Dọn dữ liệu bàn giao

- Đã dừng API/worker/scheduler trong maintenance window.
- Đã truncate 35 bảng dữ liệu, reset identity sequence và giữ nguyên schema.
- Giữ lại đúng 1 admin:
  `toeicdoc.englishcenter@gmail.com`.
- User còn lại: `0`; teacher/student/load-test account: `0`.
- Attempts: `0`; answers: `0`; devices: `0`.
- MinIO giữ 5 bucket rỗng, tổng số object: `0`.
- Redis cache đã flush.
- Alembic head vẫn là `0023_performance_hot_path`.

## Dọn artifact test

Đã xóa fixture và raw output k6 trong `/tmp`, cùng các harness chỉ phục vụ load
test: `load-tests/`, `backend/scripts/build_loadtest_fixture.py`.
Các unit/integration test backend còn lại được giữ lại.

## Benchmark

Không chạy thêm k6 hoặc tạo thêm 300 user sau yêu cầu bàn giao. Các số liệu lịch
sử vẫn nằm trong [BENCHMARK.md](BENCHMARK.md) và không được xem là capacity
certification của trạng thái sạch hiện tại.

## Kiểm tra sau dọn

- `/health`: HTTP `200`.
- `/health/ready`: HTTP `200`.
- API container: `healthy`.
- MinIO bucket listing: 5 bucket, 0 object mỗi bucket.
- PostgreSQL: chỉ còn admin, không còn attempt/answer.
