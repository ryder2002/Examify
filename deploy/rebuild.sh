#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

# The documented production command may run through sudo while the checkout is
# owned by the deploy user.  Scope Git's ownership exception to this exact repo
# instead of requiring a global safe.directory mutation on the host.
git_repo=(git -c "safe.directory=$repo_root")
if commit_revision=$("${git_repo[@]}" rev-parse --verify HEAD 2>/dev/null); then
  if [[ -n $("${git_repo[@]}" status --porcelain --untracked-files=all) ]]; then
    APP_REVISION="${commit_revision}-dirty"
  else
    APP_REVISION="$commit_revision"
  fi
else
  # Development exports and copied workspaces may not include .git. Keep the
  # image label useful without making the one-host deployment depend on Git.
  APP_REVISION="${APP_REVISION:-workspace-dirty}"
fi
BACKEND_IMAGE=${BACKEND_IMAGE:-examify-backend:local}
export APP_REVISION BACKEND_IMAGE

echo "Validating compose configuration"
docker compose config --quiet

echo "Building shared backend artifact from revision ${APP_REVISION}"
# All Python roles resolve to BACKEND_IMAGE, so one backend build is enough and
# there is no separate stale worker tag left behind by Compose.
docker compose build --no-cache api frontend postgres

expected_backend_id=$(docker image inspect "$BACKEND_IMAGE" --format '{{.Id}}')
image_revision=$(docker image inspect "$BACKEND_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
if [[ "$image_revision" != "$APP_REVISION" ]]; then
  echo "Backend image revision mismatch: expected $APP_REVISION, got $image_revision" >&2
  exit 1
fi

echo "Verifying backend artifact before deployment"
docker run --rm --entrypoint python "$BACKEND_IMAGE" \
  -c 'import audio_processing, toeic_audio_cutter; print("backend audio modules ok")'

echo "Recreating the application stack"
docker compose up -d --force-recreate --wait --wait-timeout 180 \
  api worker maintenance-worker scheduler frontend nginx

echo "Verifying worker audio runtime modules"
docker compose exec -T worker python -c 'import audio_processing, toeic_audio_cutter; print("worker audio modules ok")'

echo "Verifying all backend containers use image ${expected_backend_id}"
for service in api worker maintenance-worker scheduler; do
  container_id=$(docker compose ps -q "$service")
  if [[ -z "$container_id" ]]; then
    echo "Service $service has no running container" >&2
    exit 1
  fi
  actual_image_id=$(docker inspect "$container_id" --format '{{.Image}}')
  if [[ "$actual_image_id" != "$expected_backend_id" ]]; then
    echo "Service $service uses $actual_image_id, expected $expected_backend_id" >&2
    exit 1
  fi
done

echo "Current service status"
docker compose ps
