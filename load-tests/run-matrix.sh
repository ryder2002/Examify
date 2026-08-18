#!/usr/bin/env bash
set -euo pipefail

fixture_file="${LOAD_TEST_FIXTURES:-./fixtures.local.json}"
base_url="${BASE_URL:-http://127.0.0.1}"
duration="${DURATION:-2m}"

for users in 50 100 150 200; do
  LOAD_TEST_FIXTURES="$fixture_file" BASE_URL="$base_url" VUS="$users" DURATION="$duration" \
    k6 run --summary-export="result-mixed-${users}.json" student-matrix.js
done

LOAD_TEST_FIXTURES="$fixture_file" BASE_URL="$base_url" VUS=200 \
  k6 run --summary-export=result-autosave-200.json autosave-spike.js
LOAD_TEST_FIXTURES="$fixture_file" BASE_URL="$base_url" VUS=200 \
  k6 run --summary-export=result-submit-200.json peak-submit.js
LOAD_TEST_FIXTURES="$fixture_file" BASE_URL="$base_url" VUS=200 \
  k6 run --summary-export=result-audio-200.json audio-range.js
