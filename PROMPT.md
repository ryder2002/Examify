# Xây dựng Module Hướng Dẫn Sử Dụng (Để ở dưới Footer)

## Mục tiêu

Xây dựng một module **Hướng dẫn sử dụng** hoàn chỉnh cho ứng dụng hiện tại.

Module gồm hai phần:

1. Trang quản trị dành cho Admin để tạo và chỉnh sửa nội dung hướng dẫn.
2. Trang hiển thị hướng dẫn dành cho người dùng.

Không thay đổi hoặc làm hỏng các chức năng đang hoạt động trong hệ thống. Hãy kiểm tra cấu trúc dự án hiện tại, tái sử dụng component, API, authentication và design system có sẵn.

---

# 1. Phân quyền

Chỉ tài khoản có role `ADMIN` mới được phép:

* Truy cập trang quản lý hướng dẫn.
* Tạo bài hướng dẫn.
* Chỉnh sửa bài hướng dẫn.
* Xóa bài hướng dẫn.
* Đăng hoặc ẩn bài hướng dẫn.
* Upload ảnh và video.
* Thay đổi thứ tự hiển thị bài viết.

Người dùng thông thường chỉ được:

* Xem danh sách bài hướng dẫn đã được đăng.
* Xem chi tiết bài hướng dẫn.
* Tìm kiếm bài viết.
* Lọc bài viết theo danh mục nếu có.

Cần kiểm tra quyền ở cả Frontend và Backend. Không chỉ ẩn nút trên giao diện mà phải bảo vệ API bằng role Admin.

---

# 2. Trang quản lý Hướng dẫn sử dụng dành cho Admin

Tạo một trang Admin mới với tên:

`Quản lý hướng dẫn sử dụng`

Trang này hiển thị danh sách tất cả bài hướng dẫn.

Mỗi bài viết hiển thị:

* Tiêu đề.
* Ảnh đại diện.
* Danh mục.
* Trạng thái.
* Thứ tự hiển thị.
* Ngày tạo.
* Ngày cập nhật.
* Người tạo.
* Nút chỉnh sửa.
* Nút xem trước.
* Nút đăng hoặc ẩn.
* Nút xóa.

Các trạng thái đề xuất:

* `DRAFT`: Bản nháp.
* `PUBLISHED`: Đã đăng.
* `HIDDEN`: Đã ẩn.

Trang quản lý cần có:

* Tìm kiếm theo tiêu đề.
* Lọc theo trạng thái.
* Lọc theo danh mục.
* Phân trang.
* Sắp xếp theo ngày tạo hoặc thứ tự hiển thị.
* Nút `Tạo hướng dẫn mới`.

Sau khi tạo, chỉnh sửa, đăng hoặc xóa bài viết, danh sách phải được cập nhật ngay mà không cần tải lại toàn bộ ứng dụng.

---

# 3. Form tạo và chỉnh sửa bài hướng dẫn

Form tạo bài hướng dẫn cần có các trường:

* Tiêu đề bài viết.
* Slug hoặc đường dẫn thân thiện.
* Mô tả ngắn.
* Ảnh đại diện.
* Danh mục.
* Nội dung hướng dẫn.
* Thứ tự hiển thị.
* Trạng thái.
* Từ khóa tìm kiếm nếu cần.

Có các nút:

* Lưu bản nháp.
* Xem trước.
* Đăng bài.
* Hủy.
* Cập nhật bài viết.

Cần cảnh báo nếu Admin rời trang khi có nội dung chưa lưu.

---

# 4. Trình soạn thảo văn bản Rich Text Editor

Tạo hoặc tích hợp một trình soạn thảo văn bản có đầy đủ các chức năng cơ bản tương tự Word hoặc Office.

Có thể sử dụng một thư viện phù hợp với công nghệ hiện tại, ưu tiên:

* Tiptap.
* Lexical.
* CKEditor.
* Quill.
* Hoặc thư viện Rich Text Editor đang có sẵn trong dự án.

Ưu tiên Tiptap nếu dự án chưa có editor.

## Các chức năng bắt buộc

### Định dạng chữ

