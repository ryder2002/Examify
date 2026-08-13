# PROMPT: FULL SYSTEM AUDIT, SECURITY HARDENING, RESPONSIVE UI, TAURI CROSS-PLATFORM & OCR OPTIMIZATION

Hãy thực hiện một đợt **audit, refactor, optimization và hardening toàn diện** cho toàn bộ source code của dự án hiện tại.

Mục tiêu là đưa hệ thống về trạng thái **ổn định, bảo mật, hiệu năng cao, responsive hoàn chỉnh và có thể chạy production**, đồng thời đảm bảo Web App và Tauri Desktop App sử dụng chung logic/API một cách nhất quán.

Không chỉ đưa ra nhận xét hoặc kế hoạch. Hãy trực tiếp kiểm tra source code, xác định vấn đề, thực hiện các bản vá cần thiết, chạy test và báo cáo lại những thay đổi đã thực hiện.

---

# 1. RÀ SOÁT TOÀN BỘ SOURCE CODE

Đọc và phân tích toàn bộ repository trước khi sửa.

Cần xác định rõ:

* Kiến trúc frontend.
* Backend.
* API.
* Database.
* Authentication / Authorization.
* Tauri Desktop App.
* Python Core.
* OCR pipeline.
* PDF/Image processing.
* Upload/download.
* Storage.
* Queue/background processing nếu có.
* Cache.
* Rate limit.
* Các dependency hiện tại.
* Các biến môi trường.
* Các endpoint public/private.
* Các chức năng liên quan đến làm bài thi, luyện tập và OCR.

Sau khi hiểu architecture mới bắt đầu refactor.

Không được sửa theo kiểu đoán hoặc patch cục bộ làm ảnh hưởng các module khác.

---

# 2. SECURITY AUDIT TOÀN HỆ THỐNG

Rà soát toàn bộ source code để tìm các lỗ hổng bảo mật có thể tồn tại.

Kiểm tra tối thiểu:

* SQL Injection.
* XSS.
* CSRF.
* SSRF.
* Path Traversal.
* Command Injection.
* File Upload vulnerability.
* Arbitrary File Read.
* Arbitrary File Write.
* Broken Authentication.
* Broken Authorization.
* IDOR.
* Token leakage.
* JWT/session implementation.
* API key exposure.
* Sensitive data nằm trong frontend bundle.
* Hard-coded secrets.
* CORS configuration.
* WebSocket security nếu có.
* Rate limit.
* Brute-force protection.
* Replay request.
* Request size limit.
* Upload size limit.
* Malicious PDF/image upload.
* Dependency vulnerabilities.
* Logging có làm lộ token/password/API key hay không.
* Tauri IPC security.
* Tauri command permissions.
* CSP của Web và Tauri.

Nếu phát hiện vấn đề:

1. Xác định nguyên nhân.
2. Đánh giá mức độ:

   * Critical
   * High
   * Medium
   * Low
3. Thực hiện bản vá.
4. Không làm thay đổi behavior hợp lệ của hệ thống nếu không cần thiết.

---

# 3. HARDENING CHỐNG DDOS / API ABUSE

Kiểm tra hệ thống hiện tại đã có đầy đủ cơ chế chống abuse và DDoS ở tầng application hay chưa.

Lưu ý: không tuyên bố rằng application có thể tự chống được mọi loại DDoS. Những cuộc tấn công volumetric phải được xử lý thêm ở CDN/WAF/reverse proxy/infrastructure.

Ở tầng application hãy triển khai hoặc cải thiện:

### Rate Limiting

Thiết lập rate limit hợp lý theo:

* IP.
* User.
* Token.
* Endpoint.

Không dùng một threshold duy nhất cho toàn bộ API.

Ví dụ:

* Login: rất thấp.
* Register/reset password: thấp.
* API thông thường: trung bình.
* OCR/PDF processing: giới hạn riêng.
* Upload: giới hạn riêng.
* Download: giới hạn riêng.

### Abuse Protection

Thiết lập:

* Request timeout.
* Maximum request body.
* Maximum uploaded file size.
* Maximum number of concurrent OCR jobs/user.
* Maximum PDF pages nếu cần.
* Maximum image resolution nếu cần.
* Queue OCR thay vì cho request chiếm worker vô hạn.
* Retry/backoff hợp lý.
* Connection limit.
* Pagination bắt buộc cho API trả danh sách lớn.

Nếu hệ thống có Redis hãy ưu tiên Redis-based distributed rate limiting.

Nếu không có Redis hãy lựa chọn giải pháp phù hợp architecture hiện tại.

