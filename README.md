# Smart Exam Converter

Ứng dụng local chuyển PDF TOEIC dạng scan thành bài thi trên trình duyệt. Pipeline
nhận biết riêng Listening/Reading, OCR câu hỏi theo cột và giữ passage, ảnh, bảng,
sơ đồ dưới dạng crop gốc.

## Khả năng chính

- Listening:
  - Part 1: cắt từng ảnh, hiển thị cùng A–D.
  - Part 2: tạo phiếu chọn A–C.
  - Part 3–4: OCR câu hỏi/A–D và gắn graphic cho đúng nhóm ba câu.
  - Chọn một audio Full hoặc bốn audio riêng Part 1–4; mỗi file tối đa 30 MiB
    (MP3, WAV, M4A, AAC, OGG, WebM, FLAC) và phát đúng Part trong màn thi.
- Reading:
  - Part 5: Tesseract nhận diện toàn trang một lần rồi phân lại theo hai cột.
  - Part 6–7: giữ flyer, e-mail, message, article và advertisement dưới dạng ảnh.
  - Đoạn đôi/ba được gom theo range `Questions x-y`, hỗ trợ nhiều ảnh qua nhiều trang.
- Job OCR bất đồng bộ với tiến độ theo trang.
- Màn review để sửa text, đáp án, liên kết stimulus và vùng crop.
- Part 3 và Part 4 hiển thị/điều hướng theo nhóm ba câu: 32–34, 35–37, v.v.
- Có thể lưu Listening ở màn review, tiếp tục tạo Reading rồi ghép thành một đề.
- Mỗi draft Listening/Reading có khu vực answer key riêng: bấm trực tiếp,
  paste text dạng `1(D) 2(A)`/`101(B)`, upload ảnh hoặc Ctrl+V ảnh để OCR local.
- Giao diện production navy–trắng, không dùng gradient; card và nút có stroke,
  shadow và trạng thái tương tác rõ ràng.
- Chọn số câu theo nhóm, không tách câu khỏi passage/graphic.
- Question Navigator chia rõ theo Part 1–7.
- Bài đầy đủ 200 câu mặc định 120 phút; Listening 45 phút và Reading 75 phút.
- Submit hoặc hết giờ chuyển tới trang Result riêng, hiển thị câu đúng, sai,
  chưa làm và chi tiết đáp án.
- Đề đã finalize được lưu trong **My Exams** để làm lại nhiều lần.
- Trang Admin sinh mã kích hoạt dùng một lần, quản lý thiết bị và theo dõi OCR.

## Công nghệ

- Frontend: Next.js 16, React 19, TailwindCSS.
- Backend: FastAPI stateless, Celery/Redis, PostgreSQL và MinIO.
- OCR worker: pdfplumber, Poppler/pdf2image, Tesseract/pytesseract, Pillow và OpenCV.
- Triển khai: Docker Compose và Nginx.

## Chạy production bằng Docker Compose

```bash
cp .env.example .env
# Sửa toàn bộ password/JWT secret trong .env trước khi chạy.
docker compose up -d --build
docker compose ps
```

Mở `http://localhost/admin` và đăng nhập bằng
`ADMIN_EMAIL`/`ADMIN_PASSWORD`. `/activate` chỉ dành cho người dùng nhập mã.
Mặc định chỉ chạy một OCR worker để bảo vệ RAM/CPU. Chỉ tăng worker sau khi đã
tính lại ngân sách tài nguyên và chạy load test trên cấu hình server đích:

```bash
docker compose up -d --scale worker=4
```

Chi tiết backup, TLS và vận hành xem
[PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md).

## Cài đặt

Yêu cầu Python 3.10+, Node.js 18+ và Poppler:

```bash
sudo apt install poppler-utils

cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cd ../frontend
npm install
```

Chạy hai service:

```bash
cd backend
.venv/bin/uvicorn main:app --reload --port 8000

cd frontend
npm run dev
```

Mở `http://localhost:3000`, chọn Listening hoặc Reading rồi upload PDF. Frontend
gọi backend qua Next.js rewrite `/api`.

Để tạo đề đầy đủ, xử lý Listening trước và chọn **Tiếp tục tạo Reading** ở màn
Review. Sau khi finalize Reading, frontend ghép hai phần và giữ nguyên asset của
cả hai extraction job.

## API v2

### `POST /api/extractions`

Multipart fields:

- `file`: PDF, tối đa 30 MiB.
- `exam_type`: `listening` hoặc `reading`.
- `audio_full` hoặc `audio_part_1`…`audio_part_4`: tùy chọn cho Listening;
  mỗi file tối đa 30 MiB. Trường `audio` cũ vẫn được hỗ trợ như Audio Full.

