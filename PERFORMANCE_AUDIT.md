# Báo cáo audit hiệu năng và độ tin cậy

## Current architecture after server OCR rollback (2026-08-14)

- Web `POST /api/extractions` uploads PDF/audio and returns a durable job id.
- Celery queue `ocr` runs the existing layout-aware pipeline with Tesseract
  `eng`, Poppler, OpenCV headless, `OCR_PAGE_WORKERS=2` and an engine pool of 2.
- Browser polls job progress and opens server review data. Old `clientDraft`
  records remain readable but are no longer produced by the new upload form.
- Pipeline 3.6 performs repeated-watermark OCR filtering, number/header-anchored
  recovery and bounded crop retries. Source page JPEGs are never modified.
- Verified fixtures: LC5 11 pages/100 questions in 37.1s; RC5 28 pages/100
  questions in 85.3s. LC5 Part 3/4 is 69/69; RC5 options are 100/100,
  including the corrected Part 6 layout for 144–146.

The browser-only OCR audit below is historical context from the interrupted
client-OCR experiment. It is not the production architecture after this
rollback.

## Historical browser OCR audit and DB hot path (2026-08-14)

> Đây là baseline bắt buộc trước khi triển khai. Các mục trong phần này mô tả
> trạng thái repository tại thời điểm audit, không phải tuyên bố đã rollout.

### Kiến trúc và baseline hiện tại

- Ứng dụng dùng Next.js 16/React 19 ở frontend, FastAPI/SQLAlchemy ở backend,
  PostgreSQL cho dữ liệu, MinIO cho object, Redis/Celery cho job và có bản
  desktop Tauri. Không có `.codegraph/`, vì vậy audit được thực hiện trực tiếp
  trên routes, models, migrations, query layer, Docker/Nginx và tests.
- Luồng tạo đề trước thay đổi upload PDF vào `POST /api/extractions`, backend
  render/OCR trong FastAPI/Celery rồi frontend poll job mỗi
  1,2 giây. Ảnh answer key và PDF lời giải scan cũng có thể gọi OCR server.
- Review/crop đang tải ảnh trang do server render; job progress liên tục đọc và
  ghi bảng `jobs`. MinIO `stat/copy` còn xuất hiện bên trong transaction tạo
  exam/version.
- Trước khi sửa mã, frontend test pass **75/75**, lint pass, production build
  pass và route `/quiz` có JavaScript gzip **219,3 KiB** trên ngân sách 250 KiB.
  Đây là baseline để ngăn OCR bundle lọt vào màn hình làm bài.
- Full LC có baseline OCR local đạt 100/100 câu; dịch vụ OCR ngoài không ổn
  định về layout/geometry và chậm hơn. Hai fixture
  `TEST 1 LC.pdf`, `TEST 1 RC .pdf` sẽ được dùng làm nguồn golden corpus, nhưng
  mọi số accuracy mới chỉ được công bố sau khi có ground truth được kiểm tay.

### Findings và hướng xử lý

| ID | Mức độ | Phát hiện/nguyên nhân | Cách sửa | Expected impact |
|---|---|---|---|---|
| BOCR-C-01 | CRITICAL | OCR remote đưa tài liệu ra dịch vụ ngoài nhưng không cung cấp geometry đáng tin cậy; cấu hình, secret và fallback làm runtime khó kiểm soát. | Xóa toàn bộ provider/config/secret/benchmark OCR remote; Tesseract.js self-host là engine duy nhất và chạy trong browser/desktop WebView. | Không còn chi phí, privacy risk và phụ thuộc OCR remote. |
| BOCR-C-02 | CRITICAL | PDF đề, answer key và solution scan đều có đường gọi OCR server; nhiều Teacher có thể tạo request storm CPU/RAM/API. | Chuyển đủ ba luồng sang local OCR, checkpoint IndexedDB/OPFS; server chỉ cấp upload policy, validate manifest và persist. Không có fallback OCR server. | OCR scale theo thiết bị người tạo đề, không theo 8 CPU của server. |
| BOCR-H-03 | HIGH | Pipeline chính chưa có de-watermark hình học; regex legacy có thể xóa nhầm `TEST`, `PART`, `Directions` hoặc header hợp lệ. | Phát hiện lặp đa trang bằng bbox/IoU, vùng 12% header/footer, page sequence và mask watermark bảo vệ edge chữ; luôn giữ pass ảnh gốc và chỉ merge evidence tin cậy. | Giảm silent omission, không xóa chữ thật bị watermark đè. |
| BOCR-H-04 | HIGH | Review/crop phụ thuộc page JPEG và API recrop do server tạo. | PDF.js render/crop WebP tại browser; server lưu source và asset cuối theo immutable key. | Bỏ CPU, disk tạm và round trip crop trên server. |
| BOCR-H-05 | HIGH (OPEN) | Luồng edit session cũ của đề đã tồn tại vẫn còn compatibility clone job/page; chưa được nối vào local manifest/source URL mới. | Cần migrate UI edit sang tải source ký local, tái sử dụng manifest hiện tại và commit copy-on-write; giữ endpoint cũ 410 sau pilot. | Không còn server render/copy khi chỉnh đề cũ; gate này chưa đóng trong release hiện tại. |
| DB-H-01 | HIGH | `jobs` là nhóm query nổi bật do polling/progress OCR. | Client extraction session không lưu tiến độ từng trang; progress/draft hoàn toàn local, server chỉ theo dõi upload/commit/media. | Giảm read/write liên tục và Redis/Celery traffic. |
| DB-H-02 | HIGH | `_snapshot_exam`/persist exam gọi MinIO và materialize asset trong transaction; insert stimulus còn `flush()` theo item. | `stat`/validation/materialization trước transaction, immutable object key, pre-generate UUID và Core bulk insert. | Transaction ngắn, statement count bounded, giảm lock/connection hold time. |
| DB-M-03 | MEDIUM | Autosave delta tối đa 50 câu vẫn đọc projection 100--200 câu để validate. | Chỉ select `question_number` thuộc delta/part; projection đáp án đúng chỉ đọc khi submit/timeout. | Giảm row read và serialization trên hot path 200 học viên. |
| DB-M-04 | MEDIUM | History dùng `OFFSET`, student start lookup lặp, token list/export query owner/device theo từng dòng. | Cursor `(submitted_at,id)`, một session cho start attempt, batch owner/device aggregate. | Query count cố định và latency ổn định khi dữ liệu tăng. |

### Kiến trúc đích đã khóa

```text
Browser/Tauri
  PDF.js text layer + page render
  -> OpenCV.js preprocessing/layout masks
  -> Tesseract.js Web Worker (1 worker, tối đa 2 nếu >= 8 logical cores)
  -> TypeScript parser + local review/crops
  -> OPFS/IndexedDB checkpoint theo source hash + pipeline version
  -> direct presigned upload tới MinIO khi người dùng bấm lưu
  -> ClientExtractionManifestV1 commit vào FastAPI/PostgreSQL

Student /quiz
  -> không import PDF.js, OpenCV.js hoặc Tesseract.js
  -> giữ nguyên delta autosave + idempotent submit
```

- PDF tối đa 50 MiB/500 trang; canvas tối đa 24 MP; mỗi worker chỉ giữ một
  trang. Runtime OCR được lazy-load và model `eng` được self-host/cache, không
  tải CDN.
- Text layer hợp lệ được parse trước; trang scan dùng baseline grayscale 225
  DPI, Listening/ROI nhỏ tối đa 300 DPI. Recovery chỉ chạy khi coverage hoặc
  confidence không đạt và mọi bất đồng phải thành review issue.
- Manifest giới hạn 200 câu, 200 stimulus, 300 asset và 5 MiB; server kiểm tra
  links, bbox, option, duplicate, unresolved issue và idempotency hash trước
  persist. Không gửi page image/OCR text tới endpoint xử lý OCR.
- Giữ 4 Uvicorn worker, pool `5 + 1` mỗi worker và PostgreSQL
  `max_connections=80`; chưa có bằng chứng cần PgBouncer, Redis cache mới,
  microservice hoặc thêm index tìm kiếm.

### Phần giữ nguyên vì đang bảo vệ tính toàn vẹn

- PostgreSQL/MinIO, immutable `ExamVersion`, unique answer
  `(attempt_id, question_number)`, batch sync ledger, row lock khi submit và
  scoring projection deterministic.
- Delta autosave/retry/multi-tab recovery trên trang làm bài, audio Range và
  object metadata trong PostgreSQL. Thay đổi OCR không được chạm vào behavior
  tính điểm hay làm mất đáp án.
- Redis/Celery chỉ còn phù hợp cho media/document deterministic như FFmpeg và
  LibreOffice; không thêm worker OCR hoặc tăng concurrency để che bottleneck.

### Release gates

- Golden LC/RC đủ 100/100 câu tương ứng; watermark corpus đạt ít nhất 98% word
  recall chuẩn hóa, 100% question/option anchor recall và mọi thiếu hụt hiện
  thành issue thay vì silent omission.
- `/quiz` vẫn không vượt 250 KiB gzip. Trong lúc OCR không có polling và không
  gửi page/answer-key image lên server.
- Commit không thực hiện MinIO I/O trong DB transaction; autosave/history/token
  list đạt query budget cố định đã nêu trong kế hoạch.
- Capacity chỉ được báo cáo sau k6 50/100/150/200, autosave spike, submit peak,
  audio Range, mixed workload và client-extraction commit trên staging.

### Golden verification after implementation (2026-08-14)

- Playwright Chromium regression trên đúng hai fixture trong repository đã PASS:
  LC đủ dãy 1--100 và RC đủ dãy 101--200, không còn issue
  `question_missing`/`options_missing`. Đây là kiểm tra anchor/coverage, chưa
  phải chứng nhận normalized word recall cho mọi watermark.
- Thời gian chạy một lần trên máy phát triển là khoảng 2,3--2,5 phút mỗi
  fixture. Đây không phải warm-cache benchmark của máy 4-core/8 GiB hoặc
  8-core/16 GiB; gate latency/peak-memory vẫn để pending trong
  `LOAD_TEST.md`.

## Audit sự cố OCR LC/RC và phạm vi Kho đề theo Teacher (2026-08-13)

Phần này được hoàn thành trước khi sửa mã cho yêu cầu hiện tại. Phạm vi kiểm
tra gồm toàn bộ đường OCR (`main.py` -> Celery/job store -> `pipeline.py` ->
Tesseract -> parser -> review), golden fixtures `LC.pdf`/`RC.pdf`, test OCR,
schema PostgreSQL/Alembic, authentication/authorization, classroom membership,
exam bank API, frontend Kho đề và deployment OCR worker. Hai PDF fixture hiện
có ở repository và được dùng làm regression fixture; hai file DOCX trong
`Giai_Chi_Tiet/` là lời giải tham khảo, không phải nguồn OCR.

### Kiến trúc hiện tại

```text
Teacher finalize OCR
  -> Exam(owner_user_id, library_scope=teacher_shared)
  -> ExamVersion immutable cho attempt
  -> GET /api/v1/exam-bank
  -> hiện lọc toàn bộ teacher_shared, chưa ràng buộc Teacher/ClassMember

Student join code
  -> ClassMember(user_id, classroom_id, status)
  -> Classroom(owner_teacher_id, status)
  -> ClassAssignment chỉ phục vụ bài được giao thủ công

PDF scan LC/RC
  -> Poppler render (LC default 240 DPI, RC 300 DPI)
  -> OCR toàn trang sau khi scale 75% (LC thực tế khoảng 180 DPI)
  -> Tesseract PSM 11 + parser theo hai cột
  -> retry trang thiếu text/options
  -> recovery Part 3/4 dựa chủ yếu số lượng marker (A)/(D) để cắt block
```

### Bottleneck và lỗi nghiệp vụ phát hiện

| ID | Mức | Phát hiện | Nguyên nhân | Cách sửa | Expected impact |
|---|---|---|---|---|---|
| OCR-C-01 | CRITICAL | LC Part 3/4 mất chữ, mất đáp án hoặc ghép/tách sai dù PDF nguồn rõ | Đường OCR Listening render 240 DPI rồi hạ còn 75%, khiến chữ nhỏ chỉ còn xấp xỉ 180 DPI; lọc confidence sớm làm mất token yếu | OCR Listening ở native render resolution 300 DPI; bỏ median filter ở normal pass để không làm mòn nét chữ nhỏ; chỉ dùng khử nhiễu mạnh trong recovery scan | Giữ nét chữ/marker và giảm mất câu/đáp án ở Part 3/4 |
| OCR-C-02 | CRITICAL | Một câu Part 3/4 có marker/số câu bị OCR hụt làm recovery không chạy hoặc gán block sang câu lân cận | `_question_block_rois` đòi số marker A/D đúng bằng số câu của trang; đây là điều kiện quá mong manh với scan bị che/mất một marker | Tạo ROI trước từ toạ độ số câu theo từng cột, sau đó mới fallback A/D; merge recovery chỉ bổ sung field thiếu và không thay text đã xác thực | Không dịch đáp án sang câu kế bên, số câu thiếu được phục hồi an toàn |
| OCR-H-03 | HIGH | Golden test LC mới kiểm tra non-empty nên không chặn regression issue flag ở Part 3/4; RC chạy opt-in nên dễ bị bỏ qua CI | Regression chưa yêu cầu các Part OCR phải không có `question_missing`/`options_missing`, fixture không được chạy trong test command mặc định | Nâng golden assertion cho LC/RC; bổ sung benchmark/command rõ ràng, chỉ báo cáo kết quả thực đo | Ngăn tái phát “có 100 câu nhưng text sai/thiếu” |
| AUTH-C-01 | CRITICAL | Student vào `/exam-bank` thấy toàn bộ đề `teacher_shared` của mọi Teacher | `list_exam_bank` chỉ lọc `library_scope`, không có `ClassMember -> Classroom.owner_teacher_id == Exam.owner_user_id` | Lọc Student bằng `EXISTS` membership `active` ở bất kỳ lớp nào thuộc đúng owner Teacher; bắt buộc kiểm tra tương tự khi start attempt | Student A chỉ thấy đúng kho của Teacher có lớp mà em đã join; chặn IDOR bằng exam ID |
| AUTH-C-02 | CRITICAL | Teacher B có thể list, edit, archive/delete và publish đề Teacher A | `_shared_exam` và mutation chỉ kiểm role Teacher/Admin, không kiểm owner của exam | Teacher chỉ được quản trị `Exam.owner_user_id` của mình; Admin vẫn có quyền toàn hệ thống; kiểm tra ownership ở finalize/edit session/publication | Không còn sửa/xóa/công bố chéo dữ liệu Teacher |
| AUTH-H-03 | HIGH | Tên đề `shared_title_key` chưa phân biệt Tag, nên `TEST 1` ở 2018 va vào 2019 | Schema/key cũ chỉ có namespace Teacher + title | Chuẩn hoá key theo `owner_user_id + normalized_category + normalized_title`, backfill bằng Alembic migration; giữ unique DB để chống race trong cùng Teacher/Tag | Một Teacher được dùng lại title giữa các Tag, nhưng không tạo trùng trong cùng Tag |
| DB-M-04 | MEDIUM | Điều kiện visibility mới là hot path list/start nhưng index hiện chỉ rời rạc `class_members.user_id` | `EXISTS` join membership/classroom cần lọc user/status/classroom lặp lại | Thêm một composite index đúng predicate; không thêm cache/Redis mới | List/start bounded, không scan membership khi có 200 Student active |

### Phần không cần sửa

- Giữ FastAPI, PostgreSQL, MinIO, Celery/Redis, immutable `ExamVersion`, batch
  answer autosave và idempotent submit. Chúng không phải nguyên nhân gây sai
  nhận dạng chữ hoặc lộ kho đề.
- Không đưa OCR vào request đồng bộ và không tăng worker vô hạn. OCR tiếp tục
  là một Celery job bounded; độ chính xác được tăng bằng resolution/layout
  recovery, không bằng tăng số process.
- Không bỏ `ClassAssignment`: nó vẫn cần cho bài thi chính thức, giới hạn lần
  làm, giám sát và công bố điểm. Luồng mới chỉ thay việc phải publish từng đề
  luyện tập vào từng lớp để Student nhìn thấy Kho đề của Teacher chủ lớp.
- Không tự sửa chữ OCR bằng mô hình ngôn ngữ suy đoán. Khi không đủ bằng chứng,
  hệ thống vẫn đánh dấu review thay vì bịa nội dung/đáp án.

### Kế hoạch triển khai sau audit

1. Sửa OCR normal/recovery cho Listening và bổ sung regression layout cho
   Part 3/4, sau đó chạy LC và RC golden test thực tế.
2. Đưa access predicate dùng chung vào exam-bank, bảo vệ list/tags/start và
   mọi mutation; đổi UI Student thành “Kho đề của giáo viên lớp đã tham gia”.
3. Thêm migration/index/backfill key, cập nhật tests theo quan hệ Teacher A /
   lớp 500, 600, 800 / Student join bất kỳ lớp nào.
4. Chạy backend unit/integration, frontend typecheck/test/build và ghi kết quả
   thật vào báo cáo; không tuyên bố accuracy hoặc capacity khi chưa đo.

### Kết quả triển khai và xác nhận (2026-08-13)

- OCR Listening đã dùng render 300 DPI ở full-page, không còn scale xuống 75%
  như luồng cũ. Layout được dựng lại từ word box theo từng cột; Part 3/4 ưu
  tiên block ROI neo theo số câu, còn marker A/D chỉ là fallback. Merge chỉ
  thay dữ liệu khi crop có bằng chứng mạnh hơn; không có bước đoán/sửa câu theo
  mô hình ngôn ngữ.
- Golden `LC.pdf` PASS: đủ dãy 1--100, Part 3/4 có text và bốn đáp án, không
  có `question_missing` hoặc `options_missing`, và có đầy đủ mapping nhóm
  Listening. Thời gian test thực tế 34,062 giây.
- Golden `RC.pdf` PASS: đủ dãy 101--200, kiểm tra các text/đáp án dễ hỏng và
  mapping/crop Part 5--7 (kể cả nhóm e-mails + notice 186--190). Thời gian test
  thực tế 68,100 giây. Hai fixture là regression đã kiểm chứng, không phải cam
  kết mọi bản scan/PDF chưa từng thấy sẽ không cần Review.
- Kho đề nay dùng predicate chung: Admin thấy toàn hệ thống, Teacher chỉ thấy
  đề mình sở hữu, Student chỉ thấy đề của owner của bất kỳ lớp mà membership
  của Student còn `active`. List, tag list, edit session, update/delete và
  start attempt đều đi qua cùng ràng buộc; không còn IDOR theo `exam_id`.
- Test integration xác nhận Student chưa join không thấy đề; join lớp `900+`
  của Teacher B chỉ thấy B; sau đó join riêng lớp `600+` của Teacher A thì thấy
  toàn bộ bank của A và B. Teacher B không thể sửa/xóa đề A. Hai Teacher được
  đặt cùng title mà không va chạm dữ liệu.

### Follow-up theo format upload mới (2026-08-13)

- `content_start_page` không còn được suy luận từ OCR. Pipeline luôn giữ page 1
  và trả `skipped_pages=[]`; người dùng chịu trách nhiệm cắt cover/directions.
- `shared_title_key` đã chuyển sang `Teacher + normalized Tag + normalized
  title`; migration `0025_tag_scoped_exam_titles` backfill key hiện hữu.
- Regression trong backend image: tag-scope title PASS, owner-scope/routing
  PASS, `test_pipeline_v2` PASS 80 tests (6 skip). Golden LC/RC chưa rerun sau
  follow-up vì hai PDF fixture không còn hiện diện trong workspace hiện tại.

## Audit incident backend cạn `/tmp` khi tạo nhiều đề (2026-08-11)

Phần audit này được hoàn thành **trước khi sửa code/config** cho lỗi trên
Mac/iPad và các máy web: sau khi tạo nhiều đề, màn upload báo
`Máy chủ hoặc proxy đang gián đoạn`. Phạm vi kiểm tra gồm toàn bộ log hiện có
của API/Celery/Nginx, trạng thái container và tài nguyên host, request multipart
Next.js -> Nginx -> FastAPI, PostgreSQL `jobs`, MinIO, local cache của
`PersistentJobStore`, luồng review/crop/finalize và cấu hình Docker production.
Các phần auth, autosave/submit, database indexes/pool, classroom, backup,
monitoring và load test đã được audit ở các mục còn lại của tài liệu này; không
có bằng chứng cho thấy chúng gây ra incident upload hiện tại.

### Kiến trúc và call path tại thời điểm lỗi

```text
Safari/Chrome (Mac/iPad/Windows)
  -> multipart PDF <= 50 MiB + audio (mỗi file <= 50 MiB, tổng <= 300 MiB)
  -> Nginx proxy_request_buffering=off, timeout 1.800 giây
  -> FastAPI/Starlette spool multipart vào /tmp
  -> create_extraction đọc lại từng UploadFile
       -> tạo thêm .upload-*.pdf/.audio-upload-* trong cùng /tmp
       -> hash -> tạo Job PostgreSQL -> sync nguồn sang MinIO
  -> Celery worker dùng /tmp riêng 4 GiB, materialize từ MinIO, OCR/FFmpeg
  -> Review tải asset/page/audio qua FastAPI
       -> PersistentJobStore tải object MinIO trở lại /tmp API
       -> FileResponse proxy toàn bộ byte qua API
```

API có bốn Uvicorn worker nhưng chỉ một tmpfs dùng chung, kích thước `768 MiB`.
Worker OCR có tmpfs riêng nên dung lượng trống của worker không cứu được API.
Rate limit upload giới hạn số request/phút nhưng không giới hạn số upload đang
đồng thời hoặc tổng scratch bytes.

### Bằng chứng runtime và log

- Host còn `49 GiB` trống, inode chỉ dùng `9%`; container không bị OOM và không
  restart. Lỗi nằm riêng trong tmpfs `/tmp` của API: `718/768 MiB` (`94%`).
- `/tmp/smart-exam` giữ tám job tạo trong khoảng 36 phút, tổng `717 MiB`.
  Sáu job Listening chiếm `95-108 MiB/job`; riêng thư mục audio khoảng `85 MiB`
  vì giữ đồng thời audio Full 42-45 MiB và 54 clip auto-cut.
- Log backend tại `main.py:create_extraction` ghi trực tiếp
  `OSError: [Errno 28] No space left on device` khi
  `audio_output.write(chunk)`. Các file `.audio-upload-*` 0 B, 8 KiB và
  40.779.776 B còn sót chứng minh nhánh lỗi/disconnect trước khi item được thêm
  vào danh sách cleanup làm rò file staging.
- Sau khi đầy tmpfs, một Uvicorn child chết. Parent spawn child mới nhưng import
  `job_store.py` gọi `tempfile.gettempdir()` và lặp lại
  `FileNotFoundError: No usable temporary directory found in ['/tmp', ...]`.
  Nginx sau đó ghi `upstream prematurely closed connection` và trả `502` cho cả
  `/api/v1/auth/me`; blast radius không còn giới hạn ở upload.
- PostgreSQL vẫn nhỏ (`19 MB`) và responsive; `jobs` có 47 dòng
  (`19 ready`, `19 review`, `7 failed`, `2 queued`), payload tổng khoảng
  `778 KiB`. Pool tại thời điểm đo có 35 idle/1 active connection, chưa cạn.
- MinIO đang giữ khoảng `4,69 GB`: sources `576 MB`, assets `858 MB`, audio
  `3,26 GB`. Cleanup định kỳ chỉ xóa job thuộc edit session đã hết hạn; job OCR
  thông thường không có retention task nên object storage tăng không giới hạn.
- Log worker/maintenance không có OOM/WorkerLost/timeout tương ứng. Các 401/403
  login là lỗi nghiệp vụ/xác thực; các 404 `.env`/WordPress là scan Internet;
  502 lúc container mới khởi động không cùng nguyên nhân và không cần đổi luồng
  tạo đề.

### Bottleneck và rủi ro

| ID | Mức | Phát hiện | Nguyên nhân | Cách sửa | Expected impact |
|---|---|---|---|---|---|
| UPLOAD-C-01 | CRITICAL | Tạo nhiều đề lấp đầy tmpfs API và làm Uvicorn mất worker/toàn backend có 502 | Cache review tải toàn bộ media MinIO về tmpfs 768 MiB; TTL local 24 giờ không có byte budget | Với server persistent, authorize trong FastAPI rồi dùng presigned `X-Accel-Redirect` để Nginx stream thẳng MinIO; không materialize media vào API | Review/listening không tiêu thụ scratch theo số đề và không proxy bandwidth qua Python |
| UPLOAD-C-02 | CRITICAL | Một multipart hợp lệ có thể dùng gần gấp đôi dung lượng request; hai upload đồng thời có thể vượt 768 MiB | Starlette đã spool file, endpoint lại copy toàn bộ PDF/audio sang named staging trước khi upload MinIO | Hash trực tiếp `UploadFile`, rewind và stream chính file đã spool lên MinIO; dùng scratch volume SSD thay tmpfs RAM nhỏ và giới hạn concurrent upload tại Nginx | Bỏ một bản sao 50-350 MiB/request, tránh cạn RAM/tmpfs và event-loop block |
| UPLOAD-C-03 | CRITICAL | Worker chết không thể respawn khi `/tmp` đầy, làm endpoint không liên quan lỗi theo | `tempfile.gettempdir()` chạy lúc import; readiness không kiểm tra scratch; ENOSPC không được chuyển thành response có cấu trúc | Kiểm tra scratch trong `/health/ready`, cleanup/preflight trước nhận job, bắt ENOSPC thành lỗi rõ ràng và giữ headroom | Hệ thống fail-fast có quan sát thay vì suy giảm âm thầm/vòng lặp spawn |
| UPLOAD-H-04 | HIGH | File `.audio-upload-*` rò khi client ngắt hoặc write lỗi giữa audio | Path chỉ được lưu vào `staged_audios` sau khi copy hoàn tất; `finally` không biết path đang dang dở | Đăng ký mọi temp path ngay khi tạo và luôn unlink trong `finally`; cleanup stale temp khi startup/request | Retry Safari/mạng chập chờn không tích lũy file mồ côi |
| REVIEW-H-05 | HIGH | Save câu hỏi không đổi crop vẫn materialize PDF/pages/assets/audio toàn job | `patch_draft()` gọi `store.job_dir()` trước khi biết có asset cần recrop; persistent `job_dir()` tải mọi prefix | Chỉ tải đúng source page khi bbox/page thay đổi; save text/answer không chạm filesystem | Giảm MinIO GET, disk I/O, latency và tránh cache 85 MiB audio khi chỉ sửa text |
| STORAGE-H-06 | HIGH | MinIO tăng khoảng nhiều GB/ngày khi liên tục tạo đề | `JOB_TTL_SECONDS` chỉ dọn local cache; maintenance không purge standalone terminal/stale jobs | Thêm maintenance bounded, chỉ purge job hết retention và không thuộc edit session active; giữ `ExamSource`/immutable version assets của đề đã finalize | Dung lượng object storage có trần mà không làm mất đề/nguồn durable |
| UX-M-07 | MEDIUM | UI quy mọi 5xx/network close thành “proxy gián đoạn”, không chỉ ra scratch/quá tải | Backend mất kết nối trước JSON; frontend chưa có thông báo 507/503/429 chuyên biệt | Trả detail/request ID khi có thể; map 507/503/429 và hướng dẫn retry, không tự submit duplicate | Người dùng biết lỗi tạm thời, giảm click spam và hỗ trợ truy log theo request |
| OPS-M-08 | MEDIUM | Readiness vẫn xanh khi scratch còn khoảng 50 MiB và worker count đang suy giảm | Health chỉ kiểm tra OCR/PostgreSQL/MinIO/Redis; không đo disk/scratch | Thêm scratch free bytes/ratio vào readiness và metrics/alert production | Watchdog/load balancer phát hiện trước khi upload gây crash |

