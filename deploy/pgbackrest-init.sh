#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

for attempt in $(seq 1 24); do
  if docker compose exec -T -u postgres postgres \
    sh -ec 'PGHOST=127.0.0.1 PGUSER="$POSTGRES_USER" PGPASSWORD="$POSTGRES_PASSWORD" pgbackrest --stanza=examify stanza-create'; then
    docker compose exec -T -u postgres postgres \
      sh -ec 'PGHOST=127.0.0.1 PGUSER="$POSTGRES_USER" PGPASSWORD="$POSTGRES_PASSWORD" pgbackrest --stanza=examify check'
    exit 0
  fi
  echo "pgBackRest chưa sẵn sàng (lần $attempt/24), thử lại sau 5 giây" >&2
  sleep 5
done

echo "Không thể khởi tạo pgBackRest stanza; không được tiếp tục production." >&2
exit 1
