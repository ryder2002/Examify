# Module Hướng dẫn sử dụng

## Cấu hình

Module dùng chung kết nối MinIO hiện có (`MINIO_ENDPOINT`,
`MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_SECURE`) và thêm:

```env
MINIO_BUCKET_GUIDES=guide-media
MINIO_PUBLIC_URL=
GUIDE_IMAGE_MAX_BYTES=10485760
GUIDE_VIDEO_MAX_BYTES=209715200
```

Bucket có thể để private. Ảnh và video được phát qua
`GET /api/v1/guide-media/{id}/content`, vì vậy không cần đưa access key hoặc
secret key xuống frontend. Backend tự tạo bucket khi khởi động.

## Migration

```bash
cd backend
alembic upgrade head
```

Migration `0006_guides` tạo các bảng:

- `guide_categories`
- `guides`
- `guide_media`

## Đường dẫn giao diện

- `/guides`: danh sách hướng dẫn đã đăng.
- `/guides/read?slug=...`: đọc chi tiết.
- `/admin/guides`: quản lý bài viết (chỉ Admin).
- `/admin/guides/new`: tạo bài viết.
- `/admin/guides/edit?id=...`: chỉnh sửa.
- `/admin/guides/preview?id=...`: xem trước cả bản nháp.

## API

Public:

- `GET /api/v1/guides`
- `GET /api/v1/guides/search`
- `GET /api/v1/guides/{slug}`
- `GET /api/v1/guide-categories`
- `GET /api/v1/guide-media/{id}/content`

Admin:

- `GET|POST /api/v1/admin/guides`
- `GET|PUT|DELETE /api/v1/admin/guides/{id}`
- `POST /api/v1/admin/guides/{id}/publish`
- `POST /api/v1/admin/guides/{id}/unpublish`
- `POST /api/v1/admin/guide-categories`
- `POST /api/v1/admin/guide-media/upload`
- `GET /api/v1/admin/guide-media`
- `DELETE /api/v1/admin/guide-media/{id}`

## Kiểm tra thủ công

1. Đăng nhập Admin và mở `/admin/guides`.
2. Tạo danh mục, nhập tiêu đề và soạn nội dung.
3. Thử upload/kéo thả/dán ảnh; resize và căn ảnh trong editor.
4. Chèn bảng, link, YouTube và video upload.
5. Chờ trạng thái `Đã lưu`, tải lại trang và kiểm tra bản nháp.
6. Xem trước, đăng bài, rồi mở `/guides` bằng cửa sổ không đăng nhập.
7. Ẩn bài và xác nhận bài không còn xuất hiện ở API/trang public.
8. Chạy kiểm thử:

```bash
cd backend
python3 -m unittest test_platform.py -v

cd ../frontend
npm run lint
npm test
npm run build
npm run build:desktop

cd ../src-tauri
cargo check
```

Tauri CSP cho phép kết nối/ảnh từ API production, video HTTPS hợp lệ và iframe
YouTube/YouTube No-Cookie. Nếu đổi domain API/MinIO, cập nhật đồng thời
`remote_api` trong `src-tauri/src/lib.rs` và các directive `connect-src`,
`img-src`, `media-src` trong `src-tauri/tauri.conf.json`.
