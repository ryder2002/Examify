"""Create the private application buckets before API startup."""

from __future__ import annotations

from object_storage import storage


def main() -> None:
    if storage is None:
        raise RuntimeError("MINIO_ENDPOINT chưa được cấu hình")
    storage.ensure_buckets()
    print("Object storage buckets are ready")


if __name__ == "__main__":
    main()
