#!/usr/bin/env bash
set -euo pipefail

RESET_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESET_COMPOSE=(docker compose -f "$RESET_ROOT/compose.yaml")
RESET_WORKERS=(worker maintenance-worker scheduler)
RESET_MODE="${1:---dry-run}"

if [[ "$RESET_MODE" != "--dry-run" && "$RESET_MODE" != "--execute" ]]; then
  echo "Usage: scripts/reset_system_data.sh [--dry-run|--execute]" >&2
  exit 2
fi

if [[ "$RESET_MODE" == "--execute" ]]; then
  if [[ "${BACKUP_VERIFIED:-}" != "YES" || "${CONFIRM_RESET:-}" != "YES" ]]; then
    echo "Refusing reset: BACKUP_VERIFIED=YES and CONFIRM_RESET=YES are required." >&2
    exit 2
  fi
fi

resume_workers() {
  "${RESET_COMPOSE[@]}" start "${RESET_WORKERS[@]}" >/dev/null
}

if [[ "$RESET_MODE" == "--dry-run" ]]; then
  "${RESET_COMPOSE[@]}" exec -T \
    -e "KEEP_ADMIN_ID=${KEEP_ADMIN_ID:-}" \
    api python - --dry-run < "$RESET_ROOT/scripts/reset_system_data.py"
  exit 0
fi

echo "Pausing background workers for the maintenance window..."
"${RESET_COMPOSE[@]}" stop "${RESET_WORKERS[@]}"
trap resume_workers EXIT

"${RESET_COMPOSE[@]}" exec -T \
  -e "KEEP_ADMIN_ID=${KEEP_ADMIN_ID:-}" \
  -e "BACKUP_VERIFIED=YES" \
  -e "CONFIRM_RESET=YES" \
  api python - < "$RESET_ROOT/scripts/reset_system_data.py"

echo "Clearing Redis/Celery/session/rate-limit state..."
"${RESET_COMPOSE[@]}" exec -T redis sh -ec \
  'redis-cli --no-auth-warning -a "$REDIS_PASSWORD" FLUSHDB'

echo "Restarting API to clear bounded in-process caches..."
"${RESET_COMPOSE[@]}" restart api
resume_workers
trap - EXIT

echo "Reset complete. The next Admin login creates a fresh device session."
