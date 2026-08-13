# Windows build and sidecar smoke diagnostics

The Windows release workflow builds a CPU-stable packaged sidecar, runs the smoke test before bundling and repeats it against the installed NSIS layout.

`smoke-sidecar.py` accepts `--request-timeout`, `--job-timeout` and `--diagnostics-dir`. Local transport timeouts/refused connections return a retryable status during health/job polling. A true deadline failure includes the job ID and last state; sidecar stdout, stderr and data/log files are copied to the diagnostics directory.

The CI job sets `OCR_PAGE_WORKERS=1` and `GPU_OCR_ENABLED=false` for deterministic runner behavior. This does not change production desktop defaults. If a native runner still exceeds the bounded job deadline, inspect the uploaded `sidecar.stderr.log` and `data/logs/sidecar.log` before changing OCR worker counts or timeouts.
