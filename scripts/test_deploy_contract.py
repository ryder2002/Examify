"""Regression tests for the shared API/OCR-worker image contract."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeployContractTests(unittest.TestCase):
    def test_backend_roles_share_one_named_image(self) -> None:
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("x-backend-image: &backend-image", compose)
        self.assertIn("image: ${BACKEND_IMAGE:-examify-backend:local}", compose)
        for service in (
            "migrate",
            "api",
            "worker",
            "maintenance-worker",
            "scheduler",
        ):
            match = re.search(
                rf"^  {re.escape(service)}:\n(?P<body>.*?)(?=^  [a-z][a-z0-9-]*:\n|\Z)",
                compose,
                flags=re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, service)
            assert match is not None
            self.assertIn("<<: *backend-image", match.group("body"), service)
            self.assertNotIn("build: ./backend", match.group("body"), service)

    def test_image_build_imports_audio_runtime(self) -> None:
        dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            "COPY --chown=examify:examify audio_processing.py toeic_audio_cutter.py ./",
            dockerfile,
        )
        self.assertIn(
            "python -c 'import audio_processing, toeic_audio_cutter, pytesseract;",
            dockerfile,
        )
        self.assertIn("PYTHONPATH=/app", dockerfile)
        self.assertIn("org.opencontainers.image.revision", dockerfile)

        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("exec python -m celery -A celery_app.celery_app worker", compose)

    def test_rebuild_verifies_live_container_image_ids(self) -> None:
        rebuild = (ROOT / "deploy" / "rebuild.sh").read_text(encoding="utf-8")
        self.assertIn('docker image inspect "$BACKEND_IMAGE"', rebuild)
        self.assertIn('actual_image_id=$(docker inspect "$container_id"', rebuild)
        self.assertIn("docker compose exec -T worker python -c", rebuild)


if __name__ == "__main__":
    unittest.main()
