from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from dictionary_api import router
from dictionary_service import (
    DictionaryNotFound,
    DictionaryService,
    DictionaryUnavailable,
    DictionaryValidationError,
    LocalDictionary,
    PronunciationNotFound,
    normalize_query,
)


class MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, dict] = {}

    def get(self, key: str):
        value = self.values.get(key)
        return dict(value) if value else None

    def set(self, key: str, value: dict, _ttl: int) -> None:
        self.values[key] = dict(value)


def dictionary_payload(
    word: str,
    *,
    example: str | None = "This is an example.",
    audio: str = "https://api.dictionaryapi.dev/media/example-us.mp3",
) -> list[dict]:
    return [
        {
            "word": word,
            "phonetics": [{"text": "/ɪɡˈzɑːmpəl/", "audio": audio}],
            "meanings": [
                {
                    "partOfSpeech": "noun",
                    "definitions": [
                        {
                            "definition": "Something representative of a group.",
                            "example": example,
                            "synonyms": ["instance"],
                            "antonyms": [],
                        }
                    ],
                }
            ],
            "license": {"name": "CC BY-SA 3.0"},
            "sourceUrls": [f"https://en.wiktionary.org/wiki/{word}"],
        }
    ]


def translation_payload(*values: str) -> dict:
    primary = values[0] if values else ""
    return {
        "responseStatus": 200,
        "responseData": {"translatedText": primary, "match": 1},
        "matches": [
            {"translation": value, "match": 1 - index / 100, "quality": 74}
            for index, value in enumerate(values)
        ],
    }


def make_service(handler, cache=None) -> DictionaryService:
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return DictionaryService(client=client, cache=cache or MemoryCache())


def test_normalize_query_keeps_vietnamese_and_rejects_long_text() -> None:
    assert normalize_query("  từ   điển  ") == "từ điển"
    with pytest.raises(DictionaryValidationError):
        normalize_query("đây là một câu có nhiều hơn năm từ")
    with pytest.raises(DictionaryValidationError):
        normalize_query("x" * 81)


def test_english_lookup_combines_word_translation_definition_and_audio() -> None:
    mymemory_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.dictionaryapi.dev":
            return httpx.Response(200, json=dictionary_payload("example"))
        if request.url.host == "api.mymemory.translated.net":
            mymemory_queries.append(request.url.params["q"])
            return httpx.Response(200, json=translation_payload("ví dụ", "thí dụ"))
        raise AssertionError(f"Unexpected request: {request.url}")

    result = make_service(handler).lookup("example", "en")

    assert result["resolved_english_word"] == "example"
    assert result["translations"] == ["ví dụ", "thí dụ"]
    assert result["meanings"][0]["senses"][0]["example"] == "This is an example."
    assert result["phonetics"][0]["has_audio"] is True
    assert mymemory_queries == ["example"]


def test_vietnamese_lookup_tries_ranked_candidates_until_dictionary_matches() -> None:
    requested_words: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.mymemory.translated.net":
            return httpx.Response(
                200, json=translation_payload("for example", "instance", "example")
            )
        if request.url.host == "api.dictionaryapi.dev":
            word = request.url.path.rsplit("/", 1)[-1]
            requested_words.append(word)
            if word == "for example":
                return httpx.Response(404)
            return httpx.Response(200, json=dictionary_payload(word))
        raise AssertionError(f"Unexpected request: {request.url}")

    result = make_service(handler).lookup("ví dụ", "vi")

    assert result["resolved_english_word"] == "instance"
    assert requested_words == ["for example", "instance"]
    assert result["translations"][:2] == ["for example", "instance"]


def test_tatoeba_supplies_examples_with_attribution_when_dictionary_has_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.dictionaryapi.dev":
            return httpx.Response(
                200, json=dictionary_payload("example", example=None, audio="")
            )
        if request.url.host == "api.mymemory.translated.net":
            return httpx.Response(200, json=translation_payload("ví dụ"))
        if request.url.host == "api.tatoeba.org":
            assert request.url.params["q"] == "=example"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 12,
                            "text": "Give an example.",
                            "owner": "contributor",
                            "license": "CC BY 2.0 FR",
                            "is_unapproved": False,
                        },
                        {
                            "id": 13,
                            "text": "Bad example.",
                            "owner": "other",
                            "license": "CC BY 2.0 FR",
                            "is_unapproved": True,
                        },
                    ]
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    result = make_service(handler).lookup("example", "en")

    assert [item["text"] for item in result["fallback_examples"]] == [
        "Give an example."
    ]
    assert result["fallback_examples"][0]["author"] == "contributor"
    assert any(item["name"] == "Tatoeba" for item in result["attribution"])


def test_cache_prevents_duplicate_provider_calls() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.host == "api.dictionaryapi.dev":
            return httpx.Response(200, json=dictionary_payload("example"))
        return httpx.Response(200, json=translation_payload("ví dụ"))

    service = make_service(handler, MemoryCache())
    assert service.lookup("example", "en")["cached"] is False
    assert service.lookup("example", "en")["cached"] is True
    assert calls == 2