* Chữ đậm.
* Chữ nghiêng.
* Gạch chân.
* Gạch ngang.
* Màu chữ.
* Màu nền chữ.
* Xóa định dạng.
* Chỉ số trên.
* Chỉ số dưới.

### Font chữ

* Chọn font chữ.
* Chọn cỡ chữ.
* Heading 1.
* Heading 2.
* Heading 3.
* Heading 4.
* Đoạn văn thông thường.

Hỗ trợ một số font phổ biến:

* Arial.
* Times New Roman.
* Roboto.
* Inter.
* Tahoma.
* Verdana.

### Căn chỉnh

* Căn trái.
* Căn giữa.
* Căn phải.
* Căn đều hai bên.

### Danh sách

* Danh sách dấu đầu dòng.
* Danh sách đánh số.
* Danh sách nhiều cấp.
* Tăng lề.
* Giảm lề.

### Thành phần nội dung

* Đường kẻ ngang.
* Trích dẫn.
* Khối code.
* Code inline.
* Hyperlink.
* Bảng.
* Undo.
* Redo.
* Copy.
* Paste.
* Chọn toàn bộ.
* Xóa nội dung.

### Link

Cho phép:

* Thêm link.
* Chỉnh sửa link.
* Xóa link.
* Mở link trong tab mới.
* Kiểm tra URL hợp lệ.

---

# 5. Chèn ảnh trong nội dung

Admin có thể chèn ảnh trực tiếp tại vị trí con trỏ trong bài viết.

Các hình thức chèn ảnh:

* Chọn ảnh từ máy tính.
* Kéo thả ảnh vào editor.
* Dán ảnh từ clipboard.
* Chọn ảnh đã upload trước đó nếu hệ thống có Media Library.

Ảnh phải được upload lên MinIO.

Không lưu ảnh dưới dạng Base64 trực tiếp trong nội dung HTML hoặc JSON.

Sau khi upload thành công, editor chỉ lưu:

* URL ảnh.
* Object key.
* Bucket.
* Alt text.
* Caption nếu có.
* Kích thước hiển thị.

---

# 6. Upload ảnh lên MinIO

Sử dụng cấu hình MinIO hiện có trong dự án.

Nếu chưa có service upload, hãy tạo module upload riêng.

Các biến môi trường đề xuất:

```env
MINIO_ENDPOINT=
MINIO_PORT=
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
MINIO_BUCKET_GUIDES=
MINIO_USE_SSL=true
MINIO_PUBLIC_URL=
```

Không hard-code access key, secret key, endpoint hoặc bucket trong source code.

## Quy trình upload

1. Frontend chọn ảnh.
2. Backend kiểm tra quyền Admin.
3. Backend kiểm tra định dạng và dung lượng.
4. Backend tạo object key duy nhất.
5. Backend upload file lên MinIO.
6. Backend trả về URL và metadata.
7. Frontend chèn ảnh vào editor.

Object key nên có cấu trúc:

```text
guides/{year}/{month}/{uuid}-{safe-file-name}
```

Ví dụ:

```text
guides/2026/07/550e8400-e29b-41d4-a716-huong-dan-thi-thu.webp
```

## Định dạng ảnh cho phép

* JPG.
* JPEG.
* PNG.
* WEBP.
* GIF nếu cần.

Giới hạn dung lượng hợp lý, ví dụ:

* Tối đa 10 MB mỗi ảnh.

Không chỉ kiểm tra phần mở rộng file. Cần kiểm tra MIME type thực tế.

Tên file phải được xử lý an toàn, tránh path traversal và ký tự nguy hiểm.

---

# 7. Thu phóng và căn chỉnh ảnh

Ảnh được chèn vào editor phải có khả năng thay đổi kích thước.

Yêu cầu:

* Kéo các điểm điều khiển ở góc để phóng to hoặc thu nhỏ.
* Giữ đúng tỷ lệ ảnh khi resize.
* Không cho ảnh vượt quá chiều rộng vùng nội dung.
* Hỗ trợ chiều rộng theo pixel hoặc phần trăm.
* Hỗ trợ căn trái.
* Hỗ trợ căn giữa.
* Hỗ trợ căn phải.
* Có thể hiển thị ảnh toàn chiều rộng.
* Có thể thêm caption.
* Có thể thêm alt text.
* Có thể thay ảnh.
* Có thể xóa ảnh.