### Reverse Proxy / CDN

Kiểm tra và đề xuất cấu hình production cho:

* Cloudflare hoặc CDN tương đương.
* Nginx/Caddy/Traefik nếu project đang sử dụng.
* WAF.
* Bot protection.
* HTTP/2 hoặc HTTP/3.
* Compression.
* Caching.
* Static asset caching.
* API cache phù hợp.

Không cache dữ liệu riêng tư hoặc dữ liệu làm bài thi của user.

---

# 4. RESPONSIVE TOÀN BỘ HỆ THỐNG

Rà soát toàn bộ UI, đặc biệt là **giao diện làm bài**.

Mục tiêu:

Hệ thống phải hiển thị tốt trên:

* Mobile.
* iPhone.
* Android.
* iPad.
* Tablet.
* Laptop.
* Desktop.
* Màn hình lớn.

Kiểm tra tối thiểu các breakpoint:

* 320px.
* 375px.
* 390px.
* 430px.
* 768px.
* 820px.
* 1024px.
* 1280px.
* 1440px.
* 1920px.

Không chỉ sửa một màn hình.

Hãy rà soát toàn bộ:

* Sidebar.
* Header.
* Modal.
* Dropdown.
* Form.
* Table.
* Pagination.
* Dashboard.
* Trang làm bài.
* Trang kết quả.
* Trang OCR.
* PDF viewer.
* Question navigation.
* Answer options.
* Timer.
* Audio player.
* Speaking UI.
* Các component dùng chung.

Không được xảy ra:

* Horizontal overflow.
* Text bị cắt.
* Button ra ngoài màn hình.
* Modal lớn hơn viewport.
* Layout vỡ trên iPad.
* Fixed width không cần thiết.
* Hover-only interaction trên mobile.

Ưu tiên responsive bằng CSS/layout hiện có của project thay vì tạo thêm duplicated component cho mobile.

---

# 5. GIAO DIỆN LÀM BÀI – PRACTICE MODE & MOCK TEST MODE

Hiện tại hệ thống có ít nhất hai chế độ:

* Chế độ luyện tập.
* Chế độ thi thử.

Hãy tách behavior rõ ràng.

## Practice Mode

Dictionary hiện tại được phép tồn tại.

Giữ nguyên chức năng dictionary nếu đang hoạt động đúng.

## Mock Test / Thi thử

Trong chế độ thi thử:

**Dictionary phải bị vô hiệu hóa hoàn toàn.**

Không được chỉ ẩn button.

Hãy kiểm tra toàn bộ logic kích hoạt dictionary.

Dictionary không được xuất hiện khi:

* Click vào từ.
* Double click.
* Select text.
* Right click nếu dictionary được trigger từ context menu.
* Keyboard shortcut.
* Touch selection trên mobile.
* Long press trên mobile/tablet.
* Event bubbling từ component khác.

Nếu dictionary được implement bằng global listener:

* mouseup
* dblclick
* selectionchange
* contextmenu
* touch
* pointer events

thì cần đảm bảo listener đó không thực hiện dictionary lookup khi:

```text
mode === mock-test
```

hoặc trạng thái tương đương của hệ thống.

Không được làm ảnh hưởng dictionary của Practice Mode.

Cần đảm bảo logic này hoạt động giống nhau trên:

* Web.
* Tauri Windows.
* Tauri macOS.

---

# 6. RÀ SOÁT TOÀN BỘ TAURI APP

Audit toàn bộ Tauri Desktop App.

Mục tiêu:

Tauri phải sử dụng đúng API và behavior giống Web App.

Kiểm tra:

* API base URL.
* Environment detection.
* Authentication.
* Token storage.
* Refresh token.
* Cookie/session.
* Upload.
* Download.
* OCR.
* PDF.
* Audio.
* Microphone.
* Camera.
* Filesystem.
* Clipboard.
* WebSocket nếu có.
* Deep links nếu có.
* Auto-update nếu có.
* Tauri commands.
* Permissions/capabilities.
* CSP.
* CORS.
* File protocol.
* IPC.

Không được có tình trạng:

Web gọi API A nhưng Tauri lại gọi API B hoặc sử dụng payload khác.

Hãy refactor để Web và Desktop chia sẻ API client/service layer nhiều nhất có thể.

---

# 7. MACOS SUPPORT – INTEL & APPLE SILICON

Đảm bảo Tauri app build và chạy được trên:

### Intel Mac

```text
x86_64-apple-darwin
```

### Apple Silicon

```text
aarch64-apple-darwin
```

Kiểm tra toàn bộ dependency native.

