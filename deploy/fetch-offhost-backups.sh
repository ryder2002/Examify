#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${CONFIRM_FETCH_BACKUP:-}" != "YES" ]]; then
  echo "Lệnh này tải backup off-host vào volume local." >&2
  echo "Chạy lại với CONFIRM_FETCH_BACKUP=YES." >&2
  exit 2
fi

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"
docker compose --profile restore run --rm pgbackrest-fetch
docker compose up -d minio
docker compose --profile restore run --rm minio-fetch