### Phần không cần sửa

- Không đổi PostgreSQL, framework, OCR/FFmpeg hoặc MinIO. Database không phải
  bottleneck của incident và durable exam/answer data vẫn nguyên vẹn.
- Không tăng vô hạn tmpfs, Uvicorn worker hoặc PostgreSQL connection. Tăng dung
  lượng đơn thuần chỉ dời thời điểm crash và vẫn proxy/copy byte thừa.
- Không xóa object job đang xử lý, edit session active, `ExamSource` hay
  `ExamVersionAsset`. Cleanup phải bounded, theo retention và kiểm tra tham
  chiếu; đề đã finalize tiếp tục dùng media/version immutable.
- Không chuyển OCR vào request đồng bộ. HTTP `202` + Celery vẫn là boundary
  đúng; vấn đề nằm ở ingest/cache/scratch trước khi queue và đường serve media.
- Không coi Mac/iPad là nguyên nhân. Safari có thể làm request retry/disconnect
  dễ lộ leak staging hơn, nhưng log xác nhận ENOSPC phía server là root cause.

### Kế hoạch triển khai sau audit

1. Tách đường ingest persistent: hash/validate file spool hiện có, stream lên
   MinIO, rollback object/job khi lỗi; giữ đường local Desktop hiện hữu.
2. Serve asset/audio/page job persistent bằng `X-Accel-Redirect`, giữ fallback
   streaming khi không có Nginx accel và giữ FileResponse cho Desktop.
3. Lazy materialize đúng page cho crop; thêm cleanup byte/age, stale staging,
   scratch readiness và retention task MinIO/PostgreSQL bounded.
4. Chuyển API scratch sang Docker volume trên SSD, giới hạn concurrent upload,
   giữ body-size/timeout hiện có và cải thiện lỗi frontend.
5. Thêm regression test cho ingest rollback/cleanup, media redirect/access,
   lazy crop và maintenance safety; chạy backend/frontend/deploy contract/build,
   sau đó E2E PDF + audio và kiểm tra log/disk trước-sau.

## Audit UX/progress giai đoạn xử lý audio (2026-08-10)

Phần audit này được hoàn thành **trước khi sửa code** cho hiện tượng
Teacher thấy job kẹt ở 1% khi tạo đề Listening. Phạm vi truy vết gồm
`frontend/app/page.tsx`, API polling `GET /api/extractions/{job_id}`,
Celery task, `prepare_web_audio()`, bộ cắt TOEIC FFmpeg và JobStore
PostgreSQL/MinIO.

### Kiến trúc và luồng trước triển khai (đã sửa ngày 2026-08-11)

```text
Upload PDF + Audio Full
  -> job queued 0%
  -> Celery ghi cố định progress=1, stage="phân tích và cắt Audio Full"
  -> ffprobe + silencedetect (tối đa hai lượt)
  -> FFmpeg encode tuần tự 54 clip quiz (+ direction nếu có)
  -> bắt đầu OCR và lại ghi progress=1
  -> frontend poll mỗi 1,2 giây, chỉ hiển một progress chung
```

Job không bị treo: worker vẫn xử lý audio, nhưng không có callback nào
giữa các subprocess/các clip. Vì API chỉ có `progress` và `stage`, UI
không thể phân biệt tiến độ audio với OCR.

Luồng sau triển khai dùng chung cho web và Desktop/local:

```text
Upload PDF + Audio
  -> job queued
  -> nhánh Audio: ffprobe/FFmpeg + progress audio
  -> nhánh OCR: render/OCR + progress OCR (chạy đồng thời)
  -> chờ cả hai future, merge metadata dưới lock
  -> review 100% hoặc failed terminal state
```

### Bottleneck và rủi ro

| ID | Mức | Phát hiện | Nguyên nhân | Cách sửa | Expected impact |
|---|---|---|---|---|---|
| AUDIO-UX-H-01 | HIGH | Thanh tiến độ giữ 1% suốt giai đoạn audio dù worker vẫn hoạt động | Celery chỉ ghi 1% trước `prepare_web_audio()`; cutter không nhận progress callback | Phát progress thật theo probe, silence pass, alignment và số output đã encode | Teacher biết job đang tiến triển và đang ở bước nào |
| AUDIO-UX-H-02 | HIGH | UI không nói rõ audio chạy trước OCR | Contract job chưa có phase/phase progress; frontend chỉ render progress chung | Thêm `processing_phase` và `phase_progress`; mở dialog modal trong phase audio, ghi rõ OCR tự chạy sau audio | Không còn cảm giác OCR bị treo hoặc hệ thống không phản hồi |
| AUDIO-UX-M-03 | MEDIUM | Khi chuyển sang OCR, overall progress có thể tụt từ mốc audio về 1% | `_run_extraction()` dùng thang 1–98 riêng và không biết phase audio đã hoàn tất | Dành một khoảng overall cho audio và scale tiến độ OCR vào phần còn lại | Thanh tổng tiến chỉ tăng, không nhảy lùi gây hiểu nhầm |
| AUDIO-UX-M-04 | MEDIUM | Ghi state sau mỗi frame/thời gian FFmpeg có thể tăng DB/MinIO I/O | `PersistentJobStore.write()` có thể đồng bộ local media trước khi update PostgreSQL | Chỉ report tại mốc công việc và clip hoàn tất có giới hạn; không poll/log theo frame | UX mượt mà không tạo write storm hoặc làm chậm worker |
| AUDIO-UX-H-05 | HIGH | Nhánh local/Desktop không chạy cùng luồng chuẩn hóa/cắt audio của Celery | `ThreadPoolExecutor` gọi thẳng `_run_extraction()`, trong khi `prepare_web_audio()` chỉ nằm trong Celery task | Gom audio-then-OCR thành một orchestration dùng chung cho Celery và local executor | Web và Desktop hiển thị/cắt audio nhất quán; local failure có terminal state thay vì poll vô hạn |

### Phần không cần sửa

- Chờ audio future tại barrier cuối: OCR có thể chạy sớm, nhưng draft chỉ
  được finalize sau khi manifest audio cuối cùng đã merge và FFmpeg/dependency
  được xác nhận.
- Giữ polling 1,2 giây; với một job/Teacher đây là tần suất hợp lý và
  không cần WebSocket/SSE mới.
- Giữ FFmpeg encode tuần tự, `threads=1`; chạy song song hàng chục clip sẽ
  tranh CPU/I/O với OCR và không phù hợp server 8 core.
- Không hiển thị phần trăm giả dựa trên timer. Progress phải đến từ công
  việc backend đã hoàn thành.

## Audit auto-cut audio TOEIC bằng FFmpeg (2026-08-10)

