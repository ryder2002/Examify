from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path

from desktop_store import DesktopStore
from job_store import JobStore


class DesktopStoreTests(unittest.TestCase):
    def test_epoch_change_quarantines_business_data_and_starts_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "data"
            store = DesktopStore(root)
            first_epoch = str(uuid.uuid4())
            second_epoch = str(uuid.uuid4())
            self.assertFalse(store.ensure_data_epoch(first_epoch))
            store.save_exam(
                {"exam_type": "reading", "questions": [], "stimuli": [], "audios": []},
                title="Old epoch exam",
                category="",
                asset_paths={},
            )

            self.assertTrue(store.ensure_data_epoch(second_epoch))
            self.assertEqual(store.data_epoch(), second_epoch)
            self.assertEqual(store.list_exams(), [])
            quarantines = list((root / "quarantine").iterdir())
            self.assertEqual(len(quarantines), 1)
            self.assertTrue((quarantines[0] / "desktop.sqlite3").is_file())
            mode = (quarantines[0] / "desktop.sqlite3").stat().st_mode & 0o777
            if os.name == "nt":
                # Windows exposes a read-only file as read bits for all
                # classes; owner/group/other distinctions are not preserved.
                self.assertEqual(mode & 0o444, 0o444)
                self.assertEqual(mode & 0o222, 0)
            else:
                self.assertEqual(mode, 0o400)

    def test_ocr_job_cache_is_scoped_to_its_desktop_account(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            jobs = JobStore(Path(temporary) / "jobs")
            job_id, _ = jobs.create(
                filename="exam.pdf",
                exam_type="reading",
                file_hash="same-file",
                owner_user_id="user-a",
            )
            state = jobs.read(job_id)
            state["status"] = "ready"
            jobs.write(job_id, state)

            self.assertEqual(jobs.owner_id(job_id), "user-a")
            self.assertIsNone(
                jobs.find_cached(
                    file_hash="same-file",
                    exam_type="reading",
                    owner_user_id="user-b",
                )
            )
            self.assertEqual(
                jobs.find_cached(
                    file_hash="same-file",
                    exam_type="reading",
                    owner_user_id="user-a",
                )["job_id"],
                job_id,
            )

    def test_account_namespaces_do_not_share_local_exams(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            users_root = Path(temporary) / "users"
            first = DesktopStore(users_root / "user-a")
            second = DesktopStore(users_root / "user-b")
            first.save_exam(
                {
                    "exam_type": "reading",
                    "questions": [],
                    "stimuli": [],
                    "audios": [],
                },
                title="Only account A",
                category="",
                asset_paths={},
            )

            self.assertEqual([item["title"] for item in first.list_exams()], ["Only account A"])
            self.assertEqual(second.list_exams(), [])

    def test_exam_and_assets_survive_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset = root / "crop.webp"
            asset.write_bytes(b"RIFF-test-webp")
            source_pdf = root / "input.pdf"
            source_pdf.write_bytes(b"%PDF-source-fixture")
            store = DesktopStore(root / "data")
            client_id = store.save_exam(
                {
                    "exam_type": "reading",
                    "questions": [],
                    "stimuli": [
                        {
                            "assets": [
                                {"id": "crop.webp", "url": "/temporary"}
                            ]
                        }
                    ],
                    "audios": [],
                },
                title="Offline test",
                category="ETS",
                asset_paths={
                    "crop.webp": (asset, "stimulus", "image/webp"),
                    "source-reading.pdf": (
                        source_pdf,
                        "source",
                        "application/pdf",
                    ),
                },
            )
            reopened = DesktopStore(root / "data")
            manifest = reopened.manifest(client_id)
            self.assertEqual(manifest["title"], "Offline test")
            self.assertEqual(manifest["assets"][0]["size"], len(asset.read_bytes()))
            self.assertIn("source", {item["kind"] for item in manifest["assets"]})
            self.assertIn(client_id, reopened.pending())
            self.assertTrue(
                manifest["payload"]["stimuli"][0]["assets"][0]["url"].startswith(
                    "/api/desktop/exams/"
                )
            )

    def test_attempt_history_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "data"
            attempt_id = "attempt-production-smoke"
            DesktopStore(root).save_attempt(
                {
                    "id": attempt_id,
                    "client_exam_id": "exam-production-smoke",
                    "exam_title": "Production smoke",
                    "exam_type": "reading",
                    "score_toeic": 5,
                    "reading_score": 5,
                    "correct_count": 1,
                    "total_questions": 1,
                    "duration_seconds": 60,
                    "time_spent_seconds": 10,
                    "answers": {"101": "A"},
                }
            )
            saved = DesktopStore(root).list_attempts()
            self.assertEqual(saved[0]["id"], attempt_id)
            self.assertEqual(saved[0]["answers"], {"101": "A"})
            self.assertFalse(saved[0]["has_solutions"])
            self.assertTrue(saved[0]["submitted_at"].endswith("+00:00"))

    def test_combines_components_into_one_durable_full_test(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            listening_asset = root / "listening.webp"
            reading_asset = root / "reading.webp"
            listening_asset.write_bytes(b"listening")
            reading_asset.write_bytes(b"reading")
            store = DesktopStore(root / "data")
            listening_id = store.save_exam(
                {
                    "schema_version": 2,
                    "job_id": "listening-job",
                    "exam_type": "listening",
                    "questions": [{"number": number} for number in range(1, 101)],
                    "stimuli": [{"assets": [{"id": "listening.webp", "url": "/old"}]}],
                    "audios": [],
                },
                title="Listening Component",
                category="",
                asset_paths={
                    "listening.webp": (listening_asset, "stimulus", "image/webp")
                },
            )
            reading_id = store.save_exam(
                {
                    "schema_version": 2,
                    "job_id": "reading-job",
                    "exam_type": "reading",
                    "questions": [{"number": number} for number in range(101, 201)],
                    "stimuli": [{"assets": [{"id": "reading.webp", "url": "/old"}]}],
                    "audios": [],
                },
                title="Reading Component",
                category="",
                asset_paths={
                    "reading.webp": (reading_asset, "stimulus", "image/webp")
                },
            )

            store.set_category(reading_id, "ETS 2025")
            with store.connect() as connection:
                connection.execute(
                    "UPDATE local_exams SET title=? WHERE client_exam_id=?",
                    ("ETS 2025 Test 03", reading_id),
                )
            self.assertEqual(store.repair_legacy_split_exams(), 1)

            reopened = DesktopStore(root / "data")
            exams = reopened.list_exams()
            self.assertEqual(len(exams), 1)
            self.assertEqual(exams[0]["exam_type"], "combined")
            self.assertEqual(exams[0]["category"], "ETS 2025")
            combined = exams[0]["payload"]
            self.assertEqual(
                [question["number"] for question in combined["questions"]],
                list(range(1, 201)),
            )
            manifest = reopened.manifest(combined["client_exam_id"])
            self.assertEqual(len(manifest["assets"]), 2)
            self.assertTrue(
                all(
                    combined["client_exam_id"] in asset["url"]
                    for stimulus in combined["stimuli"]
                    for asset in stimulus["assets"]
                )
            )

            jobs = reopened.create_edit_jobs(
                combined["client_exam_id"], JobStore(root / "jobs")
            )
            listening_draft = JobStore(root / "jobs").read(jobs["listening"])
            reading_draft = JobStore(root / "jobs").read(jobs["reading"])
            self.assertEqual(
                [item["number"] for item in listening_draft["questions"]],
                list(range(1, 101)),
            )
            self.assertEqual(
                [item["number"] for item in reading_draft["questions"]],
                list(range(101, 201)),
            )
            self.assertEqual(listening_draft["status"], "review")

    def test_normalizes_category_and_legacy_component_job_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = DesktopStore(Path(temporary))
            client_id = store.save_exam(
                {
                    "schema_version": 2,
                    "job_id": "listening-job+reading-job",
                    "exam_type": "combined",
                    "questions": [],
                    "stimuli": [],
                    "audios": [],
                    "category": "ETS 2024",
                },
                title="Full Test",
                category="",
                asset_paths={},
            )
            self.assertEqual(store.normalize_exams(), 1)
            manifest = store.manifest(client_id)
            self.assertEqual(manifest["category"], "ETS 2024")
            self.assertEqual(
                manifest["payload"]["component_job_ids"],
                {"listening": "listening-job", "reading": "reading-job"},
            )

    def test_cached_classrooms_and_publication_queue_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = DesktopStore(Path(temporary))
            client_id = store.save_exam(
                {"exam_type": "reading", "questions": [], "stimuli": [], "audios": []},
                title="Offline",
                category="",
                asset_paths={},
            )
            store.cache_classrooms([
                {"id": "class-a", "name": "Lớp A", "can_publish": True},
            ])
            store.queue_publications(client_id, ["class-a", "class-a"])
            store.queue_publications(client_id, ["class-a"])
            self.assertEqual(len(store.cached_classrooms()), 1)
            self.assertEqual(len(store.pending_publications(client_id)), 1)
            store.mark_publication(client_id, "class-a")
            self.assertEqual(store.pending_publications(client_id), [])

    def test_exam_summary_exposes_durable_sync_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = DesktopStore(Path(temporary))
            client_id = store.save_exam(
                {"exam_type": "reading", "questions": [], "stimuli": [], "audios": []},
                title="Sync failure",
                category="",
                asset_paths={},
            )
            store.mark_failed(client_id, "Máy chủ từ chối vì hết hạn mức")

            exam = store.list_exams()[0]
            self.assertEqual(exam["sync_status"], "failed")
            self.assertEqual(exam["sync_error"], "Máy chủ từ chối vì hết hạn mức")

    def test_revision_reconcile_never_overwrites_stale_local_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = DesktopStore(Path(temporary))
            clean_id = store.save_exam(
                {"exam_type": "reading", "questions": [], "stimuli": [], "audios": []},
                title="Clean cache",
                category="",
                asset_paths={},
            )
            store.mark_synced(clean_id, "remote-clean", 1)
            self.assertEqual(store.manifest(clean_id)["base_revision"], 1)
            result = store.reconcile(
                [{
                    "client_exam_id": clean_id,
                    "exam_id": "remote-clean",
                    "revision": 2,
                    "deleted": False,
                }]
            )
            self.assertEqual(result["removed"], [clean_id])
            self.assertEqual(store.list_exams(), [])
            self.assertTrue((Path(temporary) / "quarantine" / "reconciled").is_dir())

            dirty_id = store.save_exam(
                {"exam_type": "reading", "questions": [], "stimuli": [], "audios": []},
                title="Dirty cache",
                category="",
                asset_paths={},
            )
            store.mark_synced(dirty_id, "remote-dirty", 3)
            store.set_category(dirty_id, "Local edit")
            equal_result = store.reconcile(
                [{
                    "client_exam_id": dirty_id,
                    "exam_id": "remote-dirty",
                    "revision": 3,
                    "deleted": False,
                }]
            )
            self.assertEqual(equal_result["updated"], [dirty_id])
            self.assertEqual(store.list_exams()[0]["sync_status"], "pending")
            result = store.reconcile(
                [{
                    "client_exam_id": dirty_id,
                    "exam_id": "remote-dirty",
                    "revision": 4,
                    "deleted": False,
                }]
            )
            self.assertEqual(result["conflicts"], [dirty_id])
            self.assertEqual(store.list_exams()[0]["sync_status"], "conflict")
            self.assertNotIn(dirty_id, store.pending())
            store.delete_local_exam(dirty_id)
            self.assertEqual(store.list_exams(), [])

    def test_local_delete_is_recoverable_and_rejects_server_owned_exam(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = DesktopStore(root)
            local_id = store.save_exam(
                {"exam_type": "reading", "questions": [], "stimuli": [], "audios": []},
                title="Local only",
                category="",
                asset_paths={},
            )
            store.delete_local_exam(local_id)
            self.assertEqual(store.list_exams(), [])
            self.assertTrue((root / "quarantine" / "reconciled").is_dir())

            synced_id = store.save_exam(
                {"exam_type": "reading", "questions": [], "stimuli": [], "audios": []},
                title="Server owned",
                category="",
                asset_paths={},
            )
            store.mark_synced(synced_id, "remote-id", 1)
            with self.assertRaisesRegex(ValueError, "đã đồng bộ"):
                store.delete_local_exam(synced_id)

    def test_pending_sync_lease_survives_reload_and_recovers_when_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = DesktopStore(root)
            client_id = store.save_exam(
                {"exam_type": "reading", "questions": [], "stimuli": [], "audios": []},
                title="Leased sync",
                category="",
                asset_paths={},
            )

            self.assertEqual(store.claim_pending(), [client_id])
            self.assertEqual(DesktopStore(root).claim_pending(), [])

            with store.connect() as connection:
                connection.execute(
                    "UPDATE sync_queue SET updated_at=0 WHERE client_exam_id=?",
                    (client_id,),
                )
            self.assertEqual(DesktopStore(root).claim_pending(), [client_id])

            store.mark_failed(client_id, "offline")
            self.assertNotIn(client_id, store.claim_pending())
            with store.connect() as connection:
                connection.execute(
                    "UPDATE sync_queue SET next_attempt_at=0 WHERE client_exam_id=?",
                    (client_id,),
                )
            self.assertEqual(store.claim_pending(), [client_id])