Gợi ý các mức kích thước nhanh:

* 25%.
* 50%.
* 75%.
* 100%.
* Full width.

Trên màn hình nhỏ, ảnh phải tự động responsive:

```css
max-width: 100%;
height: auto;
```

Không để ảnh phá vỡ layout của trang hướng dẫn.

---

# 8. Chèn video

Editor cần hỗ trợ chèn video.

Các hình thức:

* Upload video từ máy tính.
* Chèn YouTube URL.
* Chèn video URL hợp lệ.
* Chèn video đã được upload lên MinIO.

Với video upload từ máy tính:

* Upload video lên MinIO.
* Không lưu Base64.
* Lưu URL và metadata.
* Hiển thị bằng HTML5 video player.

Video player cần có:

* Play.
* Pause.
* Thanh tiến trình.
* Âm lượng.
* Fullscreen.
* Responsive theo chiều rộng nội dung.

Định dạng video cho phép:

* MP4.
* WEBM.
* MOV nếu backend có hỗ trợ hoặc chuyển đổi phù hợp.

Cần giới hạn dung lượng file và kiểm tra MIME type.

Nếu file video lớn, ưu tiên upload bằng presigned URL hoặc multipart upload để tránh backend bị quá tải.

---

# 9. Media Library

Nếu phù hợp với cấu trúc dự án, tạo thêm Media Library để Admin quản lý các file đã upload.

Media Library gồm:

* Danh sách ảnh.
* Danh sách video.
* Tìm kiếm theo tên.
* Xem trước.
* Sao chép URL.
* Chèn vào bài viết.
* Xóa file.
* Hiển thị dung lượng.
* Hiển thị ngày upload.

Chỉ cho phép xóa file nếu:

* File không còn được sử dụng trong bất kỳ bài viết nào.

Hoặc hiển thị cảnh báo nếu file đang được sử dụng.

Không tự động xóa ảnh khỏi MinIO khi Admin chỉ xóa ảnh khỏi nội dung editor, vì ảnh có thể đang được dùng ở bài viết khác.

---

# 10. Tự động lưu bản nháp

Editor cần hỗ trợ Auto Save.

Yêu cầu:

* Tự động lưu bản nháp sau một khoảng thời gian hợp lý.
* Có debounce, không gửi API sau mỗi ký tự.
* Ví dụ lưu sau 2 đến 5 giây kể từ lần chỉnh sửa cuối cùng.
* Hiển thị trạng thái:

  * Đang lưu.
  * Đã lưu.
  * Lưu thất bại.
* Nếu mất mạng, không được làm mất nội dung vừa nhập.
* Có thể tạm lưu nội dung trên local storage trước khi đồng bộ lại.

Tránh tạo quá nhiều request tới backend.

---

# 11. Trang xem trước

Admin có thể xem trước bài viết trước khi đăng.

Trang xem trước phải hiển thị gần giống trang mà người dùng sẽ nhìn thấy:

* Tiêu đề.
* Mô tả.
* Ảnh đại diện.
* Nội dung đã định dạng.
* Ảnh đúng kích thước.
* Video.
* Bảng.
* Danh sách.
* Link.
* Code block.
* Responsive.

Bản nháp chưa đăng không được người dùng bình thường truy cập.

---

# 12. Trang Hướng dẫn sử dụng dành cho người dùng

Tạo một trang mới trong ứng dụng với tên:

`Hướng dẫn sử dụng`

Trang này hiển thị các bài viết có trạng thái `PUBLISHED`.

Giao diện gồm:

* Thanh tìm kiếm.
* Danh sách danh mục.
* Danh sách bài hướng dẫn.
* Ảnh đại diện.
* Tiêu đề.
* Mô tả ngắn.
* Ngày cập nhật.
* Nút hoặc card để xem chi tiết.

Có thể hiển thị dạng:

* Danh sách card.
* Sidebar danh mục.
* Khu vực nội dung chi tiết.

