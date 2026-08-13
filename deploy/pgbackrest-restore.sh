#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${CONFIRM_RESTORE:-}" != "YES" ]]; then
  echo "Restore pgBackRest sẽ ghi đè PostgreSQL hiện tại." >&2
  echo "Chạy lại với CONFIRM_RESTORE=YES và TARGET_TIME tùy chọn." >&2
  exit 2
fi

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

restore_args=(--stanza=examify --delta)
if [[ -n "${TARGET_TIME:-}" ]]; then
  restore_args+=(--type=time --target="$TARGET_TIME" --target-action=promote)
fi

docker compose stop api worker maintenance-worker scheduler frontend nginx postgres
docker compose run --rm --no-deps --entrypoint pgbackrest postgres \
  "${restore_args[@]}" restore
docker compose up -d postgres
docker compose run --rm migrate
docker compose up -d api maintenance-worker scheduler frontend nginx

echo "Restore hoàn tất. OCR worker vẫn đang dừng; kiểm tra dữ liệu trước khi bật lại."