Đặc biệt:

* Python binary.
* OCR engine.
* Native Node/Rust dependencies.
* OpenSSL.
* ffmpeg nếu có.
* ImageMagick nếu có.
* Poppler nếu có.
* PaddleOCR/ONNX Runtime.

Không được assume tất cả máy Mac đều là Apple Silicon.

Thiết lập build pipeline phù hợp.

Nếu project phù hợp, tạo cả:

* Intel build.
* Apple Silicon build.
* Universal Binary.

Kiểm tra installer:

* DMG.
* APP.
* PKG nếu project đang dùng.

Đảm bảo application có thể được cài đặt và khởi động bình thường.

Rà soát:

* Info.plist.
* entitlements.
* microphone permission.
* camera permission.
* file permission.
* network permission.

Nếu app chưa notarize/code sign thì giữ architecture sẵn sàng cho:

* Apple Developer signing.
* Hardened Runtime.
* Notarization.

Không hard-code certificate hoặc secret signing vào repository.

---

# 8. AUDIT PYTHON CORE

Python hiện là core xử lý:

* PDF.
* Cắt ảnh.
* OCR.

Hãy audit toàn bộ pipeline Python.

Không chỉ tối ưu OCR model.

Cần đo từng stage:

```text
PDF Input
↓
PDF Decode / Render
↓
Page Detection
↓
Image Crop
↓
Image Preprocessing
↓
OCR
↓
Text Post-processing
↓
Result
```

Hãy instrument timing để biết stage nào đang chậm.

Ví dụ:

```text
Render page: 120ms
Crop: 20ms
Preprocess: 45ms
OCR inference: 380ms
Postprocess: 30ms
Total: 595ms
```

Không tối ưu dựa trên phỏng đoán.

---

# 9. CẢI THIỆN ĐỘ CHÍNH XÁC CẮT ẢNH

Kiểm tra thuật toán hiện tại đang dùng để xác định vùng cần OCR.

Đảm bảo:

* Không crop mất chữ.
* Không crop dư quá nhiều.
* Không nhầm vùng.
* Không lệch bounding box.
* Không bị ảnh hưởng bởi DPI.
* Không bị ảnh hưởng mạnh bởi scan hơi nghiêng.
* Không bị lỗi khi PDF scan ở nhiều resolution khác nhau.

Xem xét:

* Deskew.
* Perspective correction.
* Adaptive threshold.
* Noise removal.
* Contrast enhancement.
* Grayscale.
* Sharpening nếu thực sự cải thiện OCR.
* Bounding box normalization.

Nhưng không preprocessing quá mức làm mất dấu tiếng Việt hoặc ký tự nhỏ.

---

# 10. CẢI THIỆN OCR ACCURACY

Đánh giá engine OCR hiện tại trước khi thay đổi.

Đặc biệt cần đảm bảo nhận dạng tốt:

* Tiếng Việt.
* Tiếng Anh.
* Số.
* Dấu câu.
* Mã văn bản.
* Ngày tháng.
* Các dòng chữ nhỏ.
* Document scan.

Kiểm tra:

* OCR model.
* Language model.
* Image resolution.
* DPI.
* Preprocessing.
* Detection threshold.
* Recognition threshold.
* Batch size.
* CPU/GPU execution provider.
* Thread count.

Nếu đang sử dụng OCR model nặng nhưng không cần thiết, cân nhắc model nhẹ hơn nhưng phải benchmark accuracy.

Không đổi OCR engine chỉ vì engine khác “có vẻ nhanh hơn”.

Phải benchmark.

---

# 11. TĂNG TỐC OCR

Hiện tại OCR vài trang cũng mất khá lâu.

Hãy tìm bottleneck thực tế.

Kiểm tra xem có đang xảy ra:

* Load OCR model lại mỗi trang.
* Spawn Python process lại mỗi request.
* Render PDF nhiều lần.
* Save image ra disk rồi đọc lại không cần thiết.
* Encode/decode Base64 nhiều lần.
* OCR tuần tự toàn bộ pages.
* Khởi tạo model nhiều lần.
* Copy image buffer quá nhiều.
* Process resolution quá cao.
* Blocking operation trong API.
* Không batching.
* Không multiprocessing.
* Không queue.
* Không caching.

Ưu tiên kiến trúc:

```text
Application Start
↓
Load OCR Model Once
↓
Keep Model Warm
↓
OCR Jobs
↓
Worker Pool
```

Không được:

```text
Request
↓
Load Model
↓
OCR
↓
Destroy Model
```

cho từng request.

---

