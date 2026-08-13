# Báo cáo tối ưu đã triển khai

> Bản báo cáo đầy đủ theo đúng deliverable `AGENTS.md`, gồm trạng thái từng
> phase, bảng load test, rủi ro còn lại và hướng dẫn triển khai, nằm tại
> `PERFORMANCE_OPTIMIZATION.md`.

Ngày cập nhật: 2026-08-04

## Thay đổi chính

| Khu vực | Trước | Sau |
|---|---|---|
| Lưu 200 đáp án | SELECT từng câu rồi insert/update | Một bulk upsert theo batch + một count |
| Submit cá nhân | Save transaction riêng rồi transaction chấm | Một transaction có row lock, upsert, chấm và receipt |
| Submit lặp | Có thể trả 409 hoặc race | Trả lại receipt của attempt đã submitted |
| Autosave client | Lỗi bị bỏ qua; reload có thể ghi đè local | Draft durable, revision/ack, retry backoff+jitter, reconciliation |
| Submit lỗi mạng | Vẫn xóa attempt và sang result | Giữ attempt/draft, hiển thị lỗi và cho retry |
| DB pool | Trần lý thuyết khoảng 120 | Trần steady-state khoảng 28 theo Compose |
| Presence | Ghi device/member mỗi request | Throttle theo cửa sổ mặc định 60 giây |
| Identity socket | DB sync mỗi 2 giây/socket trong async loop | DB chạy ở thread, kiểm tra mặc định 15 giây |
| Assignment list | 3 query/assignment | Query assignment+version và attempts theo batch |
| Monitoring | Toàn bộ lịch sử mỗi 5 giây | Latest riêng, history giới hạn, aggregate DB |
| Anti-cheat batch | SELECT từng event | Một SELECT cho toàn batch |
| Classroom media | FastAPI truyền byte MinIO | FastAPI authorize; nginx internal redirect truyền byte/Range |
| Maintenance | Chung worker với OCR dài | Worker/queue riêng |
| Readiness | Không probe persistence | Probe PostgreSQL, Redis và MinIO, trả 503 khi lỗi |
| Schema startup | API gọi `create_all` | Alembic là chủ sở hữu schema |

## Toàn vẹn dữ liệu

- `answer_revision` tăng đơn điệu trên attempt và server trả `accepted_revision`.
- Batch cũ/lặp không ghi đè revision mới.
- Save và submit khóa attempt; finalize chỉ xảy ra một lần ở PostgreSQL.
- Client chỉ xóa draft sau receipt có `submitted_at` và exam hợp lệ.
- Unique constraint hiện có trên `(attempt_id, question_number)` vẫn là hàng rào cuối.

## Migration và index

- `0014_attempt_answer_revision.py`: thêm acknowledgement revision.
- `0015_attempt_hot_path_indexes.py`: index cho user history, assignment monitoring và partial overdue finalization.

Index là lựa chọn theo hot query đã audit. Sau khi có dataset gần production vẫn phải lưu `EXPLAIN (ANALYZE, BUFFERS)` trước/sau để xác nhận planner dùng index và chi phí write chấp nhận được.

## Trạng thái xác minh

Đã có test hồi quy cho save revision, stale batch, duplicate submit, durable draft và lỗi submit phía client. Các artifact load/attack test disposable đã được gỡ khỏi repository; việc chứng nhận capacity phải thực hiện bằng công cụ bên ngoài trên staging tương đương.