Trả `202`:

```json
{
  "job_id": "uuid",
  "status": "queued",
  "cached": false
}
```

### Các endpoint tiếp theo

- `GET /api/extractions/{job_id}`: tiến độ và `ExamDraft`.
- `GET /api/extractions/{job_id}/assets/{asset_id}`: crop WebP.
- `GET /api/extractions/{job_id}/audio/{audio_id}`: stream audio Listening,
  hỗ trợ byte-range của trình duyệt.
- `GET /api/extractions/{job_id}/pages/{page}`: trang nguồn dùng trong crop editor.
- `POST /api/extractions/{job_id}/answer-key-image`: OCR ảnh answer key local.
- `PATCH /api/extractions/{job_id}/draft`: lưu questions/stimuli đã review.
- `POST /api/extractions/{job_id}/finalize`: answer key, count và shuffle.

Upload/OCR/review có thể thực hiện trước khi kích hoạt. Endpoint `finalize` yêu
cầu phiên thiết bị hợp lệ; frontend sẽ chuyển sang `/activate` và quay lại đúng
job sau khi kích hoạt.

### API quản trị và sở hữu dữ liệu

- `POST /api/v1/auth/login`: đăng nhập Admin.
- `GET /api/v1/admin/dashboard`: thống kê hệ thống.
- `POST /api/v1/admin/tokens`: tạo mã một lần.
- `GET /api/v1/admin/tokens`: quản lý mã, chủ sở hữu và số đề.
- `GET /api/v1/admin/tokens/{token_id}`: thiết bị và đề thi gắn với mã.
- `POST /api/v1/admin/tokens/{token_id}/reissue`: cấp mã cho máy mới, giữ
  nguyên user, My Exams và attempts.
- `POST /api/v1/admin/tokens/{token_id}/revoke`: thu hồi mã.
- `GET /api/v1/admin/users`: danh sách người dùng và dữ liệu.
- `GET /api/v1/admin/devices`: danh sách thiết bị.
- `POST /api/v1/admin/devices/{device_id}/revoke`: thu hồi thiết bị.

OpenAPI/Swagger production có tại `/docs` và `/openapi.json`.

Schema hiện tại là `schema_version: 2`. Question có `option_letters` riêng để
Part 1/2 vẫn render được lựa chọn khi PDF không in nội dung đáp án.

## Runtime và cấu hình

- `TOOL_TAO_DE_DATA_DIR`: nơi lưu job; mặc định
  `/tmp/tool-tao-de/jobs`.
- `TOOL_TAO_DE_JOB_TTL`: thời gian sống tính bằng giây; mặc định 86400.
- Nếu không khai báo `DATABASE_URL`/`MINIO_ENDPOINT`, backend tự chuyển sang
  filesystem mode để phát triển và chạy golden test cũ.
- Trong Docker, PostgreSQL và MinIO là nguồn dữ liệu chính; thư mục worker chỉ
  là cache tạm có thể xóa.
- OCR được xếp hàng bằng Redis/Celery; mỗi container worker chạy concurrency 1
  và có thể scale theo số core/RAM.

## Kiểm thử

Unit tests và golden LC:

```bash
cd backend
.venv/bin/python -m unittest \
  test_parser_unit.py test_pipeline_v2.py test_answer_key.py -v
```

Kiểm thử token dùng một lần, My Exams và attempt:

```bash
cd backend
.venv/bin/python -m unittest test_platform.py -v
```

Golden TES mất khoảng một phút nên được bật rõ ràng:

```bash
RUN_GOLDEN_TES=1 .venv/bin/python -m unittest \
  test_pipeline_v2.ReadingGoldenTest -v
```

Golden RC đầy đủ 100 câu:

```bash
RUN_GOLDEN_RC=1 .venv/bin/python -m unittest \
  test_pipeline_v2.FullReadingGoldenTest -v
```

Frontend:

```bash
cd frontend
npm run build
```

Kết quả kỳ vọng với file mẫu:

- `LC.pdf`: 100 câu, Part 1/2/3/4 = 6/25/39/30, 11 stimulus.
- `TES.pdf`: 71 câu liên tục 101–171, Part 5/6/7 = 30/16/25, 13 stimulus.
- `RC.pdf`: 100 câu liên tục 101–200, Part 5/6/7 = 30/16/54; đoạn
  176–185 có hai tài liệu và 186–200 có ba tài liệu mỗi nhóm.

## Giới hạn

- Audio không nằm trong PDF phải được tải riêng khi tạo Listening.
- PDF không có answer key vẫn làm bài được nhưng không chấm đúng/sai.
- Crop confidence thấp được đưa vào màn review thay vì tự động loại bỏ.
