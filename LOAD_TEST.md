# Load-test archive

Active k6/300-user load-test harnesses were removed on 2026-08-11 at the
owner's request before handover. This file is retained only as an archive marker;
no load test should be run from this repository.

The last controlled runs were recorded in [BENCHMARK.md](BENCHMARK.md). They are
historical measurements, not a current capacity certification. The final
handover state is documented in [HANDOVER_REPORT.md](HANDOVER_REPORT.md).

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
