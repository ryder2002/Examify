"""Crash-safe local desktop cache and synchronization queue.

This module deliberately uses only the Python standard library.  It is loaded
only by the desktop profile and never contains remote database or object-store
credentials.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS local_jobs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS local_exams (
  client_exam_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  exam_type TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL,
  manifest_hash TEXT,
  sync_status TEXT NOT NULL DEFAULT 'pending',
  remote_exam_id TEXT,
  remote_revision INTEGER,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS local_assets (
  id TEXT PRIMARY KEY,
  client_exam_id TEXT NOT NULL REFERENCES local_exams(client_exam_id)
    ON DELETE CASCADE,
  kind TEXT NOT NULL,
  local_path TEXT NOT NULL,
  filename TEXT NOT NULL,
  content_type TEXT NOT NULL,
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  uploaded INTEGER NOT NULL DEFAULT 0,
  UNIQUE(client_exam_id, id)
);
CREATE TABLE IF NOT EXISTS sync_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_exam_id TEXT NOT NULL REFERENCES local_exams(client_exam_id)
    ON DELETE CASCADE,
  operation TEXT NOT NULL DEFAULT 'upload_exam',
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  next_attempt_at REAL NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  UNIQUE(client_exam_id, operation)
);
CREATE TABLE IF NOT EXISTS classroom_cache (
  classroom_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  can_publish INTEGER NOT NULL DEFAULT 0,
  cached_at REAL NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS sync_publications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_exam_id TEXT NOT NULL REFERENCES local_exams(client_exam_id)
    ON DELETE CASCADE,
  classroom_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  next_attempt_at REAL NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  UNIQUE(client_exam_id, classroom_id)
);
CREATE TABLE IF NOT EXISTS site_policies (
  key TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS exam_tags (
  id TEXT PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS local_attempts (
  id TEXT PRIMARY KEY,
  client_exam_id TEXT NOT NULL,
  exam_title TEXT NOT NULL,
  exam_type TEXT NOT NULL,
  score_toeic INTEGER NOT NULL,
  listening_score INTEGER NOT NULL,
  reading_score INTEGER NOT NULL,
  correct_count INTEGER NOT NULL,
  total_questions INTEGER NOT NULL,
  duration_seconds INTEGER NOT NULL,
  time_spent_seconds INTEGER NOT NULL,
  mode TEXT NOT NULL,
  submitted_at REAL NOT NULL,
  answers_json TEXT NOT NULL DEFAULT '{}'
);
"""


class DesktopStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database_path = self.root / "desktop.sqlite3"
        self.exam_root = self.root / "exams"
        self.exam_root.mkdir(exist_ok=True)
        self._lock = threading.RLock()
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(local_exams)")
            }
            if "manifest_hash" not in columns:
                connection.execute("ALTER TABLE local_exams ADD COLUMN manifest_hash TEXT")
            if "remote_revision" not in columns:
                connection.execute(
                    "ALTER TABLE local_exams ADD COLUMN remote_revision INTEGER"
                )

    @property
    def epoch_path(self) -> Path:
        return self.root / "data_epoch.txt"

    def data_epoch(self) -> str | None:
        try:
            value = self.epoch_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        return value or None

    def ensure_data_epoch(self, data_epoch: str) -> bool:
        """Quarantine the old business namespace when the server epoch changes."""

        try:
            normalized = str(uuid.UUID(data_epoch))
        except ValueError as exc:
            raise ValueError("data_epoch không hợp lệ") from exc
        previous = self.data_epoch()
        if previous == normalized:
            return False
        if previous is None:
            self.epoch_path.write_text(normalized, encoding="utf-8")
            return False
        quarantine = (
            self.root
            / "quarantine"
            / f"{previous}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        )
        quarantine.mkdir(parents=True, exist_ok=False)
        with self._lock:
            for source in (
                self.database_path,
                self.database_path.with_name(self.database_path.name + "-wal"),
                self.database_path.with_name(self.database_path.name + "-shm"),
                self.exam_root,
            ):
                if source.exists():
                    shutil.move(str(source), str(quarantine / source.name))
            self.exam_root.mkdir(exist_ok=True)
            with self.connect() as connection:
                connection.executescript(SCHEMA)
            self.epoch_path.write_text(normalized, encoding="utf-8")
        for item in sorted(quarantine.rglob("*"), reverse=True):
            try:
                item.chmod(0o500 if item.is_dir() else 0o400)
            except OSError:
                pass
        quarantine.chmod(0o500)
        return True

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def canonical_manifest_hash(manifest: dict[str, Any]) -> str:
        canonical = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def save_exam(
        self,
        payload: dict[str, Any],
        *,
        title: str,
        category: str,
        asset_paths: dict[str, tuple[Path, str, str]],
    ) -> str:
        client_exam_id = str(payload.get("client_exam_id") or uuid.uuid4())
        payload["client_exam_id"] = client_exam_id
        managed_dir = self.exam_root / client_exam_id
        managed_dir.mkdir(parents=True, exist_ok=True)
        managed_assets: dict[str, tuple[Path, str, str]] = {}
        for asset_id, (source, kind, content_type) in asset_paths.items():
            safe_id = Path(asset_id).name
            if not safe_id or safe_id != asset_id:
                raise ValueError("Asset không hợp lệ")
            destination = (managed_dir / safe_id).resolve()
            if source.resolve() != destination:
                shutil.copy2(source, destination)
            managed_assets[asset_id] = (destination, kind, content_type)
        for stimulus in payload.get("stimuli") or []:
            for asset in stimulus.get("assets") or []:
                asset["url"] = (
                    f"/api/desktop/exams/{client_exam_id}/assets/{asset['id']}"
                )
        for audio in payload.get("audios") or []:
            audio["url"] = (
                f"/api/desktop/exams/{client_exam_id}/assets/{audio['id']}"
            )
        if payload.get("audio"):
            payload["audio"]["url"] = (
                f"/api/desktop/exams/{client_exam_id}/assets/"
                f"{payload['audio']['id']}"
            )
        now = time.time()
        with self._lock, self.connect() as connection:
            connection.execute(
                """INSERT INTO local_exams
                   (client_exam_id,title,exam_type,category,payload_json,manifest_hash,
                    sync_status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,'pending',?,?)
                   ON CONFLICT(client_exam_id) DO UPDATE SET
                    title=excluded.title, category=excluded.category,
                    payload_json=excluded.payload_json, manifest_hash=excluded.manifest_hash,
                    sync_status='pending',
                    updated_at=excluded.updated_at""",
                (
                    client_exam_id,
                    title,
                    str(payload.get("exam_type", "reading")),
                    category,
                    json.dumps(payload, ensure_ascii=False),
                    self.canonical_manifest_hash(
                        {
                            "client_exam_id": client_exam_id,
                            "title": title,
                            "category": category,
                            "payload": payload,
                            "assets": [
                                {
                                    "asset_id": asset_id,
                                    "kind": kind,
                                    "filename": path.name,
                                    "content_type": content_type,
                                    "size": path.stat().st_size,
                                    "sha256": self._sha256(path),
                                }
                                for asset_id, (path, kind, content_type) in sorted(
                                    managed_assets.items()
                                )
                            ],
                        }
                    ),
                    now,
                    now,
                ),
            )
            connection.execute(
                "DELETE FROM local_assets WHERE client_exam_id=?",
                (client_exam_id,),
            )
            for asset_id, (path, kind, content_type) in managed_assets.items():
                connection.execute(
                    """INSERT INTO local_assets
                       (id,client_exam_id,kind,local_path,filename,content_type,
                        size,sha256,uploaded)
                       VALUES (?,?,?,?,?,?,?,?,0)""",
                    (
                        asset_id,
                        client_exam_id,
                        kind,
                        str(path.resolve()),
                        path.name,
                        content_type,
                        path.stat().st_size,
                        self._sha256(path),
                    ),
                )
            connection.execute(
                """INSERT INTO sync_queue
                   (client_exam_id,operation,status,created_at,updated_at)
                   VALUES (?,'upload_exam','pending',?,?)
                   ON CONFLICT(client_exam_id,operation) DO UPDATE SET
                    status='pending', last_error=NULL,
                    next_attempt_at=0, updated_at=excluded.updated_at""",
                (client_exam_id, now, now),
            )
        return client_exam_id

    def combine_exams(
        self,
        listening_exam_id: str,
        reading_exam_id: str,
        *,
        title: str,
        category: str,
        target_exam_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically replace two local components with one durable Full Test."""
        if listening_exam_id == reading_exam_id:
            raise ValueError("Hai đề thành phần phải khác nhau")
        now = time.time()
        combined_id = target_exam_id or str(uuid.uuid4())
        with self._lock, self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM local_exams
                   WHERE client_exam_id IN (?, ?)""",
                (listening_exam_id, reading_exam_id),
            ).fetchall()
            by_id = {row["client_exam_id"]: row for row in rows}
            if listening_exam_id not in by_id or reading_exam_id not in by_id:
                raise KeyError("Không tìm thấy đề Listening hoặc Reading")
            listening = json.loads(by_id[listening_exam_id]["payload_json"])
            reading = json.loads(by_id[reading_exam_id]["payload_json"])
            if listening.get("exam_type") != "listening":
                raise ValueError("Đề thứ nhất không phải Listening")
            if reading.get("exam_type") != "reading":
                raise ValueError("Đề thứ hai không phải Reading")

            listening_numbers = {
                int(item.get("number", 0)) for item in listening.get("questions") or []
            }
            reading_numbers = {
                int(item.get("number", 0)) for item in reading.get("questions") or []
            }
            if listening_numbers != set(range(1, 101)):
                raise ValueError("Listening phải có đủ câu 1-100")
            if reading_numbers != set(range(101, 201)):
                raise ValueError("Reading phải có đủ câu 101-200")

            questions = sorted(
                (listening.get("questions") or []) + (reading.get("questions") or []),
                key=lambda item: int(item["number"]),
            )
            stimuli = (listening.get("stimuli") or []) + (reading.get("stimuli") or [])
            audios = listening.get("audios") or (
                [listening["audio"]] if listening.get("audio") else []
            )
            payload: dict[str, Any] = {
                "schema_version": 2,
                "job_id": f"{listening.get('job_id', '')}+{reading.get('job_id', '')}",
                "exam_type": "combined",
                "requested_count": len(questions),
                "returned_count": len(questions),
                "total": len(questions),
                "questions": questions,
                "stimuli": stimuli,
                "audio": None,
                "audios": audios,
                "solutions": (listening.get("solutions") or [])
                + (reading.get("solutions") or []),
                "title": title,
                "category": category,
                "client_exam_id": combined_id,
                "sync_status": "pending",
                "component_job_ids": {
                    "listening": str(listening.get("job_id", "")),
                    "reading": str(reading.get("job_id", "")),
                },
            }
            for stimulus in stimuli:
                for asset in stimulus.get("assets") or []:
                    asset["url"] = (
                        f"/api/desktop/exams/{combined_id}/assets/{asset['id']}"
                    )
            for audio in audios:
                audio["url"] = (
                    f"/api/desktop/exams/{combined_id}/assets/{audio['id']}"
                )
            payload["audio"] = next(
                (audio for audio in audios if audio.get("part") == "full"),
                None,
            )

            combined_dir = self.exam_root / combined_id
            combined_dir.mkdir(parents=True, exist_ok=True)
            component_assets = connection.execute(
                """SELECT id,local_path FROM local_assets
                   WHERE client_exam_id IN (?, ?)""",
                (listening_exam_id, reading_exam_id),
            ).fetchall()
            for asset_row in component_assets:
                source = Path(asset_row["local_path"]).resolve()
                destination = (combined_dir / asset_row["id"]).resolve()
                if source != destination:
                    shutil.copy2(source, destination)
                connection.execute(
                    "UPDATE local_assets SET local_path=? WHERE id=?",
                    (str(destination), asset_row["id"]),
                )

            existing_target = connection.execute(
                "SELECT created_at FROM local_exams WHERE client_exam_id=?",
                (combined_id,),
            ).fetchone()
            connection.execute(
                """INSERT INTO local_exams
                   (client_exam_id,title,exam_type,category,payload_json,
                    sync_status,created_at,updated_at)
                   VALUES (?,?,?,?,?,'pending',?,?)
                   ON CONFLICT(client_exam_id) DO UPDATE SET
                    title=excluded.title, exam_type='combined',
                    category=excluded.category, payload_json=excluded.payload_json,
                    sync_status='pending', updated_at=excluded.updated_at""",
                (
                    combined_id,
                    title,
                    "combined",
                    category,
                    json.dumps(payload, ensure_ascii=False),
                    float(existing_target["created_at"]) if existing_target else min(
                        float(by_id[listening_exam_id]["created_at"]),
                        float(by_id[reading_exam_id]["created_at"]),
                    ),
                    now,
                ),
            )
            connection.execute(
                """UPDATE local_assets SET client_exam_id=?
                   WHERE client_exam_id IN (?, ?)""",
                (combined_id, listening_exam_id, reading_exam_id),
            )
            connection.execute(
                """UPDATE local_attempts SET client_exam_id=?
                   WHERE client_exam_id IN (?, ?)""",
                (combined_id, listening_exam_id, reading_exam_id),
            )
            connection.execute(
                """DELETE FROM sync_queue
                   WHERE client_exam_id IN (?, ?)""",
                (listening_exam_id, reading_exam_id),
            )
            connection.execute(
                """DELETE FROM local_exams
                   WHERE client_exam_id IN (?, ?)""",
                (listening_exam_id, reading_exam_id),
            )
            connection.execute(
                """INSERT INTO sync_queue
                   (client_exam_id,operation,status,created_at,updated_at)
                   VALUES (?,'upload_exam','pending',?,?)""",
                (combined_id, now, now),
            )
        return payload

    def normalize_exams(self) -> int:
        """Backfill category and component metadata without guessing from titles."""
        changed = 0
        with self._lock, self.connect() as connection:
            rows = connection.execute(
                "SELECT client_exam_id,category,payload_json FROM local_exams"
            ).fetchall()
            for row in rows:
                payload = json.loads(row["payload_json"])
                category = str(row["category"] or payload.get("category") or "").strip()
                dirty = category != str(row["category"] or "")
                if payload.get("category") != category:
                    payload["category"] = category
                    dirty = True
                if payload.get("exam_type") == "combined" and not payload.get(
                    "component_job_ids"
                ):
                    parts = str(payload.get("job_id") or "").split("+", 1)
                    if len(parts) == 2 and all(parts):
                        payload["component_job_ids"] = {
                            "listening": parts[0],
                            "reading": parts[1],
                        }
                        dirty = True
                if dirty:
                    connection.execute(
                        """UPDATE local_exams SET category=?,payload_json=?
                           WHERE client_exam_id=?""",
                        (
                            category,
                            json.dumps(payload, ensure_ascii=False),
                            row["client_exam_id"],
                        ),
                    )
                    changed += 1
        return changed

    def create_edit_jobs(
        self,
        client_exam_id: str,
        job_store: Any,
        *,
        owner_user_id: str | None = None,
    ) -> dict[str, str]:
        """Create durable editable drafts from a Full Test, including legacy payloads."""
        manifest = self.manifest(client_exam_id)
        payload = manifest["payload"]
        if payload.get("exam_type") != "combined":
            raise ValueError("Đề này không phải Full Test")
        questions = payload.get("questions") or []
        if {int(item.get("number", 0)) for item in questions} != set(range(1, 201)):
            raise ValueError("Full Test phải có đủ 200 câu")

        jobs: dict[str, str] = {}
        assets_by_id = {
            item["asset_id"]: self.asset_path(client_exam_id, item["asset_id"])
            for item in manifest["assets"]
        }
        for exam_type, start, end in (
            ("listening", 1, 100),
            ("reading", 101, 200),
        ):
            job_id, job_dir = job_store.create(
                filename=f"{manifest['title']} - {exam_type}.pdf",
                exam_type=exam_type,
                file_hash=f"edit:{client_exam_id}:{exam_type}:{uuid.uuid4()}",
                owner_user_id=owner_user_id,
            )
            selected_questions = [
                item for item in questions if start <= int(item.get("number", 0)) <= end
            ]
            selected_numbers = {int(item["number"]) for item in selected_questions}
            selected_solutions = [
                item
                for item in payload.get("solutions") or []
                if set(int(number) for number in item.get("question_numbers") or []).issubset(
                    selected_numbers
                )
            ]
            selected_stimuli = []
            used_assets: set[str] = set()
            for stimulus in payload.get("stimuli") or []:
                numbers = {
                    int(number) for number in stimulus.get("question_numbers") or []
                }
                if numbers & selected_numbers:
                    selected_stimuli.append(stimulus)
                    used_assets.update(
                        str(asset["id"]) for asset in stimulus.get("assets") or []
                    )
            audios = (payload.get("audios") or []) if exam_type == "listening" else []
            if exam_type == "listening":
                used_assets.update(str(audio["id"]) for audio in audios)
            for asset_id in used_assets:
                source = assets_by_id.get(asset_id)
                if source is None:
                    continue
                destination_dir = job_dir / (
                    "audio"
                    if any(str(audio.get("id")) == asset_id for audio in audios)
                    else "assets"
                )
                shutil.copy2(source, destination_dir / asset_id)
            state = job_store.read(job_id)
            state.update(
                {
                    "status": "review",
                    "stage": "Đang chỉnh sửa Full Test",
                    "progress": 100,
                    "questions": selected_questions,
                    "stimuli": selected_stimuli,
                    "returned_count": len(selected_questions),
                    "requested_count": len(selected_questions),
                    "solutions": selected_solutions,
                    "audio": next(
                        (audio for audio in audios if audio.get("part") == "full"), None
                    ),
                    "audios": audios,
                }
            )
            state["metadata"]["editing_client_exam_id"] = client_exam_id
            job_store.write(job_id, state)
            jobs[exam_type] = job_id
        return jobs

    def repair_legacy_split_exams(self) -> int:
        """Merge old 100+100 desktop records created by the former workflow."""
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT client_exam_id,title,exam_type,category,payload_json,
                          created_at
                   FROM local_exams
                   WHERE exam_type IN ('listening','reading')
                   ORDER BY created_at ASC"""
            ).fetchall()
        listening_rows = []
        reading_rows = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            numbers = {
                int(question.get("number", 0))
                for question in payload.get("questions") or []
            }
            item = (row, numbers)
            if (
                row["exam_type"] == "listening"
                and row["title"] == "Listening Component"
                and numbers == set(range(1, 101))
            ):
                listening_rows.append(item)
            elif (
                row["exam_type"] == "reading"
                and numbers == set(range(101, 201))
            ):
                reading_rows.append(item)

        repaired = 0
        used_reading: set[str] = set()
        for listening_row, _ in listening_rows:
            candidates = [
                reading_row
                for reading_row, _ in reading_rows
                if reading_row["client_exam_id"] not in used_reading
                and float(reading_row["created_at"]) >= float(listening_row["created_at"])
                and float(reading_row["created_at"]) - float(listening_row["created_at"])
                <= 24 * 60 * 60
            ]
            if not candidates:
                continue
            reading_row = min(
                candidates,
                key=lambda row: float(row["created_at"]) - float(listening_row["created_at"]),
            )
            self.combine_exams(
                listening_row["client_exam_id"],
                reading_row["client_exam_id"],
                title=reading_row["title"],
                category=reading_row["category"],
            )
            used_reading.add(reading_row["client_exam_id"])
            repaired += 1
        return repaired

    def list_exams(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT client_exam_id,title,exam_type,category,sync_status,
                          remote_exam_id,remote_revision,payload_json,
                          created_at,updated_at,
                          (SELECT last_error FROM sync_queue
                           WHERE sync_queue.client_exam_id=local_exams.client_exam_id
                             AND sync_queue.operation='upload_exam'
                           LIMIT 1) AS sync_error,
                          (SELECT COUNT(*) FROM local_attempts
                           WHERE local_attempts.client_exam_id=local_exams.client_exam_id)
                            AS attempt_count,
                          (SELECT MAX(submitted_at) FROM local_attempts
                           WHERE local_attempts.client_exam_id=local_exams.client_exam_id)
                            AS last_attempt_at
                   FROM local_exams ORDER BY created_at DESC"""
            ).fetchall()
        return [
            {
                **dict(row),
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def manifest(self, client_exam_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            exam = connection.execute(
                "SELECT * FROM local_exams WHERE client_exam_id=?",
                (client_exam_id,),
            ).fetchone()
            assets = connection.execute(
                "SELECT * FROM local_assets WHERE client_exam_id=? ORDER BY id",
                (client_exam_id,),
            ).fetchall()
        if exam is None:
            raise KeyError(client_exam_id)
        return {
            "data_epoch": self.data_epoch() or "",
            "client_exam_id": client_exam_id,
            "title": exam["title"],
            "category": exam["category"],
            "base_revision": exam["remote_revision"],
            "payload": json.loads(exam["payload_json"]),
            "assets": [
                {
                    "asset_id": row["id"],
                    "kind": row["kind"],
                    "filename": row["filename"],
                    "content_type": row["content_type"],
                    "size": row["size"],
                    "sha256": row["sha256"],
                }
                for row in assets
            ],
        }

    def cache_classrooms(self, classrooms: list[dict[str, Any]]) -> None:
        now = time.time()
        with self._lock, self.connect() as connection:
            for classroom in classrooms:
                classroom_id = str(classroom.get("id") or classroom.get("classroom_id") or "").strip()
                if not classroom_id:
                    continue
                connection.execute(
                    """INSERT INTO classroom_cache
                       (classroom_id,name,can_publish,cached_at,payload_json)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(classroom_id) DO UPDATE SET
                         name=excluded.name, can_publish=excluded.can_publish,
                         cached_at=excluded.cached_at, payload_json=excluded.payload_json""",
                    (
                        classroom_id,
                        str(classroom.get("name") or classroom.get("title") or classroom_id),
                        1 if classroom.get("can_publish", True) else 0,
                        now,
                        json.dumps(classroom, ensure_ascii=False),
                    ),
                )

    def cached_classrooms(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM classroom_cache ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [
            {
                "id": row["classroom_id"],
                "name": row["name"],
                "can_publish": bool(row["can_publish"]),
                "cached_at": row["cached_at"],
                **json.loads(row["payload_json"] or "{}"),
            }
            for row in rows
        ]

    def queue_publications(self, client_exam_id: str, classroom_ids: list[str]) -> None:
        now = time.time()
        with self._lock, self.connect() as connection:
            for classroom_id in dict.fromkeys(str(item).strip() for item in classroom_ids):
                if not classroom_id:
                    continue
                connection.execute(
                    """INSERT INTO sync_publications
                       (client_exam_id,classroom_id,status,created_at,updated_at)
                       VALUES (?,?,'pending',?,?)
                       ON CONFLICT(client_exam_id,classroom_id) DO UPDATE SET
                         status=CASE WHEN sync_publications.status='synced'
                                     THEN sync_publications.status ELSE 'pending' END,
                         last_error=NULL, next_attempt_at=0, updated_at=excluded.updated_at""",
                    (client_exam_id, classroom_id, now, now),
                )

    def pending_publications(self, client_exam_id: str | None = None) -> list[dict[str, Any]]:
        now = time.time()
        query = """SELECT * FROM sync_publications
                   WHERE status IN ('pending','failed') AND next_attempt_at<=?"""
        values: list[Any] = [now]
        if client_exam_id:
            query += " AND client_exam_id=?"
            values.append(client_exam_id)
        query += " ORDER BY created_at"
        with self.connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [dict(row) for row in rows]

    def publication_statuses(self, client_exam_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM sync_publications
                   WHERE client_exam_id=? ORDER BY classroom_id""",
                (client_exam_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_publication(self, client_exam_id: str, classroom_id: str, *, error: str | None = None) -> None:
        now = time.time()
        with self._lock, self.connect() as connection:
            row = connection.execute(
                "SELECT attempts FROM sync_publications WHERE client_exam_id=? AND classroom_id=?",
                (client_exam_id, classroom_id),
            ).fetchone()
            if row is None:
                return
            if error is None:
                connection.execute(
                    """UPDATE sync_publications SET status='synced',last_error=NULL,
                       next_attempt_at=0,updated_at=?
                       WHERE client_exam_id=? AND classroom_id=?""",
                    (now, client_exam_id, classroom_id),
                )
                return
            attempts = int(row["attempts"]) + 1
            delay = min(300, 2 ** min(attempts, 8))
            connection.execute(
                """UPDATE sync_publications SET status='failed',attempts=?,last_error=?,
                   next_attempt_at=?,updated_at=?
                   WHERE client_exam_id=? AND classroom_id=?""",
                (attempts, error[:1000], now + delay, now, client_exam_id, classroom_id),
            )

    def pending(self) -> list[str]:
        now = time.time()
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT client_exam_id FROM sync_queue
                   WHERE status IN ('pending','failed') AND next_attempt_at<=?
                   UNION
                   SELECT client_exam_id FROM sync_publications
                   WHERE status IN ('pending','failed') AND next_attempt_at<=?
                   ORDER BY client_exam_id""",
                (now, now),
            ).fetchall()
        return [row["client_exam_id"] for row in rows]

    def claim_pending(self, *, limit: int = 20, lease_seconds: int = 300) -> list[str]:
        """Atomically lease due sync work to one desktop coordinator.

        A WebView reload can interrupt a pass after reading the queue.  The
        bounded lease prevents another pass from racing immediately while also
        making abandoned work eligible again without user intervention.
        """
        now = time.time()
        stale_before = now - max(30, lease_seconds)
        bounded_limit = max(1, min(limit, 100))
        with self._lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT client_exam_id FROM sync_queue
                   WHERE (
                     status IN ('pending','failed') AND next_attempt_at<=?
                   ) OR (status='syncing' AND updated_at<=?)
                   UNION
                   SELECT client_exam_id FROM sync_publications
                   WHERE status IN ('pending','failed') AND next_attempt_at<=?
                   ORDER BY client_exam_id
                   LIMIT ?""",
                (now, stale_before, now, bounded_limit),
            ).fetchall()
            client_ids = [str(row["client_exam_id"]) for row in rows]
            for client_exam_id in client_ids:
                connection.execute(
                    """UPDATE sync_queue SET status='syncing',updated_at=?
                       WHERE client_exam_id=?""",
                    (now, client_exam_id),
                )
        return client_ids

    def asset_path(self, client_exam_id: str, asset_id: str) -> Path:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT local_path FROM local_assets
                   WHERE client_exam_id=? AND id=?""",
                (client_exam_id, asset_id),
            ).fetchone()
        if row is None:
            raise KeyError(asset_id)
        path = Path(row["local_path"]).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def mark_synced(
        self, client_exam_id: str, remote_exam_id: str, remote_revision: int
    ) -> None:
        now = time.time()
        with self._lock, self.connect() as connection:
            connection.execute(
                """UPDATE local_exams SET sync_status='synced',
                   remote_exam_id=?,remote_revision=?,updated_at=?
                   WHERE client_exam_id=?""",
                (remote_exam_id, max(1, int(remote_revision)), now, client_exam_id),
            )
            connection.execute(
                """UPDATE sync_queue SET status='done',last_error=NULL,
                   updated_at=? WHERE client_exam_id=?""",
                (now, client_exam_id),
            )

    def mark_conflict(self, client_exam_id: str, error: str) -> None:
        """Stop automatic retries until the owner resolves a server edit conflict."""
        now = time.time()
        detail = error[:1000]
        with self._lock, self.connect() as connection:
            connection.execute(
                """UPDATE local_exams SET sync_status='conflict',updated_at=?
                   WHERE client_exam_id=?""",
                (now, client_exam_id),
            )
            connection.execute(
                """UPDATE sync_queue SET status='blocked',last_error=?,updated_at=?
                   WHERE client_exam_id=?""",
                (detail, now, client_exam_id),
            )

    def _quarantine_exam_directory(self, client_exam_id: str, reason: str) -> None:
        source = (self.exam_root / client_exam_id).resolve()
        if source.parent != self.exam_root.resolve() or not source.exists():
            return
        quarantine = self.root / "quarantine" / "reconciled"
        quarantine.mkdir(parents=True, exist_ok=True)
        destination = quarantine / (
            f"{client_exam_id}-{reason}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        )
        shutil.move(str(source), str(destination))
        for item in sorted(destination.rglob("*"), reverse=True):
            try:
                item.chmod(0o500 if item.is_dir() else 0o400)
            except OSError:
                pass
        destination.chmod(0o500)

    def reconcile(self, records: list[dict[str, Any]]) -> dict[str, list[str]]:
        """Apply server revisions without overwriting unsynced local work."""
        removed: list[str] = []
        conflicts: list[str] = []
        updated: list[str] = []
        with self._lock:
            for record in records[:1000]:
                client_exam_id = str(record.get("client_exam_id") or "")
                if not client_exam_id:
                    continue
                with self.connect() as connection:
                    row = connection.execute(
                        """SELECT sync_status,remote_exam_id,remote_revision
                           FROM local_exams WHERE client_exam_id=?""",
                        (client_exam_id,),
                    ).fetchone()
                if row is None:
                    continue
                deleted = bool(record.get("deleted"))
                server_revision = max(1, int(record.get("revision") or 1))
                local_revision = int(row["remote_revision"] or 0)
                local_dirty = row["sync_status"] in {"pending", "failed", "conflict"}
                stale = local_revision != server_revision
                if deleted or stale:
                    if local_dirty:
                        self.mark_conflict(
                            client_exam_id,
                            "Đề đã bị xóa trên web."
                            if deleted
                            else "Đề đã được chỉnh sửa trên web; cần chọn phiên bản giữ lại.",
                        )
                        conflicts.append(client_exam_id)
                        continue
                    self._quarantine_exam_directory(
                        client_exam_id, "deleted" if deleted else "stale"
                    )
                    with self.connect() as connection:
                        connection.execute(
                            "DELETE FROM local_exams WHERE client_exam_id=?",
                            (client_exam_id,),
                        )
                    removed.append(client_exam_id)
                    continue
                remote_exam_id = str(record.get("exam_id") or row["remote_exam_id"] or "")
                with self.connect() as connection:
                    if local_dirty:
                        connection.execute(
                            """UPDATE local_exams SET remote_exam_id=?,remote_revision=?
                               WHERE client_exam_id=?""",
                            (remote_exam_id, server_revision, client_exam_id),
                        )
                    else:
                        connection.execute(
                            """UPDATE local_exams SET remote_exam_id=?,remote_revision=?,
                               sync_status='synced',updated_at=?
                               WHERE client_exam_id=?""",
                            (
                                remote_exam_id,
                                server_revision,
                                time.time(),
                                client_exam_id,
                            ),
                        )
                updated.append(client_exam_id)
        return {"removed": removed, "conflicts": conflicts, "updated": updated}

    def delete_local_exam(self, client_exam_id: str) -> None:
        """Delete only an exam that has never become a server-owned record."""
        with self._lock, self.connect() as connection:
            row = connection.execute(
                """SELECT remote_exam_id,sync_status FROM local_exams
                   WHERE client_exam_id=?""",
                (client_exam_id,),
            ).fetchone()
            if row is None:
                raise KeyError(client_exam_id)
            if row["remote_exam_id"] and row["sync_status"] != "conflict":
                raise ValueError("Đề đã đồng bộ; hãy xóa bản web khi đang online")
        self._quarantine_exam_directory(client_exam_id, "local-delete")
        with self._lock, self.connect() as connection:
            connection.execute(
                "DELETE FROM local_exams WHERE client_exam_id=?",
                (client_exam_id,),
            )

    def set_category(self, client_exam_id: str, category: str) -> None:
        now = time.time()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM local_exams WHERE client_exam_id=?",
                (client_exam_id,),
            ).fetchone()
            if row is None:
                raise KeyError(client_exam_id)
            payload = json.loads(row["payload_json"])
            payload["category"] = category
            connection.execute(
                """UPDATE local_exams SET category=?,payload_json=?,
                   sync_status='pending',updated_at=? WHERE client_exam_id=?""",
                (
                    category,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    client_exam_id,
                ),
            )
            connection.execute(
                """UPDATE sync_queue SET status='pending',last_error=NULL,
                   next_attempt_at=0,updated_at=? WHERE client_exam_id=?""",
                (now, client_exam_id),
            )

    def mark_failed(self, client_exam_id: str, error: str) -> None:
        now = time.time()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT attempts FROM sync_queue WHERE client_exam_id=?",
                (client_exam_id,),
            ).fetchone()
            attempts = int(row["attempts"] if row else 0) + 1
            delay = min(300, 2 ** min(attempts, 8))
            connection.execute(
                """UPDATE sync_queue SET status='failed',attempts=?,
                   last_error=?,next_attempt_at=?,updated_at=?
                   WHERE client_exam_id=?""",
                (attempts, error[:1000], now + delay, now, client_exam_id),
            )
            connection.execute(
                """UPDATE local_exams SET sync_status='failed',updated_at=?
                   WHERE client_exam_id=?""",
                (now, client_exam_id),
            )

    def list_tags(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT name FROM exam_tags ORDER BY name ASC").fetchall()
        return [row["name"] for row in rows]

    def add_tag(self, name: str) -> str:
        clean = name.strip()
        if not clean:
            return ""
        now = time.time()
        tag_id = str(uuid.uuid4())
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO exam_tags (id, name, created_at) VALUES (?, ?, ?) ON CONFLICT(name) DO NOTHING",
                (tag_id, clean, now),
            )
        return clean

    def get_policy(self, key: str) -> dict[str, str]:
        with self.connect() as connection:
            row = connection.execute("SELECT title, content FROM site_policies WHERE key=?", (key,)).fetchone()
        if row:
            return {"title": row["title"], "content": row["content"]}
        default_titles = {
            "terms": "Điều khoản dịch vụ",
            "privacy": "Chính sách bảo mật",
        }
        default_contents = {
            "terms": "Chưa có nội dung điều khoản dịch vụ.",
            "privacy": "Chưa có nội dung chính sách bảo mật.",
        }
        return {"title": default_titles.get(key, "Chính sách"), "content": default_contents.get(key, "")}

    def save_policy(self, key: str, title: str, content: str) -> None:
        now = time.time()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO site_policies (key, title, content, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET title=excluded.title, content=excluded.content, updated_at=excluded.updated_at""",
                (key, title, content, now),
            )

    def save_attempt(self, payload: dict) -> dict:
        now = time.time()
        attempt_id = str(payload.get("id") or uuid.uuid4())
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO local_attempts
                   (id, client_exam_id, exam_title, exam_type, score_toeic, listening_score, reading_score, correct_count, total_questions, duration_seconds, time_spent_seconds, mode, submitted_at, answers_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    attempt_id,
                    str(payload.get("client_exam_id", "")),
                    str(payload.get("exam_title", "Đề thi TOEIC")),
                    str(payload.get("exam_type", "combined")),
                    int(payload.get("score_toeic", 0)),
                    int(payload.get("listening_score", 0)),
                    int(payload.get("reading_score", 0)),
                    int(payload.get("correct_count", 0)),
                    int(payload.get("total_questions", 0)),
                    int(payload.get("duration_seconds", 0)),
                    int(payload.get("time_spent_seconds", 0)),
                    str(payload.get("mode", "practice")),
                    now,
                    json.dumps(payload.get("answers", {})),
                ),
            )
        return {"id": attempt_id, "ok": True}

    def list_attempts(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT local_attempts.*, local_exams.payload_json AS exam_payload_json
                   FROM local_attempts
                   LEFT JOIN local_exams
                     ON local_exams.client_exam_id=local_attempts.client_exam_id
                   ORDER BY local_attempts.submitted_at DESC"""
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            try:
                exam_payload = json.loads(row["exam_payload_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                exam_payload = {}
            items.append({
                "id": row["id"],
                "client_exam_id": row["client_exam_id"],
                "exam_title": row["exam_title"],
                "exam_type": row["exam_type"],
                "score_toeic": row["score_toeic"],
                "listening_score": row["listening_score"],
                "reading_score": row["reading_score"],
                "correct_count": row["correct_count"],
                "total_questions": row["total_questions"],
                "duration_seconds": row["duration_seconds"],
                "time_spent_seconds": row["time_spent_seconds"],
                "mode": row["mode"],
                "submitted_at": datetime.fromtimestamp(row["submitted_at"], tz=timezone.utc).isoformat(),
                "answers": json.loads(row["answers_json"] or "{}"),
                "has_solutions": bool(exam_payload.get("solutions")),
            })
        return items