Phần audit này được hoàn thành **trước khi sửa code** cho yêu cầu cắt một file
Listening đầy đủ thành audio theo câu/nhóm. Phạm vi gồm upload, worker OCR,
FFmpeg, manifest `AudioRef`, MinIO, màn Quiz và mã nguồn MIT của
[`jinjor/wave-cutter-for-toeic`](https://github.com/jinjor/wave-cutter-for-toeic)
tại revision `4e4ce393864d2d7aa8944c5efa0c9350ea5ea8c6` (2016-04-12).

### Kiến trúc và hành vi hiện tại

```text
Upload full audio
  -> JobStore/MinIO giữ một AudioRef(scope=full)
  -> OCR worker chỉ ffprobe + chuyển MP3 128 kbps khi cần
  -> FinalExam vẫn chứa đúng một file full
  -> Quiz ước lượng mốc chuyển câu từ tổng thời lượng cố định

Upload theo câu/nhóm
  -> 31 file câu 1-31 + 23 file nhóm 32-100
  -> Quiz chuyển sau sự kiện ended của từng file
```

Mã nguồn tham chiếu cũ không nhận diện giọng nói và không biết số câu. Nó duyệt
PCM, coi biên độ tuyệt đối dưới `0.01` (xấp xỉ `-40 dBFS`) là im lặng và tạo
điểm cắt khi im lặng kéo dài hơn `60.000` mẫu. Với 44,1 kHz đây là khoảng
1,361 giây; với 48 kHz là 1,25 giây. Điểm cắt nằm ở **cuối** khoảng lặng.
Ứng dụng cũ sau đó yêu cầu người dùng xóa/gộp thủ công. Chế độ `All+` mong đợi
123 wave: 31 câu đầu và 23 nhóm Part 3/4, mỗi nhóm gồm passage + ba prompt câu
hỏi. Kết quả 134 wave vì vậy là raw segmentation có khoảng 11 đoạn dư, thường
là direction/“go on to the next page” hoặc khoảng lặng nội bộ bị tách.

### Bottleneck và rủi ro phát hiện trước sửa

| ID | Mức | Phát hiện | Nguyên nhân | Cách sửa | Expected impact |
|---|---|---|---|---|---|
| AUDIO-CUT-C-01 | CRITICAL | Full audio chuyển câu theo timestamp ước lượng, dễ lệch với từng CD | Worker không hề cắt/đo cấu trúc; frontend chỉ nội suy theo profile tổng thời lượng | Port phép dò im lặng của repo sang `ffmpeg silencedetect`, rồi căn chỉnh raw waves với cấu trúc TOEIC bằng dynamic programming | Mỗi đề có timeline lấy từ chính audio thay vì mốc chung |
| AUDIO-CUT-C-02 | CRITICAL | Part 3/4 có thể chuyển sau passage hoặc một prompt thay vì nghe đủ ba câu | Một nhóm TOEIC cần bốn đoạn nguyên tử nhưng manifest phát theo một asset | Ghép passage + prompt 1 + prompt 2 + prompt 3 thành đúng một file cho mỗi nhóm 32-34 … 98-100 | Sự kiện `ended` chỉ xảy ra sau prompt thứ ba, đúng yêu cầu thi |
| AUDIO-CUT-H-03 | HIGH | Ánh xạ raw wave theo chỉ số cố định sẽ sai giữa CD và khi một khoảng nghỉ ngắn bị bỏ lỡ | Repo cũ chỉ split biên độ và chính README yêu cầu chỉnh tay; mô tả raw index từ người dùng cũng không đủ số đoạn để hard-code one-to-one | Dùng bộ giải cấu trúc có skip/merge, vai trò duration và checkpoint Part; tính confidence và fallback về full audio khi không đủ chắc chắn | Không âm thầm gán sai câu chỉ để luôn tạo đủ file |
| AUDIO-CUT-H-04 | HIGH | 134 subprocess FFmpeg riêng cho phân tích/cắt có thể kéo dài worker và cạnh tranh OCR | Cắt tuần tự từng raw wave và encode lại nhiều lần là I/O/process overhead không cần thiết | Chạy một lượt `silencedetect` chuẩn và tối đa một lượt ngưỡng ngắn khi phát hiện wave đáng ngờ; sau đó chỉ encode 54 output cuối, tuần tự trong worker hiện có | Giới hạn CPU/RAM, tránh upload 123 file trung gian |
| AUDIO-CUT-M-05 | MEDIUM | Ngưỡng `60.000` mẫu bị thay đổi theo sample rate nếu sao chép thành số giây cố định | Thuật toán cũ dùng sample count, không dùng duration cố định | Lấy sample rate bằng ffprobe và đặt `d=60000/sample_rate`; lưu ngưỡng/revision vào metadata | Tái tạo đúng hành vi tham chiếu ở 44,1/48 kHz |
| AUDIO-CUT-M-06 | MEDIUM | Review 54 audio và MinIO có thể nhận cả raw/source không cần thiết | JobStore sync toàn thư mục audio; FinalExam chỉ cần asset được tham chiếu | Không tạo 123 raw file; FinalExam chỉ tham chiếu 54 clip (+ direction đầu nếu có), giữ source ở job để audit/retry | Giảm object/payload và vẫn phục hồi được nguồn |

### Quyết định kỹ thuật

- Chỉ dùng `ffprobe`/`ffmpeg` cho phân tích và cắt; không Whisper, ASR, dịch vụ
  ngoài hoặc model mới. Python chỉ điều phối, parse log và giải bài toán ánh xạ.
- Không vendoring ứng dụng web/npm năm 2015. Chỉ port thuật toán nhỏ, ghi nguồn,
  revision và giữ nguyên giấy phép MIT để tránh dependency cũ/không bảo trì.
- Output chuẩn là 54 audio làm bài: câu 1-31 và 23 nhóm ba câu của Part 3/4.
  Direction mở đầu có thể là asset riêng; đoạn transition ngắn bị loại.
- Không hard-code “wave 6 là câu 1” hay một offset riêng cho 134 waves. Đây chỉ
  là dấu hiệu đầu vào; kết quả phải qua scoring cấu trúc và ngưỡng confidence.
- Không tạo background service hoặc tăng concurrency. Việc cắt chạy trong OCR
  worker hiện có; transaction PostgreSQL không bao quanh FFmpeg/MinIO.
- Nếu âm thanh không giống cấu trúc TOEIC chuẩn hoặc confidence thấp, giữ file
  full và phát cảnh báo có thể kiểm tra, không xuất 54 file sai.

### Tiêu chí xác nhận implementation

1. Ngưỡng FFmpeg tương đương `60.000` mẫu ở cả 44,1 và 48 kHz, biên nằm tại
   `silence_end` và metadata ghi raw wave count/threshold/revision.
2. Fixture 134 raw waves có thể bỏ 11 transition/direction và ánh xạ đủ 123 vai
   trò nguyên tử mà không dựa vào index cố định.
3. Sinh đúng 31 asset câu + 23 asset nhóm; mọi asset Part 3/4 chứa passage và đủ
   ba prompt trước khi kết thúc.
4. Audio cấu trúc không hợp lệ không bị publish dưới dạng các clip gán sai; hệ
   thống fallback file full kèm issue/diagnostic.
5. Unit test, parser test, frontend typecheck/build và Docker worker xác nhận có
   `ffmpeg`/`ffprobe`; không thêm Whisper/model/process thường trú.

## Audit lỗi tạo Full Test, Answer key và lối vào Giải chi tiết (2026-08-09)

Phần audit này được hoàn thành trước khi sửa code cho ba lỗi Teacher báo cáo.
Phạm vi truy vết gồm màn Review/Kho đề, finalize OCR, API combine, immutable
`ExamVersion`, submit lượt tự luyện và `SolutionEditor`/solution-import API.

### Phát hiện

| ID | Mức | Phát hiện | Nguyên nhân | Cách sửa | Expected impact |
|---|---|---|---|---|---|
| FULL-C-02 | CRITICAL | Một Full Test 200 câu bị giữ thành hai đề 100 câu | Reading component được finalize với tên chính thức; API combine sau đó tạo Exam mới cùng `shared_title_key` và bị unique constraint từ chối. Frontend bỏ qua mọi response combine không phải 2xx, vẫn xóa `pending-listening-exam` và chuyển trang | Finalize Reading bằng tên component không xung đột; bắt buộc combine thành công mới hoàn tất, giữ trạng thái pending và hiển thị lỗi nếu combine thất bại | Một thao tác tạo Full Test chỉ sinh một đề hiển thị 200 câu; không còn lỗi âm thầm |
| KEY-C-02 | CRITICAL | Kết quả báo “Chưa có answer key” dù card kho đề đếm đủ đáp án | Submit API đã trả immutable exam có `correct`, nhưng `quiz/page.tsx` chủ động ghi đè `payload.exam` bằng bản `data` đã được sanitize lúc bắt đầu thi | Ưu tiên exam trong submit response; chỉ fallback bản đang làm khi server không trả exam | Kết quả tự luyện hiển thị/chấm theo answer key snapshot đúng, không làm lộ key trước submit |
| SOL-H-03 | HIGH | Teacher không thấy nơi import lời giải chi tiết từ Kho đề | Import DOCX/DOC/PDF chỉ render trong tab `Giải chi tiết` của màn Review; menu Kho đề chỉ có nhãn chung “Chỉnh sửa đề” và luôn mở tab Nội dung | Thêm action “Nhập giải chi tiết”, mở edit session thẳng vào tab Solutions; giữ chung một pipeline save/version | Tính năng hiện hữu và dễ tìm, không tạo API hay luồng lưu trùng lặp |

### Phần không cần sửa

- Answer key đã được lưu vào `QuestionRecord`, `AnswerKey` và
  `ExamVersionQuestion`; ảnh chụp `100 đáp án` xác nhận lỗi không nằm ở việc
  PostgreSQL làm mất key.
- Giữ unique normalized title của Kho chung; đây là bảo vệ integrity đúng, lỗi
  nằm ở thứ tự đặt tên/ghép component và xử lý response phía client.
- Giữ parser/import lời giải và endpoint `solution-imports`; chỉ bổ sung lối vào
  rõ ràng từ Kho đề và regression coverage.

## Audit Kho đề thi chung, version bất biến và Giải chi tiết (2026-08-09)

Phần này là baseline audit được hoàn thành **trước khi triển khai** yêu cầu Kho
đề thi chung và Giải chi tiết. Phạm vi truy vết gồm model/migration PostgreSQL,
FastAPI exam/classroom/public routes, luồng finalize OCR, Next.js/Tauri, MinIO,
autosave/submit và hai file mẫu `Giai_Chi_Tiet/*.docx`.

### Kiến trúc hiện tại

```text
Teacher OCR/finalize
  -> JobStore (state + ảnh/audio trên MinIO)
  -> POST /api/v1/exams/finalize
  -> Exam.payload + QuestionRecord/StimulusRecord/Asset/AnswerKey

Kho cá nhân
  -> mọi query và mutation lọc Exam.owner_user_id
  -> Attempt.exam_id đọc/chấm từ QuestionRecord mutable

Bài lớp
  -> publish tạo ExamVersion + ExamVersionQuestion/ExamVersionAsset
  -> ClassAssignment.exam_version_id
  -> Attempt gián tiếp dùng version qua assignment

Public mini-test
  -> PublicExamShare.exam_id
  -> lượt mới và submit vẫn phụ thuộc Exam/QuestionRecord hiện hành
```

`ExamVersion` hiện đã là nền móng phù hợp nhưng mới bảo vệ đầy đủ luồng bài
lớp. Kho cá nhân, lượt tự làm và public submission chưa pin version. Tag đã là
bảng dùng chung nhưng API tạo Tag chưa áp quyền Teacher/Admin chặt chẽ. Frontend
đang lấy `/my-exams` làm màn chính của Teacher; Student mặc định vào lớp học và
không được phép đi tới một route kho đề chung.

### Bottleneck và rủi ro phát hiện trước sửa

| ID | Mức | Phát hiện | Nguyên nhân | Cách sửa | Expected impact |
|---|---|---|---|---|---|
| BANK-C-01 | CRITICAL | Teacher khác không thể xem/sửa/gán đề và mỗi Teacher có kho riêng | Authorization gắn quyền quản trị với `Exam.owner_user_id` ở platform, classroom và publication routes | Thêm `library_scope=teacher_shared`, quyền ngang nhau cho Teacher/Admin trên đề shared; chỉ ownership lớp/co-teach còn được giữ | Một kho duy nhất nhưng không mở quyền quản trị lớp của Teacher khác |
| VER-C-01 | CRITICAL | Sửa/xóa đề có thể làm đổi câu, đáp án hoặc điểm của lượt tự làm/public đang diễn ra | `Attempt` và `PublicExamSubmission` không có `exam_version_id`; submit đọc `QuestionRecord` mutable | Pin `ExamVersion` khi bắt đầu mọi lượt, chấm từ `ExamVersionQuestion`, giữ asset snapshot | Edit/archive/delete không làm sai dữ liệu hoặc điểm lịch sử |
| EDIT-C-01 | CRITICAL | Hai Teacher edit cùng đề có thể ghi đè nhau | Job OCR thuộc owner và finalize hiện không có optimistic concurrency theo revision đề | Draft copy-on-write qua `ExamEditSession`; finalize khóa row và so `base_revision`, trả 409 nhưng giữ draft | Không mất chỉnh sửa và có conflict rõ ràng |
| SOL-H-01 | HIGH | Chưa có schema lời giải hay ánh xạ ổn định với câu TOEIC | Payload chỉ có question/stimulus; frontend/backend không thống nhất singleton/range | `SolutionEntry` có `question_numbers`; validate Part/range/overlap, lưu trong payload version và đưa vào content hash | Lời giải ánh xạ chính xác, version cũ không bị biến đổi |
| SOL-H-02 | HIGH | Import file mẫu chưa có parser/preview an toàn | Pipeline hiện chỉ xử lý PDF đề thi, không đọc bảng DOCX/DOC/PDF lời giải | Import có trạng thái, parser theo header chuẩn hóa, preview duplicate/range/missing; merge là mặc định | Teacher kiểm tra trước khi áp dụng, import một phần không xóa dữ liệu cũ |
| TITLE-H-01 | HIGH | Tên đề và Tag có thể trùng do khác hoa/thường/khoảng trắng; check ở app dễ race | Unique hiện tại không bảo vệ normalized title toàn kho và Tag chỉ unique nguyên văn | Lưu normalized key và unique constraint DB; bắt `IntegrityError` thành 409 | Hai request đồng thời vẫn không tạo duplicate |
| MEDIA-H-01 | HIGH | Payload bắt đầu thi có thể mang đáp án/lời giải hoặc proxy audio qua Python | Personal attempt trả payload mutable; audio metadata frontend thiếu scope câu/nhóm | Sanitize version payload trước submit, lazy-load lời giải, phát asset token qua Nginx/MinIO với Range | Không lộ đáp án; 200 audio stream không giữ worker FastAPI |
| RESET-C-01 | CRITICAL | Reset script hiện có thể xóa Admin rồi tạo tài khoản/mật khẩu hard-code | Script cũ coi Admin như seed thay vì dữ liệu cần bảo toàn | Dry-run + `CONFIRM_RESET=YES`, giữ đúng Admin hiện tại/hash, yêu cầu `KEEP_ADMIN_ID` nếu mơ hồ, bảo toàn terms/privacy, rotate epoch | Reset maintenance có thể kiểm chứng và không đổi credential Admin |
| EPOCH-H-01 | HIGH | Desktop/PWA có thể đồng bộ dữ liệu cũ trở lại sau reset | Server và client chưa có generation/epoch dùng chung | `SystemState.data_epoch`; từ chối manifest cũ, quarantine namespace Desktop và xóa cache nghiệp vụ PWA | Dữ liệu đã reset không bị hồi sinh từ thiết bị cũ |
| LIST-M-01 | MEDIUM | List đề có nguy cơ payload lớn và query phụ theo từng đề | `/exams` trả cấu trúc phục vụ kho cá nhân, không có contract metadata/pagination chung | `/exam-bank` chỉ trả metadata, pagination tối đa 50 và aggregate theo batch | Search/list ổn định ở 200 user, không N+1 hoặc kéo solutions |

### Quyết định giữ nguyên

- Giữ Next.js 16, FastAPI, SQLAlchemy/PostgreSQL, MinIO, Celery/Redis và pipeline
  OCR; không thêm service hoặc cache phân tán mới.
- Giữ `owner_user_id` để audit/contributor/quota, nhưng không dùng nó làm ranh
  giới quyền trên đề `teacher_shared`.
- Giữ binary PDF/image/audio ngoài PostgreSQL. `ExamVersionAsset` chỉ giữ
  metadata/object key và URL phát media tiếp tục qua token + Nginx/MinIO.
- Giữ autosave batch/revision/idempotency và submit receipt hiện tại; mở rộng
  chúng sang version pin thay vì tạo một luồng save mới.
- Không chạy reset khi migrate hoặc container startup. Reset là thao tác
  maintenance riêng sau backup và restore verification.

### Tiêu chí xác nhận implementation

1. Hai Teacher cùng sửa: revision đầu finalize, revision sau nhận 409 và draft
   vẫn còn; Teacher B quản trị được đề A nhưng không thể gán vào lớp không thuộc
   quyền của mình.
2. Mọi Attempt/Public submission/ClassAssignment lưu version; edit/archive/xóa
   sau khi bắt đầu không đổi hash, câu, đáp án, asset hoặc điểm.
3. Payload trước submit không có `correct`, answer key hoặc `solutions`; owner
   của attempt đã submit mới lấy được lời giải, anonymous public không lấy được.
4. Parser hai file mẫu và fixture DOC/PDF xử lý đúng singleton/range, Unicode,
   header lặp, overlap và import một phần; trường lời giải bị giới hạn kích thước.
5. Unique title/Tag được DB bảo vệ dưới concurrent request; list kho phân trang,
   chỉ trả metadata và không phát sinh N+1.
6. Reset dry-run/real trên staging giữ nguyên Admin ID/password hash và đúng hai
   policy, xóa MinIO/Redis/nghiệp vụ, rotate epoch; client epoch cũ bị từ chối.
7. Unit/integration/typecheck/lint/build và k6 staging xác nhận mục tiêu latency,
   zero data loss, 200 submit/solution/audio Range đồng thời. Các chỉ số chưa
   chạy trên staging sẽ được ghi là chưa đo, không suy diễn từ unit test.

## Audit luồng media, audio, PWA và dữ liệu Desktop theo tài khoản (2026-08-08)

Phần này là baseline audit được hoàn thành **trước khi sửa code** cho sự cố
production hiện tại. Phạm vi truy vết gồm Next.js/PWA/Tauri, FastAPI auth và
exam routes, PostgreSQL metadata, MinIO private buckets, Nginx
`X-Accel-Redirect`, SQLite desktop store và luồng login/logout/sync.

### Kiến trúc và call path hiện tại

```text
Web/Tauri quiz
  -> GET/POST /api/v1/exams/* -> FastAPI -> PostgreSQL Exam/Asset
  -> URL /api/v1/exams/<exam>/assets/<file>?token=<JWT>
  -> FastAPI authorize -> X-Accel-Redirect /_protected_minio/<bucket>/<key>
  -> Nginx -> MinIO private bucket

Tauri WebView
  -> /api/desktop/* -> sidecar FastAPI -> một DesktopStore dùng chung
  -> <DESKTOP_DATA_DIR>/desktop.sqlite3 + <DESKTOP_DATA_DIR>/exams/*
  -> frontend trộn local exams với remote exams của tài khoản hiện tại

PWA
  -> /sw.js -> cache-first cho icon/logo/manifest/static image
  -> cache name cố định examify-pwa-v3 và offline asset cache dùng chung origin
```

### Bottleneck/phát hiện trước sửa

| ID | Mức | Phát hiện | Nguyên nhân | Cách sửa dự kiến | Expected impact |
|---|---|---|---|---|---|
| MEDIA-C-01 | CRITICAL | Ảnh/audio exam trả 403 dù metadata và object tồn tại | FastAPI phát `X-Accel-Redirect` chỉ chứa bucket/key; Nginx proxy request anonymous vào MinIO trong khi mọi bucket được `mc anonymous set none`. Internal location chỉ chặn browser gọi trực tiếp, không xác thực Nginx với MinIO | Tạo presigned S3 query cho internal redirect, giữ đúng signed `Host`, Range và không lộ MinIO ra public; fallback streaming chỉ khi không dùng accel | Khôi phục toàn bộ ảnh/audio, vẫn để Nginx truyền byte thay vì giữ Python worker |
| MEDIA-H-02 | HIGH | CI không phát hiện regression 403 media | Test asset hiện chỉ assert response không phải 401; 403 vẫn được coi là pass, không kiểm tra redirect/signature/MinIO path | Thêm unit/integration test cho token asset, owner, anonymous denial và signed internal redirect | Chặn tái diễn lỗi quyền media |
| AUDIO-H-01 | HIGH | Chế độ thi thử không có nút bật/tắt audio; autoplay có thể bị browser chặn im lặng | `quizMode=exam` chỉ render `HiddenExamAudio`; component gọi `audio.play().catch(() => undefined)` và toàn bộ element/control đều hidden. `AudioWavePlayer` chỉ render ở practice | Thêm control play/pause rõ ràng cho exam audio, không cho seek nếu cần giữ quy tắc thi; hiển thị trạng thái khi autoplay bị chặn và dùng cùng URL media đã authorize | Học viên chủ động bắt đầu/tạm dừng audio; không còn trạng thái im lặng không thể phục hồi |
| PWA-H-01 | HIGH | Deploy logo/branding mới nhưng client tiếp tục thấy logo cũ tới khi hard refresh | Service worker cache-first mọi image cùng origin; `/logo.png`, icon và manifest là URL mutable nhưng cache không revalidate. Cache version là literal thủ công và registration không gọi `update()`/`updateViaCache: none` | Network-first/revalidate cho mutable branding/manifest, version URL branding, bump cache schema và chủ động kiểm tra SW update ngoài lúc đang thi | Deploy mới tự cập nhật asset/manifest mà không cần Ctrl+Shift+R |
| PWA-M-02 | MEDIUM | PWA đã cài có thể tiếp tục mang tên Examify dù source manifest hiện là Examify | Manifest cũ từng được cache tối đa theo SW/browser; tên installed app do OS quản lý và không đảm bảo refresh ngay. Một số chuỗi UI/legacy namespace còn dùng Examify nhưng không phải tất cả đều là product label | Không cache manifest dài hạn, làm mới registration; đổi các nhãn product-facing còn sót. Ghi rõ app PWA đã cài từ manifest cũ có thể cần đóng/mở lại hoặc cài lại một lần | Branding mới nhất nhất quán trên tab, install prompt và lần cài mới |
| DESKTOP-C-03 | CRITICAL | Tài khoản Tauri mới nhìn thấy kho đề/lịch sử local của tài khoản trước | `DesktopStore` chỉ có một `desktop.sqlite3`; `local_exams`, `local_attempts`, `classroom_cache`, tags và queue không có `owner_user_id`. `/api/desktop/exams` trả toàn bộ rồi frontend ghép vào danh sách remote đã scope theo user | Thiết lập active desktop user sau auth, namespace/migrate local store theo durable user id, từ chối local user context rỗng, scope exam/history/cache/queue/assets; clear context khi logout | Tài khoản mới có kho local rỗng; dữ liệu và sync intent không rò qua tài khoản khác trên cùng máy |
| DESKTOP-H-05 | HIGH | Sync coordinator có thể chạy trước khi identity local được ràng buộc | `DesktopBootstrap` start coordinator ngay khi mount; local endpoints chỉ cần per-launch desktop secret, không biết remote user | Bind local user trước khi list/sync; coordinator đợi auth-ready event và hủy/đổi namespace khi logout/login | Không sync đề của user cũ bằng token user mới |
| DATA-H-01 | HIGH | Session/local browser data có nhiều key toàn cục | Quiz session là ngắn hạn nhưng offline auth/quota/class session/offline packs dùng key origin-wide; logout không dọn toàn bộ quiz context. Server exam list đã scope đúng `Exam.owner_user_id` | Scope dữ liệu bền theo user/attempt; dọn session exam khi đổi identity; không xóa answer draft đang cần phục hồi nếu chưa xác định owner | Tránh UI trộn state cũ trong khi vẫn bảo toàn đáp án chưa ACK |

### Bằng chứng quan trọng

- Compose tạo năm bucket MinIO private bằng `mc anonymous set none`.
- `MINIO_ACCEL_REDIRECT_PREFIX=/_protected_minio` được bật cho API production.
- Nginx `/_protected_minio/` chỉ đặt `Host minio:9000`; request không có
  `X-Amz-Signature` hoặc Authorization S3, nên private object bị từ chối.
- `_exam_payload()` đã tạo token asset hai giờ và endpoint có kiểm tra token;
  lỗi nằm ở hop Nginx -> MinIO sau authorization, không phải object bị lưu vào
  PostgreSQL hay browser thiếu credential MinIO.
- Server `GET /api/v1/exams` đã lọc `Exam.owner_user_id == current user`.
  Dữ liệu chéo tài khoản quan sát trên Tauri đến từ local SQLite không scope,
  rồi được `my-exams/page.tsx` chủ động ghép với remote list.
- Manifest, metadata Next và Tauri config trên source đều đã là `Examify`;
  việc vẫn thấy `Examify` ở PWA đã cài là stale deployment/cache metadata, dù
  vẫn còn một số chuỗi product-facing cần đổi.

### Phần không cần rewrite

- Giữ PostgreSQL là source of truth phía server và MinIO private; không public
  bucket và không phát credential MinIO cho frontend.
- Giữ Nginx `X-Accel-Redirect` để phục vụ Range/audio hiệu quả, nhưng redirect
  nội bộ phải có presigned query hợp lệ.
- Giữ Tauri + sidecar + SQLite WAL. Chỉ bổ sung isolation theo user và migration
  tương thích, không thay desktop database.
- Giữ cơ chế asset JWT hiện tại cho browser media tags; không buộc mỗi thẻ
  `<img>/<audio>` mang custom device header.
- Không xóa cache offline của đề thi đang làm một cách mù quáng; cache exam
  phải gắn identity/assignment và được dọn có chủ đích.

### Tiêu chí xác nhận implementation

1. Owner mở image/audio qua URL exam nhận 200/206; anonymous/token sai nhận
   403; Nginx internal request tới MinIO mang presigned query hợp lệ.
2. Practice và mock exam đều có nút play/pause; autoplay bị chặn phải hiện nút
   actionable, không nuốt lỗi.
3. Deploy đổi logo/manifest được nhận qua reload thường; `/sw.js` và manifest
   không bị cache stale, hashed Next assets vẫn immutable.
4. Login user B trên cùng Tauri install không thấy exam/history/cache/queue của
   user A và không thể sync chúng bằng token B; login lại A vẫn thấy dữ liệu A.
5. Backend/frontend/DesktopStore regression tests, typecheck và production web
   + desktop build đều pass. Việc MinIO thực nhận 200/206 qua Nginx phải được
   smoke-test trên Compose, không suy diễn chỉ từ mock test.

## Audit luồng Desktop Windows/macOS, offline sync và OCR (2026-08-08)

Phần này là baseline audit được chốt **trước khi sửa code** cho yêu
cầu desktop hiện tại. Audit bao phủ static export Next.js, Tauri/Rust,
Python OCR sidecar, SQLite local store/outbox, API FastAPI/PostgreSQL/MinIO,
Nginx và hai workflow build native Windows/macOS.

### Kiến trúc và call path hiện tại

```text
Tauri WebView (static Next.js export)
  |-- /api/extractions, /api/desktop/* + X-Desktop-Secret
  |       -> Python sidecar 127.0.0.1:<random port>
  |       -> PDF/Poppler/PaddleOCR ONNX Runtime
  |       -> jobs + SQLite WAL + managed exam/audio/image files
  |
  `-- /api/v1/* + short-lived Bearer token
          -> https://toeicdoc.com -> Nginx -> FastAPI
          -> PostgreSQL metadata/exam -> MinIO assets

Finalize local
  -> DesktopStore.save_exam()
  -> local_exams + local_assets + UNIQUE sync_queue intent
  -> Desktop sync coordinator (startup/online/visible/30 seconds)
  -> POST manifest -> PUT missing assets -> POST complete
  -> optional classroom publication intents
```

- Windows build tạo sidecar PyInstaller onedir, đóng gói PaddleOCR models/Poppler và
  NSIS x64; CI smoke-test cả output build lẫn layout sau khi cài.
- macOS build tạo sidecar onefile riêng cho Apple Silicon và Intel, vendor
  dylib, build `.app`/`.dmg`, kiểm tra Mach-O architecture và ad-hoc signature.
- Dữ liệu local là durable: SQLite WAL, file được copy vào thư mục
  app-local-data, queue retry có exponential backoff. Remote sync dùng unique
  `(user_id, client_exam_id)`, SHA-256 manifest/asset và chỉ upload asset thiếu.

### Bottleneck/phát hiện trước sửa

| ID | Mức | Phát hiện | Nguyên nhân | Cách sửa dự kiến | Expected impact |
|---|---|---|---|---|---|
| DESK-C-01 | CRITICAL | Không thể mở lại app vào luồng tạo đề khi offline | `AuthGate` luôn gọi remote `/api/v1/auth/state`; cache role chỉ ở `sessionStorage`, mất khi app restart | Lưu snapshot identity desktop bị giới hạn, chỉ fallback khi lỗi transport/offline; 401/403 vẫn thu hồi phiên | App đã login có thể OCR/finalize/lưu local không cần mạng |
| DESK-C-02 | CRITICAL | Refresh token được ghi plaintext vào `~/.smart_exam_converter/session.dat` trên cả Windows/macOS | `store_refresh_token` ghi fallback file trước khi thử Keychain/Credential Manager và bỏ qua lỗi keyring | Keyring là authoritative trên Windows/macOS, migrate-xóa fallback cũ; không silently ghi token plaintext | Giữ sync tự động mà không làm lộ credential dài hạn |
| DESK-H-01 | HIGH | Chưa có integration test cho toàn bộ local sidecar -> remote sync -> MinIO -> retry/restart | Test hiện tại chủ yếu test store và mock frontend; coordinator không có coverage | Thêm test transport/retry/idempotency, queue qua restart, online event/single-flight và manifest update | Chứng minh local/web thật sự đồng bộ thay vì chỉ có code path |
| DESK-H-02 | HIGH | Hai request sync/complete đồng thời có thể tranh chấp record unique hoặc cùng finalize | Server check-then-insert và `_owned_sync` đọc detached row không lock; `complete` không serialize theo sync row | UPSERT/IntegrityError recovery cho init; row lock + recheck trong complete, giữ MinIO ngoài transaction | Duplicate/retry không tạo hai đề hoặc tăng quota hai lần |
| DESK-H-03 | HIGH | Chỉ Tauri startup giữ `CommandChild`; sidecar thoát sau startup không tự khôi phục | Event loop chỉ log output, không ghi readiness/lifecycle state hoặc restart | Expose health/lifecycle rõ cho UI và bounded restart hoặc actionable recovery | Tránh app mở nhưng OCR/sync local chết im lặng |
| DESK-H-04 | HIGH | Local sync gửi access token trong JSON body tới sidecar và HTTP client timeout tổng quá dài | Sidecar cần proxy upload; request model lưu token trong object body và không tách connect/read/write/pool | Chuyển token sang header loopback hoặc giữ strictly in-memory, không log; timeout phân pha và error phân loại | Giảm rủi ro credential và retry đúng theo lỗi |
| DESK-M-01 | MEDIUM | `DesktopStore` khởi tạo schema/migration lặp lại mỗi endpoint | Mỗi route tạo instance mới và chạy `executescript(SCHEMA)` | Tạo singleton trong lifespan/app state, vẫn mở connection ngắn theo operation | Giảm lock/schema I/O cục bộ, đặc biệt khi UI poll |
| DESK-M-02 | MEDIUM | Retry queue không có lease/`syncing`; hai WebView event gần nhau chỉ được chặn bằng biến JS trong một runtime | SQLite queue có status nhưng worker không claim atomically | Atomic claim/lease và reset lease stale; frontend single-flight vẫn giữ | Chịu được reload/multi-window/crash giữa sync |
| DESK-M-03 | MEDIUM | macOS artifact chỉ ad-hoc, không notarized | Không có Apple Developer ID/notarization | Giữ hướng dẫn quarantine cho internal; production public cần Developer ID | Build có thể chạy nhưng không thể hứa Gatekeeper one-click |
| OCR-H-01 | HIGH | Heuristic tự nhận diện cover/direction có thể skip nhầm trang đầu và làm lệch crop | File upload thực tế đã được người dùng tự cắt bìa; detector dựa OCR là không cần thiết và dễ sai với trang ảnh Listening | Bỏ auto-skip; luôn xử lý từ physical page 1. Listening yêu cầu trang 1 là ảnh 1/2; Reading yêu cầu trang 1 chứa câu 101 | Không còn lệch số trang do detector; trách nhiệm cắt bìa rõ ràng ở input |
| OCR-M-01 | MEDIUM | Windows và macOS dùng hai PyInstaller layout/spec khác nhau; test native chỉ chạy trong GitHub runner tương ứng | Không thể cross-run Mach-O/NSIS sidecar trên Linux | Giữ matrix native, smoke OCR sau bundle/install và upload diagnostics; local Linux chỉ là static/unit gate | Phát hiện thiếu DLL/dylib/model trước release |

### Phần đang đúng và không cần rewrite

- Giữ Tauri + Python sidecar + SQLite; không cần Electron hay database mới.
- Giữ `client_exam_id`, unique constraint server, SHA-256 manifest/asset và
  upload missing-only. Đây là nền tảng idempotency phù hợp.
- Giữ asset trong managed local directory và MinIO; không lưu binary vào
  SQLite/PostgreSQL.
- Giữ coordinator startup + `online` + visibility + chu kỳ 30 giây, nhưng
  bổ sung identity offline, durable claim và test thay vì thay queue.
- Giữ hai macOS target native và Windows x64; không ghép universal sidecar.
- Giữ PaddleOCR/Poppler đóng gói và fallback provider CPU an toàn; tối ưu accuracy
  phải dựa trên golden fixtures, không chỉ benchmark thời gian.

### Tiêu chí xác nhận cho implementation

1. Login online một lần, tắt app, mất mạng, mở lại vẫn tạo,
   OCR, review và finalize đề local được.
2. Restart app/database local không mất exam, asset hay queue intent.
3. Khi có mạng, không cần bấm Sync: coordinator lấy token mới, upload
   đủ checksum, complete đề và ghi `remote_exam_id`.
4. Retry/duplicate init, asset và complete trả cùng exam; manifest mới cập
   nhật đúng record, không tạo duplicate/quota drift.
5. Windows NSIS và macOS arm64/x64 bundle chạy smoke OCR bằng binary/resource
   bên trong artifact, không vô tình dùng Homebrew/Python của runner.
6. Typecheck, frontend/backend/Rust tests và static config checks đều pass;
   giới hạn nền tảng nào không thể chạy trên host Linux phải được
   báo cáo trung thực, không suy diễn là đã pass native.

### Kết quả implementation/verification

| Gate | Kết quả |
|---|---|
| Backend full suite | **PASS: 93 passed, 5 skipped** (5 PDF golden fixtures không có trong repository) |
| Frontend full suite | **PASS: 48 passed** |
| TypeScript | **PASS** |
| Next production web build | **PASS**; arbitrary `/public-test/<code>` trả HTTP 200 trên standalone server |
| Next static desktop export | **PASS: 23 routes**; đã sửa lỗi `generateStaticParams` |
| Rust host tests | **PASS: 2 passed**; `cargo fmt --check` pass |
| Tauri release compile | **PASS trên Linux `tauri build --no-bundle`** |
| Fresh PyInstaller sidecar | **PASS** build + OCR/finalize/SQLite/attempt/restart/outbox smoke |
| Remote desktop sync integration | **PASS** checksum sai bị từ chối, checksum đúng upload, premature complete bị chặn, changed manifest reconcile, duplicate/stale completion trả cùng exam |
| Compose profiles | **PASS** default, monitoring, backup, restore và TLS config |
| Native Windows/macOS artifact | **PENDING NATIVE CI**: Linux thiếu MSVC `lib.exe` và Apple clang/SDK; workflow NSIS + macOS arm64/x64 đã có full bundle/smoke gate |

Những thay đổi đã xử lý DESK-C-01, DESK-C-02, DESK-H-01,
DESK-H-02, DESK-M-02 và OCR-H-01. DESK-H-03 (bounded sidecar auto-restart)
chưa được thêm: app hiển thị lỗi actionable và native smoke kiểm tra
restart, nhưng sidecar crash bất ngờ trong một phiên vẫn cần restart app.
DESK-M-03 là giới hạn chủ đích của ad-hoc signing.

## Audit hiện hành — production 300 concurrent, stretch 400 (2026-08-08)

Phần này là kết luận audit có hiệu lực và trạng thái implementation của source
hiện tại. Các mục bên dưới được giữ lại như **audit lịch sử trước tối ưu** để
không làm mất bằng chứng; chúng không còn mô tả implementation hiện tại.

### Phạm vi, máy đích và giới hạn kết luận

- Workload: 300–400 tài khoản, production gate là **300 học viên active đồng
  thời**; 400 chỉ là stretch test.
- Máy đơn Linux: Intel Core i5-12400F (6 core vật lý/12 thread), 32 GB RAM,
  SSD 512 GB, uplink giả định 1 Gbps.
- PostgreSQL là nguồn dữ liệu chuẩn; Redis chỉ chứa cache, rate-limit và
  presence có thể tái tạo; MinIO chứa object/media.
- OCR phải dừng hoặc giới hạn concurrency trong cửa sổ thi.
- Một host và một SSD không phải HA. RTO 30 phút chỉ áp dụng cho lỗi
  container/OS/reboot; hỏng SSD có thể cần nhiều giờ để phục hồi từ offsite.
- Chưa có kết quả load test 300 VU trên đúng staging/profile production, vì
  vậy audit này **không tuyên bố capacity đã được chứng nhận**.

### Kiến trúc source hiện tại

```text
Browser / Next.js 16 / React 19
              |
       Nginx TLS + rate/body limits
          |                  |
  FastAPI stateless      X-Accel-Redirect
     |      |      |             |
PostgreSQL Redis Celery        MinIO
                 |               |
             OCR/maintenance   audio/image/PDF
```

- API FastAPI dùng SQLAlchemy/PostgreSQL và pool theo process.
- Đề thi được đóng băng trong `exam_versions`; attempt/answer được bảo vệ bởi
  unique constraint, revision, row lock và batch upsert.
- Frontend dùng localStorage + singleton IndexedDB durable outbox; giao thức
  mới gửi delta tối đa 50 câu mỗi 10 giây với jitter và chỉ một request/attempt.
- File lớp học được authorize bởi FastAPI rồi truyền Range qua Nginx/MinIO;
  byte media không đi xuyên qua Python.
- Docker Compose có resource limit 32 GB, health/readiness, log rotation,
  monitoring, TLS, pgBackRest/WAL archive và off-host mirror.

### Bottleneck audit và trạng thái xử lý

| ID | Mức | Bottleneck trước sửa | Trạng thái source | Evidence/còn phải xác nhận |
|---|---|---|---|---|
| C-300-01 | CRITICAL | IP limit chặn NAT trường học | Đã thay bằng Redis/Lua token bucket theo IP/user/email/attempt | Unit test pass; phải chạy NAT storm 300 account |
| H-300-01 | HIGH | Autosave full snapshot sau 700 ms | Đã có delta, revision, UUID ledger, outbox 10 giây+jitter | Sync query gate <=5; cần browser/offline + load test |
| H-300-02 | HIGH | Heartbeat ghi DB mỗi 10 giây | Presence Redis TTL 75 giây, DB checkpoint 60 giây | Cần đo WAL/write rate ở 300 VU |
| H-300-03 | HIGH | Autosave deep-copy đề 200 câu | Projection `exam_version_questions` + joined context | Migration backfill 200 rows pass |
| H-300-04 | HIGH | Finalize hàng trăm attempt trong một transaction | Batch 25 + `SKIP LOCKED` | Unit/integration pass; cần peak timeout thật |
| H-300-05 | HIGH | k6 dùng chung user/attempt | Fixture 1 user/device/token/attempt mỗi VU + exact SQL verifier | Harness syntax pass; runtime chưa chạy |
| H-300-06 | HIGH | External Directions/preload/bitrate cao | Internal Directions, metadata/current audio, MP3 128 kbps worker rendition | Audio unit tests pass; cần đo 300 Range stream |
| H-300-07 | HIGH | Một SSD là SPOF | pgBackRest/WAL + versioned off-host mirror + restore runbook | Config pass; restore drill/RPO thực tế chưa đo |
| M-300-01 | MEDIUM | Auth tra User + Device liên tục | Redis TTL 30 giây, invalidation, joined DB fallback | Unit/integration pass; cần đo hit rate |
| M-300-02 | MEDIUM | Monitoring gửi history mỗi 5 giây | Live 10 giây, history/export on-demand phân trang | Cần profile teacher dashboard thật |
| M-300-03 | MEDIUM | Admin aggregate N+1/unbounded lists | Aggregate query + pagination hard limit | Integration tests pass |
| M-300-04 | MEDIUM | Runtime budget theo host nhỏ cũ | 32 GB budget, API 4 workers/pool 4+2, PostgreSQL max 80 | Compose pass; cần tune bằng 300 VU |

Kết quả repository mới nhất: backend `88 passed, 5 skipped`; frontend `45
passed`; TypeScript/Next.js 16.3.0 build pass; `/quiz` 215,4 KiB gzip;
`npm audit --omit=dev` không còn vulnerability; Alembic database trắng và
legacy `0019` → `0020`/200-row backfill pass; mọi Compose profile, Nginx
HTTP/TLS và Prometheus 14 rules đều hợp lệ. Đây là bằng chứng correctness/config,
không phải chứng nhận capacity.

### Bất biến dữ liệu bắt buộc

1. Click phải được ghi local trước network; refresh, offline 5–30 giây và retry
   không được làm mất delta chưa ACK.
2. Database bảo vệ uniqueness theo attempt/câu hỏi; client không phải nguồn đảm
   bảo toàn vẹn.
3. Sync retry cùng `batch_id` phải idempotent; stale revision trả conflict cùng
   canonical snapshot, không silently overwrite.
4. Submit cuối cùng lưu snapshot, chấm điểm, trạng thái và durable receipt trong
   một transaction ngắn; duplicate submit trả cùng receipt.
5. Không gọi MinIO/Redis/external service trong transaction submit.

### Phần không cần thay đổi

- Không rewrite Next.js, FastAPI, SQLAlchemy, PostgreSQL hoặc MinIO.
- Không thêm Kubernetes, microservice, Kafka/RabbitMQ hay Redis làm nguồn dữ
  liệu đáp án.
- Chưa cần PgBouncer khi tổng pool được giữ dưới ngân sách 80 connections và
  load test chưa chứng minh pool là bottleneck.
- Không proxy byte audio qua FastAPI; giữ cơ chế authorize + X-Accel hiện tại.
- Không tạo index tràn lan. Chỉ thêm projection PK và latest-attempt composite;
  mọi index khác phải qua `EXPLAIN (ANALYZE, BUFFERS)` với dữ liệu staging.

### Production gate sau implementation

- 300 VU: p50 <100 ms, p95 <300 ms, p99 <500 ms, error <0,1%, chạy đạt ba
  lần và soak tối thiểu hai giờ.
- Peak submit 300: p95 <1 giây, p99 <2 giây, không thiếu/trùng receipt.
- CPU sustained <70%, RAM <80%, PostgreSQL connections <60, pool-wait p95
  <50 ms; không OOM/swap thrash/full scan lớn ở hot path.
- 400 VU chỉ được ghi là stretch; dù latency đạt, không go-live nếu verifier
  phát hiện dù chỉ một đáp án sai hoặc mất.

---

## Audit lịch sử trước tối ưu (2026-08-04)

Ngày audit: 2026-08-04  
Phạm vi: toàn bộ repository `Tool_Tao_De`, tập trung vào kỳ thi online có 300–400 tài khoản và khoảng 200 thí sinh hoạt động đồng thời.  
Hạ tầng mục tiêu dùng để đánh giá: Linux, 8 CPU, **12 GB RAM theo giả định bảo thủ**, 100 GB SATA SSD, PostgreSQL và MinIO.

> Đây là baseline được chốt trước khi sửa mã. Trạng thái sau triển khai nằm trong `OPTIMIZATION_REPORT.md`; các phát hiện bên dưới không bị viết lại để tránh làm mất dấu audit ban đầu.

> `AGENTS.md` đồng thời nhắc 16 GB và 12 GB RAM. Báo cáo dùng mốc thấp hơn là 12 GB cho tới khi cấu hình triển khai được chốt.

## 1. Kết luận điều hành

Hệ thống có nền tảng đúng cho một ứng dụng thi trực tuyến: đề thi được đóng băng theo phiên bản, đáp án có ràng buộc duy nhất, chấm điểm xác định, file được giữ trong MinIO riêng tư, upload được stream và các tác vụ OCR nặng đã được đưa sang Celery. Frontend cũng đã debounce autosave và backend không dùng `SELECT *` thô.

Tuy nhiên, **chưa nên cam kết phục vụ an toàn 200 thí sinh đồng thời** ở trạng thái hiện tại. Các nguyên nhân chính là:

1. Frontend có thể hiển thị kết quả và xóa trạng thái phiên thi ngay cả khi submit lên server thất bại. Autosave lỗi bị bỏ qua, và khi tải lại trang, bản server cũ có thể ghi đè đáp án mới hơn còn ở máy thí sinh. Đây là rủi ro mất câu trả lời có mức **CRITICAL**.
2. Lưu một bộ 200 đáp án hiện thực hiện khoảng một truy vấn kiểm tra cho từng câu rồi mới insert/update. Đợt submit đồng loạt tạo ra hàng chục nghìn truy vấn nhỏ, đúng lúc tải cao nhất.
3. Pool kết nối được tạo riêng trong từng process. Cấu hình hiện tại có thể cho phép khoảng 120 kết nối từ API, worker và scheduler, cao hơn giới hạn PostgreSQL mặc định thường gặp, chưa tính công cụ quản trị.
4. Audio của lớp học được proxy qua FastAPI; mỗi luồng cần xác thực qua database rồi truyền toàn bộ byte qua API. Hai trăm lượt nghe đồng thời có thể chiếm worker, kết nối MinIO, băng thông và CPU của cùng máy phục vụ autosave/submit.
5. Chưa có backup/restore tự động, kiểm thử tải, số liệu latency/error/DB pool, hay readiness probe thực sự cho PostgreSQL/Redis/MinIO. Vì vậy chưa có bằng chứng vận hành để xác nhận mục tiêu tải và không có RPO/RTO rõ ràng.

Ưu tiên triển khai nên là: bảo toàn đáp án và submit có idempotency; bulk upsert và kiểm soát pool; tách đường truyền media khỏi API; sau đó mới tối ưu truy vấn danh sách, cache và tinh chỉnh frontend.

## 2. Phương pháp và giới hạn

Audit gồm:

- đọc cấu trúc và call path bằng CodeGraph;
- đọc mã backend, frontend, migration, Docker Compose, nginx và cấu hình Celery;
- kiểm tra schema/index/ràng buộc trong model và migration;
- chạy test backend/frontend, typecheck và production build;
- kiểm tra trạng thái git để không ghi đè thay đổi đang có của người dùng.

Không có dataset production hoặc phiên bản database có kích thước tương đương thực tế trong phạm vi audit. Vì vậy:

- các index đề xuất là **ứng viên**, phải xác nhận bằng `EXPLAIN (ANALYZE, BUFFERS)` trên dữ liệu gần production;
- chưa có số p50/p95/p99, throughput hoặc ngưỡng bão hòa đáng tin cậy;
- không tuyên bố hệ thống hiện chịu được 200 người dùng cho tới khi hoàn tất load test và soak test.

## 3. Kiến trúc hiện tại

```text
Browser / Next.js 16
        |
      nginx
        |
  FastAPI (2 Uvicorn workers)
    |       |        |
PostgreSQL Redis    MinIO
              \       /
               Celery worker (concurrency 1)
               Celery beat
```

- Backend: FastAPI, endpoint chủ yếu là hàm đồng bộ, SQLAlchemy 2 + psycopg.
- Dữ liệu nghiệp vụ: PostgreSQL.
- Object: MinIO cho đề, audio, asset và bài nộp.
- Background jobs: Redis + Celery cho OCR, xử lý ảnh và maintenance.
- Frontend: Next.js 16, React 19.
- Triển khai: Docker Compose và nginx; API chạy 2 Uvicorn workers.
- Desktop: Tauri/sidecar SQLite là luồng bổ sung, không nằm trên đường nóng của kỳ thi web.

### Đường nóng của một kỳ thi

```text
Start attempt
  -> tải toàn bộ snapshot đề + URL asset đã ký
  -> phát audio qua FastAPI -> MinIO

Trong khi thi
  -> PATCH toàn bộ map đáp án sau 700 ms không đổi
  -> heartbeat mỗi 10 giây
  -> sự kiện anti-cheat theo từng hành vi
  -> mỗi HTTP request còn cập nhật last_seen ở device/member

Submit
  -> duyệt từng đáp án: SELECT rồi INSERT/UPDATE
  -> tải đáp án để chấm
  -> lưu điểm/trạng thái
  -> tải lại dữ liệu để dựng kết quả
```

## 4. Danh mục phát hiện

| ID | Mức | Khu vực | Phát hiện |
|---|---|---|---|
| C-01 | CRITICAL | Độ bền đáp án | Client coi submit thất bại như đã hoàn tất, xóa attempt cục bộ và chuyển sang trang kết quả |
| C-02 | CRITICAL | Database | Autosave/submit dùng upsert kiểu N+1 cho từng đáp án, gây bão truy vấn ở đỉnh submit |
| C-03 | CRITICAL | Database | Tổng trần pool theo process có thể đạt khoảng 120 kết nối, chưa có PgBouncer hay ngân sách kết nối |
| C-04 | CRITICAL | Khôi phục dữ liệu | Chưa có backup định kỳ, retention, mã hóa, kiểm tra restore cho PostgreSQL/MinIO |
| H-01 | HIGH | Đồng thời submit | Chưa khóa attempt khi submit và chưa có idempotency key/receipt rõ ràng |
| H-02 | HIGH | Autosave | Không có hàng đợi durable, version/sequence và cơ chế hòa giải server–client |
| H-03 | HIGH | Media | Classroom asset/audio được proxy byte qua FastAPI sau nhiều truy vấn xác thực |
| H-04 | HIGH | Write amplification | Mọi request cập nhật `device.last_seen_at`; request lớp học còn cập nhật `member.last_seen_at` |
| H-05 | HIGH | Query/API | Nhiều danh sách N+1 hoặc không phân trang; monitoring trả cả lịch sử sau mỗi 5 giây |
| H-06 | HIGH | Production | Readiness không kiểm tra PostgreSQL, Redis hoặc MinIO |
| H-07 | HIGH | Worker | Một worker concurrency 1 xử lý chung OCR dài và maintenance, có thể làm trễ auto-finalize |
| H-08 | HIGH | Bảo vệ tải | API đáp án không có giới hạn item/body/rate phù hợp; auth limit theo IP dễ ảnh hưởng NAT chung |
| H-09 | HIGH | Xác minh tải | Không có k6/Locust/Artillery, soak test hoặc tiêu chí p95/p99/error rate |
| H-10 | HIGH | Vận hành | Thiếu metrics, request ID, cảnh báo pool/DB/queue/disk và giới hạn log |
| M-01 | MEDIUM | Auth | Identity WebSocket truy vấn DB mỗi 2 giây trên các trang có Header và dùng DB đồng bộ trong async loop |
| M-02 | MEDIUM | Payload | Một số API đọc entity chứa JSON đề đầy đủ dù response chỉ cần metadata |
| M-03 | MEDIUM | Index | Thiếu một số composite/partial index có khả năng hỗ trợ hot query |
| M-04 | MEDIUM | Frontend | Heartbeat 10 giây và anti-cheat theo event tạo tải nền đáng kể |
| M-05 | MEDIUM | Deploy | Chưa có resource limit, PostgreSQL tuning, timeout DB hoặc tách tài nguyên workload |
| M-06 | MEDIUM | Migration | API vẫn gọi `create_all`; migration gốc phụ thuộc metadata hiện tại, giảm tính tái lập |
| M-07 | MEDIUM | Security | `.env` có quyền đọc rộng hơn cần thiết và chứa credential ngoài phạm vi runtime |
| M-08 | MEDIUM | HTTP | Thiếu nén/caching/static policy chi tiết; timeout và body limit đang quá rộng theo location |
| L-01 | LOW | Frontend | Timer tạo lại interval mỗi giây |
| L-02 | LOW | Asset | Logo công khai gần 1 MB và đang dùng chế độ không tối ưu ảnh |
| L-03 | LOW | Tài liệu | README còn conflict marker và tham chiếu tài liệu không tồn tại |

## 5. Phân tích chi tiết và hướng xử lý

### D-01 — Artifact macOS ARM tải về bị Gatekeeper chặn (CRITICAL nếu phát hành đại trà)

**Bằng chứng audit ngày 2026-08-04**

- Trước thay đổi, `.github/workflows/macos-release.yml` không truyền
  `APPLE_CERTIFICATE`, không import Developer ID certificate và không chạy
  notarization/stapling; artifact khi đó là unsigned.
- Hiện tại `src-tauri/tauri.conf.json` đặt `bundle.macOS.signingIdentity: "-"`
  và workflow ép `APPLE_SIGNING_IDENTITY=-`, nên artifact được ký ad-hoc.
- Verifier CI kiểm tra app sau khi đóng gói vào DMG/ZIP; hộp thoại Gatekeeper
  còn lại là giới hạn dự kiến của ad-hoc, không phải bằng chứng sidecar OCR làm
  hỏng dữ liệu. Lỗi xảy ra trước khi Tauri/Rust có cơ hội khởi động.

**Nguyên nhân gốc và giới hạn đã chấp nhận**

Commit `77e064b` đã biến bản tag thành unsigned; đó là nguyên nhân của lỗi
“app bị hỏng” ban đầu. Ad-hoc signing hiện khôi phục chữ ký cho ARM/Intel mà
không cần Apple Developer, nhưng không tạo được danh tính nhà phát triển hoặc
notarization ticket. Vì vậy Gatekeeper vẫn có thể từ chối app có quarantine;
đây là giới hạn bảo mật của lựa chọn miễn phí, không phải lỗi build.

**Cách sửa đã triển khai**

1. Cấu hình `signingIdentity: "-"` cho Tauri và không dùng Apple secrets.
2. Verify `codesign --verify`; chạy `spctl --assess` ở chế độ thông tin và ghi
   nhận việc ad-hoc có thể bị từ chối.
3. Kiểm tra chữ ký sau khi mount DMG và sau khi giải nén `.app.zip`; tạo
   checksum để phát hiện artifact bị thay đổi khi tải.
4. Giữ hai target native độc lập (`aarch64-apple-darwin`,
   `x86_64-apple-darwin`), kiểm tra `file`/Mach-O architecture và smoke-test
   sidecar trên runner tương ứng.
5. Đưa `MACOS_ADHOC_INSTALL.md` vào GitHub Release để tester biết cách dùng
   **Open Anyway** hoặc gỡ quarantine trên đúng bundle đã xác minh.

**Expected impact**

Bản ARM tải từ release có ad-hoc signature, không còn là Mach-O unsigned và
được kiểm tra đúng kiến trúc. Người dùng vẫn có thể cần `xattr -dr
com.apple.quarantine /Applications/Examify.app`; ad-hoc không thể hứa hẹn
Gatekeeper tự mở như Developer ID/notarized. Việc kiểm tra sau đóng gói bắt
được lỗi mất quyền execute, sidecar sai target hoặc DMG bị thay đổi.

**Không cần sửa**

Rust device identity, Keychain, OCR sidecar và logic answer không liên quan đến
hộp thoại Gatekeeper này; chỉ cần giữ các smoke test hiện có để tránh hồi quy
runtime sau khi bundle đã được ký.

### C-01 — Client có thể báo nộp thành công giả

**Bằng chứng**

- `frontend/app/quiz/page.tsx:680-725`: khi request submit lỗi hoặc trả status không thành công, code vẫn tạo `quiz-result`, xóa `quiz-attempt-id` cùng dữ liệu phiên và điều hướng sang kết quả.
- Lỗi autosave trong `frontend/app/quiz/page.tsx:233-253` bị bỏ qua.
- Khi reload, `frontend/app/quiz/page.tsx:166-199` nhận đáp án server rồi ghi đè state/session storage, nên bản local mới hơn nhưng chưa đồng bộ có thể bị mất.

**Nguyên nhân gốc**

Client không phân biệt `local result`, `server accepted`, `pending retry` và `conflict`. Không có số thứ tự bản ghi, acknowledgement hay outbox durable cho đáp án.

**Khuyến nghị**

1. Chỉ chuyển sang kết quả chính thức sau khi server trả receipt có `attempt_id`, `submitted_at`, trạng thái và revision đã chấp nhận.
2. Dùng state machine: `editing -> saving -> saved/pending -> submitting -> submitted`.
3. Lưu outbox đáp án trong IndexedDB, không chỉ sessionStorage; retry có backoff và jitter.
4. Mỗi batch có `client_revision` tăng đơn điệu; server trả `accepted_revision`.
5. Khi reconnect/reload, hòa giải theo revision/timestamp rõ ràng, không ghi đè mù.
6. Nếu submit chưa được xác nhận, giữ nguyên attempt và hiển thị trạng thái “đang chờ gửi”; cung cấp nút retry.

**Tác động kỳ vọng**

Loại bỏ đường mất đáp án do mạng chập chờn và tránh để thí sinh hiểu nhầm bài đã được server ghi nhận.

### C-02 — Upsert đáp án N+1

**Bằng chứng**

- `backend/classroom_api.py:742-784`: với mỗi câu trả lời, thực hiện một `SELECT` tìm bản ghi rồi insert/update; cuối cùng lại `COUNT`.
- `backend/platform_api.py:1325-1358`: luồng đề cá nhân có cùng mẫu truy vấn.
- `backend/platform_api.py:1361-1402`: submit cá nhân còn gọi save trong transaction riêng rồi mở transaction thứ hai để chấm.

Với batch 200 câu, một request có thể tạo khoảng 200 lần lookup cộng các write và truy vấn chấm điểm. Nếu 200 thí sinh nộp gần nhau, riêng bước lookup đã có thể lên tới khoảng 40.000 truy vấn nhỏ.

**Khuyến nghị**

- Chuẩn hóa payload thành danh sách hàng đã validate.
- Dùng PostgreSQL `INSERT ... ON CONFLICT (attempt_id, question_number) DO UPDATE` theo một batch hoặc batch nhỏ cố định.
- Chấm điểm và chuyển trạng thái trong cùng transaction.
- Tránh `COUNT` sau lưu nếu có thể suy ra từ batch hoặc lấy bằng `RETURNING`.
- Giới hạn số câu theo snapshot đề ở cả schema request và business validation.

**Tác động kỳ vọng**

Giảm query count từ tuyến tính theo số câu xuống số batch cố định, rút ngắn transaction và giảm tranh chấp pool đúng thời điểm submit đồng loạt.

### C-03 — Ngân sách kết nối PostgreSQL chưa an toàn

**Bằng chứng**

- `backend/database.py:18-24`: mỗi engine dùng `pool_size=10`, `max_overflow=20`, tức tối đa 30 kết nối/process.
- `docker-compose.yml`: API có 2 Uvicorn workers; Celery worker và Celery beat là các process riêng cùng import engine.

Trần lý thuyết là khoảng `2 x 30 + 30 + 30 = 120` kết nối, chưa tính migration, shell/admin và các process phát sinh. Không có `pool_timeout`, `pool_recycle`, PgBouncer hoặc cấu hình `max_connections` trong repository.

**Khuyến nghị**

- Lập ngân sách kết nối từ `max_connections`, luôn giữ headroom cho maintenance/admin.
- Bước đầu đặt pool nhỏ theo vai trò, ví dụ API 5–8/process, worker 2–4, beat 1–2; giá trị cuối phải dựa trên load test.
- Thêm `pool_timeout`, `pool_recycle` và timeout phía PostgreSQL: `statement_timeout`, `lock_timeout`, `idle_in_transaction_session_timeout` phù hợp.
- Cân nhắc PgBouncer transaction pooling khi số process/replica tăng.
- Xuất metric checked-out/wait/overflow và cảnh báo trước khi cạn pool.

**Tác động kỳ vọng**

Tránh lỗi dây chuyền khi pool hoặc PostgreSQL cạn kết nối và biến thời gian chờ vô hạn thành lỗi có kiểm soát, quan sát được.

### C-04 — Chưa có chiến lược backup/restore

Repository không có job backup PostgreSQL/MinIO, retention, off-host copy, kiểm tra checksum, mã hóa hoặc diễn tập restore. Redis AOF đã bật nhưng Redis không phải nguồn dữ liệu đáp án chính.

**Khuyến nghị tối thiểu**

- PostgreSQL: backup full hằng ngày + WAL/PITR nếu RPO yêu cầu; giữ nhiều thế hệ và một bản off-host.
- MinIO: versioning/replication hoặc `mc mirror` có retention sang nơi độc lập.
- Mã hóa backup, tách credential backup khỏi app.
- Job xác minh backup và diễn tập restore định kỳ vào môi trường cô lập.
- Chốt RPO/RTO bằng văn bản; theo dõi tuổi bản backup cuối và dung lượng đĩa.

Không được xem backup là hoàn tất chỉ vì file dump được tạo; restore test mới là bằng chứng.

### H-01 — Submit đồng thời chưa được tuần tự hóa đầy đủ

`backend/classroom_api.py:2655-2670` kiểm tra trạng thái rồi lưu/chấm nhưng không khóa row attempt bằng `SELECT ... FOR UPDATE`. Hai submit đồng thời có thể cùng nhìn thấy `in_progress`; hai insert đáp án mới có thể va vào unique constraint. Luồng cá nhân tách save và finalize thành hai transaction, tạo thêm race window. Submit lại của đề cá nhân trả lỗi thay vì receipt kết quả ổn định.

**Khuyến nghị**

- Khóa attempt trong transaction submit hoặc dùng conditional update kiểu `WHERE status='in_progress'` và kiểm tra row count.
- Dùng idempotency key gắn với attempt, lưu receipt và trả cùng kết quả cho request lặp lại.
- Bulk upsert đáp án, finalize và receipt trong một transaction.
- Thêm test hai request submit song song, retry sau timeout và duplicate payload.

### H-02 — Autosave chưa có giao thức đồng bộ bền vững

Debounce 700 ms giảm số request nhưng mỗi lần gửi toàn bộ map đáp án. Không có batch delta, revision, server acknowledgement, retry queue hay backpressure. `time_left` do client gửi cũng không nên là nguồn sự thật duy nhất.

**Khuyến nghị**

- Gửi delta đã thay đổi theo batch 1–2 giây, flush khi đổi trang/visibility/submit.
- Mỗi batch có `client_revision`, `client_saved_at` và idempotency key.
- Server lưu `accepted_revision`, deadline server-side và trả acknowledgement.
- IndexedDB outbox retry với exponential backoff + jitter; giới hạn một request đang bay/attempt.
- Submit phải flush outbox và xác nhận server đã nhận revision cuối.

### H-03 — Audio đi xuyên qua API

`backend/classroom_api.py:544-696` phát token URL tới backend; endpoint asset giải token, đọc asset/assignment/member từ DB rồi `get_object` MinIO và stream byte. Range request được hỗ trợ và cache private 1 giờ là điểm tốt, nhưng API vẫn nằm trong data path.

**Khuyến nghị**

- Sau khi xác thực quyền một lần, trả presigned MinIO URL sống ngắn hoặc dùng nginx `X-Accel-Redirect`/internal location.
- Giữ bucket private, TTL ngắn, content disposition/type cố định và audit access.
- Nếu cần chống chia sẻ nghiêm ngặt, dùng signed URL ở reverse proxy/object gateway thay vì Python streaming.
- Không preload toàn bộ audio khi chưa cần; preload metadata và warm-up có kiểm soát trước giờ thi.

**Tác động kỳ vọng**

Giải phóng API worker và DB khỏi lưu lượng byte lớn, để tài nguyên phục vụ autosave/submit.

### H-04 — Ghi `last_seen` quá thường xuyên

- `backend/auth_service.py:166-233`: hầu hết request đã xác thực cập nhật `device.last_seen_at`.
- `backend/classroom_api.py:238-258`: request lớp học còn cập nhật `member.last_seen_at`.
- Client gửi heartbeat mỗi 10 giây tại `frontend/app/quiz/page.tsx:332-354`.

Với 200 thí sinh, riêng heartbeat là khoảng 20 request/giây và ít nhất hai UPDATE/request, chưa tính autosave, sự kiện và submit.

**Khuyến nghị**

- Chỉ ghi khi mốc cũ hơn 30–60 giây, hoặc gom heartbeat thành upsert batch.
- Không cập nhật device trên mọi request business; cache/throttle presence bằng Redis rồi flush định kỳ nếu cần lịch sử.
- Tách “session còn sống” khỏi dữ liệu audit lâu dài.

### H-05 — Danh sách N+1 và response tăng không giới hạn

Các điểm điển hình:

- `backend/classroom_api.py:894-903`: payload mỗi classroom chạy thêm các count riêng.
- `backend/classroom_api.py:1608-1684`: mỗi assignment đọc version, đếm attempt và lấy attempt mới nhất.
- lịch sử attempt trong `backend/platform_api.py:1405-1459` tải thêm question/answer cho từng attempt;
- monitoring giáo viên được frontend poll mỗi 5 giây (`frontend/app/classrooms/[id]/ClassroomDetail.tsx:250-274`) và backend trả cả attempt hiện tại lẫn lịch sử, không phân trang.

**Khuyến nghị**

- Thay query trong vòng lặp bằng aggregate/subquery/window function hoặc select-in theo batch.
- Pagination/cursor cho history, events, users và attempts.
- Monitoring chỉ trả assignment hiện tại, delta kể từ cursor hoặc snapshot gọn; tải history ở endpoint riêng.
- Tránh chọn cột JSON đề khi danh sách chỉ cần metadata.

### H-06 — Readiness có thể báo xanh khi persistence hỏng

`backend/main.py:252-264` kiểm tra dependency OCR và cờ cấu hình nhưng không thực hiện probe PostgreSQL, Redis hoặc MinIO. Nginx dùng endpoint này làm `/health`.

**Khuyến nghị**

- Liveness chỉ kiểm tra process/event loop.
- Readiness thực hiện probe ngắn có timeout cho DB, Redis và MinIO; phân biệt dependency bắt buộc/tùy chọn.
- Không chạy truy vấn nặng; trả lý do machine-readable và metric trạng thái.

### H-07 — OCR có thể làm maintenance đói hàng đợi

Celery worker concurrency 1 cùng nghe `ocr,image,maintenance`, trong khi OCR có time limit tới hàng chục phút và beat xếp auto-finalize mỗi 30 giây. Một tác vụ OCR dài có thể làm attempt hết giờ chưa được finalize đúng hạn nếu không có request tiếp theo.

**Khuyến nghị**

- Tách worker/queue `maintenance` với concurrency 1 và pool DB nhỏ riêng.
- Worker OCR nhận giới hạn CPU/RAM và queue riêng.
- Theo dõi queue age, task latency/failure/retry.
- Deadline vẫn phải được enforce khi đọc/submit bằng thời gian server, không phụ thuộc duy nhất vào beat.

### H-08 — Giới hạn tải và chống lạm dụng chưa đúng theo route

`deploy/nginx.conf` đặt `client_max_body_size 300m` toàn cục; autosave/submit không có body limit nhỏ hoặc rate limit. Schema answer dùng dictionary không có số item tối đa. Ngược lại, login/register/refresh dùng chung limit theo IP; nhiều thí sinh sau cùng NAT có thể bị từ chối đồng loạt.

**Khuyến nghị**

- Body limit nhỏ riêng cho answers/events/heartbeat; giữ limit lớn chỉ ở location upload.
- Giới hạn số answer bằng số câu và hard cap cấu hình.
- Rate limit theo authenticated user/attempt ở app hoặc gateway, không chỉ IP.
- Tách bucket login, refresh, activation và đặt burst phù hợp cho lớp học dùng chung mạng.
- Thêm `limit_conn`, timeout và response rõ `429` + `Retry-After`.

### H-09/H-10 — Thiếu bằng chứng tải và quan sát production

Không thấy load-test script, dashboard hay alert. Logging dùng cấu hình cơ bản; Docker log không có giới hạn trong Compose. Không có request ID/latency histogram, DB-pool metric, slow-query metric, queue age, MinIO latency hay disk alert.

**Khuyến nghị**

- Metrics tối thiểu: request rate/error/p50/p95/p99 theo route; DB pool in-use/wait; query latency; active attempts; autosave conflict/retry; submit receipt latency; Celery queue age; Redis/MinIO error; CPU/RAM/disk IOPS/free space.
- Structured JSON log có request/attempt correlation ID nhưng không log đáp án/token.
- Log rotation/retention và cảnh báo disk > 80/90%.
- Tracing mẫu cho start/save/submit và media authorization.
- Load test phải tạo dữ liệu gần thật: kích thước đề, 200 câu, audio và phân bố hành vi.

### M-01 — Identity WebSocket tạo tải DB nền

`backend/main.py:267-316` xác thực lại identity khoảng mỗi 2 giây cho mỗi socket; đường này gọi database đồng bộ trong handler async. Ở 200 socket trên các trang có Header, mức nền có thể gần 100 lượt xác thực DB/giây. Trang quiz hiện không render Header nên không trực tiếp cộng vào đỉnh thi, nhưng vẫn là tải đáng kể ở dashboard/admin.

Nên dùng token/session cache ngắn, push invalidation qua Redis pub/sub hoặc tăng nhịp kiểm tra; mọi I/O đồng bộ phải được đưa khỏi event loop async.

### M-02/M-03 — Payload rộng và index ứng viên

Một số query chọn toàn entity `Exam`/`ExamVersion`, kéo cả JSON payload dù response chỉ cần metadata. Full snapshot đề cũng được gửi/lưu ở client; cần đo payload thực tế và tách metadata/content endpoint nếu lớn.

Các index nên kiểm chứng:

- attempt cá nhân: `(user_id, status, submitted_at DESC)`;
- attempt lớp: `(class_assignment_id, started_at DESC)` và các biến thể phục vụ latest/history;
- overdue attempt: partial index theo `deadline_at` với `status='in_progress'`;
- event/history theo `(attempt_id, occurred_at/id)`;
- các foreign key dùng join/count nhưng mới chỉ có index đơn hoặc chưa có thứ tự phù hợp.

Không thêm index hàng loạt trước khi đo. Mỗi index làm tăng write amplification của autosave/submit; chỉ giữ index chứng minh được bằng kế hoạch thực thi.

### M-05/M-06 — Cấu hình DB và migration

Compose chưa đặt giới hạn tài nguyên hoặc thông số PostgreSQL. Trên SATA SSD và 12 GB RAM, memory phải được chia có chủ đích cho PostgreSQL, MinIO, Next.js, API, OCR và page cache; không nên sao chép cấu hình tuning chung mà không đo workload.

API vẫn gọi `Base.metadata.create_all` khi startup dù đã có Alembic migration. Migration `0001` tạo schema từ metadata hiện tại, khiến lịch sử migration khó tái lập/offline. Nên để migration là chủ sở hữu duy nhất của schema, tạo baseline độc lập và kiểm thử upgrade từ bản phát hành được hỗ trợ.

### M-07 — Quản lý bí mật

`.env` đã được git ignore, nhưng permission hiện cho phép group/other đọc. File cũng chứa credential không cần thiết cho runtime ứng dụng. Không đưa các giá trị bí mật vào log hoặc tài liệu.

Khuyến nghị: permission `0600`, tách secret theo service, rotate token ngoài phạm vi app, dùng Docker secrets hoặc secret manager và kiểm tra secret scanning trong CI.

## 6. Những phần chưa cần thay đổi ngay

Các điểm sau đang hợp lý và nên được giữ, chỉ bổ sung test/metrics khi cần:

- `AttemptAnswer` có unique constraint `(attempt_id, question_number)`, là hàng rào toàn vẹn quan trọng.
- Attempt lớp có unique constraint theo assignment/member/attempt number.
- Anti-cheat event có `client_event_id` duy nhất theo attempt, hỗ trợ retry không nhân bản.
- Đề lớp dùng immutable `ExamVersion` snapshot, tránh đề thay đổi giữa kỳ thi.
- Chấm điểm backend là xác định và không phụ thuộc kết quả client.
- Start attempt đã khóa assignment để tránh vượt chính sách số lượt; chưa cần bỏ khóa trước khi có thiết kế thay thế an toàn.
- MinIO bucket là private; URL có thời hạn và classroom asset có kiểm tra quyền.
- Asset proxy đã hỗ trợ HTTP Range. Khi chuyển khỏi API cần giữ hành vi này.
- Upload PDF/audio được stream theo chunk và có giới hạn kích thước phía ứng dụng.
- OCR/image đã nằm ngoài request path và Celery có time limit/prefetch thấp.
- Redis bật AOF.
- Next.js đã tách bundle theo route; route quiz production build khoảng 165 KB raw/~51 KB gzip, chưa phải nút thắt chính.
- Một số endpoint danh sách đã có pagination; nên mở rộng cùng pattern thay vì thay framework.

## 7. Kế hoạch triển khai đề xuất sau audit

### P0 — Bảo toàn bài thi

1. Sửa state machine autosave/submit; không xóa attempt nếu server chưa xác nhận.
2. Thêm revision, idempotency key, receipt và IndexedDB outbox.
3. Bulk upsert + lock/conditional finalize trong một transaction.
4. Test mất mạng, retry, reload, hai submit song song và crash sau commit/trước response.

### P1 — Sống sót ở đỉnh 200 submit

1. Lập ngân sách connection pool và timeout.
2. Tách maintenance worker khỏi OCR.
3. Throttle `last_seen`, giảm heartbeat/write amplification.
4. Presigned/direct media path; warm-up có kiểm soát.
5. Giới hạn body/items/rate theo route và identity.

### P2 — Query và payload

1. Xóa N+1 ở classroom/assignment/history.
2. Pagination/cursor và monitoring delta.
3. Tách metadata khỏi JSON payload lớn.
4. Thu thập plan rồi mới thêm composite/partial index.

### P3 — Production readiness

1. Readiness thật, metrics, structured logs, alert và log rotation.
2. Backup PostgreSQL/MinIO + restore drill.
3. Resource limit và PostgreSQL tuning dựa trên đo đạc.
4. Nginx compression/cache/security header/TLS policy và timeout theo route.

### P4 — Xác minh tải

Chỉ đạt tiêu chí hoàn thành khi test ít nhất các kịch bản:

- 200 thí sinh start trong một cửa sổ ngắn;
- 200 heartbeat và autosave theo nhịp thực tế, có jitter;
- 200 submit với 200 câu trong cùng cửa sổ 30–60 giây;
- phát audio có Range/concurrent reconnect;
- mạng chập chờn, duplicate request và client retry;
- OCR dài đồng thời với auto-finalize;
- soak test đủ dài để phát hiện pool leak, memory growth và disk/log growth.

Tiêu chí cần chốt trước khi chạy: p95/p99 cho save/submit, error budget, zero lost answer, zero duplicate finalization, queue-age tối đa, DB connection headroom, CPU/RAM/disk IOPS và thời gian recovery.

## 8. Kết quả kiểm tra repository

| Kiểm tra | Kết quả |
|---|---|
| Backend `pytest -q` | 71 passed, 4 skipped, 1 failed |
| Nguyên nhân test backend thất bại | Thiếu fixture local `LC.pdf`; test golden listening chưa skip đúng khi fixture vắng |
| Frontend Vitest | 7 files, 26 tests passed |
| Frontend typecheck/lint script | Passed |
| Next.js production build | Passed, 22 route được build |
| Load test | Không có trong repository |
| Backup/restore automation | Không có trong repository |
| `git diff --check` | Passed |

Test hiện tại xác nhận nhiều đường chức năng thông thường, nhưng chưa bao phủ concurrent submit, idempotent retry, offline reconciliation, 200-user spike, pool exhaustion hoặc media concurrency.

## 9. Tiêu chí quyết định go-live

Chưa go-live cho mục tiêu 200 active users cho tới khi tất cả điều kiện sau có bằng chứng:

- không mất đáp án khi refresh, offline, timeout hoặc retry;
- submit lặp/đồng thời trả cùng một kết quả hợp lệ;
- bulk answer write không còn N+1;
- pool có headroom đo được và không vượt ngân sách PostgreSQL;
- audio không chiếm đường truyền/worker API chính;
- backup đã restore thành công;
- readiness, metrics và alert hoạt động;
- kịch bản 200 người đạt SLO đã chốt trong nhiều lần chạy, gồm ít nhất một soak test.

Đây là điểm kết thúc Phase 1. Các thay đổi implementation sau đó được ghi ở
`PERFORMANCE_OPTIMIZATION.md` và phải được đánh giá lại bằng test/load evidence;
không coi baseline audit này là bằng chứng capacity production.

## 10. Addendum — Đồng bộ kho đề Desktop → Web (2026-08-04)

## 11. Full-system audit addendum (2026-08-07)

> Addendum này là phát hiện lịch sử. Implementation hiện tại đã sửa các mục
> IDOR/pagination/page-limit bên dưới. Chính sách Teacher “miễn hoàn toàn” cũng
> đã được thay thế theo kế hoạch production 300 VU: Teacher có token-bucket lane
> gấp bốn nhưng vẫn bị giới hạn ở upload và endpoint tốn CPU.

The repository-wide audit is recorded in `AUDIT_REPORT.md`. The following items
were found in the current working tree and are part of the implementation scope:

- CRITICAL: the public exam asset route resolves an exam/asset without enforcing
  owner/admin or active-public-share authorization. This is an IDOR/data exposure
  risk, not merely a cache or latency issue.
- HIGH: public share/submission authorization currently treats any `teacher` as
  authorized for another teacher's exam. Public submission writes also need a
  signed start token, a bounded answer map, and idempotent token validation.
- HIGH: Nginx `limit_req` runs before FastAPI and cannot identify the durable
  role. It therefore violates the explicit policy that Teacher must not be rate
  limited. API rate limiting must be role-aware in the application; proxy body
  limits and upstream timeouts remain appropriate.
- HIGH: public-submission listing is unbounded and returns full answer JSON.
  Pagination and a hard page-size cap are required before exposing it to a
  teacher dashboard.
- HIGH: PDF upload validates bytes and extension but needs a page-count ceiling
  before render/OCR to prevent CPU/disk amplification.
- MEDIUM: guide image validation needs a decompressed pixel ceiling; byte limits
  alone do not prevent image bombs.
- MEDIUM: no current repository evidence certifies 50/100/150/200 concurrent
  users, peak submit, audio Range concurrency, or mixed workload. The new
  parameterized plan in `LOAD_TEST.md` is a test harness, not benchmark evidence.

The existing answer durability, bulk-upsert, transaction, pool timeout, X-Accel,
pagination, readiness, backup/restore and frontend draft work remains valid and
must not be regressed while these security/load-protection changes are applied.

### CRITICAL — Giao diện báo Public trước khi server xác nhận

Luồng desktop hiện tại chỉ ghi yêu cầu vào `sync_publications` trong SQLite rồi
ngay lập tức đưa lớp vào trạng thái “đã Public” trên giao diện. Việc upload đề,
asset và tạo `ClassAssignment` trên PostgreSQL diễn ra sau đó trong background;
mọi lỗi HTTP/auth/quota/manifest lại bị bỏ qua. Vì vậy desktop có thể hiển thị đã
chia sẻ trong khi server chưa có `Exam` hoặc chưa có assignment `published`, và
web/học viên không thể nhìn thấy dữ liệu.

Cách sửa: khi online, thao tác Public phải chờ đủ hai receipt từ server:
`exam_id` của đề đã lưu trong PostgreSQL và publication `synced` cho từng lớp.
Chỉ các lớp có receipt mới được hiển thị “Đã Public”. Khi offline vẫn được ghi
outbox, nhưng UI phải ghi rõ “đang chờ đồng bộ”, không được coi là thành công.

Expected impact: loại bỏ trạng thái thành công giả; lỗi đăng nhập, giới hạn đề,
mạng, MinIO hoặc publication được hiển thị để giáo viên xử lý ngay.

### HIGH — Trang lớp chỉ đọc kho remote nhưng không phối hợp với outbox desktop

Dropdown “Chọn đề trong kho” gọi thẳng `GET /api/v1/exams` và không chờ hàng đợi
desktop. Response lỗi của endpoint này cũng không được kiểm tra, nên lỗi auth/API
bị biến thành danh sách rỗng. Đây là nguyên nhân trực tiếp khiến đề có trong kho
local nhưng không xuất hiện khi tổ chức thi.

Cách sửa: trên desktop online, flush hàng đợi trước khi tải dữ liệu lớp; sau đó
đọc lại danh sách remote và bắt buộc kiểm tra status của cả ba request lớp, thành
viên và đề thi. SQLite local không được đưa trực tiếp vào assignment vì backend
cần `Exam.id` thật và snapshot `ExamVersion` để bảo toàn dữ liệu.

Expected impact: đề vừa tạo xuất hiện trong dropdown ngay sau khi PostgreSQL và
MinIO xác nhận; lỗi đồng bộ không còn bị trình bày như “không có đề”.

### HIGH — Bản đề đã sync không thể đồng bộ lại sau khi chỉnh sửa

`DesktopSync` unique theo `(user_id, client_exam_id)`, nhưng API trả 409 khi
manifest của cùng đề thay đổi. Trong khi đó desktop giữ nguyên `client_exam_id`
khi chỉnh sửa, nên đề đã sync một lần sẽ mắc kẹt vĩnh viễn ở trạng thái failed.

Cách sửa: coi `Exam` trong kho là bản mutable của giáo viên, còn đề đã giao vẫn
là snapshot immutable trong `ExamVersion`. Khi manifest đổi, reset phiên upload,
chỉ tái sử dụng asset có cùng checksum và cập nhật đúng `Exam` hiện hữu trong
một transaction; các assignment cũ không bị thay đổi.

Expected impact: chỉnh sửa đề rồi Public lại tạo snapshot mới đúng nội dung,
không nhân quota, không làm đổi bài mà học viên đã/đang làm.

### MEDIUM — Thiếu trạng thái lỗi đồng bộ có thể quan sát

SQLite đã lưu `sync_queue.last_error`, `attempts` và trạng thái publication,
nhưng API danh sách/UI không hiển thị chúng. Coordinator chạy 30 giây một lần và
nuốt lỗi, khiến lỗi production chỉ biểu hiện bằng dữ liệu web không xuất hiện.

Cách sửa: trả trạng thái/error trong local exam summary, phát kết quả sync có
cấu trúc, và dùng thông báo phân biệt `pending`, `failed`, `synced`.

## 12. Trạng thái triển khai Desktop/Tauri (2026-08-08)

Các mục ở addendum phía trên là kết quả audit trước khi sửa. Trạng thái sau
triển khai và kiểm thử local:

| Hạng mục | Trạng thái | Bằng chứng |
| --- | --- | --- |
| Mở app offline sau khi đã đăng nhập online | Đã sửa | Identity snapshot có giới hạn; phản hồi online signed-out sẽ thu hồi snapshot |
| Refresh token plaintext trên Windows/macOS | Đã sửa | Chỉ dùng Credential Manager/Apple Keychain; tự migrate và xóa `session.dat` cũ |
| Tự đồng bộ sau reconnect | Đã sửa | Coordinator chạy lúc startup, sự kiện `online`, resume và mỗi 30 giây |
| Hai coordinator cùng lấy một outbox item | Đã sửa | SQLite `BEGIN IMMEDIATE` và lease `syncing`; lease stale tự phục hồi |
| Sync lại đề đã chỉnh sửa | Đã sửa | Manifest mới reconcile vào cùng remote exam, không nhân quota |
| Complete trùng/race/crash giữa hai transaction | Đã sửa | PostgreSQL row lock, completion lease và unique owner/client ID |
| Asset sai hoặc thiếu | Đã sửa | Complete từ chối asset thiếu; upload kiểm tra size và SHA-256 trước MinIO |
| Link chia sẻ thành `tauri.localhost` | Đã sửa | Desktop dựng URL từ remote web origin |
| Export route `/public-test/[code]` | Đã sửa | Static placeholder cho Tauri; route động web vẫn trả HTTP 200 |
| OCR retry bị giảm độ phân giải | Đã sửa | Fast pass 225 DPI, retry thật ở 300 DPI; text layer Reading phải đủ cấu trúc |
| Persistence qua restart sidecar | Đã kiểm thử | Native smoke contract restart cùng data directory và kiểm tra exam/outbox |

Các kiểm thử đã chạy trên host Linux: frontend 48 pass; backend 93 pass và 5
golden OCR fixture skip; Rust 2 pass; web build, desktop static build, Tauri
release `--no-bundle`, PyInstaller sidecar và restart smoke đều pass. Linux
không có Apple SDK/Mach-O hoặc MSVC `lib.exe`, vì vậy artifact `.dmg` arm64/x64
và NSIS của chính commit này chỉ được phép phát hành sau khi các workflow native
macOS/Windows build, cài và smoke xanh.

Giới hạn còn lại: năm golden fixture OCR thực tế chưa có trong repository nên
chưa thể định lượng độ chính xác trên bộ PDF thật; macOS đang ký ad-hoc nên vẫn
cần Developer ID và notarization nếu muốn trải nghiệm cài đặt một click.

## 13. Incident audit — Docker API unhealthy và Nginx 502 (2026-08-08)

Phần này là kết quả kiểm tra read-only trên stack Compose đang chạy, trước khi
thay đổi cấu hình.

### Bằng chứng runtime

| Kiểm tra | Kết quả |
|---|---|
| PostgreSQL | healthy; migration exited `0` |
| Redis | healthy |
| MinIO | healthy; bucket check trong readiness `true` |
| API process | Uvicorn chạy đủ 4 worker, không crash |
| `/health/live` | HTTP `200` |
| `/health` | HTTP `200` |
| `/health/ready` | HTTP `503`, `postgres=true`, `minio=true`, `redis=true`, `ocr_ready=false` |
| OCR model files | Có đủ, checksum hợp lệ |
| OCR probe với `XDG_CACHE_HOME=/tmp/...` | `ocr_ready=true` |
| OCR probe mặc định | Thất bại vì `[Errno 30] Read-only file system: '/app/.cache'` |

### Nguyên nhân gốc

`api`, `worker` và các worker backend chạy với root filesystem read-only.
`backend/rapid_ocr.py` luôn tạo provider cache tại
`$XDG_CACHE_HOME/examify/rapidocr`; trong image hiện tại `HOME=/app`, nên
đường dẫn mặc định là `/app/.cache/examify/rapidocr`, không thể ghi. CPU OCR
cũng đi qua bước tạo thư mục cache này, dù cache chủ yếu phục vụ provider
runtime.

Readiness cố ý gộp `ocr_ready` vào điều kiện sẵn sàng, vì vậy API không được
đánh dấu healthy. `frontend` phụ thuộc vào API healthy nên vẫn ở trạng thái
`Created`; Nginx không có upstream frontend đang chạy và trả `502 Bad Gateway`.

### Mức độ và cách sửa

| ID | Mức | Phát hiện | Cách sửa | Expected impact |
|---|---|---|---|---|
| DOCKER-C-01 | CRITICAL | API readiness 503 do OCR cache ghi vào filesystem read-only | Cấu hình `XDG_CACHE_HOME=/tmp/.cache` trong backend environment dùng chung | API healthy, frontend được khởi động, Nginx có upstream |
| DOCKER-H-02 | HIGH | OCR worker sẽ lỗi ở request/job đầu tiên dù healthcheck được bỏ qua | Dùng cùng writable cache path trong anchor environment cho API và Celery workers | OCR job khởi tạo được; không che lỗi bằng cách bỏ OCR khỏi readiness |
| DOCKER-M-03 | MEDIUM | Nhiều worker process cùng cold-start OCR và kiểm tra checksum model | Giữ pool bounded hiện tại; kiểm chứng thời gian startup/RAM sau khi stack healthy | Không tăng số worker/pool trong hotfix; tránh làm cạn tài nguyên |

### Không cần sửa trong incident này

Không cần đổi framework, database, storage, health endpoint, Nginx upstream,
hoặc tắt readiness OCR. PostgreSQL/Redis/MinIO đã chứng minh healthy; 502 là
hậu quả dây chuyền của dependency gate, không phải lỗi route proxy. Các tối ưu
autosave, query plan và load test vẫn là hạng mục audit production riêng, chưa
được coi là đã chứng nhận chỉ từ việc Docker khởi động thành công.

## 14. OCR audit — LC.pdf và RC.pdf (2026-08-08)

Hai PDF mới là scan ảnh: `LC.pdf` có 11 trang, `RC.pdf` có 28 trang và không
có text layer dùng được.

| Phát hiện | Mức | Nguyên nhân | Cách sửa | Kiểm chứng |
|---|---|---|---|---|
| Crop Part 1 của LC bắt đầu từ trang 4 | HIGH | OCR bỏ sót marker 1–6 trên trang ảnh, detector chọn Part 2 làm nội dung bắt đầu | Neo Listening Part 1 theo header Part 2 và prefix ảnh 3 trang | `content_start_page=1`, crop đúng trang 1–3 và đủ 11 stimuli |
| RC mất câu 186 | HIGH | Scan ghi `186.What` không có khoảng trắng | Cho phép marker câu không có whitespace sau dấu chấm | RC có đủ 101–200 theo thứ tự |
| RC mất đáp án câu 113 | HIGH | OCR tách `B` khỏi `evaluate` và `(C)` khỏi `evaluating` | Gộp riêng marker fragment cùng dòng và chuẩn hóa marker A–D trơ | Câu 113 có đủ A/B/C/D |
| RC Part 6 lệch sequence | HIGH | Số câu lặp trong passage blank và cạnh đáp án | Chọn candidate trùng cùng trang có chất lượng cao hơn trước khi resolve | RC không còn extraction issue |
| Listening OCR mất nhiều thời gian | HIGH | Mọi trang scan đều render 300 DPI dù PP-OCRv4 resize ở max-side 2000px | Listening mặc định 240 DPI; Reading giữ 300 DPI cho Part 6; có thể override `OCR_RENDER_DPI` | LC benchmark còn 59.12s, baseline 212.99s |

Đây là benchmark xử lý một job, không phải load test production nhiều user và
không dùng để chứng nhận mục tiêu 200 concurrent users.

## 15. Scan in phức tạp và source-crop review — 2026-08-08

| ID | Mức | Phát hiện | Quyết định |
|---|---|---|---|
| OCR-H-05 | HIGH | Scan in có thể mất riêng header/số câu trong khi A–D vẫn hiện; retry nguyên trang tăng thời gian nhưng không sửa được fixture | Khôi phục theo block phương án chỉ giữa hai số câu đáng tin cậy và chỉ khi số block khớp khoảng số; nếu không khớp giữ cảnh báo review |
| REVIEW-H-06 | HIGH | Review trước đây chỉ có thể recrop asset đã cắt, không thể đổi sang trang gốc khác | Giữ source pages private trong job/MinIO và thêm chọn Trang PDF gốc + tạo stimulus crop thủ công có validate bbox/câu |
| OCR-M-07 | MEDIUM | Listening 1–31 là câu audio, marker scan mất gây `question_missing` không có ý nghĩa nghiệp vụ | Tạo deterministic A–D/A–C và dành OCR cho Parts 3/4 |

Không bật `OCR_SCAN_RETRY_PAGES` mặc định. Khi một fixture mới chứng minh được
normalization có ích, phải đo time/RAM và xác nhận không làm thay đổi đáp án
câu đã đọc đúng trước khi bật tối đa 12 trang retry.

## 16. OCR capacity and prefix routing follow-up — 2026-08-08

| ID | Mức | Phát hiện | Cách xử lý | Kiểm chứng |
|---|---|---|---|---|
| OCR-H-08 | HIGH | Worker OCR bị giới hạn 2 CPU/4 GB, trong khi một scan dài cần nhiều page OCR độc lập | Giữ một job Celery tại một thời điểm; tăng quota worker thành 4 CPU/6 GB và dùng tối đa bốn ONNX CPU session một-thread | Container được recreate với `NanoCpus=4000000000`, `Memory=6442450944`; fixture dùng ~400% CPU, ~0.75 GB RAM |
| OCR-H-09 | HIGH | Detector Listening có thể dựa vào Part 2 khi trang ảnh Part 1 bị OCR ít text; prefix bìa/hướng dẫn dễ làm crop lệch | Ưu tiên trang có marker `1.` và `2.`, chống nhầm numbered directions bằng phân tích heading/toạ độ; fallback Part 2 giữ nguyên | OCR thật: `TEST 1 LC.pdf` bắt đầu trang 3; `TEST 1 RC .pdf` bắt đầu trang 2 |
| DESK-H-04 | HIGH | macOS CI chỉ smoke sidecar staging, không chạy binary sau khi Tauri đóng gói `.app` | Thêm smoke test sidecar trong `Contents/MacOS` với resource path thật của app | CI phát hiện model/Poppler/resource path hỏng trước khi phát hành DMG |

Không nâng `--concurrency` Celery: tăng nhiều PDF chạy đồng thời sẽ cạnh tranh
với API, PostgreSQL và MinIO trên máy 8 core/16 GB. Hiệu năng tổng phải được
xác nhận thêm bằng benchmark trên server production với fixture và tải thực tế.

## 17. Answer-key image routing follow-up — 2026-08-08

| ID | Mức | Phát hiện | Cách xử lý | Kiểm chứng |
|---|---|---|---|---|
| OCR-H-10 | HIGH | Ảnh bảng Reading `101–200` dán trong draft Listening `1–100` bị lọc hết rồi báo lỗi chung | Giữ raw OCR để phát hiện lệch phạm vi và báo đúng loại đề; không ghi đáp án ngoài phạm vi | Unit test scope mismatch; API/worker production trả thông báo cụ thể |
| OCR-C-11 | CRITICAL | Recovery 5 cột có thể lấy chữ cái đúng nhưng remap theo vị trí sang phạm vi sai nếu OCR số câu thuộc đề khác | Từ chối grid candidate khi số câu nhận dạng không giao với cột kỳ vọng; không fallback thành đáp án giả | Unit test `101–200` không thể tạo candidate `1–100` |
| OCR-M-12 | MEDIUM | Pipeline cũ chạy full-page rồi thêm nhiều lượt grid tuần tự; ảnh 5 cột mất khoảng 21,7 giây trong smoke test | Cắt header, OCR một lượt vùng bảng trước; chỉ recovery 5 cột khi thiếu; ảnh answer key 1000px, API dùng 1 session/4 ONNX threads, ngân sách lỗi vẫn bounded 30 giây/6 giây mỗi pass/45 giây browser | Smoke test tương đương nhận 100/100 trong khoảng 5,8 giây; backend 68 test và frontend 51 test pass |

## 18. Answer-key photo performance follow-up — 2026-08-08

Log request trước khi sửa cho thấy ảnh `1–100` trả `recognized=0`, `duration_ms=16859`
và chỉ có một token OCR rác. Nguyên nhân không phải phạm vi đáp án mà là
PaddleOCR detector bị header/border làm nhiễu, sau đó recovery chạy nhiều lượt
trên bitmap 1500px.

Đã đổi đường đi cho ảnh 5 x 20:

- cắt vùng bảng theo hình học, bỏ title band và border trước OCR;
- giới hạn answer-key raster ở 1000px thay vì dùng kích thước page OCR 1500px;
- OCR toàn vùng một lượt trước, chỉ chạy crop 5 cột khi số câu còn thiếu;
- API production dùng `OCR_ENGINE_POOL_SIZE=1` và `OCR_ONNX_INTRA_THREADS=4`;
- giữ guard số câu và không remap khi không có bằng chứng số câu cùng phạm vi;
- tăng cache version PWA lên `examify-pwa-v6` để không phục vụ bundle cũ.

Smoke test bằng engine PP-OCRv4 trong container nhận đúng 100/100, không sai
đáp án, khoảng 5,8 giây. API readiness sau recreate đạt `ready`, model checksum
hợp lệ và PostgreSQL/Redis/MinIO đều healthy.
## 19. Build incident audit — Windows/macOS native release (2026-08-09)

Phần này được bổ sung sau khi audit read-only repository và đối chiếu log CI
do người dùng cung cấp. Không có mã nguồn hay cấu hình runtime nào được thay
đổi trước khi hoàn tất phần audit này.

### Kiến trúc build hiện tại

```text
GitHub Actions native runner
  -> Python 3.11 + desktop dependencies
  -> PyInstaller OCR sidecar (Windows onedir / macOS native onefile)
  -> scripts/smoke-sidecar.py
       -> POST PDF extraction
       -> answer-key OCR
       -> PATCH review draft
       -> POST finalize
       -> SQLite exam/history/restart checks
  -> Next static desktop build
  -> Tauri cargo check + NSIS (Windows) hoặc .app/.dmg (macOS)
  -> artifact upload / GitHub Release
```

Windows chạy smoke trước khi bundle và thêm một lần trên layout NSIS đã cài.
macOS chạy smoke trước khi bundle và thêm một lần trong `Contents/MacOS` của
`.app`, với resource path thực tế sau đóng gói.

### Bằng chứng lỗi

| ID | Mức độ | Phát hiện | Nguyên nhân | Ảnh hưởng |
|---|---|---|---|---|
| BUILD-C-01 | CRITICAL | Cả Windows và macOS smoke đều fail ở `POST /api/extractions/{job_id}/finalize` với HTTP 422; log liệt kê các câu 102–200 còn thiếu | `backend/main.py` gọi `ensure_question_coverage()` khi finalize; `scripts/smoke-sidecar.py` PATCH chỉ question 101 cho draft Reading. Các câu 102–200 được tạo thành placeholder có `question_missing/options_missing`, nên API từ chối finalize đúng theo data-integrity contract | Mọi native job dừng trước bước đóng gói artifact; không phải lỗi riêng kiến trúc CPU hay Tauri |
| BUILD-H-02 | HIGH | macOS bước `Upload diagnostics` nhận `Failed to CreateArtifact: ENOTFOUND` sau smoke failure | GitHub artifact endpoint/DNS không truy cập được tại thời điểm upload; đây là lỗi hạ tầng CI phụ, không phải lỗi sidecar | Che khuất lỗi gốc và làm diagnostic upload trở thành một failure thứ hai |
| BUILD-M-03 | MEDIUM | Regression test cho smoke contract chưa được chạy trong hai workflow native | Workflow chạy backend/frontend tests nhưng không chạy `scripts/test_smoke_sidecar.py`; test hiện có chỉ kiểm tra transport retry | Regression về payload smoke không bị bắt trước khi tiêu tốn runner native/OCR |
| BUILD-M-04 | MEDIUM | `PERFORMANCE_AUDIT.md` trước đó ghi native smoke/release đã pass cho commit này | Kết luận cũ dựa trên trạng thái trước commit thêm coverage placeholder và chưa đối chiếu run thất bại hiện tại | Báo cáo release không phản ánh trạng thái thật |
| BUILD-M-05 | MEDIUM | macOS chỉ upload diagnostics của smoke trước đóng gói | Packaged-app smoke ghi vào thư mục `examify-sidecar-diagnostics-installed` nhưng workflow chỉ trỏ tới thư mục pre-install | Mất bằng chứng khi lỗi chỉ xuất hiện sau khi Tauri đóng gói |

### Nguyên nhân kỹ thuật chi tiết

`expected_question_numbers("reading")` là `101..200`. Pipeline và endpoint
review chủ động bổ sung placeholder cho số OCR bị thiếu để giáo viên sửa thủ
công. Đây là hành vi cần giữ vì không được âm thầm mất câu hoặc cho phép đề
thi có cấu trúc không đầy đủ.

Smoke test tạo đúng một câu 101 nhằm kiểm tra persistence local, nhưng sau khi
PATCH request, state hợp lệ chứa thêm 99 placeholder. `finalize_extraction()`
kiểm tra toàn bộ state trước khi áp dụng answer key, thấy các placeholder chưa
được sửa và trả 422. Vì vậy không nên nới lỏng validation production hay cho
phép finalize một draft thiếu câu; smoke fixture phải mô phỏng một draft
Reading hoàn chỉnh, đồng thời có thể đặt `count=1` để vẫn kiểm tra một exam
nhỏ ở bước output.

### Cách sửa đã triển khai sau audit

1. `smoke-sidecar.py` gửi đủ dải Reading 101–200 với dữ liệu hợp lệ, giữ câu
   101 là câu OCR đã kiểm tra và dùng `count=1` cho output nhỏ.
2. `scripts/test_smoke_sidecar.py` có regression test cho đủ coverage; cả hai
   native workflow đều chạy test này trước khi tốn thời gian build sidecar.
3. Bước upload diagnostics có `continue-on-error: true`: DNS/artifact failure
   không thay đổi nguyên nhân smoke/build; tùy chọn này không che lỗi build.
4. Đã chạy unit tests, lint/typecheck, frontend build, Rust checks và Linux
   sidecar smoke. Windows NSIS và macOS arm64/x64 vẫn cần native runner.
5. macOS upload diagnostics nhận cả thư mục pre-install và packaged-app;
   upload vẫn non-blocking để lỗi DNS của GitHub không thay đổi root cause.

### Expected impact

- Smoke native đi qua finalize và tiếp tục kiểm tra SQLite, attempt history,
  restart và pending sync như thiết kế.
- Không thay đổi behavior nghiệp vụ của finalize hoặc nới lỏng data integrity.
- Lỗi CI artifact upload phụ không còn che diagnostic/root failure; job vẫn
  fail nếu smoke/build thật sự fail.
- Không ảnh hưởng connection pool, PostgreSQL, MinIO hay workload exam web.

### Những phần không cần sửa trong incident này

- Không đổi framework, Tauri, Python OCR engine, PostgreSQL, MinIO hay schema
  production chỉ để làm native build xanh.
- Không giảm `ensure_question_coverage()` hoặc bỏ kiểm tra unresolved
  questions; đó là lớp bảo vệ tính chính xác đề thi.
- Không thể xác nhận latency/CPU/RAM của 50/100/150/200 concurrent users từ
  các log build này. `LOAD_TEST.md` vẫn phải giữ số liệu TBD cho đến khi chạy
  trên môi trường staging có PostgreSQL/MinIO thực.

### Trạng thái audit

Audit incident hoàn tất. Regression smoke đã được sửa và xác nhận bằng Linux
sidecar; trước khi có run native mới, Windows/macOS artifact vẫn chưa được
tuyên bố đạt release gate. Chưa có bằng chứng cho thấy PyInstaller, Tauri,
NSIS hoặc DMG là nguyên nhân gốc.

## 20. Build incident follow-up — macOS Intel packaged smoke (2026-08-09)

### Bằng chứng

- Windows và macOS ARM đã pass cùng commit `63c66e6`.
- macOS Intel pass các bước build sidecar, smoke trước đóng gói và bundle; chỉ
  fail tại `Smoke test OCR sidecar in the packaged app`.
- Log cho thấy `POST /answer-key-image` trả HTTP 200, nhưng không có log tiếp
  theo của `PATCH /draft` hoặc `finalize`. Harness hiện kiểm tra chặt rằng OCR
  phải trả `answer_key["101"] == "A"`, nên failure xảy ra ngay sau endpoint này.
- macOS hiện ưu tiên `CoreMLExecutionProvider` cho cả ARM và Intel. Đây là khác
  biệt runtime duy nhất đáng kể giữa hai target; Intel CoreML có khả năng trả
  kết quả nhận dạng khác CPU/ARM cho cùng ảnh smoke. Đây là kết luận cần xác
  nhận bằng lần chạy Intel tiếp theo, không phải tuyên bố đã có native pass.

### Sửa đã triển khai

- `backend/rapid_ocr.py` chỉ dùng CoreML tự động trên `arm64/aarch64`; Intel
  macOS dùng `CPUExecutionProvider` để ưu tiên tính ổn định/deterministic OCR.
- Thêm unit tests xác nhận provider selection cho Intel và Apple Silicon.
- Ảnh answer-key smoke đổi sang chữ lớn, tương phản cao, dạng `101 A` để giảm
  sai khác detector/recognizer giữa execution providers.
- Khi answer-key smoke fail, harness in toàn bộ payload OCR (không chứa secret)
  để chẩn đoán được letter/number thực tế thay vì chỉ báo HTTP 200.

### Expected impact và giới hạn

- ARM không đổi provider; Intel tránh CoreML path đã liên quan trực tiếp đến
  failure hiện tại. CPU có thể chậm hơn nhưng nằm trong timeout smoke 180 giây
  và không đánh đổi đáp án.
- Linux unit và sidecar smoke pass sau thay đổi. Cần rerun native Intel; chỉ
  khi bước packaged smoke pass mới được coi artifact Intel đạt release gate.
## Audit chuyển dependency sang máy chủ ngoài (2026-08-10)

### Phạm vi và kiến trúc hiện tại

Repository là một monolith triển khai bằng Docker Compose: Nginx phục vụ Next.js
và proxy FastAPI; FastAPI/SQLAlchemy/Alembic lưu metadata trong PostgreSQL;
Redis/Celery xử lý OCR và maintenance; file nguồn, asset, audio, answer-key và
guide media nằm trong năm bucket MinIO riêng tư. Compose hiện tự chạy
PostgreSQL, MinIO và `minio-init`; OCR server vẫn chạy RapidOCR/ONNX cục bộ
trong API và OCR worker. Pool SQLAlchemy được giới hạn theo process, migration
chạy trước API, admin được bootstrap idempotent khi database chưa có admin.

Yêu cầu triển khai mới đặt PostgreSQL, MinIO và PaddleOCR trên ba máy ngoài.
Redis vẫn phải giữ lại vì là broker, result backend, rate limiter, presence và
identity cache; bỏ Redis sẽ làm sai kiến trúc hiện tại chứ không chỉ bỏ cache.

### Phát hiện trước khi sửa

| ID | Mức độ | Phát hiện | Nguyên nhân | Cách xử lý | Expected impact |
| --- | --- | --- | --- | --- | --- |
| EXT-C-01 | CRITICAL | PostgreSQL mở TCP `10.10.10.3:5432` nhưng từ chối URI được cung cấp với lỗi `password authentication failed for user postgres` | Credential trên server không khớp URI hoặc role chưa được đặt password như yêu cầu | Không đoán/đổi dữ liệu; cấu hình app theo URI đã cấp nhưng chặn migration/bootstrap cho đến khi credential được sửa hoặc cấp lại | Tránh khởi tạo nhầm database hay báo test giả thành công |
| EXT-H-02 | HIGH | Compose gắn cứng `DATABASE_URL` vào service `postgres`, MinIO vào `minio:9000`, và `depends_on` bắt buộc các service nội bộ | Anchor environment và dependency graph được thiết kế cho stack all-in-one | Đọc `DATABASE_URL`, `MINIO_ENDPOINT` và PaddleOCR URL từ `.env`; bỏ PostgreSQL/MinIO khỏi default service graph, giữ init bucket one-shot trỏ ra ngoài | App chỉ sử dụng các dependency ngoài, không chạy hai nguồn dữ liệu song song |
| EXT-H-03 | HIGH | MinIO ngoài reachable và credential hợp lệ nhưng chưa có bucket | Đây là máy mới; object storage cần đủ năm bucket theo domain hiện hữu | Chạy initializer idempotent tạo `examify-sources`, `examify-assets`, `examify-audio`, `examify-answers`, `examify-guides`, giữ private | Readiness và mọi luồng upload/download có nơi lưu đúng, không đổi schema metadata |
| EXT-H-04 | HIGH | PaddleOCR ngoài có `POST /ocr` nhận multipart `file`, nhưng pipeline chỉ gọi adapter ONNX trong process | `REMOTE_OCR_ENABLED` hiện chỉ là feature flag cho phép OCR server-side, không phải địa chỉ OCR từ xa | Thêm adapter HTTP bounded với timeout, giới hạn response và normalize raw boxes/text về `OCRResult`; server profile dùng adapter ngoài, desktop vẫn OCR local | Bỏ CPU/RAM inference khỏi application host nhưng giữ parser và layout semantics hiện tại |
| EXT-M-05 | MEDIUM | Readiness hiện bắt buộc kiểm tra model ONNX local kể cả khi inference được chuyển sang PaddleOCR ngoài | Health logic gọi `ocr_dependency_status()` theo adapter cũ | Khi có remote URL, readiness probe `/health` của PaddleOCR với timeout ngắn; không tải model local trong API | API startup không phụ thuộc model cục bộ, health phản ánh dependency thật |
| EXT-M-06 | MEDIUM | `postgres-exporter`, pgBackRest scripts và MinIO mirror/fetch giả định volume/service nội bộ | Monitoring/backup được ghép với container storage trong cùng Compose | Không tự bật exporter/backup profile cũ cho máy ngoài; tài liệu hóa backup/monitoring phải chạy cạnh dependency hoặc dùng credential riêng | Tránh cấu hình backup tưởng đang bảo vệ dữ liệu nhưng thực tế trỏ sai host/volume |
| EXT-M-07 | MEDIUM | User hiện tại không có quyền Docker daemon; `sudo` yêu cầu mật khẩu tương tác | Socket thuộc `root:docker`, user không nằm trong group `docker` | Hoàn thành code/config/test không cần daemon trước; việc xóa/start container phải do phiên có Docker permission thực hiện | Không chạy lệnh phá hủy mù hoặc tuyên bố đã dọn container khi chưa thực hiện được |

### Phần không cần sửa

- Không đổi PostgreSQL, SQLAlchemy/Alembic, schema, transaction hoặc pool hiện có.
- Không đổi năm bucket theo domain; chỉ chuyển endpoint và chạy khởi tạo
  idempotent.
- Không bỏ Redis/Celery maintenance vì chúng phục vụ autosave/presence/rate
  limit và deadline attempts, không phải container dư thừa.
- Không proxy byte MinIO qua FastAPI; luồng private presigned internal redirect
  và Range audio vẫn phù hợp.
- Không thay đổi parser TOEIC, answer persistence, submit idempotency hoặc cách
  chấm điểm. Adapter OCR chỉ thay nơi inference và phải giữ output layout.

### Trạng thái sau triển khai

`EXT-H-02`, `EXT-H-03`, `EXT-H-04` và `EXT-M-05` đã được xử lý trong code/config.
`EXT-C-01` ban đầu tái hiện lỗi password nhưng lần kết nối sau đã xác thực và
migration đầy đủ thành công. `EXT-M-07` còn mở: không có quyền Docker nên chưa
thể dọn orphan/start stack thật; đây là giới hạn quyền hệ điều hành, không phải
lỗi kết nối ba dependency ngoài.
## Audit thiếu chữ OCR trên LC.pdf và RC.pdf (2026-08-10)

Hai fixture production được kiểm tra trực tiếp:

- `LC.pdf`: 11 trang scan, không có text layer.
- `RC.pdf`: 28 trang scan, không có text layer.
- Ảnh scan nguồn rõ; raw PaddleOCR nhận đủ marker câu trên các trang kiểm tra.

| ID | Mức độ | Phát hiện | Nguyên nhân | Cách sửa | Expected impact |
| --- | --- | --- | --- | --- | --- |
| OCR-C-REMOTE-01 | CRITICAL | Review báo thiếu hàng loạt câu/nội dung dù raw OCR đọc đủ | Pipeline gửi trang LC kích thước `1489×2105`; PaddleOCR ngoài resize thành `849×1200` và trả raw box theo ảnh resize. Adapter không scale box về input. `_paddle_page_result` vì thế coi gần như mọi box thuộc cột trái, trộn xen kẽ hai cột và gắn options sai câu | Đọc kích thước annotated image có giới hạn, scale mọi polygon về kích thước ảnh request trước khi trả `OCRResult`; fallback không scale khi server giữ nguyên hoặc không có metadata | Khôi phục đúng cột, thứ tự câu và ánh xạ A–D mà không hạ confidence hay bịa text |
| OCR-H-REMOTE-02 | HIGH | Contract remote OCR không có trường width/height riêng, chỉ có data URI annotated image | API tự mô tả là raw model detections; box không bảo đảm cùng coordinate space với upload | Encapsulate coordinate normalization trong adapter và test tỷ lệ không vuông; giới hạn base64/header parsing | Không phụ thuộc ngầm vào max-side/model version của Paddle server |
| OCR-H-REMOTE-03 | HIGH | Sau khi sửa box, RC đủ 100 số câu nhưng câu 183 mất `(D) Tour 5`; OCR riêng nửa trang đọc đúng với confidence 99.22% | OCR server ép full page `1861×2632` xuống khoảng 1200 px max-side, làm chữ nhỏ mất trước recognition | Tile ảnh lớn theo lưới có overlap; map box về toàn trang và chỉ giữ detection có tâm thuộc core tile | Giữ độ phân giải chữ nhỏ, không tạo duplicate ở biên tile |

Không sửa bằng cách hạ `text_score`: các trang LC đo được không có detection nào
trong khoảng confidence 45–55 và raw OCR đã đủ marker. Hạ threshold sẽ tăng noise
nhưng không sửa lỗi coordinate/cột.

### Trạng thái sau sửa

- `OCR-C-REMOTE-01`: đã xử lý bằng scale X/Y theo kích thước ảnh annotated mà
  remote OCR trả về.
- `OCR-H-REMOTE-02`: đã xử lý với parser header data URI có giới hạn; không
  decode toàn bộ ảnh annotated vào bộ nhớ.
- `OCR-H-REMOTE-03`: đã xử lý bằng tile overlap có giới hạn, dịch box về tọa độ
  toàn trang và chỉ giữ detection thuộc core của tile.
- Navigation/footer ngoài nội dung đề và dấu ngoặc full-width được chuẩn hóa/lọc
  trước parser để không dính vào lựa chọn cuối trang.

## Audit crop Reading và latency RC.pdf (2026-08-10)

Đã đối chiếu trực tiếp trang 16 (`Questions 168-171`) và trang 22 (câu 181-185)
của `RC.pdf` với token/box PaddleOCR và asset WebP mà review UI hiển thị.

| ID | Mức độ | Phát hiện | Nguyên nhân | Cách sửa | Expected impact |
| --- | --- | --- | --- | --- | --- |
| CROP-C-01 | CRITICAL | Crop passage kéo cả câu hỏi 168-171 và các nhóm Part 7 khác | `_question_top()` chỉ nhận token chứa riêng `168.`. Remote OCR trả một line/token `168. What is one purpose...`, nên anchor luôn `None` và `_build_stimuli()` dùng fallback tới 92% trang | Nhận question marker ở đầu token/line bằng cùng grammar `QUESTION_START` mà parser đang dùng; giữ margin phía trên câu hỏi và test bằng token thật của RC | Crop dừng trước câu hỏi thay vì bao gần cả trang |
| CROP-C-02 | CRITICAL | Crop Part 6 có thể kéo cả block đáp án | `_first_option_top()` chỉ `fullmatch` token riêng `(A)` trong khi remote OCR trả `(A) serve` | Nhận option A ở đầu line, hỗ trợ ASCII/full-width punctuation; không nhận chữ A trong văn xuôi | Part 6 chỉ giữ passage có blank, loại block lựa chọn |
| CROP-H-03 | HIGH | Không có invariant cuối cùng ngăn asset chạm vùng câu hỏi khi anchor hợp lệ | Crop dựa vào zone heuristic rồi trim/split ảnh; validation chỉ đếm số asset, không kiểm tra giao với question top | Clamp mọi passage zone theo semantic boundary trước trim/split và thêm test `asset.bottom < first_question.top` | Không tái phát crop câu hỏi khi whitespace/split thay đổi |
| OCR-H-LATENCY-04 | HIGH | RC 28 trang mất 146,93 giây ở cấu hình chính xác hiện tại | Mọi trang bị chia 6 tile (168 lượt OCR trước recovery), dù raw full-page đã đủ 100 số câu và chỉ từng thiếu một option ở trang 22 | Dùng một full-page locator/OCR request; chỉ chạy high-resolution question ROI cho trang thực sự thiếu. Anchor fix làm targeted recovery hoạt động đúng | Giảm số request thông thường từ 168 xuống khoảng 28 + số trang recovery |
| OCR-M-LATENCY-05 | MEDIUM | Tăng page concurrency 2→6 không tăng throughput server OCR | Benchmark 6 trang với 2 horizontal tile: 1 worker 22,05s; 2 worker 19,16s; 4 worker 19,11s; 6 worker 19,60s | Giữ connection ceiling nhưng dùng concurrency worker phù hợp với server; ưu tiên giảm request thay vì tăng thread | Giảm contention/queue và RAM mà không làm chậm throughput |

Đo cấu hình 2 tile/trang trên đủ 28 trang vẫn đạt 100/100 câu và đủ A-D nhưng
riêng OCR mất 94,41 giây. Vì vậy chỉ giảm 6→2 tile chưa đủ; hướng triển khai là
single-pass full page cộng recovery ROI có điều kiện. Không hạ DPI toàn cục và
không cắt passage bằng tỷ lệ trang cố định.

### Trạng thái sau sửa crop/latency

- `CROP-C-01`, `CROP-C-02`: đã sửa grammar spatial anchor cho cả token riêng và
  whole-line token; hỗ trợ punctuation full-width.
- `CROP-H-03`: passage zone luôn dừng trước semantic question/option boundary.
  Full `RC.pdf` tạo 26 asset/19 nhóm, `overlaps=[]`, không `crop_review`.
- `OCR-H-LATENCY-04`: full-page chạy một request/trang; chỉ trang 22 recovery
  question ROI. Tổng 78,89 giây thay vì 146,93 giây, vẫn đủ 100/100 và A-D.
- `OCR-M-LATENCY-05`: production page workers/remote connections mặc định là 2.
- NumPy crop analysis không còn bị tắt theo khi OpenCV thiếu shared library.
- 26 Reading asset đổi từ WebP lossless của JPEG source sang quality 95/method
  0: benchmark cùng crop giảm 19.988.962 xuống 7.310.038 byte (63,4%), encode
  lại toàn bộ trong 3,288 giây; kiểm tra trực quan giữ rõ nét chữ.

## Audit Part 6 và Listening assets trên RC/LC (2026-08-10)

Đã kiểm tra raw spatial token trang RC 4–7 và ảnh nguồn LC trang 1–3, 7, 8, 11.

| ID | Mức độ | Phát hiện | Nguyên nhân | Cách sửa | Expected impact |
| --- | --- | --- | --- | --- | --- |
| P6-C-01 | CRITICAL | Crop Part 6 vẫn chứa toàn bộ block đáp án 131–146 | Dòng đầu block là token ghép `131.(A)serve`, `135.(A)quickly`...; `_first_option_top()` chỉ nhận token bắt đầu trực tiếp bằng `(A)`, nên bốn trang đều trả `None` | Nhận grammar `question-number + option A` ở đầu line, giới hạn number đúng group; lấy min spatial top làm answer boundary | Crop dừng sau passage, trước block lựa chọn |
| P6-H-02 | HIGH | Không thể dùng `_question_top()` như Part 7 | Số 131–146 còn xuất hiện inline trong blank của passage, ví dụ `131.` tại y=30,1%; answer block bắt đầu y=50,1% | Dùng marker kết hợp `131.(A)` thay vì occurrence đầu tiên của số câu | Không cắt mất passage hoặc kéo answer vào crop |
| LC-H-03 | HIGH | Part 1 fallback crop chứa nhãn số, nhiều whitespace và mép ảnh kế tiếp | `_dominant_content_bbox()` trả nguyên coarse box khi OpenCV không load; crop 1 hiện rộng 1747×1221 và chạm phần đầu ảnh 2 | Dùng NumPy projection tìm largest dense rectangular photo, độc lập OpenCV; margin nhỏ quanh ảnh | Sáu asset chỉ giữ ảnh, không giữ số/footer/ảnh kế |
| LC-M-04 | MEDIUM | Năm graphic Part 3/4 dùng coarse bbox đúng trang nhưng còn viền trắng không đồng đều | Mapping nhóm 62–64, 65–67, 68–70, 95–97, 98–100 đúng với LC.pdf; bbox chưa fit theo ink thật | Chạy trim bbox một mảnh trong coarse safe zone, fallback về spec nếu ảnh không đủ evidence | Graphic gọn hơn nhưng không ăn câu hỏi bên dưới |

Raw boundary Part 6 đo được: page 4 y=0,501; page 5 y=0,407; page 6
y=0,468; page 7 y=0,545. Đây là semantic answer-block boundary, không phải tỷ lệ
hard-code. Các graphic LC đều nằm hoàn toàn phía trên câu hỏi liên quan trong
coarse safe zone hiện có.

### Trạng thái sau sửa Part 6/Listening assets

- `P6-C-01`, `P6-H-02`: đã xử lý. Bốn asset Part 6 lần lượt kết thúc ở 0,4830;
  0,3887; 0,4497; 0,5272, đều nhỏ hơn answer boundary 0,5010; 0,4067; 0,4677;
  0,5452. `issues=[]` và kiểm tra trực quan không còn lựa chọn A-D.
- `LC-H-03`: đã xử lý bằng NumPy dense-rectangle fitting. Sáu ảnh từ coarse
  1747×1221/1277 được fit còn khoảng 1253–1257 px chiều rộng, bỏ number label,
  whitespace và ảnh kế tiếp; không phụ thuộc OpenCV.
- `LC-M-04`: năm graphic đã trim theo ink trong safe zone và kiểm tra trực quan
  chỉ chứa bảng/sơ đồ. Tổng Listening stimuli vẫn đúng 11 (6 photo + 5 graphic).

## Audit PDF bản in/scan xấu (2026-08-10)

Hai fixture mới được render và kiểm tra độc lập với fixture PDF đẹp:

- `Đề Listening (bản in).pdf`: 11 trang, ảnh chụp Test 2, nền tối/không đều,
  cong trang và xuyên chữ mặt sau; không có text layer dùng được.
- `Đề Reading (BẢN IN).pdf`: 28 trang, ảnh chụp Test 2, có biến dạng phối cảnh,
  illumination không đều và bleed-through; không có text layer dùng được.
- Đây là nội dung Test 2 và bố cục hình minh họa khác fixture Test 1; không thể
  sửa bằng cách hard-code thêm tọa độ theo đúng hai file mới.

| ID | Mức độ | Phát hiện | Nguyên nhân | Cách sửa | Expected impact |
| --- | --- | --- | --- | --- | --- |
| SCAN-C-01 | CRITICAL | Reading scan xấu có thể làm toàn bộ extraction crash với `ValueError: min() iterable argument is empty` | `_merge_fallback_option_fragments()` luôn gọi `min()` dù ROI không nhận được bất kỳ option fragment nào | Nếu không có fragment thì giữ candidate lượt đầu; chỉ merge dữ liệu thực sự có bằng chứng OCR, thêm regression test danh sách rỗng | Một trang OCR yếu không làm hỏng cả job 28 trang |
| SCAN-H-02 | HIGH | Listening baseline đủ dải 1–100 nhưng còn 8 câu 50, 51, 52, 56, 76, 83, 89, 90 thiếu text/option | Full-page fast pass 225 DPI mất nét cục bộ trên nền cong/tối; normalized full-size recovery đang mặc định tắt hoàn toàn | Chọn trang retry từ completeness sau parse, giới hạn số trang và chỉ merge field/câu đang thiếu | Phục hồi scan xấu nhưng PDF đẹp vẫn giữ single-pass nhanh |
| SCAN-H-03 | HIGH | Graphic Part 3/4 của Test 2 bị cắt theo khung Test 1; ví dụ hình phải trang 8 rộng tới khoảng 93% trang nhưng bbox cũ dừng ở 86% | Năm nhóm câu/trang là cấu trúc TOEIC ổn định, còn kích thước hình không ổn định giữa đề | Lấy đáy safe zone từ spatial question marker và lấy toàn bộ nửa cột; trim ink trong semantic safe zone, fallback spec cũ khi thiếu token | Giữ đủ bảng/sơ đồ của nhiều booklet mà không ăn câu hỏi bên dưới |
| SCAN-M-04 | MEDIUM | Part 1 tạo đủ sáu asset nhưng crop scan xấu còn dính số/ảnh kế tiếp hoặc footer | Ngưỡng foreground 245 coi giấy xám và bleed-through là mực, nối ảnh với nội dung lân cận thành một khối | Dùng dark-ink projection ở ngưỡng 200 chỉ để tìm biên; vẫn lưu pixel grayscale gốc, thêm regression biên theo số câu/footer | Crop giữ đúng riêng từng ảnh trên cả bản đẹp và scan xấu |
| SCAN-M-05 | MEDIUM | OpenCV trong host không load do thiếu `libGL.so.1`, làm nhánh normalize/deskew không thể được benchmark ngoài container | Dependency dùng `opencv-python`; Docker production có `libgl1` nhưng môi trường test/headless không có | Bổ sung normalization Pillow/NumPy làm fallback và giữ OpenCV như acceleration khi khả dụng; không phụ thuộc deskew để đảm bảo completeness | Recovery có hành vi nhất quán ở server/headless và dễ regression-test |

Baseline Listening hiện tại: 22,07 giây cho 11 trang, 100/100 số câu, còn 8
câu không hoàn chỉnh và tạo đủ 11 stimulus. Do đó không có lý do OCR lại toàn
bộ tài liệu hoặc tăng số worker; retry phải chỉ chạy trên các trang lỗi.

### Kết quả sau triển khai

- Listening scan xấu: 47,03 giây, đủ 100/100 số câu và 11/11 stimulus; còn
  câu 50, 56, 89, 90 được đánh dấu duyệt tay thay vì ghép dữ liệu không chắc
  chắn. Câu 50 bị cắt mất ngay trên ảnh nguồn.
- Reading scan xấu: 101,95 giây, đủ 100/100 số câu và 19 nhóm đoạn văn; còn
  câu 109, 126, 165, 176, 181 cần duyệt. Paddle không phát hiện dòng câu 181
  qua các crop/threshold/scale đã thử nên pipeline không tự bịa text.
- Hai nhóm Reading 176–180 và 191–195 giữ đầy đủ tài liệu nguồn nhưng có
  `crop_review`, vì bleed-through làm khoảng trắng tách tài liệu không đủ chắc.
- Regression PDF đẹp: `LC.pdf` 20,99 giây, 100/100, không thiếu, 11 asset;
  `RC.pdf` 66,04 giây, 100/100, 0 issue, 19 stimulus/26 asset và không có
  `crop_review`.
- Backend regression: 66 pass, 5 skip, 1 Poppler golden deselected; compile,
  Compose config và diff checks pass.

## Audit finalize, Full Test, Tag và quyền quản lý đề (2026-08-10)

Đã đối chiếu code frontend/backend và dữ liệu PostgreSQL đang chạy. Normal User
hiện có đồng thời một đề `combined` 200 câu, một Reading component 100 câu và
hai Listening component 100 câu đều ở trạng thái `ready`; vì vậy ảnh chụp màn
hình hiển thị ba đề không phải lỗi render đơn thuần.

| ID | Mức độ | Phát hiện | Nguyên nhân | Cách sửa | Expected impact |
| --- | --- | --- | --- | --- | --- |
| FLOW-C-01 | CRITICAL | Hoàn tất Full Test có thể để lại Reading/Listening thành các đề độc lập; retry làm số đề tăng | Component được persist với `status=ready`; `/exams` không loại component pending. Combine hiện chỉ soft-delete component sau khi toàn bộ chuỗi API thành công | Đánh dấu `component_pending`, loại khỏi list/attempt, giữ combine idempotent và chỉ công bố một bản `combined` | My Exams chỉ có một Full Test 200 câu |
| FLOW-H-02 | HIGH | Sau khi lưu thành công frontend đôi khi vẫn ở `/review` | Điều hướng dùng `router.push()` sau chuỗi mutate dài; lỗi combine/tag bị giữ trên trang và nút có thể được bấm lại. Destination cũng hard-code `/my-exams` cho mọi role | Resolve role sau commit, dùng full navigation tới `/my-exams` cho User hoặc `/exam-bank` cho Teacher; khóa submit và hiển thị lỗi finalize rõ ràng | Không bấm lặp, reload đúng thư viện sau commit |
| ACL-H-03 | HIGH | Normal User không có menu ba chấm dù API owner đã cho PATCH/DELETE | JSX chỉ render menu khi `isTeacher`; quyền UI không phản ánh `_can_manage_exam`/owner API | Render menu quản lý cho Teacher và `role=user`; chọn endpoint personal/shared đúng role, chỉ Teacher thấy action public/archive dùng chung | Owner sửa/xóa đề cá nhân; Teacher tiếp tục quản trị Kho chung |
| TAG-H-04 | HIGH | Normal User nhập Tag mới nhưng không tạo được và không thấy lỗi | `POST /api/v1/tags` chỉ cho Teacher/Admin, frontend bỏ qua response 403; GET chỉ đọc `exam_tags` dùng chung | Với User, validate Tag và lưu trực tiếp vào category của đề cá nhân; GET trả union Tag chung + category cá nhân; frontend hiển thị lỗi thay vì nuốt | User tự phân loại My Exams, Teacher vẫn quản lý taxonomy chung |
| OCR-H-05 | HIGH | Part 5 mất ký hiệu chỗ trống và dính nhiều từ | Paddle trả các run như `....`, `.-`, `...` không đồng nhất; parser không canonicalize blank. Scan cong làm recognizer trả `smallissuescanbe`, `theybecomebigones` nhưng completeness hiện chỉ đếm ký tự | Canonicalize đúng một blank Part 5 thành `_____`; đánh điểm spacing, retry block nghi ngờ và tách bounded English glued tokens bằng word-frequency segmenter | Người dùng nhìn rõ vị trí điền và câu dễ đọc hơn, không thay option/answer |

Dữ liệu thực tế câu 108 đang lưu là
`Proper maintenance of yourheating equipment ensures that smallissuescanbe fixed ... theybecomebigones.`;
đây là bằng chứng OCR/post-processing, không phải lỗi CSS trang Quiz.

## Audit OCR Desktop local trên macOS/Windows (2026-08-10)

Đã lần theo đường chạy của bản đóng gói từ Tauri tới Python sidecar, model ONNX,
Poppler và pipeline. Kết luận quan trọng: bản Desktop **đang dùng model local**.
`desktop_entry.configure()` ép `APP_PROFILE=desktop`, tắt `REMOTE_OCR_ENABLED`,
xóa endpoint PostgreSQL/MinIO/Redis, kiểm tra checksum model đóng gói và warm-up
ONNX trước khi báo ready. `rapid_ocr.recognize()` cũng chỉ cho phép HTTP OCR khi
có endpoint và không phải profile Desktop; Desktop không có nhánh fallback ra
máy chủ OCR.

Tuy nhiên, việc chạy local chưa được cấu hình theo phần cứng. Benchmark cùng
container giới hạn 4 CPU/4 GB, cùng model và cùng hai fixture đẹp cho thấy điểm
nghẽn là số ONNX session, không phải tải model qua mạng:

| Fixture | Cấu hình | Thời gian | Kết quả |
| --- | --- | ---: | --- |
| `LC.pdf` | 1 engine, 2 page worker, 2 thread/engine | 79,51 s | 100 câu, 0 issue |
| `LC.pdf` | 2 engine, 2 page worker, 2 thread/engine | 50,89 s | 100 câu, 0 issue |
| `LC.pdf` | 4 engine, 4 page worker, 1 thread/engine | 55,80 s | 100 câu, 0 issue |
| `RC.pdf` | 1 engine, 2 page worker, 2 thread/engine | 237,81 s | 100 câu, 0 issue |
| `RC.pdf` | 2 engine, 2 page worker, 2 thread/engine | 145,82 s | 100 câu, 0 issue |

Hai engine giảm RC 38,7%; tăng lên bốn engine lại chậm hơn do tranh CPU/memory.
Điều này phù hợp với hiện tượng hơn 5 phút trên Intel Mac khi máy chậm hơn môi
trường đo hoặc đang chạy thêm ứng dụng khác.

| ID | Mức độ | Phát hiện | Nguyên nhân | Cách sửa | Expected impact |
| --- | --- | --- | --- | --- | --- |
| DESK-C-01 | CRITICAL | Nhiều page worker nhưng toàn bộ OCR local xếp hàng qua một CPU engine | `OCR_PAGE_WORKERS` mặc định từ 2–6, còn `DEFAULT_CPU_POOL_SIZE=1`; page executor không tạo được song song inference | Profile Desktop theo kiến trúc: Intel/CPU dùng đúng 2 engine × 2 thread và 2 page worker; giữ accelerator một session | Giảm khoảng 35–40% thời gian full booklet trên CPU 4 core mà không đổi kết quả |
| DESK-H-02 | HIGH | Bản cài không công bố pool/thread/hardware nên người dùng không xác minh được local acceleration | Readiness chỉ có provider/model; log không có runtime concurrency | Trả `ocr_local`, provider, hardware, pool, page worker và thread trong `/health/ready`; ghi cùng dữ liệu vào sidecar log | Chẩn đoán được installer đang dùng CPU/CoreML/DirectML và cấu hình thật |
| DESK-H-03 | HIGH | Cấu hình CoreML M1 lệch default tối ưu của RapidOCR | Adapter ép model format cũ `NeuralNetwork`, không khai báo specialization `FastPrediction`; cache đã có nhưng không đủ để tối ưu compile/inference | Dùng `MLProgram` + `FastPrediction`, giữ cache ngoài app bundle và fallback CPU nếu provider lỗi | M1 có đường native CoreML phù hợp hơn; vẫn giữ fallback an toàn |
| DESK-H-04 | HIGH | CI không phát hiện regression “full PDF hơn 5 phút” | Smoke installer chỉ OCR một trang và chỉ kiểm tra `ocr_ready`; timeout 180 giây không phản ánh booklet | Thêm assertion local route/provider và benchmark nhiều trang/full fixture có ngân sách thời gian, lưu JSON timing artifact | Release lỗi provider hoặc quay lại serialize bị chặn trước khi phát hành |
| DESK-M-05 | MEDIUM | Script benchmark chính thức hiện crash trước OCR | Job temp thiếu hai thư mục `pages/` và `assets/` mà pipeline yêu cầu | Tạo đúng job layout, xuất runtime config cùng timing và hỗ trợ ngưỡng regression | Benchmark tái lập được trên Intel, M1 và Windows |
| DESK-M-06 | MEDIUM | Listening OCR cả cover/directions trước khi biết trang nội dung | Content-start chỉ được tính sau full-page OCR; fixture LC xác định nội dung bắt đầu trang 4 | Locator nhẹ cho prefix với điều kiện bằng chứng chặt, hoặc cache result locator trước full pass; không hard-code số trang | Tiết kiệm OCR các trang bìa nhưng không ảnh hưởng PDF không có prefix |

Không giảm độ phân giải/max-side toàn cục chỉ để lấy benchmark đẹp: thử giới
hạn cạnh 1600 giảm RC còn 133,28 giây nhưng mới được xác minh trên PDF đẹp và
có nguy cơ làm mất chữ ở scan xấu. Đây không được chọn làm giải pháp mặc định.

### Kết quả sau triển khai Desktop 0.1.6

- CPU local dùng 2 engine × 2 ONNX thread và 2 page worker trên máy có ít nhất
  4 logical CPU; máy 2 CPU tự hạ về 1×1. Accelerator CoreML/DirectML vẫn chỉ
  tạo một session, tránh tranh GPU/NPU memory.
- M1 dùng CoreML `MLProgram`/`FastPrediction` và cache compiled model ngoài app
  bundle. Intel macOS giữ CPU provider vì fixture trước đây cho thấy CoreML
  Intel có recognition drift; đây là quyết định bảo toàn dữ liệu.
- Packaged readiness nay công bố và smoke-test `ocr_local=true`,
  `ocr_remote=false`, provider, kiến trúc, pool và thread. Release fail nếu
  artifact dùng remote hoặc PDF tổng hợp 8 trang vượt 90 giây.
- Sửa thêm lỗi scan độc lập với concurrency: detector từng nhầm giá trị `1` và
  `2` trong bảng trang 8 là caption ảnh Part 1, làm bỏ trang 1–7 và mất câu
  32–64. Part 1 nay chỉ được chọn trước một Part 2 có directions/number evidence
  và caption phải có bố cục dọc phù hợp.

Benchmark local CPU sau sửa (cùng model PP-OCRv4, 2×2, không HTTP OCR):

| Fixture | Trang | Thời gian | Coverage | Peak RSS |
| --- | ---: | ---: | ---: | ---: |
| `LC.pdf` | 11 | 48,05 s | 100/100, 0 issue | 2,91 GB |
| `RC.pdf` | 28 | 144,74 s | 100/100, 0 issue | 2,96 GB |
| `Đề Listening (bản in).pdf` | 11 | 99,80 s | 100/100 | 2,86 GB |
| `Đề Reading (BẢN IN).pdf` | 28 | 196,77 s | 100/100 | 3,11 GB |

Các số trên là benchmark Linux x86_64 local trong môi trường hiện tại, không
được giả làm số đo native Mac. M1/Intel/Windows được bảo vệ bằng native release
matrix và performance smoke; cần lưu JSON artifact của CI để có số chính xác
theo từng máy runner. Scan Reading vẫn là bottleneck còn lại (3 phút 17 giây),
nhưng đã dưới 5 phút và không hy sinh DPI/completeness.

## Audit import lời giải, Result và Solutions (2026-08-10)

Đã chạy parser thật trên hai fixture mới:

- `GIẢI FULL TEST 1.pdf`: 34 trang, PDF text, thực tế chỉ chứa lời giải
  Reading 101–200 dù tên file có chữ Full Test.
- `GIẢI READING TEST 1  (1).pdf`: 44 trang, PDF text, cũng chỉ chứa Reading
  101–200. File này có một số nội dung nguồn bị cắt ở cuối; câu 200 kết thúc tại
  `Dẫn chứng & Giải thích: Trong email bình` nên hệ thống không được tự bịa phần
  còn thiếu.
- Cả hai dùng bảng ba cột `STT / Giải chi tiết / Dịch`; chữ số STT được xếp dọc
  như `1\n0\n1`. `parse_solution_number()` hiện xóa whitespace nên trường hợp
  nằm trong cùng một cell đã thành 101, nhưng chưa có test/diagnostic rõ ràng
  và chưa xử lý tốt header/candidate lặp.

| Fixture | Thời gian baseline | Entry | Issue baseline | Chất lượng mapping |
| --- | ---: | ---: | ---: | --- |
| Full Test 1 | 47,07 s | 100 | 1.219 | Câu 101–200 đủ; 1.184 duplicate do table lặp |
| Reading Test 1 | 71,36 s | 100 | 1.776 | Câu 101–200 đủ số nhưng câu cuối bị cụt từ PDF nguồn |

| ID | Mức độ | Phát hiện | Nguyên nhân | Cách sửa | Expected impact |
| --- | --- | --- | --- | --- | --- |
| SOL-C-01 | CRITICAL | Preview báo đủ 100 nhưng có thể giữ bản lời giải bị cắt/ngắn thay vì bản đầy đủ hơn | PDF Quartz lặp lại table fragment trên nhiều trang; importer nhận candidate đầu rồi loại mọi bản sau là overlap | Gom theo key, bỏ duplicate giống hệt, chọn candidate giàu nội dung/đủ cấu trúc nhất; chỉ báo một summary warning có giới hạn | Mapping ổn định, không có hàng nghìn lỗi giả và không giữ tùy tiện occurrence đầu |
| SOL-H-02 | HIGH | Header hợp lệ `S\nT\nT / Giải chi tiết / Dịch` bị báo `invalid_stt` | `_is_header()` chỉ chấp nhận đúng `STT / Nội dung đề / Dịch` cho Reading và giữ khoảng trắng trong STT dọc | Chuẩn hóa STT không phụ thuộc whitespace; chấp nhận alias `Giải chi tiết`, `Giải thích`, `Nội dung đề` | Hai format mới khớp contract import chính thức |
| SOL-H-03 | HIGH | Import PDF text mất 47–71 giây dù không cần OCR | Mỗi trang chạy cả `extract_text(layout=True)` và table detection pdfplumber; tiếp tục quét các trang chỉ lặp dữ liệu | Ưu tiên ruled-table extraction, dừng có kiểm soát sau coverage + trang lookahead, không OCR PDF đã có text | Preview nhanh hơn, không chiếm OCR worker vô ích |
| SOL-H-04 | HIGH | File Reading tải ở bước Listening trả hàng trăm lỗi range khó hiểu | API nhận `exam_type` từ UI nhưng không phát hiện toàn bộ STT thuộc component đối diện | Trả một issue `exam_type_mismatch` chỉ rõ file là Reading 101–200 và không merge | Ngăn gắn lời giải Reading vào Listening component |
| SOL-H-05 | HIGH | Solutions navigation tô xanh mọi câu có lời giải, không phản ánh bài làm | Màu nút dùng `hasEntry`, không dùng `student_answers` và `question.correct` | Tính status đúng/sai/chưa làm/chưa chấm theo từng câu; group đỏ nếu có câu sai, xanh chỉ khi toàn nhóm đúng; thêm legend | Học viên nhận biết ngay câu đúng và sai |
| SOL-H-06 | HIGH | Normal User/Desktop có thể thấy editor nhưng import/xem lời giải không đi hết luồng | Import/solutions API loại role `user`; Result Desktop history không set `attempt_id/has_solutions`; Solutions chỉ gọi remote attempt API | Đồng bộ owner permission, dựng payload local từ SQLite exam + attempt và mở route cho Normal User | Web/Teacher/Normal User/Desktop cùng một behavior |
| RESULT-M-07 | MEDIUM | Overall/Listening/Reading nằm chung grid trái, overall nhỏ và không phải điểm nhấn | Score cards dùng `xl:grid-cols-5`; TOEIC chỉ `text-4xl` như các metric khác | Hero score căn giữa, overall lớn; Listening/Reading đặt ngay bên dưới; thống kê đúng/sai/thời gian là hàng riêng | Result có hierarchy rõ ràng trên desktop/mobile |
| RESULT-H-08 | HIGH | Mở Result từ Student history có thể tính/hiển thị sai release state | Frontend nhận `scores`, `score_released`, `answers_released` từ API nhưng bỏ các field này khi dựng `QuizResult` | Preserve nguyên release/scores payload; không tự tính điểm bị giáo viên ẩn | Không rò điểm và không lệch điểm đã lưu |
| SOL-M-09 | MEDIUM | Nội dung giải bị co và khó đọc | Main giới hạn 1500 px, sidebar 280 px, ảnh max-height 520 và lời giải/dịch chia hai cột text 14 px | Tăng content width/typography, ảnh đọc tài liệu lớn hơn, panel giải responsive ưu tiên một cột ở màn hình vừa | Đọc passage/lời giải rõ hơn mà vẫn giữ navigation sticky |

Phần không cần sửa: schema solution immutable, giới hạn 12.000 ký tự/field,
payload 2 MiB, MinIO temporary object, một import active/user, rate limit và
preview-before-merge đều đang đúng. File nguồn câu 200 bị cắt phải được cảnh
báo để người dùng sửa tay, không thể phục hồi bằng thuật toán.

### Kết quả sau triển khai

- `GIẢI FULL TEST 1.pdf` import đúng ở bước Reading: 100/100 câu, không thiếu
  key, khoảng 19,48 giây thay vì 47,07 giây và không còn issue giả. Tên file có chữ Full Test nhưng
  nội dung không có lời giải Listening 1–100.
- Nếu chọn file trên ở bước Listening, parser dừng trong 19,87 giây, không tạo
  entry và trả đúng một `exam_type_mismatch`; không còn hàng trăm lỗi range.
- `GIẢI READING TEST 1  (1).pdf`: 100/100 câu, không thiếu key, 17,75 giây
  thay vì 71,36 giây. Preview chỉ còn cảnh báo câu 200 bị cụt từ chính PDF
  nguồn; row Quartz trùng y hệt được xử lý im lặng.
- STT `1 0 1`, `1\n0\n1`, `Câu 101` và `Question 101.` đều được ánh xạ thành
  câu 101; span vẫn bị giới hạn theo đúng group TOEIC để không merge nhầm.
- Result giữ score/release state từ API, Overall được đặt giữa và phóng lớn;
  Listening/Reading ở ngay bên dưới. Solutions dùng trạng thái đáp án thực:
  xanh đúng, đỏ sai, xám chưa làm, vàng chưa chấm.
- Normal User, Teacher/Student theo ownership và Desktop local đều đi được từ
  Result tới Solutions; chỉ attempt đã submit mới được trả lời giải.

## Audit Full Script Listening Test 1 (2026-08-10)

`FULL SCRIPT TEST 1 (1).pdf` được kiểm tra độc lập sau hai file Reading:

- 18 trang, 602.653 byte, PDF text do macOS Quartz/TextEdit tạo; không cần OCR.
- Bảng ba cột `S\nT\nT / Transcript / Dịch nghĩa tiếng Việt` đúng contract
  importer. Các group 71–73 được biểu diễn dọc thành `7\n1\n-\n7\n3` và parser
  chuẩn hóa đúng thành `[71, 72, 73]`.
- Kết quả thực tế trong 11,31 giây: 54 entry, phủ 100/100 câu, không thiếu key.
- Danh sách group bằng tuyệt đối `allowed_solution_groups("listening")`:
  Part 1 có 6 singleton, Part 2 có 25 singleton, Part 3 có 13 group ba câu và
  Part 4 có 10 group ba câu.
- Kiểm tra nội dung không phát hiện field rỗng; Part 1 đủ marker A–D ở cả hai
  cột, Part 2 đủ A–C, mọi transcript nhóm Part 3/4 đều vượt ngưỡng nội dung tối
  thiểu và có bản dịch tương ứng.
- 232 row lặp là fingerprint giống hệt do Quartz lặp nguyên table fragment ở
  trang sau. Đây là dữ liệu trùng kỹ thuật đã được dedupe, không phải lỗi cần
  người dùng sửa và không nên làm preview báo “1 lỗi”.

File này chỉ cung cấp transcript/bản dịch cho Listening; việc merge không thay
đổi answer key của đề. Mapping lời giải dùng key immutable 1–31, 32–34 ...
98–100 nên khớp trực tiếp với navigation/nhóm câu của trang Solutions.

### Kết quả sau triển khai

- Dòng lặp cùng fingerprint được dedupe im lặng; chỉ nội dung khác nhau cùng
  key mới tạo warning yêu cầu xem preview.
- Chạy lại file thật: 11,23 giây, 54 entry, 100/100 câu,
  `missing_keys=[]`, `issues=[]`, `groups_exact=true`.
- Regression tổng hợp STT dọc trên đủ 54 group qua 22/22 test parser.

## Audit đăng nhập bằng IP LAN (2026-08-10)

### Phát hiện

`.env` giữ `PUBLIC_BASE_URL=https://toeicdoc.com`, nên cookie access trước đây
luôn có cờ `Secure`. Khi trình duyệt mở `http://10.10.10.5`, POST login có thể
trả 200 nhưng Chrome không gửi access cookie ở các request kế tiếp; `/auth/me`
và các API quản trị trả 401, khiến giao diện quay lại trang login.

### Xử lý

- Cookie `access`, `refresh` và onboarding nay chọn `Secure` theo scheme thực tế:
  `X-Forwarded-Proto` từ Nginx, fallback về scheme của request.
- HTTPS production vẫn giữ `Secure`; IP HTTP nội bộ dùng cookie không `Secure`.
- API login và activation truyền request context đầy đủ; Desktop activation vẫn
  giữ đường token riêng.

### Xác minh

- `http://10.10.10.5/api/v1/auth/login`: HTTP 200.
- Cookie access/refresh: `Secure=False` trên HTTP IP.
- `/api/v1/auth/state`: HTTP 200; `/api/v1/auth/me`: HTTP 200.
- Backend cookie/platform regression: 17/17 pass.
- Docker API, frontend, worker và Nginx đã rebuild; tất cả container chính
  healthy, `/health` trả `{"status":"alive"}`.

## Audit Desktop build, OCR hardware, đồng bộ và orchestration web (2026-08-10)

### Kiến trúc hiện tại

- Desktop là Tauri 2 gọi Python sidecar PyInstaller. Sidecar dùng cùng parser,
  pipeline và RapidOCR/ONNX với web nhưng ép `APP_PROFILE=desktop`, không gửi
  tài liệu tới OCR ngoài. Windows đóng gói ONNX Runtime DirectML; macOS build
  riêng `arm64` và `x86_64`, kèm Poppler native theo đúng kiến trúc.
- Provider tự động hiện tại là DirectML trên Windows, CoreML trên Apple Silicon
  và CPU trên Intel Mac. CPU Desktop dùng pool hữu hạn; accelerator dùng một
  session để tránh gọi đồng thời vào execution provider không an toàn.
- Desktop lưu exam/asset trong SQLite + thư mục app-data, rồi đẩy manifest và
  asset có SHA-256 lên API trung tâm. Web và Desktop online cùng đọc exam bank
  PostgreSQL/MinIO, nhưng hiện chưa có luồng reconcile server -> cache Desktop.
- Web đã chạy OCR theo trang bằng `ThreadPoolExecutor`; adapter OCR ngoài còn có
  semaphore/HTTP connection pool hữu hạn. Tuy nhiên job orchestration vẫn chạy
  toàn bộ audio trước rồi mới bắt đầu OCR, nên concurrency sẵn có bị bỏ phí.

| ID | Mức độ | Phát hiện | Nguyên nhân | Cách sửa | Expected impact |
| --- | --- | --- | --- | --- | --- |
| DESK-BUILD-H-07 | HIGH | Release Windows có cờ `EnableNvidiaGpu`/`GPU_OCR_ENABLED=false` trái với artifact thực | Script luôn cài `onnxruntime-directml`, tham số NVIDIA không được dùng; smoke chỉ chấp nhận mọi local provider | Bỏ contract gây hiểu nhầm, công bố provider policy và bắt native smoke xác nhận DirectML/CoreML/CPU đúng target | Release không thể âm thầm rơi về CPU do thiếu DLL/provider |
| DESK-OCR-H-08 | HIGH | Sidecar native cũ có runtime khác nhau theo hệ điều hành | Policy native làm kết quả khó đồng nhất giữa web và desktop | Chuyển Tauri mới sang cùng pipeline Tesseract.js/WebAssembly của browser; chỉ giữ sidecar cũ trong cửa sổ tương thích | Một pipeline và một golden corpus cho web/Tauri |
| DESK-MAC-H-09 | HIGH | Intel Mac vẫn chậm; bật CoreML cho mọi Mac có thể làm OCR sai khác | Fixture/native regression trước đây cho thấy Intel CoreML drift; Apple Silicon mới là target CoreML ổn định | Giữ Intel CPU để bảo toàn dữ liệu, tối ưu pool/thread; Apple Silicon dùng CoreML cache + `MLProgram`/`FastPrediction`; native CI assert kiến trúc/provider | M-series tận dụng CPU/GPU/Neural Engine; Intel có bounded parallel CPU thay vì đổi provider mù quáng |
| SYNC-C-01 | CRITICAL | Xóa/sửa bản web có thể để cache Desktop cũ xuất hiện lại và upload đè | Chỉ có push Desktop -> web, không có reconcile tombstone/revision từ server | Thêm reconcile theo mapping `DesktopSync`; cache sạch nhưng stale/deleted được quarantine, cache đang pending được đánh dấu conflict và không tự upload | Không hồi sinh đề đã xóa và không ghi đè thay đổi web trong im lặng |
| SYNC-C-02 | CRITICAL | Upload Desktop không gửi base revision | Manifest chỉ có hash local; server update exam đã tồn tại mà không kiểm tra optimistic concurrency | Lưu `remote_revision`, gửi `base_revision`, kiểm tra ở create lẫn complete transaction và trả 409 khi xung đột | Edit hai phía không gây lost update; revision PostgreSQL là nguồn sự thật |
| SYNC-H-03 | HIGH | App-created exam có thể hiện hai card trên Desktop online | Exam-bank response thiếu `client_exam_id` dù frontend dùng field này để de-duplicate local/remote | Trả mapping client ID thuộc đúng user; reconcile sau mỗi sync | Web tạo thì app thấy; app tạo thì web thấy; app không hiển thị trùng |
| SYNC-H-04 | HIGH | Đề local chưa sync không thể xóa trong My Exams | UI luôn gọi API delete từ xa bằng local UUID; sidecar chưa có local delete route | Route xóa local crash-safe cho item chưa sync; đề đã sync xóa ở server rồi reconcile cache | Create/edit/delete có hành vi nhất quán, không gọi nhầm ID local vào API web |
| WEB-OCR-H-01 | HIGH → IMPLEMENTED | UI phải chờ audio xong mới bắt đầu OCR | `_run_extraction_job()` gọi `prepare_web_audio()` tuần tự trước `_run_extraction()` | Mọi profile có audio chạy một audio future song song với OCR page pool; chờ cả hai trước finalize, giới hạn worker hiện hữu | Thời gian job xấp xỉ nhánh chậm hơn thay vì tổng audio + OCR |
| WEB-OCR-H-02 | HIGH | Parallel hóa ngây thơ có thể làm progress/payload ghi đè nhau | Progress hiện là read-modify-write; audio và OCR sẽ cập nhật cùng job | Atomic merge trong job store, tách `audio_progress`/`ocr_progress`, serialize aggregate reporter và finalize một lần | Dialog phản ánh đúng hai tác vụ, không mất metadata audio hoặc phase OCR |

### Phần không cần sửa

- Không thêm CUDA/cuDNN vào installer Windows mặc định: DirectML đã là backend
  GPU đóng gói không phụ thuộc vendor; CUDA vẫn là tùy chọn server chuyên dụng.
- Không bật CoreML cho Intel Mac chỉ để dashboard báo “GPU”: tính đúng của OCR
  quan trọng hơn tốc độ và CPU pool hiện tại là fallback có kiểm soát.
- Không tăng vô hạn page worker/HTTP connection. OCR ngoài hiện bị giới hạn ở 2
  request đồng thời theo benchmark trước; tăng thread chỉ làm queue/contend.
- Không tạo thêm process/worker không giới hạn cho Desktop. OCR local và FFmpeg
  dùng cùng một orchestration hai nhánh với đúng một audio future; page worker,
  ONNX pool và FFmpeg thread vẫn giữ các ceiling hiện hữu để tránh cạn CPU/RAM.

### Kế hoạch triển khai sau audit

1. Làm build smoke kiểm tra provider theo từng native target và thêm provider
   override/diagnostic có fallback.
2. Bổ sung optimistic revision + reconcile hai chiều, xử lý local delete và
   loại duplicate client ID.
3. Tách progress audio/OCR và chạy song song có giới hạn cho cả web remote OCR
   và Desktop/local OCR.
4. Chạy unit/frontend/typecheck/build và native workflow contract tests; số đo
   native Windows/macOS phải lấy từ artifact CI, không suy diễn từ Linux.

### Incident `No module named 'audio_processing'` (2026-08-11)

| Mức độ | Nguyên nhân | Xử lý |
| --- | --- | --- |
| CRITICAL trong artifact/deploy cũ | Log ban đầu quy lỗi cho image thiếu module. Kiểm tra trực tiếp image cũ cho thấy file vẫn có, nhưng Celery chạy qua console script `celery` đã không giữ `/app` ổn định trong `sys.path`; dynamic import chỉ chạy sau khi task nhận nên lỗi xuất hiện trễ, sau HTTP `202` | Import `prepare_web_audio` ở lúc worker/API khởi động, đặt `PYTHONPATH=/app`, chạy Celery bằng `python -m celery`, và giữ startup preflight để thiếu module fail ngay |
| CRITICAL trong Compose deploy | `api`, `worker`, `migrate` và các Celery role dùng các implicit image tag riêng dù có cùng build context; rebuild riêng API không bắt buộc recreate worker, nên source giữa producer/consumer có thể lệch | Dùng một named backend image cho mọi Python role, gắn revision label, build-time import preflight và so image ID của các container sau deploy |

Lưu ý: `/health/ready` hiện xác nhận PostgreSQL/MinIO/Redis và cấu hình OCR,
nhưng không thể chứng minh job bất đồng bộ đã hoàn tất. Sau mỗi deploy phải chạy
import preflight trực tiếp trong container worker, xem log worker và chạy ít nhất
một job PDF + audio thật; không dùng HTTP `202` hoặc `/health/ready` làm bằng
chứng cứ xử lý thành công.

`backend/toeic_audio_cutter.py` là module bắt buộc của thay đổi này; release
pipeline phải đưa file đó vào commit/artefact. Dockerfile đã có lệnh `COPY`
tường minh để build thất bại ngay nếu checkout hoặc build context thiếu file.

Luồng OCR/audio không cần đổi thuật toán cho incident này: job Listening kiểm
tra module audio trước khi khởi chạy hai nhánh, nên import fail giải thích trực
tiếp vì OCR cũng không bắt đầu. Sau khi artifact hợp lệ, audio và OCR tiếp tục
chạy song song có giới hạn và chỉ finalize khi cả hai nhánh hoàn tất.

`202` trong Network không phải là xử lý thành công; đó chỉ là job đã được nhận. Trạng thái lỗi được ghi sau đó bởi worker.

### Xác minh end-to-end trên host (2026-08-11)

Sau khi rebuild bằng `sudo ./deploy/rebuild.sh`, bốn service Python cùng dùng
image `examify-backend:local` (ID `3da81a5b12cb`). Worker xác nhận
`PYTHONPATH=/app`, import audio thành công và command là `python -m celery`.
Upload trực tiếp `File_TEST/LC.pdf` + `File_TEST/Test 01.mp3` tạo job
`53bb4634-5422-4b10-b259-49a21a5eb8a4`; sau 75,2 giây job đạt `review`/100%:

- OCR: 100 câu duy nhất, đủ số 1–100, 11 stimulus, 0 issue, `error=null`.
- Audio: 55 asset (1 full + 31 câu + 23 nhóm), phủ đủ câu 1–100, source dài
  `2763.832` giây; audio clip trả HTTP `206 audio/mpeg` với Range request.
- Asset ảnh OCR trả HTTP `200 image/webp`; Celery log ghi `succeeded` và
  `processing_completed`, không còn `ModuleNotFoundError`.

Sau lần xác minh E2E, Docker đã được rebuild/recreate thêm một lần nữa với
image mới `sha256:8e5466e75a78730c21c2f85d8e4cf0051f4c43287a8ce387215cf7b3349662b8`;
`/health`, `/health/ready`, worker import và log sau restart đều pass.

## Audit luồng thi thử, khôi phục bài và giải chi tiết (2026-08-11)

### Kiến trúc hiện tại

- Trang làm bài là một Next.js client route cố định `/quiz`; dữ liệu đề và lượt
  làm được chuẩn bị trước rồi lưu trong `sessionStorage`. PostgreSQL đã dùng UUID
  cho `Exam`/`Attempt`, nhưng `Exam` chưa có slug công khai ổn định.
- Đáp án được lưu theo revision vào localStorage + IndexedDB trước khi gửi batch
  idempotent lên API. Backend đã ghi `Attempt.current_question_number` qua sync,
  nhưng API state và bản nháp phía trình duyệt chưa trả/lưu trường này.
- Chế độ thi thử tự phát audio qua `HiddenExamAudio`, song component vẫn render
  nút phát/tạm dừng nên học viên có thể điều khiển luồng Listening.
- Transcript và bản dịch cùng dùng bộ tách đoạn OCR. Bộ tách chỉ nhận speaker ở
  đầu dòng và chưa nhận `Nam 1`/`Nam 2`, nên bản dịch một dòng bị dồn thành đoạn.

| ID | Mức độ | Phát hiện | Nguyên nhân | Cách sửa | Expected impact |
| --- | --- | --- | --- | --- | --- |
| QUIZ-ID-H-01 | HIGH | Mọi đề cùng hiện `/quiz`, không thể nhận diện đề từ URL | `Exam` chưa có slug và các launcher push route cố định | Thêm slug unique được sinh từ tiêu đề + UUID, backfill migration, trả slug trong payload và dùng `/quiz/{slug}` | URL ổn định, phân biệt được từng đề, không xung đột khi trùng tên |
| QUIZ-RESUME-H-02 | HIGH | Reload khôi phục đáp án âm thầm nhưng không hỏi tiếp tục và có thể mất vị trí câu | Draft không lưu câu hiện tại; state API không trả projection đã có trong `Attempt` | Lưu vị trí cùng draft, trả từ state/start API, reconcile local/server và chặn màn thi bằng dialog xác nhận trước khi tiếp tục | Reload/mất mạng giữ đáp án và đưa học viên về đúng câu gần nhất |
| QUIZ-AUDIO-H-03 | HIGH | Thi thử vẫn cho phát/tạm dừng audio | Component auto-play đồng thời render manual control | Bỏ hoàn toàn manual control trong exam mode, giữ audio ẩn và tự chuyển câu | Listening tuân thủ quy tắc thi thử, học viên không can thiệp timeline |
| SOLUTION-M-01 | MEDIUM | Nút xem giải biến mất khi chưa có dữ liệu; người dùng không biết lý do | UI condition theo `has_solutions` | Luôn render hành động; thiếu dữ liệu thì mở dialog thông báo chuyên nghiệp | Hành vi nhất quán và phản hồi rõ ràng |
| SOLUTION-M-02 | MEDIUM | Speaker bản dịch dính liền trong một đoạn | OCR có thể trả speaker inline; regex chỉ tách theo newline và thiếu speaker có số | Chuẩn hóa boundary inline trước khi gom soft-wrap, thêm regression test tiếng Việt | `Nữ`, `Nam 1`, `Nam 2` xuống dòng giống Transcript |

### Phần không cần sửa

- Không thay autosave batch/revision hiện có và không tăng tần suất ghi database;
  vị trí câu đi cùng draft/presence đang có nên không tạo request storm mới.
- Không cho slug tham gia chấm điểm hoặc lookup attempt: UUID vẫn là khóa dữ liệu,
  slug chỉ là định danh URL thân thiện và được ràng buộc unique ở database.
- Không cho nút điều khiển audio trở lại để xử lý autoplay bị chặn; lỗi autoplay
  vẫn được báo, còn việc bắt đầu Listening dựa trên user gesture của màn hướng dẫn.

## Incident không chuyển từ Listening sang Reading (2026-08-11)

| ID | Mức độ | Bằng chứng production | Nguyên nhân | Cách sửa | Expected impact |
| --- | --- | --- | --- | --- | --- |
| FULLTEST-H-01 | HIGH | Safari iPad và macOS đều `PATCH draft` thành công (`200`, khoảng 40–50 ms) rồi `POST finalize` nhận `409`; scratch còn khoảng 44 GiB và không có file tồn | Precheck tên đề coi nhãn staging cố định `Listening Component` như tên đề người dùng. Một component cũ `component_pending` của cùng giáo viên làm mọi job Listening mới bị chặn trước redirect | Bỏ kiểm tra trùng tên riêng cho Full Test component; vẫn giữ uniqueness cho đề chính thức. Persistence theo `job_id` tiếp tục làm retry cùng job idempotent | Nhiều Full Test đang soạn hoặc nhiều tab/máy không chặn nhau; retry không sinh duplicate và component vẫn ẩn khỏi Kho đề |

Dung lượng tạm không phải nguyên nhân của incident này. API hiện spool multipart
vào volume SSD `/scratch` (khoảng 76 GiB tổng, 44 GiB trống tại thời điểm kiểm
tra); `/tmp` 1 GiB chỉ là fallback và readiness đã kiểm tra ngưỡng trống.

### Lifecycle của Full Test tạm

Phát hiện tiếp theo: nút `Hủy ghép` trước đây chỉ xóa `sessionStorage`; logout
chỉ thu hồi refresh token. Vì vậy `Exam.status=component_pending`, snapshot và
object MinIO có thể tồn tại vô hạn nếu giáo viên đóng tab hoặc đăng xuất.

Luồng mới dùng ba lớp:

1. `Hủy ghép` gọi API abandon có kiểm tra owner và idempotent.
2. Logout đánh dấu toàn bộ component chưa ghép của tài khoản là abandoned và
   hoàn lại quota Reading đã giữ, đúng một lần.
3. Maintenance mỗi 10 phút hard-delete component abandoned và object liên quan;
   component không có tín hiệu do đóng tab/crash được abandon sau TTL 24 giờ.

Không dùng `beforeunload` làm nguồn sự thật vì Safari có thể bỏ request và thao
tác redirect Listening → Reading cũng phát sinh unload hợp lệ.

## Audit triển khai monolith một máy chủ và nhận diện thương hiệu (2026-08-12)

> Ghi chú: phần triển khai ban đầu bên dưới được ghi trước khi thay dependency
> OCR. Trạng thái chạy thực tế mới nhất ở mục “Current runtime verification”
> cuối file và được ưu tiên khi vận hành.

### Kiến trúc thực tế đã xác nhận

- Frontend là Next.js 16/React 19/Tailwind, build standalone và chạy sau Nginx.
- Backend là FastAPI + SQLAlchemy/psycopg 3, Alembic hiện có 23 revision, Celery
  dùng Redis làm broker/result backend; API chạy 4 Uvicorn worker.
- PostgreSQL và MinIO hiện được cấu hình như dependency bên ngoài trong
  `.env.example`, nhưng `compose.yaml` đang thiếu hai service này. Vì vậy một
  máy mới không thể migrate và readiness đầy đủ chỉ bằng `docker compose up`.
- Nginx protected-media upstream đang trỏ tới `10.10.10.2:9000`, không phù hợp
  với MinIO chạy cùng Compose network.
- OCR local, Tesseract và FFmpeg được đóng gói trong backend image. `main.py`
  và `rapid_ocr.py` giữ adapter/module contract cũ nhưng chỉ sử dụng Tesseract
  local; không còn remote PaddleOCR path.
- `bootstrap_admin()` đã có transaction, password hash Argon2 và xử lý cạnh
  tranh giữa nhiều Uvicorn worker; không cần tạo thêm luồng seed riêng.

### Phát hiện cho yêu cầu lần này

| ID | Mức độ | Phát hiện | Nguyên nhân | Cách sửa | Expected impact |
| --- | --- | --- | --- | --- | --- |
| DEPLOY-CRITICAL-01 | CRITICAL | Stack mới không khởi động được migration đầy đủ | Thiếu service PostgreSQL/MinIO trong Compose nhưng migrate bắt buộc cả hai | Thêm PostgreSQL có volume/healthcheck và MinIO có volume/healthcheck vào cùng network; đổi env sang hostname nội bộ | Một máy chủ có thể tự khởi động dependency, migrate và readiness từ đầu |
| DEPLOY-HIGH-02 | HIGH | Media protected có thể trả 403/timeout trong monolith | Nginx ký request theo host/IP MinIO bên ngoài | Đổi upstream và `Host` sang `minio:9000`, đồng bộ cả HTTP/TLS config | Audio/image/PDF đi trực tiếp qua Nginx → MinIO, không tải qua Python |
| DEPLOY-HIGH-03 | HIGH | OCR không phù hợp mô hình một máy nếu còn URL ngoài | Cấu hình cũ yêu cầu `PADDLE_OCR_URL` bên ngoài | Thay adapter bằng Tesseract local, bỏ remote OCR env và giữ worker concurrency bounded | Không phụ thuộc server OCR thứ hai, giữ giới hạn CPU/RAM hiện tại |
| BRAND-MEDIUM-04 | MEDIUM | Giao diện trộn teal, indigo và logo trắng không tồn tại | Accent hard-code rải ở nhiều route/component; AuthGate tham chiếu `/logo-white.png` | Chuẩn hóa primary accent về palette Navy và dùng `/logo.png` ở mọi điểm nhận diện UI/PWA | Nhận diện nhất quán, không ảnh hưởng màu trạng thái đúng/sai/lỗi |

### Phần không cần sửa

- Không đổi framework, ORM, Celery, Redis, PostgreSQL hay MinIO.
- Không xóa volume hiện có, không reset dữ liệu và không chạy downgrade migration.
- Không thay đổi logic autosave/submit/chấm điểm; request này không có bằng chứng
  cần sửa hot path dữ liệu bài thi.
- Màu đỏ/cam/xanh lá dùng cho lỗi, cảnh báo và kết quả được giữ làm màu ngữ
  nghĩa; chỉ primary/system accent chuyển sang Navy.

## Current runtime verification — 2026-08-12

### Kiến trúc hiện tại

`nginx -> frontend/API -> PostgreSQL + MinIO + Redis`; Celery worker xử lý OCR
và audio. Tất cả chạy trên một Compose network, không có service PaddleOCR
ngoài. Pipeline nghiệp vụ vẫn dùng cùng các lớp `extractor`, `pipeline`,
`answer_key` và parser; chỉ engine OCR trong `backend/rapid_ocr.py` đã đổi sang
Tesseract.

### Findings and actions

| Mức độ | Finding | Action | Expected impact |
| --- | --- | --- | --- |
| HIGH | PaddleOCR/ONNX image dependency không phù hợp all-in-one CPU budget | Gỡ RapidOCR/ONNX dependencies; cài Tesseract system packages và pytesseract | Giảm image/model footprint và loại bỏ remote OCR dependency |
| HIGH | OCR subprocess/thread count có thể nhân với page workers | Giới hạn Tesseract pool 2, worker page workers 4, OMP 2 threads; API pool 1 | Giữ CPU bounded trên host 16 core |
| MEDIUM | Deployment contract/test còn kiểm tra import cũ | Cập nhật Docker build smoke và sidecar readiness contract | CI/deploy kiểm tra đúng runtime mới |

### Verified evidence

- `docker compose build api frontend postgres`: PASS; image backend báo Tesseract
  5.5.0 trong build step.
- `docker compose up -d --force-recreate --wait`: PASS; API, frontend, Nginx,
  PostgreSQL và MinIO healthy; `/health` và `/health/ready` HTTP 200.
- Container OCR smoke: provider `tesseract:cpu`, `ocr_ready=true`, model
  `tesseract-eng`.
- Admin login smoke: HTTP 200. Changed-area tests: `87 passed, 6 skipped`.
- Chưa chạy load test 50/100/150/200 concurrent trong phiên này; không dùng
  benchmark cũ để tuyên bố capacity mới.

### Database/MinIO namespace verification

- PostgreSQL hiện phục vụ database `examify` bằng role `examify_app`; database
  cũ `smart_exam` và role cũ `toeicdoc_app` không còn tồn tại.
- MinIO hiện chỉ còn các bucket `examify-sources`, `examify-assets`,
  `examify-audio`, `examify-answers`, `examify-guides`. Counts sau migration:
  23 source objects, 843 asset objects, 15 audio objects, 0 answer objects,
  0 guide objects.
- Kiểm tra tất cả durable bucket/object references trong PostgreSQL không còn
  namespace cũ và không có reference nào trỏ tới object bị thiếu.

## Audit incident OCR trình duyệt chậm và lỗi WASM (2026-08-14)

### Phạm vi và bằng chứng

Audit này tập trung vào hiện tượng file Listening khoảng 11 trang chạy hơn 5
phút trên Chrome/Windows. Đường xử lý thực tế là client-side, không đi qua
FastAPI để nhận dạng:

```text
Browser
  -> PDF.js đọc PDF và render canvas
  -> preprocess.worker.js (grayscale; OpenCV.js chỉ ở recovery)
  -> Tesseract.js Web Worker + eng.traineddata
  -> parser TypeScript + checkpoint IndexedDB/OPFS
```

Bằng chứng từ DevTools trong incident:

- `opencv.js` báo `wasm streaming compile failed` và sau đó
  `WebAssembly.instantiate(): expected magic word 00 61 73 6d, found 3c 21 44 4f`.
- `00 61 73 6d` là magic hợp lệ của WebAssembly; `3c 21 44 4f` là đầu của
  nội dung HTML (`<!DO...`). Vì vậy ít nhất một request `.wasm` đang nhận
  trang lỗi/fallback HTML thay vì binary WASM. Đây là lỗi artifact/deployment
  hoặc proxy route, không phải do ảnh OCR khó.
- Artifact trong repository hiện có magic đúng và kích thước hợp lý:
  `opencv_js.wasm` khoảng 3.0 MiB và
  `tesseract-core-simd-lstm.wasm` khoảng 2.7 MiB. Local Next runtime đã trả
  cả hai với `Content-Type: application/wasm` và HTTP 200; production domain
  không phản hồi được trong phiên audit nên cần kiểm tra trực tiếp sau deploy.
- Tesseract.js core được pin ở
  `frontend/lib/client-ocr/tesseract-runtime.ts:49-58`, nhưng OpenCV loader
  vẫn tự tìm `opencv_js.wasm` tương đối với `opencv.js`. Chỉ cần một file bị
  thiếu trong image frontend, bị SPA fallback hoặc trả sai MIME là recovery
  sẽ log lỗi và chờ fallback.

### Luồng hiện tại gây khuếch đại thời gian

`frontend/lib/client-ocr/runtime.ts` đang thực hiện hai pass trên toàn bộ PDF:

1. Pass layout tại dòng 525: mọi trang scan được render 120 DPI và OCR PSM 11
   để tìm cột/số câu.
2. Pass OCR chính tại dòng 552: mỗi trang lại render 225 DPI, copy toàn bộ
   `ImageData`, preprocess rồi OCR lần nữa.
3. Nếu layout có hai cột, dòng 221--248 tách thành hai canvas và nhận dạng cả
   hai vùng. Với thiết bị chỉ có một worker, `Promise.all` không tạo song song
   thật vì pool xếp hai job vào cùng một worker.
4. Nếu confidence/anchor/options chưa đạt, dòng 580--600 chạy recovery PSM 11
   thêm một lượt; recovery hai cột tiếp tục thành hai lần OCR.

Do đó 11 trang scan thường không phải 11 lần OCR mà có thể là:

```text
11 locator + (11 x 2 baseline) + (11 x 2 recovery khi cần) = tối đa 55 lượt
```

Trên máy dưới 8 logical cores, `clientOcrWorkerCount()` tại
`frontend/lib/client-ocr/capabilities.ts:58-60` chỉ tạo một worker, nên các
lượt đó chạy nối tiếp. Ngoài inference còn có chi phí khởi tạo model, render
PDF, chuyển `ImageData` giữa main thread/preprocess worker/Tesseract worker và
serialize blocks/TSV.

### Findings

| ID | Mức độ | Phát hiện/nguyên nhân | Cách sửa bắt buộc | Expected impact |
|---|---|---|---|---|
| OCR-ASSET-C-01 | CRITICAL | `.wasm` có thể bị trả HTML; OpenCV recovery không khởi tạo đúng và log lỗi gây chờ/fallback không minh bạch. | Thêm preflight kiểm tra HTTP status, MIME và 8-byte WASM magic cho OpenCV/Tesseract; build/deploy smoke phải kiểm tra mọi asset dưới `/ocr/**`; cấu hình `locateFile` OpenCV tuyệt đối, không phụ thuộc current script. | Fail-fast trong vài trăm ms với thông báo deploy rõ ràng; khi asset đúng, bỏ lỗi compile và giảm thời gian cold start/recovery. |
| OCR-PERF-C-02 | CRITICAL | Hai pass OCR toàn tài liệu, cộng split hai cột và recovery, tạo 33--55 recognition jobs cho file 11 trang. | Đổi sang một baseline pass 180--225 DPI; dùng kết quả baseline để phát hiện cột/anchor; chỉ OCR split hoặc recovery theo trang có issue, không chạy locator pass riêng toàn bộ PDF. Giữ baseline evidence để không đổi accuracy âm thầm. | Giảm số lượt OCR hot path khoảng 2--4 lần; mục tiêu file 11 trang còn dưới 60--90 giây trên máy 4-core/8 GiB sau benchmark. |
| OCR-PERF-H-03 | HIGH | Device dưới 8 logical cores chỉ có một worker; hai cột chạy nối tiếp. Chọn worker chỉ theo core, chưa tính RAM/thermal/battery. | Chọn concurrency theo `hardwareConcurrency` + `deviceMemory` + mobile/battery; desktop 8+ cores có thể dùng 2 worker, máy yếu 1 worker; không vượt trần 2. Warm một pool dùng chung thay vì khởi tạo lại theo chức năng. | Máy đủ tài nguyên tận dụng CPU; máy yếu không bị swap/thermal throttle. |
| OCR-PERF-H-04 | HIGH | Trang A4 225 DPI tạo khoảng 5 MP và nhiều bản copy RGBA; Tesseract trả `text`, `blocks`, `tsv` dù parser chỉ dùng lines/words. | Benchmark giảm 180/200 DPI theo corpus; bỏ output `tsv` nếu không cần; tái sử dụng buffer/canvas và giải phóng ngay sau mỗi page; giữ recovery độ phân giải cao chỉ khi evidence thiếu. | Giảm CPU, copy/GC và memory peak; không giảm accuracy nếu golden gate giữ nguyên. |
| OCR-PERF-H-05 | HIGH | Mỗi page gọi `putClientOcrDraft`; quota enforcement lại list toàn bộ draft/blob và structured-clone toàn bộ `pages` đang tăng dần (`local-drafts.ts:84-89,149-160`). | Checkpoint page phải bounded: quota kiểm tra khi tạo/ghi blob, progress metadata nhẹ; ghi page delta/transaction một lần, hoặc debounce checkpoint tối đa 1 lần/1--2 trang nhưng flush trước unload/cancel. | Giảm IndexedDB serialization/GC và thời gian phụ tăng theo số trang, vẫn resume được sau refresh. |
| OCR-PERF-M-06 | MEDIUM | `setParameters` chạy trước mỗi recognition và output blocks/TSV được serialize lại cho từng vùng. | Cache parameters theo worker/PSM/whitelist; chỉ yêu cầu output cần cho parser; đo riêng render, preprocess, init, recognition, checkpoint. | Giảm round-trip worker và giúp xác định đúng bottleneck trên thiết bị thật. |
| OCR-DEPLOY-H-07 | HIGH | Không có contract kiểm tra URL asset sau khi build image; local artifact đúng không chứng minh image production đúng. | CI chạy `file`, magic-byte, MIME; sau rollout dùng `curl`/smoke browser kiểm tra `.wasm`, `.js`, traineddata, PDF worker và cache header. Không dùng SPA fallback cho `/ocr/**`. | Chặn release gây đúng incident `found 3c 21 44 4f`; cache immutable chỉ áp dụng sau khi checksum đúng. |
| OCR-UX-M-08 | MEDIUM | UI chỉ hiển thị progress OCR tổng; chưa cho biết đang render/preprocess/model/recognize/checkpoint và không cảnh báo máy yếu. | Thêm telemetry local không chứa PDF/text: duration từng stage, pages, worker count, memory nếu có; hiển thị “đang khởi tạo OCR” và khuyến nghị Chrome desktop/cắm sạc khi cần. | Người dùng phân biệt được cold start, asset lỗi và inference chậm; dữ liệu đủ để tune theo device matrix. |

### Phần không cần sửa

- Không đưa PDF/OCR lên FastAPI chỉ để làm nhanh hơn cho một người; đó sẽ biến
  200 người tạo đề thành CPU/request storm trên server 8 core.
- Không thêm Redis, Kafka, Kubernetes, GPU server hoặc microservice cho OCR ở
  workload hiện tại. Server PostgreSQL/MinIO/Redis không nằm trên đường OCR
  trước commit và không phải nguyên nhân của 5 phút trong screenshot.
- Không bỏ checkpoint, parser evidence, recovery hoặc unique/idempotent data
  contract nếu chưa có golden test chứng minh tương đương. Tốc độ không được
  đánh đổi đáp án/câu hỏi bị mất.
- Không tăng worker vô hạn. Hai Tesseract WASM worker đã có thể nhân RAM model
  và làm máy 4-core chậm hơn do contention; concurrency phải bounded và đo
  trên thiết bị thật.

### Kế hoạch triển khai sau audit

1. **P0 — Deployment correctness:** sửa/check asset OpenCV/Tesseract, preflight
   fail-fast và cache/MIME contract; kiểm tra production URL sau recreate image.
2. **P1 — Reduce OCR passes:** bỏ locator OCR toàn tài liệu, giữ một baseline
   pass và chỉ fallback per-page; chạy LC/RC golden để xác nhận không giảm
   question/option anchor recall.
3. **P1 — Reduce transfer/serialization:** bỏ TSV thừa, cache parameters,
   giảm copy RGBA, bounded IndexedDB checkpoint và stage timings.
4. **P2 — Adaptive device policy:** concurrency 1/2 theo core+memory+mobile,
   warm model, không chạy OCR khi browser đang ở background nếu không cần.
5. **P2 — Device benchmark:** Chromium matrix 4-core/8 GiB và 8-core/16 GiB,
   cold-cache/warm-cache, 11/13/28 trang; ghi p50/p95, peak memory, asset
   errors và accuracy vào `LOAD_TEST.md`.

### Verification baseline

- `npm run lint`: PASS.
- `npm test`: PASS, 22 test files / 86 tests.
- `npm run check:quiz-budget`: PASS, `/quiz` 219.5 KiB gzip / 250 KiB.
- `python3 -m compileall -q backend`: PASS.
- Local Next asset smoke: `.wasm` trả HTTP 200, `Content-Type:
  application/wasm`, magic `00 61 73 6d`; production asset smoke chưa lấy được
  vì domain timeout trong phiên audit và phải chạy lại trên server/deploy thật.
- Golden Playwright trước đó trong repository PASS coverage/anchors; wall time
  dev machine khoảng 2.3--2.5 phút cho mỗi fixture, chưa phải chứng nhận cho
  file 11 trang trên end-user. `LOAD_TEST.md` vẫn phải giữ trạng thái pending
  cho device matrix.

### Follow-up sau audit — 2026-08-14

- P0/P1 đã triển khai: preflight WASM OpenCV/Tesseract, build asset gate,
  `locateFile` tuyệt đối, OpenCV timeout 1,2 giây, bounded OCR 1--2 worker,
  giảm serialization/copy và checkpoint quota scan.
- Golden Reading và Listening sau thay đổi đều PASS 100/100 question/option
  anchors. Cùng môi trường Chromium, wall time giảm còn khoảng 1m30s--1m36s
  mỗi fixture. Đây là regression benchmark trên máy phát triển, chưa phải
  SLA cho thiết bị end-user.
- `npm run lint`, `npm test` (22 file/86 test), `npm run check:ocr-assets` và
  `npm run build` đều PASS. Device matrix 4-core/8 GiB, 8-core/16 GiB và
  production asset smoke vẫn là bước bắt buộc trước khi công bố p95.
