from job_store import PIPELINE_CACHE_VERSION


def test_pipeline_cache_version_fits_persisted_job_column() -> None:
    """A cache-version bump must never make PDF uploads fail before queuing."""

    assert len(PIPELINE_CACHE_VERSION) <= 40
