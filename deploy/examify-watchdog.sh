#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
state_dir=/run/examify-watchdog
mkdir -p "$state_dir"
cd "$repo_root"

# Dependency failures are alerted, not blindly restarted. Docker already
# restarts exited containers. This watchdog only recycles stateless serving
# processes after three consecutive Docker health failures.
for service in api frontend nginx; do
  container_id=$(docker compose ps -q "$service")
  counter_file="$state_dir/$service.failures"
  if [[ -z "$container_id" ]]; then
    health=missing
  else
    health=$(docker inspect --format \
      '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
      "$container_id")
  fi

  if [[ "$health" == healthy || "$health" == running || "$health" == starting ]]; then
    printf '0\n' >"$counter_file"
    continue
  fi

  failures=0
  if [[ -f "$counter_file" ]]; then
    read -r failures <"$counter_file" || failures=0
  fi
  failures=$((failures + 1))
  printf '%s\n' "$failures" >"$counter_file"
  if (( failures >= 3 )); then
    logger -t examify-watchdog "restarting $service after $failures health failures ($health)"
    docker compose restart "$service"
    printf '0\n' >"$counter_file"
  fi
done
