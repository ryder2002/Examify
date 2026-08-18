"""Bilingual dictionary aggregation for short English/Vietnamese terms."""

from __future__ import annotations

import html
import json
import logging
import re
import sqlite3
import threading
import time
import unicodedata
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from config import settings

try:
    import redis
except ImportError:  # pragma: no cover - desktop bundles do not import this module
    redis = None


MAX_QUERY_LENGTH = 80
MAX_QUERY_TOKENS = 5
MAX_AUDIO_BYTES = 5 * 1024 * 1024
AUDIO_HOST_ALLOWLIST = {
    "api.dictionaryapi.dev",
    "ssl.gstatic.com",
    "commons.wikimedia.org",
    "upload.wikimedia.org",
}
logger = logging.getLogger(__name__)


class DictionaryValidationError(ValueError):
    pass


class DictionaryNotFound(LookupError):
    pass


class DictionaryUnavailable(RuntimeError):
    pass


class PronunciationNotFound(LookupError):
    pass


def normalize_query(value: str) -> str:
    query = re.sub(r"\s+", " ", unicodedata.normalize("NFC", value or "")).strip()
    if not query:
        raise DictionaryValidationError("Vui lòng nhập từ cần tra")
    if len(query) > MAX_QUERY_LENGTH or len(query.split(" ")) > MAX_QUERY_TOKENS:
        raise DictionaryValidationError("Chỉ hỗ trợ từ hoặc cụm từ ngắn (tối đa 5 từ)")
    if any(unicodedata.category(character).startswith("C") for character in query):
        raise DictionaryValidationError("Từ khóa chứa ký tự không hợp lệ")
    return query


def _normalized_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip().casefold()


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _accent_for_audio(url: str) -> str | None:
    lowered = url.lower()
    if any(marker in lowered for marker in ("-us.", "-us-", "_us_", "/us/")):
        return "US"
    if any(marker in lowered for marker in ("-uk.", "-uk-", "-gb.", "_gb_", "/uk/")):
        return "UK"
    return None


class DictionaryCache:
    """Redis-backed cache with a small in-process fallback."""

    def __init__(self) -> None:
        self._memory: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._lock = threading.Lock()
        self._redis = None
        if redis is not None and settings.redis_url:
            try:
                self._redis = redis.Redis.from_url(
                    settings.redis_url,
                    socket_connect_timeout=0.25,
                    socket_timeout=0.5,
                    decode_responses=True,
                )
            except Exception:
                self._redis = None

    def get(self, key: str) -> dict[str, Any] | None:
        if self._redis is not None:
            try:
                raw = self._redis.get(key)
                if raw:
                    return json.loads(raw)
            except Exception:
                pass
        now = time.monotonic()
        with self._lock:
            cached = self._memory.get(key)
            if cached is None:
                return None
            expires_at, value = cached
            if expires_at <= now:
                self._memory.pop(key, None)
                return None
            self._memory.move_to_end(key)
            return json.loads(json.dumps(value))

    def set(self, key: str, value: dict[str, Any], ttl: int) -> None:
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if self._redis is not None:
            try:
                self._redis.setex(key, ttl, serialized)
            except Exception:
                pass
        with self._lock:
            self._memory[key] = (time.monotonic() + ttl, json.loads(serialized))
            self._memory.move_to_end(key)
            while len(self._memory) > 256:
                self._memory.popitem(last=False)


