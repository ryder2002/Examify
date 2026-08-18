# Smart Exam Converter

Ứng dụng chuyển PDF TOEIC dạng scan thành bài thi trên trình duyệt. Luồng tạo đề
web upload PDF/audio lên FastAPI, Celery worker chạy Tesseract `eng` + Poppler
trên server, rồi trình duyệt poll tiến độ và mở màn review. OCR không chạy bằng
Tesseract.js trong Chrome ở luồng tạo đề mới; draft client cũ vẫn được giữ để
tương thích dữ liệu đã tạo.

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
- Job OCR server có trạng thái bền vững trong PostgreSQL/MinIO, có thể tiếp tục
  poll sau refresh; cache được phân biệt bằng phiên bản pipeline.
- Màn review để sửa text, đáp án, liên kết stimulus và vùng crop.
- Part 3 và Part 4 hiển thị/điều hướng theo nhóm ba câu: 32–34, 35–37, v.v.
- Có thể lưu Listening ở màn review, tiếp tục tạo Reading rồi ghép thành một đề.
- Mỗi draft Listening/Reading có khu vực answer key riêng: bấm trực tiếp,
  paste text dạng `1(D) 2(A)`/`101(B)`, upload ảnh/PDF hoặc Ctrl+V ảnh để OCR local.
- Giao diện production navy–trắng, không dùng gradient; card và nút có stroke,
  shadow và trạng thái tương tác rõ ràng.
- Chọn số câu theo nhóm, không tách câu khỏi passage/graphic.
- Question Navigator chia rõ theo Part 1–7.
- Bài đầy đủ 200 câu mặc định 120 phút; Listening 45 phút và Reading 75 phút.
- Submit hoặc hết giờ chuyển tới trang Result riêng, hiển thị câu đúng, sai,
  chưa làm và chi tiết đáp án.
- Đề đã finalize được lưu trong **My Exams** để làm lại nhiều lần.
- Trang Admin sinh mã kích hoạt dùng một lần và quản lý thiết bị; server theo dõi
  tiến độ OCR có giới hạn và không ghi binary lớn vào PostgreSQL.

## Công nghệ

- Frontend: Next.js 16, React 19, TailwindCSS.
- Backend: FastAPI, Celery/Redis, Tesseract `eng` + Poppler/OpenCV headless,
  PostgreSQL và MinIO.
- Frontend: Next.js 16/React 19; chỉ giữ Tesseract.js/PDF.js/OpenCV.js để đọc
  draft client cũ và các luồng compatibility, không dùng cho upload tạo đề mới.
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
OCR server chạy trong Compose bằng một worker bounded; media và OCR dùng queue
riêng nhưng cùng ceiling tài nguyên:

```bash
docker compose up -d --scale worker=1
```

Chi tiết backup, TLS và vận hành xem
[PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md).

## Cài đặt

Yêu cầu Python 3.10+ và Node.js 18+. Runtime backend cần Poppler, Tesseract
và language pack `eng`; Docker image đã cài sẵn các gói này:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Máy chạy local cần Poppler, Tesseract và language pack eng.

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

### API OCR server

`POST /api/extractions` nhận PDF/audio, tạo job idempotent và đưa vào queue
`ocr`. `GET /api/extractions/{job_id}` trả progress/questions/stimuli; asset và
audio dùng URL protected/presigned của job. `POST /api/extractions/{job_id}/finalize`
hoàn tất draft sau review.

Các session `/api/v1/client-extractions` và draft `clientDraft` chỉ còn là
compatibility cho dữ liệu cũ, không phải luồng tạo mới.

Luồng mới upload multipart tới `/api/extractions`; worker materialize source từ
MinIO, render 300 DPI và chạy Tesseract bounded. Không đưa OCR text/image ra
nhà cung cấp bên ngoài.

- `POST /api/v1/client-extractions/{id}/uploads/refresh`: cấp lại policy chưa upload.
- `POST /api/v1/client-extractions/{id}/commit`: validate `ClientExtractionManifestV1`
  và persist exam/version trong một transaction, không I/O MinIO trong transaction.
- `GET/DELETE /api/v1/client-extractions/{id}`: trạng thái media hoặc hủy session.
- `POST /api/v1/solution-imports/validate`: validate rows solution đã OCR local.

Manifest giới hạn 200 câu, 200 stimulus, 300 asset và 5 MiB; câu/phương án còn
issue không được finalize.

OCR tạo đề mới chạy trên server qua job bền vững. Browser upload PDF/audio một
lần rồi poll trạng thái; chỉ mở review khi worker hoàn tất. Các draft client cũ
vẫn có thể đọc/commit qua API compatibility, nhưng không còn là đường OCR mặc
định cho upload mới.

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
- OCR tạo đề chạy bằng Tesseract trên server worker; `OCR_PAGE_WORKERS` và
  `OCR_ENGINE_POOL_SIZE` được giới hạn để không oversubscribe CPU/RAM. PDF/DOCX
  lời giải có text vẫn parse deterministic; luồng answer-key client cũ không
  ảnh hưởng upload tạo đề mới.
- Redis/Celery lưu trạng thái job bounded; API không tạo một OCR thread cho mỗi
  request.

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
