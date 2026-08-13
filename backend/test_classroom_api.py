"""Integration coverage for teacher provisioning and account classrooms."""

from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from sqlalchemy import event, func, select


_database_file = tempfile.NamedTemporaryFile(
    prefix="classroom-test-", suffix=".db", delete=False
)
_database_file.close()
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_database_file.name}"
os.environ["AUTH_REQUIRED"] = "true"
os.environ["ADMIN_EMAIL"] = "admin@classroom.test"
os.environ["ADMIN_PASSWORD"] = "classroom-admin-password"
os.environ["JWT_SECRET"] = "classroom-test-secret-long-enough"
os.environ["TOKEN_EXPORT_SECRET"] = "classroom-token-export-secret-long-enough"
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/15"

from fastapi.testclient import TestClient  # noqa: E402

import auth_service  # noqa: E402
import config  # noqa: E402
import database  # noqa: E402
import models  # noqa: E402

importlib.reload(config)
importlib.reload(database)
importlib.reload(models)
importlib.reload(auth_service)

from auth_service import bootstrap_admin, hash_password, sha256  # noqa: E402
from database import session_scope  # noqa: E402
from main import app  # noqa: E402
from models import Attempt, Device, Exam, ExamVersion, User, utcnow  # noqa: E402
from platform_api import persist_final_exam  # noqa: E402
from classroom_api import (  # noqa: E402
    _filtered_publication_payload,
    _normalized_asset_object_key,
    finalize_expired_class_attempts,
)