Khi người dùng chọn một bài viết:

* Hiển thị nội dung đầy đủ.
* Render đúng định dạng từ editor.
* Ảnh responsive.
* Video responsive.
* Link có thể click.
* Có mục lục tự động nếu bài có nhiều heading.
* Có nút quay lại.
* Có thể điều hướng bài trước và bài tiếp theo.

---

# 13. Mục lục tự động

Tạo mục lục dựa trên các heading trong bài viết:

* H1.
* H2.
* H3.

Mục lục cần:

* Hiển thị ở sidebar hoặc phía đầu bài viết.
* Click vào mục sẽ cuộn tới đúng phần.
* Highlight mục hiện tại khi người dùng cuộn trang.
* Hỗ trợ sticky sidebar trên desktop.
* Ẩn hoặc thu gọn trên mobile.

---

# 14. Tìm kiếm bài hướng dẫn

Người dùng có thể tìm kiếm bài viết theo:

* Tiêu đề.
* Mô tả.
* Từ khóa.
* Nội dung bài viết nếu backend hỗ trợ.

Cần debounce khi tìm kiếm.

Nếu không có kết quả, hiển thị trạng thái rõ ràng:

`Không tìm thấy bài hướng dẫn phù hợp.`

---

# 15. Thiết kế dữ liệu

Hãy tạo schema phù hợp với database hiện tại.

Ví dụ bảng `guides`:

```text
id
title
slug
summary
thumbnail_url
thumbnail_object_key
category_id
content
content_format
status
sort_order
created_by
created_at
updated_at
published_at
```

Bảng `guide_categories`:

```text
id
name
slug
sort_order
created_at
updated_at
```

Bảng `guide_media`:

```text
id
file_name
original_name
object_key
bucket
url
mime_type
media_type
size
width
height
uploaded_by
created_at
```

Có thể lưu nội dung editor dưới dạng JSON hoặc HTML tùy thư viện.

Ưu tiên:

* Lưu JSON gốc của editor để dễ chỉnh sửa.
* Có thể tạo thêm HTML đã sanitize để hiển thị.

Không render trực tiếp HTML chưa được làm sạch.

---

# 16. API Backend

Tạo các API cần thiết.

## Admin API

```text
GET    /api/admin/guides
GET    /api/admin/guides/:id
POST   /api/admin/guides
PUT    /api/admin/guides/:id
DELETE /api/admin/guides/:id

POST   /api/admin/guides/:id/publish
POST   /api/admin/guides/:id/unpublish

POST   /api/admin/guide-media/upload
GET    /api/admin/guide-media
DELETE /api/admin/guide-media/:id
```

## Public hoặc User API

```text
GET /api/guides
GET /api/guides/:slug
GET /api/guide-categories
GET /api/guides/search
```

Các API public chỉ được trả về bài có trạng thái `PUBLISHED`.

---

# 17. Bảo mật

Bắt buộc xử lý các vấn đề bảo mật sau:

* Kiểm tra quyền Admin ở Backend.
* Validate toàn bộ input.
* Sanitize HTML trước khi render.
* Chống XSS.
* Chặn script trong nội dung bài viết.
* Không cho upload file thực thi.
* Kiểm tra MIME type.
* Giới hạn dung lượng upload.
* Không để lộ MinIO secret key ở Frontend.
* Không sử dụng access key trực tiếp trên trình duyệt.
* Chỉ sử dụng presigned URL khi cần.
* Giới hạn loại URL có thể embed.
* Validate YouTube URL và video URL.
* Chặn `javascript:` URL trong hyperlink.
* Xử lý an toàn tên file.
* Kiểm tra file upload không chứa nội dung nguy hiểm.

Nếu bucket MinIO là private, hãy dùng presigned URL hoặc API proxy phù hợp.

---

# 18. Tối ưu cho ứng dụng Tauri

Module này phải hoạt động trên cả Web và Tauri.

Cần kiểm tra:

* Đường dẫn ảnh MinIO.
* HTTPS.
* CORS.
* Content Security Policy của Tauri.
* Quyền mở external URL.
* Video playback trong WebView.
* Link bên ngoài.
* Editor keyboard shortcut.
* Drag and drop file.
* Paste ảnh từ clipboard.