# 12. PARALLEL PROCESSING

Nếu OCR nhiều trang, xem xét xử lý song song.

Ví dụ:

```text
Page 1 ─┐
Page 2 ─┤
Page 3 ─┼→ OCR Workers → Result Aggregator
Page 4 ─┤
Page 5 ─┘
```

Nhưng phải giới hạn concurrency.

Không tạo số worker không giới hạn vì có thể gây:

* Out of Memory.
* CPU 100%.
* DDoS nội bộ.
* Server crash.

Xác định concurrency dựa trên:

* CPU cores.
* RAM.
* OCR engine.
* GPU nếu có.

---

# 13. MEMORY OPTIMIZATION

Kiểm tra memory leak ở:

* Python.
* OCR model.
* Image buffer.
* PDF rendering.
* Node/backend.
* Frontend.
* Tauri.

Không giữ toàn bộ ảnh PDF trong RAM nếu không cần thiết.

Ưu tiên pipeline kiểu streaming/batch.

Ví dụ:

```text
Read Page
→ Process
→ OCR
→ Release memory
→ Next Page
```

thay vì:

```text
Load toàn bộ PDF
→ Convert toàn bộ thành ảnh
→ Giữ tất cả ảnh trong RAM
→ OCR
```

nếu architecture cho phép.

---

# 14. CACHE

Xem xét cache các kết quả OCR khi:

* File giống nhau.
* Page giống nhau.
* Input không thay đổi.

Có thể dùng:

```text
SHA-256(file/page)
```

làm cache key.

Không OCR lại cùng một dữ liệu nếu kết quả đã tồn tại và cache vẫn hợp lệ.

---

# 15. LOGGING & OBSERVABILITY

Thiết lập logging rõ ràng cho:

* API response time.
* OCR duration.
* PDF render duration.
* Authentication failure.
* Rate limit.
* Server error.
* Worker crash.

Không log:

* Password.
* Full access token.
* Refresh token.
* API secret.
* Sensitive user information.

Nếu có logging production, thêm request ID/correlation ID để truy vết.

---

# 16. ERROR HANDLING

Rà soát toàn bộ error handling.

Không để API trả về:

```text
500 Internal Server Error
```

cho tất cả lỗi.

Phân loại phù hợp:

```text
400 Bad Request
401 Unauthorized
403 Forbidden
404 Not Found
409 Conflict
413 Payload Too Large
415 Unsupported Media Type
422 Validation Error
429 Too Many Requests
500 Internal Server Error
503 Service Unavailable
```

Frontend/Tauri phải xử lý tương ứng.

---

# 17. TESTING

Sau khi refactor phải chạy test.

Nếu project chưa có đủ test, hãy bổ sung test cho các phần quan trọng.

Bao gồm:

### Security

* Unauthorized API access.
* Invalid token.
* Expired token.
* Rate limit.
* Oversized upload.
* Invalid PDF.
* Invalid image.

### Exam

* Practice dictionary hoạt động.
* Mock Test dictionary bị disable.
* Double-click không gọi dictionary trong Mock Test.
* Selection không gọi dictionary trong Mock Test.

### Responsive

Kiểm tra các viewport chính.

### OCR

Tạo benchmark/test dataset nhỏ.

Đo:

```text
Accuracy
Average OCR time/page
Total processing time
Peak RAM
CPU usage
```

So sánh:

```text
BEFORE
vs
AFTER
```

---

# 18. KHÔNG ĐƯỢC LÀM MẤT CHỨC NĂNG HIỆN TẠI

Trong quá trình refactor:

Không được tự ý:

* Thay đổi business logic.
* Xóa API.
* Đổi database schema không cần thiết.
* Đổi response format.
* Đổi authentication flow.
* Xóa feature đang hoạt động.

Nếu bắt buộc phải breaking change, phải ghi rõ lý do trước trong báo cáo và thực hiện migration tương ứng.

---

# 19. CODE QUALITY

Sau khi sửa:

* Remove dead code.
* Remove duplicated code.
* Remove unused dependencies.
* Remove unused imports.
* Fix TypeScript errors.
* Fix Rust warnings quan trọng.
* Fix Python lint/type issues phù hợp.
* Không để debug console.log trong production.
* Không để TODO bảo mật chưa xử lý mà không có giải thích.

Ưu tiên:

```text
Readable
Maintainable
Modular
Testable
Secure
Fast
```

Không over-engineer.

---

# 20. THỨ TỰ TRIỂN KHAI

Thực hiện theo thứ tự:

### Phase 1

Repository & architecture audit.

### Phase 2