def test_local_dictionary_returns_immediately_without_external_requests(tmp_path) -> None:
    database_path = tmp_path / "dictionary.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.execute(
        "CREATE TABLE entries (term_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO entries(term_key, payload) VALUES (?, ?)",
        (
            "contract",
            json.dumps(
                {
                    "vocabulary": "contract",
                    "ipa": "/ˈkɒntrækt/",
                    "details": [
                        {
                            "pos": "noun",
                            "means": [
                                {
                                    "mean": "hợp đồng",
                                    "example": ["Please sign the contract."],
                                }
                            ],
                        }
                    ],
                }
            ),
        ),
    )
    connection.commit()
    connection.close()

    def no_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Local hit must not call {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(no_network))
    service = DictionaryService(
        client=client,
        cache=MemoryCache(),
        local_dictionary=LocalDictionary(str(database_path)),
    )
    result = service.lookup("Contract", "en")
    assert result["translations"] == ["hợp đồng"]
    assert result["meanings"][0]["senses"][0]["example"] == "Please sign the contract."
    assert result["local"] is True


def test_local_dictionary_stems_inflected_words(tmp_path) -> None:
    database_path = tmp_path / "dictionary.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.execute(
        "CREATE TABLE entries (term_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO entries(term_key, payload) VALUES (?, ?)",
        (
            "require",
            json.dumps(
                {
                    "vocabulary": "require",
                    "ipa": "/rɪˈkwaɪə/",
                    "details": [
                        {
                            "pos": "verb",
                            "means": [{"mean": "yêu cầu", "example": ["We require details."]}],
                        }
                    ],
                }
            ),
        ),
    )
    connection.commit()
    connection.close()

    def no_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Stem match must not call network: {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(no_network))
    service = DictionaryService(
        client=client,
        cache=MemoryCache(),
        local_dictionary=LocalDictionary(str(database_path)),
    )
    result = service.lookup("required", "en")
    assert result["query"] == "required"
    assert result["resolved_english_word"] == "require"
    assert result["translations"] == ["yêu cầu"]


def test_partial_and_total_provider_failures() -> None:
    def partial_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.dictionaryapi.dev":
            return httpx.Response(200, json=dictionary_payload("example"))
        raise httpx.ConnectError("offline", request=request)

    partial = make_service(partial_handler).lookup("example", "en")
    assert partial["meanings"]
    assert partial["warnings"]

    def failed_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(DictionaryUnavailable):
        make_service(failed_handler).lookup("example", "en")


def test_not_found_is_negative_cached() -> None:
    cache = MemoryCache()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.dictionaryapi.dev":
            return httpx.Response(404)
        return httpx.Response(200, json=translation_payload())

    service = make_service(handler, cache)
    with pytest.raises(DictionaryNotFound):
        service.lookup("notaword", "en")
    with pytest.raises(DictionaryNotFound):
        service.lookup("notaword", "en")


def test_pronunciation_validates_source_and_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v2/entries/"):
            return httpx.Response(200, json=dictionary_payload("example"))
        if request.url.path.endswith("example-us.mp3"):
            return httpx.Response(
                200,
                content=b"audio",
                headers={"content-type": "audio/mpeg", "content-length": "5"},
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    content, content_type = make_service(handler).pronunciation("example", 0)
    assert content == b"audio"
    assert content_type == "audio/mpeg"

    def unsafe_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=dictionary_payload("example", audio="https://evil.example/audio.mp3"),
        )

    with pytest.raises(DictionaryUnavailable):
        make_service(unsafe_handler).pronunciation("example", 0)
    with pytest.raises(PronunciationNotFound):
        make_service(handler).pronunciation("example", 9)

    def oversized_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v2/entries/"):
            return httpx.Response(200, json=dictionary_payload("example"))
        return httpx.Response(
            200,
            content=b"small",
            headers={"content-type": "audio/mpeg", "content-length": "6000000"},
        )

    with pytest.raises(DictionaryUnavailable, match="5 MiB"):
        make_service(oversized_handler).pronunciation("example", 0)

    def invalid_mime_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v2/entries/"):
            return httpx.Response(200, json=dictionary_payload("example"))
        return httpx.Response(200, content=b"html", headers={"content-type": "text/html"})

    with pytest.raises(DictionaryUnavailable, match="định dạng"):
        make_service(invalid_mime_handler).pronunciation("example", 0)


def test_dictionary_router_requires_identity_and_maps_service_result() -> None:
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    with patch(
        "dictionary_api.current_identity",
        side_effect=HTTPException(status_code=401, detail="Thiết bị chưa được kích hoạt"),
    ):
        assert client.get("/api/v1/dictionary/lookup?q=example&source=en").status_code == 401

    expected = {
        "query": "example",
        "direction": "en-vi",
        "resolved_english_word": "example",
        "translations": ["ví dụ"],
        "phonetics": [],
        "meanings": [],
        "fallback_examples": [],
        "attribution": [],
        "warnings": [],
        "cached": False,
    }
    with (
        patch("dictionary_api.current_identity", return_value={"user_id": "user"}),
        patch("dictionary_api.dictionary_service.lookup", return_value=expected),
    ):
        response = client.get("/api/v1/dictionary/lookup?q=example&source=en")
    assert response.status_code == 200
    assert response.json() == expected