Nếu ảnh hoặc video không hiển thị trong Tauri, cần kiểm tra và cập nhật CSP, ví dụ các nguồn:

* `img-src`.
* `media-src`.
* `connect-src`.
* `frame-src`.

Chỉ thêm đúng domain MinIO và các domain thực sự cần thiết. Không cấu hình CSP quá rộng bằng `*` nếu không cần.

---

# 19. UI/UX

Giao diện phải đồng bộ với ứng dụng hiện tại.

Yêu cầu:

* Toolbar editor rõ ràng.
* Có tooltip cho từng nút.
* Các nhóm chức năng được phân tách hợp lý.
* Toolbar có thể sticky.
* Responsive.
* Không bị tràn ngang.
* Có loading state.
* Có empty state.
* Có error state.
* Có toast khi lưu thành công hoặc thất bại.
* Có dialog xác nhận khi xóa.
* Có skeleton khi tải danh sách.
* Hoạt động tốt trong Tauri window.

Trình soạn thảo nên có vùng nội dung giống một trang tài liệu:

* Chiều rộng nội dung hợp lý.
* Nền trắng.
* Khoảng cách đoạn rõ ràng.
* Có padding.
* Có giới hạn chiều rộng để dễ đọc.

---

# 20. Yêu cầu triển khai

Trước khi code, hãy:

1. Phân tích cấu trúc Frontend và Backend hiện tại.
2. Xác định framework, database, authentication và MinIO service đang sử dụng.
3. Xác định Rich Text Editor phù hợp.
4. Xác định các file cần sửa và file cần tạo.
5. Không tạo lại những service đã tồn tại.
6. Tái sử dụng API client, modal, button, form và layout hiện có.
7. Giữ nguyên coding convention của dự án.

Sau đó triển khai theo thứ tự:

1. Database migration.
2. Backend model và repository.
3. Backend service.
4. Backend API.
5. MinIO upload service.
6. Admin guide management page.
7. Rich Text Editor.
8. Image resize extension.
9. Video support.
10. Public guide page.
11. Tauri compatibility.
12. Validation và security.
13. Test toàn bộ flow.

---

# 21. Tiêu chí hoàn thành

Module chỉ được coi là hoàn thành khi:

* Admin tạo được bài hướng dẫn mới.
* Admin soạn thảo được nội dung có định dạng.
* Có thể đổi font, cỡ chữ và màu chữ.
* Có thể căn trái, giữa, phải và căn đều.
* Có thể tạo danh sách dấu đầu dòng và đánh số.
* Có thể chèn link và bảng.
* Có thể upload và chèn ảnh.
* Ảnh được lưu trên MinIO.
* Ảnh không được lưu Base64.
* Ảnh có thể phóng to, thu nhỏ và căn chỉnh.
* Có thể chèn video.
* Video hoạt động trên Web và Tauri.
* Có thể lưu bản nháp.
* Có thể xem trước.
* Có thể đăng và ẩn bài viết.
* Người dùng chỉ thấy bài đã đăng.
* Người dùng tìm kiếm được bài hướng dẫn.
* Nội dung hiển thị responsive.
* Không xảy ra lỗi XSS.
* Không lộ thông tin đăng nhập MinIO.
* Không làm hỏng chức năng hiện tại.

---

# 22. Kết quả cần trả về

Sau khi hoàn thành, hãy báo cáo:

1. Danh sách file đã tạo.
2. Danh sách file đã sửa.
3. Database migration đã thêm.
4. API đã tạo.
5. Thư viện editor đã sử dụng.
6. Cách cấu hình MinIO.
7. Các biến môi trường cần thêm.
8. Cấu hình Tauri hoặc CSP đã thay đổi.
9. Cách chạy migration.
10. Cách kiểm tra chức năng.
11. Các vấn đề chưa thể hoàn thành nếu có.

Không chỉ mô tả giải pháp. Hãy trực tiếp chỉnh sửa source code và triển khai đầy đủ module theo cấu trúc dự án hiện tại.
