#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
archive=${1:-}

if [[ "${CONFIRM_RESTORE:-}" != "YES" ]]; then
  echo "Restore sẽ ghi đè PostgreSQL và MinIO." >&2
  echo "Chạy lại với CONFIRM_RESTORE=YES deploy/restore.sh <backup>" >&2
  exit 2
fi
if [[ -z "$archive" || ! -f "$archive" ]]; then
  echo "Không tìm thấy backup: $archive" >&2
  exit 2
fi

restore_dir=$(mktemp -d)
cleanup() {
  find "$restore_dir" -mindepth 1 -delete 2>/dev/null || true
  rmdir "$restore_dir" 2>/dev/null || true
}
trap cleanup EXIT

plain_archive="$archive"
if [[ "$archive" == *.age ]]; then
  command -v age >/dev/null 2>&1 || {
    echo "Cần cài age để giải mã backup" >&2
    exit 3
  }
  if [[ -z "${AGE_IDENTITY_FILE:-}" || ! -f "$AGE_IDENTITY_FILE" ]]; then
    echo "AGE_IDENTITY_FILE phải trỏ tới identity giải mã" >&2
    exit 3
  fi
  plain_archive="$restore_dir/backup.tar.gz"
  age --decrypt --identity "$AGE_IDENTITY_FILE" \
    --output "$plain_archive" "$archive"
fi

tar -C "$restore_dir" -xzf "$plain_archive"
(
  cd "$restore_dir"
  sha256sum --check SHA256SUMS
)

cd "$repo_root"
docker compose stop api worker maintenance-worker scheduler frontend
docker compose exec -T postgres sh -ec '
  pg_restore \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges
' < "$restore_dir/postgres.dump"

docker compose run --rm --no-deps \
  -v "$restore_dir/minio:/restore:ro" \
  --entrypoint /bin/sh minio -ec '
    mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
    for bucket_path in /restore/*; do
      [ -d "$bucket_path" ] || continue
      bucket=$(basename "$bucket_path")
      mc mb --ignore-existing "local/$bucket"
      mc mirror --overwrite --remove "$bucket_path" "local/$bucket"
    done
  '

docker compose up -d api worker maintenance-worker scheduler frontend nginx
echo "Restore hoàn tất. Hãy chạy smoke test và đối chiếu số attempt/object ngay."
