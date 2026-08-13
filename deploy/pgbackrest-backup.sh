#!/usr/bin/env bash
set -Eeuo pipefail

backup_type=${1:-}
case "$backup_type" in
  full|diff|incr) ;;
  *)
    echo "Usage: deploy/pgbackrest-backup.sh full|diff|incr" >&2
    exit 2
    ;;
esac

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"
docker compose exec -T -u postgres postgres \
  sh -ec 'PGHOST=127.0.0.1 PGUSER="$POSTGRES_USER" PGPASSWORD="$POSTGRES_PASSWORD" pgbackrest --stanza=examify --type='"$backup_type"' backup'
docker compose exec -T -u postgres postgres \
  sh -ec 'PGHOST=127.0.0.1 PGUSER="$POSTGRES_USER" PGPASSWORD="$POSTGRES_PASSWORD" pgbackrest --stanza=examify check'
