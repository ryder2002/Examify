#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
backup_root=${BACKUP_DIR:-/var/backups/smart-exam}
retention_days=${BACKUP_RETENTION_DAYS:-14}

if [[ -z "$backup_root" || "$backup_root" == "/" || "$backup_root" == "$repo_root" ]]; then
  echo "BACKUP_DIR không an toàn: $backup_root" >&2
  exit 2
fi
if ! [[ "$retention_days" =~ ^[0-9]+$ ]]; then
  echo "BACKUP_RETENTION_DAYS phải là số nguyên không âm" >&2
  exit 2
fi

mkdir -p "$backup_root"
staging_dir=$(mktemp -d "$backup_root/.staging-XXXXXX")
cleanup() {
  find "$staging_dir" -mindepth 1 -delete 2>/dev/null || true
  rmdir "$staging_dir" 2>/dev/null || true
}
trap cleanup EXIT

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
archive="$backup_root/smart-exam-$timestamp.tar.gz"

cd "$repo_root"
docker compose exec -T postgres sh -ec '
  pg_dump \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --format=custom \
    --no-owner \
    --no-privileges
' > "$staging_dir/postgres.dump"

mkdir -p "$staging_dir/minio"
docker compose run --rm --no-deps \
  -v "$staging_dir/minio:/backup" \
  --entrypoint /bin/sh minio -ec '
    mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
    for bucket in examify-sources examify-assets examify-audio examify-answers examify-guides; do
      if mc stat "local/$bucket" >/dev/null 2>&1; then
        mkdir -p "/backup/$bucket"
        mc mirror --overwrite "local/$bucket" "/backup/$bucket"
      fi
    done
  '

(
  cd "$staging_dir"
  find . -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 sha256sum > SHA256SUMS
)
tar -C "$staging_dir" -czf "$archive" .

if [[ -n "${AGE_RECIPIENT:-}" ]]; then
  command -v age >/dev/null 2>&1 || {
    echo "AGE_RECIPIENT đã đặt nhưng lệnh age chưa được cài" >&2
    exit 3
  }
  age --recipient "$AGE_RECIPIENT" --output "$archive.age" "$archive"
  find "$archive" -maxdepth 0 -type f -delete
  archive="$archive.age"
fi

sha256sum "$archive" > "$archive.sha256"
find "$backup_root" -maxdepth 1 -type f \
  \( -name 'smart-exam-*.tar.gz' -o -name 'smart-exam-*.tar.gz.age' -o -name 'smart-exam-*.sha256' \) \
  -mtime "+$retention_days" -delete

echo "Backup hoàn tất: $archive"