class LocalDictionary:
    """Read-only indexed English–Vietnamese dictionary stored on the server."""

    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or settings.local_dictionary_path)

    def lookup(self, query: str) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            connection = sqlite3.connect(
                f"file:{self.path}?mode=ro", uri=True, timeout=0.2
            )
            try:
                row = connection.execute(
                    "SELECT payload FROM entries WHERE term_key = ?",
                    (_normalized_key(query),),
                ).fetchone()
            finally:
                connection.close()
        except sqlite3.Error:
            logger.warning("LOCAL_DICTIONARY_READ_FAILED path=%s", self.path, exc_info=True)
            return None
        if row is None:
            return None
        try:
            entry = json.loads(row[0])
        except (TypeError, ValueError):
            return None

        translations: list[str] = []
        meanings: list[dict[str, Any]] = []
        for detail in entry.get("details") or []:
            senses: list[dict[str, Any]] = []
            for item in detail.get("means") or []:
                definition = str(item.get("mean") or "").strip()
                if not definition:
                    continue
                if definition not in translations and len(translations) < 16:
                    translations.append(definition)
                examples = item.get("example") or []
                example = next(
                    (str(value).strip() for value in examples if str(value).strip()),
                    None,
                )
                senses.append(
                    {
                        "definition": definition,
                        "example": example,
                        "example_source": "dictionary" if example else None,
                        "synonyms": [],
                        "antonyms": [],
                    }
                )
            if senses:
                meanings.append(
                    {
                        "part_of_speech": str(detail.get("pos") or "other"),
                        "senses": senses,
                    }
                )
        if not translations and not meanings:
            return None
        word = str(entry.get("vocabulary") or query).strip()
        ipa = str(entry.get("ipa") or "").strip()
        return {
            "query": query,
            "direction": "en-vi",
            "resolved_english_word": word,
            "translations": translations,
            "phonetics": (
                [{"text": ipa, "accent": None, "has_audio": False, "variant": 0}]
                if ipa
                else []
            ),
            "meanings": meanings,
            "fallback_examples": [],
            "attribution": [
                {
                    "name": "en_vn_dic (offline)",
                    "url": "https://github.com/mduccc/en_vn_dic",
                    "license": "MIT",
                }
            ],
            "warnings": [],
            "local": True,
        }


def get_english_stem_candidates(word: str) -> list[str]:
    cleaned = word.strip().casefold()
    if not cleaned or len(cleaned) <= 2:
        return [word]

    candidates = [word, cleaned]

    if cleaned.endswith("ed") and len(cleaned) > 3:
        if cleaned.endswith("ied") and len(cleaned) > 4:
            candidates.append(cleaned[:-3] + "y")
        candidates.append(cleaned[:-1])
        candidates.append(cleaned[:-2])
        if len(cleaned) > 4 and cleaned[-3] == cleaned[-4] and cleaned[-3] in "bcdfghlmnprst":
            candidates.append(cleaned[:-3])

    if cleaned.endswith("ing") and len(cleaned) > 4:
        candidates.append(cleaned[:-3])
        candidates.append(cleaned[:-3] + "e")
        if cleaned.endswith("ying") and len(cleaned) > 4:
            candidates.append(cleaned[:-4] + "y")
        if len(cleaned) > 5 and cleaned[-4] == cleaned[-5] and cleaned[-4] in "bcdfghlmnprst":
            candidates.append(cleaned[:-4])

    if cleaned.endswith("s") and len(cleaned) > 3 and not cleaned.endswith("ss"):
        if cleaned.endswith("ies") and len(cleaned) > 4:
            candidates.append(cleaned[:-3] + "y")
        elif cleaned.endswith("es") and len(cleaned) > 4:
            candidates.append(cleaned[:-2])
            candidates.append(cleaned[:-1])
        else:
            candidates.append(cleaned[:-1])

    seen: set[str] = set()
    result: list[str] = []
    for item in candidates:
        norm = _normalized_key(item)
        if norm and norm not in seen and len(norm) >= 2:
            seen.add(norm)
            result.append(item)
    return result


def lookup_dictionary_candidates(raw_query: str, source: str) -> dict[str, Any]:
    return dictionary_service.lookup(raw_query, source)