Security audit.

### Phase 3

Security patch + anti-abuse + DDoS application hardening.

### Phase 4

Responsive toàn hệ thống.

### Phase 5

Exam Practice/Mock Test dictionary behavior.

### Phase 6

Tauri API parity audit.

### Phase 7

macOS Intel + Apple Silicon compatibility.

### Phase 8

Python core profiling.

### Phase 9

Image crop optimization.

### Phase 10

OCR accuracy optimization.

### Phase 11

OCR performance optimization.

### Phase 12

Testing.

### Phase 13

Regression test.

### Phase 14

Final production readiness review.

---

# 21. BẮT BUỘC BUILD VÀ TEST

Sau khi hoàn thành phải chạy những command phù hợp với repository, ví dụ:

```bash
lint
typecheck
test
build
```

Frontend/Web:

```bash
npm run lint
npm run typecheck
npm run test
npm run build
```

hoặc command tương ứng với package manager thực tế.

Tauri:

```bash
cargo check
cargo clippy
cargo test
tauri build
```

Python:

```bash
pytest
```

và benchmark OCR.

Không kết luận "đã hoàn thành" nếu build đang lỗi.

---

# 22. OUTPUT CUỐI CÙNG

Sau khi hoàn thành, tạo báo cáo:

```text
AUDIT_REPORT.md
```

bao gồm:

## 1. Architecture Overview

Mô tả architecture hệ thống.

## 2. Security Issues Found

| Severity | Issue | Location | Fix |
| -------- | ----- | -------- | --- |

## 3. Security Improvements

Các biện pháp hardening đã triển khai.

## 4. Responsive Improvements

Danh sách các component/page đã sửa.

## 5. Exam Mode Changes

Mô tả cách dictionary hoạt động:

```text
Practice → Enabled
Mock Test → Disabled
```

## 6. Tauri Audit

Các lỗi API/platform đã phát hiện và sửa.

## 7. macOS Compatibility

Trạng thái:

```text
Apple Silicon: PASS / FAIL
Intel: PASS / FAIL
Universal: PASS / FAIL / NOT CONFIGURED
```

## 8. OCR Benchmark

Bảng:

| Metric           | Before | After |
| ---------------- | -----: | ----: |
| OCR time/page    |        |       |
| Total processing |        |       |
| Accuracy         |        |       |
| Peak RAM         |        |       |

## 9. Files Changed

Liệt kê những file quan trọng đã sửa.

## 10. Remaining Risks

Những vấn đề chưa thể giải quyết hoàn toàn ở tầng application.

---

# 23. DEFINITION OF DONE

Chỉ coi task hoàn thành khi:

* Không còn vulnerability Critical/High đã xác định mà chưa xử lý.
* API có rate limiting hợp lý.
* Upload/OCR có resource limits.
* Không có obvious API abuse vector.
* UI responsive tốt trên Mobile/Tablet/Desktop.
* Mock Test không thể kích hoạt dictionary bằng click/double-click/select/long-press.
* Practice Mode vẫn dùng dictionary bình thường.
* Web và Tauri sử dụng API nhất quán.
* Tauri build architecture phù hợp macOS Intel và Apple Silicon.
* Python crop không làm mất vùng nội dung.
* OCR accuracy không giảm sau optimization.
* OCR performance có benchmark rõ ràng.
* Model OCR không bị load lại không cần thiết.
* Không phát sinh memory leak rõ ràng.
* Lint/typecheck/test/build thành công.
* Không phát sinh regression ở chức năng hiện tại.
* `AUDIT_REPORT.md` được tạo đầy đủ.

---

# NGUYÊN TẮC QUAN TRỌNG

Không chỉ phân tích và nói rằng "nên sửa".

Nếu xác định được vấn đề và có đủ thông tin trong repository để sửa thì:

**Hãy trực tiếp sửa source code.**

Với mỗi thay đổi lớn:

```text
Inspect
→ Understand
→ Implement
→ Test
→ Verify
```

Không sửa code dựa trên giả định.

Ưu tiên giải quyết root cause thay vì thêm workaround.

Nếu phát hiện một vấn đề nằm ngoài phạm vi ban đầu nhưng ảnh hưởng trực tiếp tới:

* Security.
* Stability.
* Performance.
* Data integrity.
* OCR.
* Tauri compatibility.

hãy xử lý luôn nếu việc sửa không tạo breaking change.

Mục tiêu cuối cùng là đưa toàn bộ hệ thống tới trạng thái:

**Production-ready, secure, responsive, cross-platform, stable và tối ưu hiệu năng.**
