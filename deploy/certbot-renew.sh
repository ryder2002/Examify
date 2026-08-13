#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"
docker compose -f compose.yaml -f compose.tls.yaml --profile tls run --rm \
  certbot renew --webroot --webroot-path /var/www/certbot --quiet
docker compose -f compose.yaml -f compose.tls.yaml exec -T nginx nginx -s reload