class ClassroomApiTests(unittest.TestCase):
    PASSWORD = "classroom-user-password"

    @classmethod
    def setUpClass(cls) -> None:
        database.create_schema()
        bootstrap_admin()

    @classmethod
    def tearDownClass(cls) -> None:
        if database.engine is not None:
            database.engine.dispose()
        Path(_database_file.name).unlink(missing_ok=True)

    def test_publication_payload_only_contains_selected_parts(self) -> None:
        payload = {
            "questions": [
                {"number": number, "correct": "A", "stimulus_id": None}
                for number in range(1, 201)
            ],
            "stimuli": [],
            "audio": {"id": "full", "part": "full"},
            "audios": [
                {"id": "p3", "part": "part_3"},
                {"id": "p5", "part": "part_5"},
                {"id": "full", "part": "full"},
            ],
        }
        filtered = _filtered_publication_payload(payload, [3])
        numbers = [question["number"] for question in filtered["questions"]]
        self.assertEqual(numbers, list(range(32, 71)))
        self.assertEqual(filtered["returned_count"], 39)
        self.assertEqual(
            {audio["part"] for audio in filtered["audios"]},
            {"part_3", "full"},
        )
        reading_only = _filtered_publication_payload(payload, [5])
        self.assertEqual([audio["part"] for audio in reading_only["audios"]], ["part_5"])
        self.assertIsNone(reading_only["audio"])

    def test_shared_bank_is_scoped_to_class_owner_and_keeps_attempt_versions(self) -> None:
        credentials = (
            ("teacher-a@shared.test", "teacher", "teacher-a-shared-device-0001"),
            ("teacher-b@shared.test", "teacher", "teacher-b-shared-device-0001"),
            ("student@shared.test", "student", "student-shared-device-key-0001"),
        )
        with session_scope() as session:
            users: dict[str, User] = {}
            for email, role, device_key in credentials:
                user = User(
                    email=email,
                    display_name=email.split("@", 1)[0],
                    password_hash=hash_password(self.PASSWORD),
                    registered_at=utcnow(),
                    role=role,
                    status="active",
                )
                session.add(user)
                session.flush()
                session.add(
                    Device(
                        user_id=user.id,
                        device_key_hash=sha256(device_key),
                        identity_kind="legacy_browser",
                        name="Shared bank integration test",
                        platform="test",
                    )
                )
                users[role if role == "student" else email] = user
            owner_id = users["teacher-a@shared.test"].id

        payload = {
            "schema_version": 2,
            "exam_type": "reading",
            "questions": [
                {
                    "number": 101,
                    "part": "Part 5",
                    "text": "Choose A",
                    "options": {"A": "A", "B": "B", "C": "C", "D": "D"},
                    "correct": "A",
                }
            ],
            "stimuli": [],
            "audios": [],
            "solutions": [
                {
                    "key": "q-101",
                    "question_numbers": [101],
                    "transcript": None,
                    "explanation": "A đúng theo ngữ pháp.",
                    "translation": "Chọn A.",
                }
            ],
        }
        exam_id = persist_final_exam(
            payload,
            job_id=None,
            owner_user_id=owner_id,
            title="Shared immutable integration exam",
        )
        teacher_b_exam_id = persist_final_exam(
            payload,
            job_id=None,
            owner_user_id=users["teacher-b@shared.test"].id,
            # Equal visible names are valid because each teacher owns a
            # separate catalogue namespace.
            title="Shared immutable integration exam",
        )
        with session_scope() as session:
            exam = session.get(Exam, exam_id)
            self.assertEqual(exam.library_scope, "teacher_shared")
            self.assertIsNotNone(exam.current_version_id)
            self.assertNotEqual(
                exam.shared_title_key,
                session.get(Exam, teacher_b_exam_id).shared_title_key,
            )

        def client_for(email: str, device_key: str) -> TestClient:
            client = TestClient(app)
            client.headers["X-Examify-Device-Key"] = device_key
            login = client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": self.PASSWORD, "device_key": device_key},
            )
            self.assertEqual(login.status_code, 200, login.text)
            return client

        teacher_a = client_for(
            "teacher-a@shared.test", "teacher-a-shared-device-0001"
        )
        teacher_b = client_for(
            "teacher-b@shared.test", "teacher-b-shared-device-0001"
        )
        listing = teacher_b.get("/api/v1/exam-bank?search=immutable")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(
            [item["id"] for item in listing.json()["items"]], [teacher_b_exam_id]
        )
        forbidden_teacher_edit = teacher_b.patch(
            f"/api/v1/exam-bank/{exam_id}",
            json={"base_revision": 1, "title": "Teacher B must not edit"},
        )
        self.assertEqual(forbidden_teacher_edit.status_code, 404, forbidden_teacher_edit.text)

        student = client_for("student@shared.test", "student-shared-device-key-0001")
        before_join = student.get("/api/v1/exam-bank?search=immutable")
        self.assertEqual(before_join.status_code, 200, before_join.text)
        self.assertEqual(before_join.json()["items"], [])
        denied_before_join = student.post(
            f"/api/v1/exam-bank/{exam_id}/attempts",
            json={"launch_mode": "practice", "part_numbers": [5]},
        )
        self.assertEqual(denied_before_join.status_code, 404, denied_before_join.text)

        # Joining Teacher B's class does not leak Teacher A's catalogue.
        class_b = teacher_b.post(
            "/api/v1/teacher/classrooms",
            json={"name": "900+", "description": "Teacher B"},
        )
        self.assertEqual(class_b.status_code, 200, class_b.text)
        joined_b = student.post(
            "/api/v1/student/classrooms/join",
            json={"code": class_b.json()["join_code"]},
        )
        self.assertEqual(joined_b.status_code, 200, joined_b.text)
        only_b = student.get("/api/v1/exam-bank?search=immutable")
        self.assertEqual(only_b.status_code, 200, only_b.text)
        self.assertEqual(
            [item["id"] for item in only_b.json()["items"]], [teacher_b_exam_id]
        )

        # A student only needs one active class owned by Teacher A. It does not
        # matter whether it is A's 500+, 600+, or 800+ class: all of A's bank
        # becomes visible automatically without a per-class publication.
        class_a_codes = []
        for name in ("500+", "600+", "800+"):
            created = teacher_a.post(
                "/api/v1/teacher/classrooms",
                json={"name": name, "description": "Teacher A"},
            )
            self.assertEqual(created.status_code, 200, created.text)
            class_a_codes.append(created.json()["join_code"])
        joined_a = student.post(
            "/api/v1/student/classrooms/join",
            json={"code": class_a_codes[1]},
        )
        self.assertEqual(joined_a.status_code, 200, joined_a.text)
        after_join = student.get("/api/v1/exam-bank?search=immutable")
        self.assertEqual(after_join.status_code, 200, after_join.text)
        self.assertEqual(
            {item["id"] for item in after_join.json()["items"]},
            {exam_id, teacher_b_exam_id},
        )
        started = student.post(
            f"/api/v1/exam-bank/{exam_id}/attempts",
            json={"launch_mode": "practice", "part_numbers": [5]},
        )
        self.assertEqual(started.status_code, 200, started.text)
        self.assertIsNone(started.json()["exam"]["questions"][0]["correct"])
        self.assertNotIn("solutions", started.json()["exam"])
        attempt_id = started.json()["attempt_id"]
        attempt_version_id = started.json()["exam_version_id"]
        with session_scope() as session:
            attempt_version_hash = session.get(
                ExamVersion, attempt_version_id
            ).content_hash

        # A later metadata revision creates a new current version, but this
        # student's open attempt remains pinned to the version it started with.
        second_edit = teacher_a.patch(
            f"/api/v1/exam-bank/{exam_id}",
            json={"base_revision": 1, "title": "Shared exam renamed by Teacher A"},
        )
        self.assertEqual(second_edit.status_code, 200, second_edit.text)
        stale_edit = teacher_a.patch(
            f"/api/v1/exam-bank/{exam_id}",
            json={"base_revision": 1, "title": "Stale overwrite must fail"},
        )
        self.assertEqual(stale_edit.status_code, 409, stale_edit.text)
        forbidden_edit = student.patch(
            f"/api/v1/exam-bank/{exam_id}",
            json={"base_revision": 2, "title": "Student must not edit"},
        )
        self.assertEqual(forbidden_edit.status_code, 403, forbidden_edit.text)
        forbidden_teacher_delete = teacher_b.delete(f"/api/v1/exam-bank/{exam_id}")
        self.assertEqual(
            forbidden_teacher_delete.status_code, 404, forbidden_teacher_delete.text
        )
        submitted = student.post(
            f"/api/v1/attempts/{attempt_id}/submit",
            json={"answers": {"101": "A"}, "time_left_seconds": 1, "client_revision": 1},
        )
        self.assertEqual(submitted.status_code, 200, submitted.text)
        solutions = student.get(f"/api/v1/attempts/{attempt_id}/solutions")
        self.assertEqual(solutions.status_code, 200, solutions.text)
        self.assertEqual(solutions.json()["solutions"][0]["key"], "q-101")
        with session_scope() as session:
            version = session.get(ExamVersion, attempt_version_id)
            attempt = session.get(Attempt, attempt_id)
            exam = session.get(Exam, exam_id)
            self.assertEqual(version.content_hash, attempt_version_hash)
            self.assertEqual(attempt.exam_version_id, attempt_version_id)
            self.assertNotEqual(exam.current_version_id, attempt_version_id)

    def test_teacher_assignment_guest_attempt_and_monitoring(self) -> None:
        admin_email = config.settings.admin_email
        admin_password = os.environ["ADMIN_PASSWORD"]
        with TestClient(app) as admin:
            login = admin.post(
                "/api/v1/auth/login",
                json={
                    "email": admin_email,
                    "password": admin_password,
                    "device_key": "admin-classroom-device-key-0001",
                },
            )
            self.assertEqual(login.status_code, 200, login.text)
            created = admin.post(
                "/api/v1/admin/tokens",
                json={
                    "count": 1,
                    "label": "Teacher One",
                    "assigned_role": "teacher",
                    "exam_limit": 20,
                },
            )
            self.assertEqual(created.status_code, 200, created.text)
            teacher_code = created.json()["codes"][0]
            student_created = admin.post(
                "/api/v1/admin/tokens",
                json={
                    "count": 1,
                    "label": "Student One",
                    "assigned_role": "student",
                },
            )
            self.assertEqual(student_created.status_code, 200, student_created.text)
            student_code = student_created.json()["codes"][0]

        exam_payload = {
            "schema_version": 2,
            "job_id": "classroom-job",
            "exam_type": "reading",
            "requested_count": 2,
            "returned_count": 2,
            "total": 2,
            "questions": [
                {
                    "number": 101,
                    "part": "Part 5",
                    "text": "Classroom question",
                    "options": {"A": "One", "B": "Two", "C": "Three", "D": "Four"},
                    "option_letters": ["A", "B", "C", "D"],
                    "correct": "A",
                    "group_id": None,
                    "stimulus_id": None,
                    "confidence": 100,
                    "issues": [],
                },
                {
                    "number": 147,
                    "part": "Part 7",
                    "text": "Second classroom question",
                    "options": {"A": "One", "B": "Two", "C": "Three", "D": "Four"},
                    "option_letters": ["A", "B", "C", "D"],
                    "correct": "B",
                    "group_id": None,
                    "stimulus_id": None,
                    "confidence": 100,
                    "issues": [],
                },
            ],
            "stimuli": [],
            "audio": None,
            "audios": [],
            "solutions": [
                {
                    "key": "q-101",
                    "question_numbers": [101],
                    "transcript": None,
                    "explanation": "Lời giải classroom vẫn mở sau submit.",
                    "translation": "Classroom solution.",
                }
            ],
        }

        with TestClient(app) as teacher:
            teacher.headers.update(
                {"X-Examify-Device-Key": "teacher-classroom-device-key-0001"}
            )
            activated = teacher.post(
                "/api/v1/activations/redeem",
                json={
                    "code": teacher_code,
                    "device_key": "teacher-classroom-device-key-0001",
                },
            )
            self.assertEqual(activated.status_code, 200, activated.text)
            self.assertEqual(activated.json()["role"], "teacher")
            registered = teacher.post(
                "/api/v1/auth/register",
                json={
                    "display_name": "Teacher One",
                    "email": "teacher@classroom.test",
                    "password": self.PASSWORD,
                    "password_confirmation": self.PASSWORD,
                },
            )
            self.assertEqual(registered.status_code, 200, registered.text)
            login = teacher.post(
                "/api/v1/auth/login",
                json={
                    "email": "teacher@classroom.test",
                    "password": self.PASSWORD,
                    "device_key": "teacher-classroom-device-key-0001",
                },
            )
            self.assertEqual(login.status_code, 200, login.text)
            with teacher.websocket_connect("/api/v1/ws/identity") as websocket:
                websocket.send_json(
                    {"device_key": "teacher-classroom-device-key-0001"}
                )
                identity_event = websocket.receive_json()
                self.assertEqual(identity_event["type"], "identity")
                self.assertEqual(identity_event["role"], "teacher")
            teacher_id = activated.json()["user_id"]
            exam_id = persist_final_exam(
                exam_payload,
                job_id=None,
                owner_user_id=teacher_id,
                title="Teacher Bank Exam",
                category="ETS 2022",
            )
            second_tag_exam_id = persist_final_exam(
                {**exam_payload, "job_id": "classroom-job-second"},
                job_id=None,
                owner_user_id=teacher_id,
                title="Teacher Bank Exam 2",
                category="ETS 2022",
            )
            classroom = teacher.post(
                "/api/v1/teacher/classrooms",
                json={"name": "Lớp TOEIC A", "description": "Test"},
            )
            self.assertEqual(classroom.status_code, 200, classroom.text)
            classroom_id = classroom.json()["id"]
            join_code = classroom.json()["join_code"]
            assignment = teacher.post(
                f"/api/v1/teacher/classrooms/{classroom_id}/assignments",
                json={
                    "exam_id": exam_id,
                    "mode": "exam",
                    "attempt_limit": 2,
                    "score_release": "immediate",
                    "answer_release": "manual",
                },
            )
            self.assertEqual(assignment.status_code, 200, assignment.text)
            assignment_id = assignment.json()["id"]

            with TestClient(app) as student:
                student.headers.update(
                    {"X-Examify-Device-Key": "student-classroom-device-key-0001"}
                )
                student_activation = student.post(
                    "/api/v1/activations/redeem",
                    json={
                        "code": student_code,
                        "device_key": "student-classroom-device-key-0001",
                    },
                )
                self.assertEqual(student_activation.status_code, 200, student_activation.text)
                self.assertEqual(student_activation.json()["role"], "student")
                student_registered = student.post(
                    "/api/v1/auth/register",
                    json={
                        "display_name": "Nguyễn Văn Học",
                        "email": "student@classroom.test",
                        "password": self.PASSWORD,
                        "password_confirmation": self.PASSWORD,
                    },
                )
                self.assertEqual(student_registered.status_code, 200, student_registered.text)
                student_login = student.post(
                    "/api/v1/auth/login",
                    json={
                        "email": "student@classroom.test",
                        "password": self.PASSWORD,
                        "device_key": "student-classroom-device-key-0001",
                    },
                )
                self.assertEqual(student_login.status_code, 200, student_login.text)
                resolved = student.post(
                    "/api/v1/student/classrooms/resolve", json={"code": join_code}
                )
                self.assertEqual(resolved.status_code, 200, resolved.text)
                joined = student.post(
                    "/api/v1/student/classrooms/join",
                    json={"code": join_code},
                )
                self.assertEqual(joined.status_code, 200, joined.text)
                duplicate_join = student.post(
                    "/api/v1/student/classrooms/join", json={"code": join_code}
                )
                self.assertEqual(duplicate_join.status_code, 200, duplicate_join.text)
                started = student.post(
                    f"/api/v1/student/assignments/{assignment_id}/attempts",
                )
                self.assertEqual(started.status_code, 200, started.text)
                self.assertIsNone(started.json()["exam"]["questions"][0]["correct"])
                attempt_id = started.json()["attempt_id"]
                events = student.post(
                    f"/api/v1/student/attempts/{attempt_id}/events",
                    json={
                        "events": [
                            {
                                "client_event_id": "event-1",
                                "event_type": "window_blur",
                            },
                            {
                                "client_event_id": "event-1",
                                "event_type": "window_blur",
                            },
                        ]
                    },
                )
                self.assertEqual(events.status_code, 200, events.text)
                self.assertEqual(events.json()["accepted"], 1)
                saved_answers = student.patch(
                    f"/api/v1/student/attempts/{attempt_id}/answers",
                    json={
                        "answers": {"101": "A"},
                        "time_left_seconds": 11,
                        "client_revision": 1,
                    },
                )
                self.assertEqual(saved_answers.status_code, 200, saved_answers.text)
                self.assertEqual(saved_answers.json()["accepted_revision"], 1)
                submit_statements: list[str] = []

                def capture_submit_sql(
                    _connection, _cursor, statement, _parameters, _context, _many
                ) -> None:
                    submit_statements.append(statement.lower())

                event.listen(
                    database.engine, "before_cursor_execute", capture_submit_sql
                )
                try:
                    submitted = student.post(
                        f"/api/v1/student/attempts/{attempt_id}/submit",
                        json={
                            "answers": {"101": "A"},
                            "time_left_seconds": 10,
                            "client_revision": 2,
                        },
                    )
                finally:
                    event.remove(
                        database.engine,
                        "before_cursor_execute",
                        capture_submit_sql,
                    )
                self.assertEqual(submitted.status_code, 200, submitted.text)
                self.assertLessEqual(len(submit_statements), 7, submit_statements)
                self.assertTrue(submitted.json()["score_released"])
                self.assertFalse(submitted.json()["answers_released"])
                self.assertNotIn("exam", submitted.json())
                self.assertGreater(submitted.json()["scores"]["toeic"], 0)
                self.assertEqual(submitted.json()["accepted_revision"], 2)
                immediate_solution = student.get(
                    f"/api/v1/attempts/{attempt_id}/solutions"
                )
                self.assertEqual(
                    immediate_solution.status_code, 200, immediate_solution.text
                )
                self.assertEqual(
                    immediate_solution.json()["solutions"][0]["key"], "q-101"
                )
                duplicate_submit = student.post(
                    f"/api/v1/student/attempts/{attempt_id}/submit",
                    json={
                        "answers": {"101": "D"},
                        "time_left_seconds": 0,
                        "client_revision": 3,
                    },
                )
                self.assertEqual(
                    duplicate_submit.status_code, 200, duplicate_submit.text
                )
                self.assertEqual(duplicate_submit.json()["answers"]["101"], "A")
                self.assertEqual(duplicate_submit.json()["accepted_revision"], 2)
                released = teacher.post(
                    f"/api/v1/teacher/classrooms/{classroom_id}/assignments/{assignment_id}/release",
                    params={"answers": True},
                )
                self.assertEqual(released.status_code, 200, released.text)
                visible_result = student.get(
                    f"/api/v1/student/attempts/{attempt_id}/result",
                )
                self.assertEqual(visible_result.status_code, 200, visible_result.text)
                self.assertEqual(
                    visible_result.json()["exam"]["questions"][0]["correct"],
                    "A",
                )
                renamed_configuration = teacher.patch(
                    f"/api/v1/teacher/classrooms/{classroom_id}/assignments/{assignment_id}",
                    json={"title": "Bài thi đã đổi tên"},
                )
                self.assertEqual(renamed_configuration.status_code, 200, renamed_configuration.text)
                self.assertEqual(renamed_configuration.json()["title"], "Bài thi đã đổi tên")
                second = student.post(
                    f"/api/v1/student/assignments/{assignment_id}/attempts",
                )
                self.assertEqual(second.status_code, 200, second.text)
                second_id = second.json()["attempt_id"]
                saved = student.patch(
                    f"/api/v1/student/attempts/{second_id}/answers",
                    json={"answers": {"101": "A"}, "time_left_seconds": 5},
                )
                self.assertEqual(saved.status_code, 200, saved.text)
                second_event = student.post(
                    f"/api/v1/student/attempts/{second_id}/events",
                    json={
                        "events": [
                            {
                                "client_event_id": "event-timeout-1",
                                "event_type": "offline",
                            }
                        ]
                    },
                )
                self.assertEqual(second_event.status_code, 200, second_event.text)
                with database.session_scope() as session:
                    attempt = session.get(models.Attempt, second_id)
                    attempt.deadline_at = models.utcnow() - timedelta(seconds=1)
                self.assertEqual(finalize_expired_class_attempts(), 1)
                expired = student.get(
                    f"/api/v1/student/attempts/{second_id}/result",
                )
                self.assertEqual(expired.status_code, 200, expired.text)
                self.assertEqual(expired.json()["status"], "submitted")
                exhausted = student.post(
                    f"/api/v1/student/assignments/{assignment_id}/attempts",
                )
                self.assertEqual(exhausted.status_code, 409, exhausted.text)
                closed = teacher.post(
                    f"/api/v1/teacher/classrooms/{classroom_id}/assignments/{assignment_id}/close"
                )
                self.assertEqual(closed.status_code, 200, closed.text)
                reopened = teacher.post(
                    f"/api/v1/teacher/classrooms/{classroom_id}/assignments/{assignment_id}/reopen",
                    json={"additional_attempts": 1, "closes_at": None},
                )
                self.assertEqual(reopened.status_code, 200, reopened.text)
                self.assertEqual(reopened.json()["attempt_limit"], 3)
                third = student.post(
                    f"/api/v1/student/assignments/{assignment_id}/attempts",
                )
                self.assertEqual(third.status_code, 200, third.text)
                third_id = third.json()["attempt_id"]
                third_submit = student.post(
                    f"/api/v1/student/attempts/{third_id}/submit",
                    json={"answers": {"101": "A"}, "time_left_seconds": 4},
                )
                self.assertEqual(third_submit.status_code, 200, third_submit.text)

            monitoring = teacher.get(
                f"/api/v1/teacher/classrooms/{classroom_id}/monitoring",
                params={"assignment_id": assignment_id},
            )
            self.assertEqual(monitoring.status_code, 200, monitoring.text)
            self.assertEqual(monitoring.json()["summary"]["submitted"], 1)
            self.assertEqual(monitoring.json()["summary"]["total_attempts"], 3)
            self.assertEqual(monitoring.json()["summary"]["completed_attempts"], 3)
            self.assertEqual(len(monitoring.json()["history"]), 3)
            self.assertEqual(
                {item["attempt_number"] for item in monitoring.json()["history"]},
                {1, 2, 3},
            )
            history = teacher.get(
                f"/api/v1/teacher/classrooms/{classroom_id}/members/"
                f"{monitoring.json()['items'][0]['member_id']}/results"
            )
            self.assertEqual(history.status_code, 200, history.text)
            self.assertEqual(len(history.json()["items"]), 3)
            self.assertEqual(
                {item["attempt_number"] for item in history.json()["items"]},
                {1, 2, 3},
            )

            practice = teacher.post(
                f"/api/v1/teacher/exams/{exam_id}/class-publications",
                json={"classroom_ids": [classroom_id]},
            )
            self.assertEqual(practice.status_code, 200, practice.text)
            self.assertEqual(practice.json()["created"], [classroom_id])
            self.assertEqual(practice.json()["kind"], "study_resource")
            duplicate_public = teacher.post(
                f"/api/v1/teacher/exams/{exam_id}/class-publications",
                json={"classroom_ids": [classroom_id]},
            )
            self.assertEqual(duplicate_public.status_code, 200, duplicate_public.text)
            self.assertEqual(duplicate_public.json()["already_published"], [classroom_id])
            tag_public = teacher.post(
                "/api/v1/teacher/exam-tags/class-publications",
                json={"tag": "ETS 2022", "classroom_ids": [classroom_id]},
            )
            self.assertEqual(tag_public.status_code, 200, tag_public.text)
            self.assertEqual(tag_public.json()["exam_count"], 2)
            self.assertEqual(tag_public.json()["created_count"], 1)
            self.assertEqual(tag_public.json()["already_published_count"], 1)
            self.assertIn(
                second_tag_exam_id,
                {item["exam_id"] for item in tag_public.json()["results"]},
            )
            tag_public_again = teacher.post(
                "/api/v1/teacher/exam-tags/class-publications",
                json={"tag": "ETS 2022", "classroom_ids": [classroom_id]},
            )
            self.assertEqual(tag_public_again.status_code, 200, tag_public_again.text)
            self.assertEqual(tag_public_again.json()["created_count"], 0)
            self.assertEqual(tag_public_again.json()["already_published_count"], 2)
            tag_status = teacher.get(
                "/api/v1/teacher/exam-tags/class-publications",
                params={"tag": "ETS 2022"},
            )
            self.assertEqual(tag_status.status_code, 200, tag_status.text)
            self.assertTrue(tag_status.json()["items"][0]["fully_published"])
            publications = teacher.get(
                f"/api/v1/teacher/exams/{exam_id}/class-publications"
            )
            self.assertEqual(publications.status_code, 200, publications.text)
            self.assertEqual(publications.json()["items"][0]["kind"], "study_resource")
            self.assertEqual(publications.json()["items"][0]["available_part_numbers"], [5, 7])
            practice_id = publications.json()["items"][0]["assignment_id"]
            with TestClient(app) as returning_student:
                returning_student.headers.update(
                    {"X-Examify-Device-Key": "student-classroom-device-key-0001"}
                )
                student_login = returning_student.post(
                    "/api/v1/auth/login",
                    json={
                        "email": "student@classroom.test",
                        "password": self.PASSWORD,
                        "device_key": "student-classroom-device-key-0001",
                    },
                )
                self.assertEqual(student_login.status_code, 200, student_login.text)
                configurations = [
                    {
                        "launch_mode": "practice",
                        "part_numbers": [5],
                        "duration_seconds": 30 * 60,
                    },
                    {
                        "launch_mode": "mock_exam",
                        "part_numbers": [5],
                        "duration_seconds": 60,
                    },
                    None,
                ]
                for index, configuration in enumerate(configurations):
                    practice_attempt = returning_student.post(
                        f"/api/v1/student/assignments/{practice_id}/attempts",
                        json=configuration,
                    )
                    self.assertEqual(
                        practice_attempt.status_code, 200, practice_attempt.text
                    )
                    expected_mode = "mock_exam" if index == 1 else "practice"
                    self.assertEqual(practice_attempt.json()["launch_mode"], expected_mode)
                    expected_parts = [5] if index == 0 else [5, 7]
                    self.assertEqual(practice_attempt.json()["selected_part_numbers"], expected_parts)
                    if index == 0:
                        self.assertEqual(practice_attempt.json()["duration_seconds"], 30 * 60)
                        reloaded = returning_student.get(
                            f"/api/v1/student/attempts/{practice_attempt.json()['attempt_id']}"
                        )
                        self.assertEqual(reloaded.status_code, 200, reloaded.text)
                        self.assertEqual(reloaded.json()["launch_mode"], "practice")
                        self.assertEqual(reloaded.json()["selected_part_numbers"], [5])
                        self.assertEqual(len(reloaded.json()["exam"]["questions"]), 1)
                    practice_submit = returning_student.post(
                        f"/api/v1/student/attempts/"
                        f"{practice_attempt.json()['attempt_id']}/submit",
                        json={"answers": {"101": "A"}, "time_left_seconds": 1},
                    )
                    self.assertEqual(
                        practice_submit.status_code, 200, practice_submit.text
                    )
                    self.assertEqual(
                        practice_submit.json()["scores"]["graded"],
                        1 if index == 0 else 2,
                    )
                student_assignments = returning_student.get(
                    f"/api/v1/student/classrooms/{classroom_id}/assignments"
                )
                self.assertEqual(
                    student_assignments.status_code, 200, student_assignments.text
                )
                practice_payload = next(
                    item
                    for item in student_assignments.json()["items"]
                    if item["id"] == practice_id
                )
                self.assertIsNone(practice_payload["attempts_remaining"])
                self.assertEqual(practice_payload["kind"], "study_resource")
                self.assertEqual(practice_payload["tag"], "ETS 2022")
                self.assertEqual(practice_payload["exam"]["category"], "ETS 2022")
                student_history = returning_student.get("/api/v1/student/history")
                self.assertEqual(student_history.status_code, 200, student_history.text)
                self.assertGreaterEqual(len(student_history.json()["items"]), 6)
                self.assertTrue(
                    all(
                        item["has_solutions"]
                        for item in student_history.json()["items"]
                    )
                )
                paged_history = returning_student.get(
                    "/api/v1/student/history",
                    params={"page": 2, "page_size": 2},
                )
                self.assertEqual(paged_history.status_code, 200, paged_history.text)
                self.assertEqual(len(paged_history.json()["items"]), 2)
                self.assertGreaterEqual(paged_history.json()["total"], 6)
                self.assertEqual(paged_history.json()["page"], 2)
            monitoring_after_practice = teacher.get(
                f"/api/v1/teacher/classrooms/{classroom_id}/monitoring",
                params={"assignment_id": assignment_id},
            )
            self.assertEqual(monitoring_after_practice.status_code, 200, monitoring_after_practice.text)
            self.assertEqual(monitoring_after_practice.json()["summary"]["total_attempts"], 3)
            self.assertEqual(len(monitoring_after_practice.json()["history"]), 3)
            official_results_only = teacher.get(
                f"/api/v1/teacher/classrooms/{classroom_id}/members/"
                f"{monitoring.json()['items'][0]['member_id']}/results"
            )
            self.assertEqual(official_results_only.status_code, 200, official_results_only.text)
            self.assertEqual(len(official_results_only.json()["items"]), 3)

            with database.session_scope() as session:
                changed_exam = session.get(models.Exam, exam_id)
                changed_payload = dict(changed_exam.payload)
                changed_payload["questions"] = [
                    *changed_payload["questions"],
                    {
                        "number": 131,
                        "part": "Part 6",
                        "text": "Newly published question",
                        "options": {"A": "One", "B": "Two", "C": "Three", "D": "Four"},
                        "option_letters": ["A", "B", "C", "D"],
                        "correct": "C",
                        "group_id": None,
                        "stimulus_id": None,
                    },
                ]
                changed_payload["requested_count"] = 3
                changed_payload["returned_count"] = 3
                changed_payload["total"] = 3
                changed_exam.payload = changed_payload
                changed_exam.question_count = 3
                changed_exam.answer_key_count = 3
            refreshed_public = teacher.post(
                f"/api/v1/teacher/exams/{exam_id}/class-publications",
                json={"classroom_ids": [classroom_id]},
            )
            self.assertEqual(refreshed_public.status_code, 200, refreshed_public.text)
            self.assertEqual(refreshed_public.json()["created"], [classroom_id])
            self.assertEqual(refreshed_public.json()["question_count"], 3)
            refreshed_list = teacher.get(
                f"/api/v1/teacher/exams/{exam_id}/class-publications"
            )
            self.assertEqual(refreshed_list.json()["items"][0]["question_count"], 3)
            self.assertEqual(
                refreshed_list.json()["items"][0]["available_part_numbers"],
                [5, 6, 7],
            )

        with TestClient(app) as admin:
            login = admin.post(
                "/api/v1/auth/login",
                json={
                    "email": admin_email,
                    "password": admin_password,
                    "device_key": "admin-classroom-device-key-0002",
                },
            )
            self.assertEqual(login.status_code, 200, login.text)
            role_change = admin.patch(
                f"/api/v1/admin/users/{teacher_id}",
                json={"role": "user"},
            )
            self.assertEqual(role_change.status_code, 409, role_change.text)
            deleted_teacher = admin.delete(f"/api/v1/admin/users/{teacher_id}")
            self.assertEqual(deleted_teacher.status_code, 200, deleted_teacher.text)
            self.assertGreaterEqual(deleted_teacher.json()["deleted_exams"], 2)
            self.assertEqual(deleted_teacher.json()["deleted_classrooms"], 1)
            with database.session_scope() as session:
                self.assertIsNone(session.get(models.User, teacher_id))
                self.assertIsNone(session.get(models.Classroom, classroom_id))
                self.assertIsNone(session.get(models.Exam, exam_id))
                self.assertEqual(
                    session.scalar(
                        select(func.count(models.ExamVersion.id)).where(
                            models.ExamVersion.owner_teacher_id == teacher_id
                        )
                    ),
                    0,
                )

    def test_legacy_extraction_urls_are_normalized_to_minio_keys(self) -> None:
        self.assertEqual(
            _normalized_asset_object_key(
                "/api/extractions/job-123/assets/image.webp"
            ),
            "jobs/job-123/assets/image.webp",
        )
        self.assertEqual(
            _normalized_asset_object_key(
                "/api/extractions/job-123/audio/listening.mp3?download=1"
            ),
            "jobs/job-123/audio/listening.mp3",
        )
        self.assertEqual(
            _normalized_asset_object_key("jobs/job-123/assets/image.webp"),
            "jobs/job-123/assets/image.webp",
        )


if __name__ == "__main__":
    unittest.main()
