from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from persistent_job_store import PersistentJobStore


class PersistentJobStoreCleanupTests(unittest.TestCase):
    def test_cleanup_removes_stale_staging_and_old_cache_over_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = PersistentJobStore.__new__(PersistentJobStore)
            store.root = root

            stale_upload = root / ".audio-upload-orphan.mp3"
            stale_upload.write_bytes(b"orphan")
            old_job = root / "11111111-1111-1111-1111-111111111111"
            old_job.mkdir()
            (old_job / "cached.bin").write_bytes(b"x" * 100)
            recent_job = root / "22222222-2222-2222-2222-222222222222"
            recent_job.mkdir()
            (recent_job / "active.bin").write_bytes(b"y" * 100)

            old = time.time() - 4_000
            os.utime(stale_upload, (old, old))
            os.utime(old_job, (old, old))
            with patch.dict(
                os.environ,
                {
                    "TOOL_TAO_DE_STAGING_TTL_SECONDS": "600",
                    "TOOL_TAO_DE_LOCAL_CACHE_MAX_BYTES": "50",
                    "TOOL_TAO_DE_CACHE_EVICT_MIN_AGE_SECONDS": "60",
                },
            ):
                store.cleanup()

            self.assertFalse(stale_upload.exists())
            self.assertFalse(old_job.exists())
            self.assertTrue(recent_job.exists())


if __name__ == "__main__":
    unittest.main()
