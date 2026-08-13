"""Download and index the pinned MIT English–Vietnamese dictionary dataset."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unicodedata
import urllib.request
from pathlib import Path


SOURCE_URL = (
    "https://raw.githubusercontent.com/mduccc/en_vn_dic/"
    "0a64a84faca584287e4d46e4350ced4ce55aea1c/"
    "assets/data/vocabularies.json"
)
SOURCE_SHA256 = "98897ad689cd847f36b316d4bb2d2b51e38617ae4fe7d25a332275bb93f04e0c"


def normalized_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip().casefold()


def database_is_ready(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size <= 1024:
        return False
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA quick_check").fetchone()
            source = connection.execute(
                "SELECT value FROM metadata WHERE key = 'source'"
            ).fetchone()
            entries = connection.execute("SELECT COUNT(*) FROM entries").fetchone()
            return bool(
                integrity
                and integrity[0] == "ok"
                and source
                and source[0] == "https://github.com/mduccc/en_vn_dic"
                and entries
                and entries[0] > 0
            )
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return False


def build() -> None:
    destination = Path(
        os.getenv("LOCAL_DICTIONARY_PATH", "/dictionary-data/en_vi.sqlite3")
    )
    if database_is_ready(destination):
        print(f"LOCAL_DICTIONARY_READY path={destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Keep the temporary database on the same mounted filesystem so the final
    # os.replace remains atomic inside Docker volumes as well.
    with tempfile.TemporaryDirectory(
        prefix=".examify-dictionary-", dir=destination.parent
    ) as temp_dir:
        source_path = Path(temp_dir) / "vocabularies.json"
        database_path = Path(temp_dir) / "en_vi.sqlite3"
        print("LOCAL_DICTIONARY_DOWNLOAD source=en_vn_dic@0a64a84")
        with urllib.request.urlopen(SOURCE_URL, timeout=90) as response:
            digest = hashlib.sha256()
            with source_path.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
        if digest.hexdigest() != SOURCE_SHA256:
            raise RuntimeError("Local dictionary checksum mismatch")
        with source_path.open("r", encoding="utf-8") as source:
            groups = json.load(source)
        connection = sqlite3.connect(database_path)
        try:
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute(
                "CREATE TABLE entries (term_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            rows = []
            for entries in groups.values():
                if not isinstance(entries, dict):
                    continue
                for term, payload in entries.items():
                    if not isinstance(payload, dict):
                        continue
                    rows.append(
                        (
                            normalized_key(str(term)),
                            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                        )
                    )
            connection.executemany(
                "INSERT OR REPLACE INTO entries(term_key, payload) VALUES (?, ?)", rows
            )
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                [
                    ("source", "https://github.com/mduccc/en_vn_dic"),
                    ("license", "MIT"),
                    ("commit", "0a64a84faca584287e4d46e4350ced4ce55aea1c"),
                    ("entries", str(len(rows))),
                ],
            )
            connection.commit()
        finally:
            connection.close()
        os.replace(database_path, destination)
        print(f"LOCAL_DICTIONARY_INDEXED entries={len(rows)} path={destination}")


if __name__ == "__main__":
    try:
        build()
    except Exception as exc:
        # External providers remain available; a download outage must not stop
        # the API from booting. A later container start retries automatically.
        print(f"LOCAL_DICTIONARY_FALLBACK reason={exc}")
