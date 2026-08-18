# Load-test archive

## Server OCR correctness gate (2026-08-14)

New web extraction is server-side again. The browser uploads and polls a bounded
Celery/Tesseract job, so correctness and OCR duration must be measured on the
server worker as well as normal API load.

The direct server pipeline was run against the requested fixtures:

| Fixture | Pages | Questions | Result | Duration |
|---|---:|---:|---|---:|
| `LC5.pdf` | 11 | 100/100; Part 3–4 69/69 | PASS, no issues | 37.1 s |
| `RC5.pdf` | 28 | 100/100; options 100/100 | PASS; Part 6 144–146 and Part 7 171 recovered | 85.3 s |

These are single-worker correctness/duration checks, not a concurrent capacity
claim. The old browser OCR harness remains useful only for compatibility drafts.

| Device profile | LC 13 pages p95 | RC 28 pages p95 | Peak memory | Result |
|---|---:|---:|---:|---|
| Local validation worker (2 page workers) | 37.1 s | 85.3 s | bounded | correctness pass |
| Production host 8 CPU / 16 GiB | pending staging run | pending staging run | pending | not certified |

## Server load-test result template

Run `load-tests/run-matrix.sh` against staging after setting `BASE_URL` and
`AUTH_TOKEN`. Do not fill this table from unit tests or a single developer
laptop; CPU/RAM values must come from the target host.

| Concurrent Users | RPS | p50 | p95 | p99 | Error Rate | CPU | RAM |
| ---------------: | --: | --: | --: | --: | ---------: | --: | --: |
| 50  | pending | pending | pending | pending | pending | pending | pending |
| 100 | pending | pending | pending | pending | pending | pending | pending |
| 150 | pending | pending | pending | pending | pending | pending | pending |
| 200 | pending | pending | pending | pending | pending | pending | pending |

Peak submission (200 users / 1–10 seconds): **pending staging run**. Capacity
must not be announced until error rate is below 0.1%, ordinary API p95 is below
300 ms, and there is no connection exhaustion, deadlock, swap or OOM.

## Monolith smoke verification (2026-08-12)

The one-host stack was started with PostgreSQL, MinIO, Redis, FastAPI/Celery,
Next.js and Nginx. `/health` and `/health/ready` returned 200, the configured
admin login returned 200, and all five MinIO buckets were present. No k6/Locust
capacity run was performed in this setup session, so there are no new p50/p95,
CPU/RAM or 200-user submission numbers to claim.

## Correctness release 2026-08-13

The LC/RC OCR and teacher-scoped bank change was verified with golden OCR,
backend integration and frontend production build tests. Those are correctness
checks, not a load test; no concurrent-user, RPS, CPU or RAM capacity figure is
added for this release.