class DictionaryService:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        cache: DictionaryCache | None = None,
        local_dictionary: LocalDictionary | None = None,
    ) -> None:
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(settings.dictionary_http_timeout_seconds),
            follow_redirects=True,
            headers={"User-Agent": "Examify-Dictionary/1.0"},
        )
        self.cache = cache or DictionaryCache()
        self.local_dictionary = local_dictionary or LocalDictionary()

    def lookup(self, raw_query: str, source: str) -> dict[str, Any]:
        query = normalize_query(raw_query)
        if source not in {"en", "vi"}:
            raise DictionaryValidationError("Ngôn ngữ nguồn phải là en hoặc vi")
        cache_key = f"dictionary:v1:{source}:{_normalized_key(query)}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            if cached.get("_not_found"):
                raise DictionaryNotFound("Không tìm thấy từ phù hợp")
            cached["cached"] = True
            return cached

        if source == "en":
            candidates = get_english_stem_candidates(query)
            for candidate in candidates:
                local_result = self.local_dictionary.lookup(candidate)
                if local_result is not None:
                    local_result["query"] = query
                    local_result["cached"] = False
                    self.cache.set(
                        cache_key, local_result, settings.dictionary_cache_ttl_seconds
                    )
                    return local_result

        try:
            result = (
                self._lookup_english(query)
                if source == "en"
                else self._lookup_vietnamese(query)
            )
        except DictionaryNotFound:
            self._remember_not_found(query, source)
            raise
        result["cached"] = False
        self.cache.set(cache_key, result, settings.dictionary_cache_ttl_seconds)
        return result

    def pronunciation(self, raw_query: str, variant: int) -> tuple[bytes, str]:
        query = normalize_query(raw_query)
        if variant < 0 or variant > 20:
            raise PronunciationNotFound("Không tìm thấy bản phát âm")
        entry = self._dictionary_entry(query)
        if entry is None:
            raise PronunciationNotFound("Từ này chưa có audio")
        phonetics = entry["phonetics"]
        selected = next(
            (item for item in phonetics if item["variant"] == variant and item.get("_audio_url")),
            None,
        )
        if selected is None:
            raise PronunciationNotFound("Từ này chưa có audio")
        audio_url = selected["_audio_url"]
        self._validate_audio_url(audio_url)
        try:
            with self.client.stream("GET", audio_url) as response:
                response.raise_for_status()
                self._validate_audio_url(str(response.url))
                content_type = (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .lower()
                )
                if not content_type.startswith("audio/"):
                    raise DictionaryUnavailable(
                        "Nguồn phát âm trả về định dạng không hợp lệ"
                    )
                try:
                    declared_size = int(
                        response.headers.get("content-length", "0") or 0
                    )
                except ValueError:
                    declared_size = 0
                if declared_size > MAX_AUDIO_BYTES:
                    raise DictionaryUnavailable(
                        "File phát âm vượt quá giới hạn 5 MiB"
                    )
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_AUDIO_BYTES:
                        raise DictionaryUnavailable(
                            "File phát âm vượt quá giới hạn 5 MiB"
                        )
        except httpx.HTTPError as exc:
            raise DictionaryUnavailable("Không tải được audio phát âm") from exc
        return bytes(content), content_type

    def _lookup_english(self, query: str) -> dict[str, Any]:
        warnings: list[str] = []
        provider_errors = 0
        entry = None
        candidates = get_english_stem_candidates(query)
        for candidate in candidates:
            try:
                entry = self._dictionary_entry(candidate)
                if entry is not None:
                    break
            except DictionaryUnavailable:
                provider_errors += 1
                warnings.append("Tạm thời không tải được định nghĩa tiếng Anh.")
                break
        try:
            translations = self._translations(query, "en", "vi")
        except DictionaryUnavailable:
            translations = []
            provider_errors += 1
            warnings.append("Tạm thời không tải được từ tương ứng tiếng Việt.")

        if entry is None and not translations:
            self._raise_empty(provider_errors, 2)

        resolved = entry["word"] if entry else query
        return self._assemble(
            query=query,
            direction="en-vi",
            resolved_word=resolved,
            translations=translations,
            entry=entry,
            warnings=warnings,
        )

    def _lookup_vietnamese(self, query: str) -> dict[str, Any]:
        try:
            translations = self._translations(query, "vi", "en")
        except DictionaryUnavailable as exc:
            raise DictionaryUnavailable("Dịch vụ tra từ Việt–Anh đang tạm gián đoạn") from exc
        if not translations:
            raise DictionaryNotFound("Không tìm thấy từ tiếng Anh tương ứng")

        entry = None
        warnings: list[str] = []
        dictionary_failed = False
        for candidate in translations[:3]:
            try:
                entry = self._dictionary_entry(candidate)
            except DictionaryUnavailable:
                dictionary_failed = True
                break
            if entry is not None:
                break
        if dictionary_failed:
            warnings.append("Tìm thấy từ tương ứng nhưng chưa tải được định nghĩa tiếng Anh.")

        return self._assemble(
            query=query,
            direction="vi-en",
            resolved_word=entry["word"] if entry else translations[0],
            translations=translations,
            entry=entry,
            warnings=warnings,
        )

    def _assemble(
        self,
        *,
        query: str,
        direction: str,
        resolved_word: str,
        translations: list[str],
        entry: dict[str, Any] | None,
        warnings: list[str],
    ) -> dict[str, Any]:
        fallback_examples: list[dict[str, str | int]] = []
        attribution: list[dict[str, str]] = []
        if translations:
            attribution.append(
                {
                    "name": "MyMemory",
                    "url": "https://mymemory.translated.net/",
                    "license": "Translation memory",
                }
            )
        if entry is not None:
            attribution.extend(entry["attribution"])
            has_examples = any(
                sense.get("example")
                for meaning in entry["meanings"]
                for sense in meaning["senses"]
            )
            if not has_examples:
                try:
                    fallback_examples = self._tatoeba_examples(resolved_word)
                except DictionaryUnavailable:
                    warnings.append("Tạm thời không tải được câu ví dụ bổ sung.")
                if fallback_examples:
                    attribution.append(
                        {
                            "name": "Tatoeba",
                            "url": "https://tatoeba.org/",
                            "license": "CC BY 2.0 FR",
                        }
                    )

        public_phonetics = []
        for item in entry["phonetics"] if entry else []:
            public_phonetics.append(
                {
                    "text": item["text"],
                    "accent": item["accent"],
                    "has_audio": bool(item.get("_audio_url")),
                    "variant": item["variant"],
                }
            )
        return {
            "query": query,
            "direction": direction,
            "resolved_english_word": resolved_word,
            "translations": translations,
            "phonetics": public_phonetics,
            "meanings": entry["meanings"] if entry else [],
            "fallback_examples": fallback_examples,
            "attribution": attribution,
            "warnings": warnings,
        }

    def _dictionary_entry(self, word: str) -> dict[str, Any] | None:
        url = f"{settings.dictionary_api_url}/api/v2/entries/en/{quote(word, safe='')}"
        try:
            response = self.client.get(url)
        except httpx.HTTPError as exc:
            raise DictionaryUnavailable("Không kết nối được Dictionary API") from exc
        if response.status_code == 404:
            return None
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DictionaryUnavailable("Dictionary API trả về dữ liệu không hợp lệ") from exc
        if not isinstance(payload, list) or not payload:
            return None

        phonetics: list[dict[str, Any]] = []
        seen_phonetics: set[tuple[str, str]] = set()
        meanings: list[dict[str, Any]] = []
        attribution: list[dict[str, str]] = []
        word_value = str(payload[0].get("word") or word)
        for entry in payload:
            license_info = entry.get("license") or {}
            source_urls = entry.get("sourceUrls") or []
            if source_urls:
                attribution.append(
                    {
                        "name": "Wiktionary / Free Dictionary API",
                        "url": str(source_urls[0]),
                        "license": str(license_info.get("name") or "CC BY-SA"),
                    }
                )
            for phonetic in entry.get("phonetics") or []:
                text = str(phonetic.get("text") or "").strip()
                audio_url = str(phonetic.get("audio") or "").strip()
                if audio_url.startswith("//"):
                    audio_url = "https:" + audio_url
                key = (text, audio_url)
                if key in seen_phonetics or not (text or audio_url):
                    continue
                seen_phonetics.add(key)
                phonetics.append(
                    {
                        "text": text,
                        "accent": _accent_for_audio(audio_url) if audio_url else None,
                        "_audio_url": audio_url or None,
                        "variant": len(phonetics),
                    }
                )
            for meaning in entry.get("meanings") or []:
                senses = []
                for definition in meaning.get("definitions") or []:
                    text = str(definition.get("definition") or "").strip()
                    if not text:
                        continue
                    senses.append(
                        {
                            "definition": text,
                            "example": str(definition.get("example") or "").strip() or None,
                            "example_source": "dictionary"
                            if definition.get("example")
                            else None,
                            "synonyms": [
                                str(item)
                                for item in definition.get("synonyms") or []
                                if str(item).strip()
                            ],
                            "antonyms": [
                                str(item)
                                for item in definition.get("antonyms") or []
                                if str(item).strip()
                            ],
                        }
                    )
                if senses:
                    meanings.append(
                        {
                            "part_of_speech": str(
                                meaning.get("partOfSpeech") or "other"
                            ),
                            "senses": senses,
                        }
                    )
        if not attribution:
            attribution.append(
                {
                    "name": "Free Dictionary API",
                    "url": "https://dictionaryapi.dev/",
                    "license": "Source license",
                }
            )
        return {
            "word": word_value,
            "phonetics": phonetics,
            "meanings": meanings,
            "attribution": attribution,
        }

    def _translations(self, query: str, source: str, target: str) -> list[str]:
        params: dict[str, str] = {"q": query, "langpair": f"{source}|{target}"}
        if settings.mymemory_contact_email:
            params["de"] = settings.mymemory_contact_email
        try:
            response = self.client.get(
                f"{settings.dictionary_translation_url}/get", params=params
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DictionaryUnavailable("Không kết nối được MyMemory") from exc
        if payload.get("responseStatus") not in {None, 200, "200"}:
            raise DictionaryUnavailable("MyMemory từ chối yêu cầu tra từ")

        ranked: list[tuple[float, str]] = []
        primary = html.unescape(str((payload.get("responseData") or {}).get("translatedText") or "")).strip()
        if primary:
            ranked.append((_number((payload.get("responseData") or {}).get("match")) + 1, primary))
        for match in payload.get("matches") or []:
            translation = html.unescape(str(match.get("translation") or "")).strip()
            if translation:
                score = _number(match.get("match")) + _number(match.get("quality")) / 1000
                ranked.append((score, translation))
        ranked.sort(key=lambda item: item[0], reverse=True)

        results: list[str] = []
        seen = {_normalized_key(query)}
        for _, value in ranked:
            normalized = _normalized_key(value)
            if (
                not normalized
                or normalized in seen
                or len(value) > MAX_QUERY_LENGTH
                or len(value.split()) > MAX_QUERY_TOKENS
            ):
                continue
            seen.add(normalized)
            results.append(value)
            if len(results) == 5:
                break
        return results

    def _tatoeba_examples(self, word: str) -> list[dict[str, str | int]]:
        try:
            response = self.client.get(
                f"{settings.dictionary_examples_url}/v1/sentences",
                params={
                    "lang": "eng",
                    "q": f"={word}",
                    "sort": "relevance",
                    "limit": "10",
                    "is_unapproved": "no",
                    "is_orphan": "no",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DictionaryUnavailable("Không kết nối được Tatoeba") from exc
        results: list[dict[str, str | int]] = []
        word_pattern = re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
        for sentence in payload.get("data") or []:
            text = str(sentence.get("text") or "").strip()
            if (
                not text
                or sentence.get("is_unapproved")
                or not word_pattern.search(text)
                or len(text) > 240
            ):
                continue
            sentence_id = int(sentence.get("id") or 0)
            results.append(
                {
                    "text": text,
                    "author": str(sentence.get("owner") or "Tatoeba contributor"),
                    "license": str(sentence.get("license") or "CC BY 2.0 FR"),
                    "url": f"https://tatoeba.org/en/sentences/show/{sentence_id}",
                    "id": sentence_id,
                }
            )
            if len(results) == 3:
                break
        return results

    def _remember_not_found(self, query: str, source: str) -> None:
        self.cache.set(
            f"dictionary:v1:{source}:{_normalized_key(query)}",
            {"_not_found": True},
            settings.dictionary_negative_cache_ttl_seconds,
        )

    def _raise_empty(self, provider_errors: int, provider_count: int) -> None:
        if provider_errors >= provider_count:
            raise DictionaryUnavailable("Các nguồn từ điển đang tạm gián đoạn")
        raise DictionaryNotFound("Không tìm thấy từ phù hợp")

    @staticmethod
    def _validate_audio_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in AUDIO_HOST_ALLOWLIST:
            raise DictionaryUnavailable("Nguồn phát âm không được phép")


dictionary_service = DictionaryService()
