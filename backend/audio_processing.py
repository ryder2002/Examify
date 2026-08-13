"""Prepare bandwidth-bounded web audio before an exam can be published."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable

from toeic_audio_cutter import auto_cut_full_audio


WEB_AUDIO_FORMAT = "ogg"
WEB_AUDIO_CODEC = "libopus"
WEB_AUDIO_MAX_BITRATE = 112_000
WEB_AUDIO_TARGET_BITRATE = "96k"
logger = logging.getLogger(__name__)
ProgressCallback = Callable[[int, str], None]


def _tool(name: str, environment_name: str) -> str | None:
    configured = str(os.environ.get(environment_name, "")).strip()
    if configured and Path(configured).is_file():
        return configured
    return shutil.which(name)


def _subprocess_options() -> dict[str, Any]:
    # Windows otherwise opens a console window for every FFmpeg/FFprobe call,
    # which looks like the Examify app has frozen during audio preparation.
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": creationflags} if creationflags else {}


def _probe(path: Path) -> tuple[str, int]:
    ffprobe = _tool("ffprobe", "FFPROBE_CMD")
    if not ffprobe:
        raise RuntimeError("ffprobe is required to validate production audio")
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,bit_rate:format=bit_rate",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        **_subprocess_options(),
    )
    payload = json.loads(result.stdout)
    stream = (payload.get("streams") or [{}])[0]
    codec = str(stream.get("codec_name") or "").lower()
    raw_bitrate = stream.get("bit_rate") or (payload.get("format") or {}).get(
        "bit_rate"
    )
    try:
        bitrate = int(raw_bitrate)
    except (TypeError, ValueError):
        bitrate = 0
    return codec, bitrate


def prepare_web_audio(
    store: Any,
    job_id: str,
    *,
    progress: ProgressCallback | None = None,
) -> int:
    """Normalize audio and auto-cut a full TOEIC recording in the OCR worker."""

    def report(percent: int, stage: str) -> None:
        if progress is not None:
            progress(max(0, min(100, int(percent))), stage)

    state = store.read(job_id)
    audios = list(
        state.get("audios")
        or ([state["audio"]] if state.get("audio") else [])
    )
    if not audios:
        return 0
    ffmpeg = _tool("ffmpeg", "FFMPEG_CMD")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to prepare production audio")

    # For Audio Full, the cutter emits every browser asset directly as OGG
    # Opus.  Re-encoding the complete source before cutting would decode and
    # encode the recording twice, which dominated total processing time.
    full_audio = next(
        (audio for audio in audios if audio.get("part") == "full"), None
    )
    if full_audio is not None and len(audios) == 1:
        report(1, "Đang phân tích Audio Full")
        source = store.audio_path(job_id, str(full_audio.get("id") or ""))
        metadata = state.setdefault("metadata", {})
        try:
            cut_result = auto_cut_full_audio(
                source,
                full_audio,
                job_id=job_id,
                ffmpeg=ffmpeg,
                ffprobe=_tool("ffprobe", "FFPROBE_CMD"),
                progress=report,
            )
            if cut_result is not None:
                metadata["audio_autocut"] = cut_result.metadata
                if cut_result.audios:
                    prepared = list(cut_result.audios)
                    metadata["web_audio"] = {
                        "format": "ogg-opus",
                        "codec": WEB_AUDIO_CODEC,
                        "target_bitrate": WEB_AUDIO_TARGET_BITRATE,
                        "converted": len(prepared),
                        "strategy": "direct-cut-from-source",
                    }
                    if hasattr(store, "update_media"):
                        store.update_media(
                            job_id,
                            audios=prepared,
                            audio=None,
                            metadata=metadata,
                        )
                    else:
                        current = store.read(job_id)
                        current["audios"] = prepared
                        current["audio"] = None
                        current.setdefault("metadata", {}).update(metadata)
                        store.write(job_id, current)
                    report(100, "Đã xử lý xong audio")
                    return len(prepared)
                # Alignment was intentionally rejected. Preserve the already
                # compliant full OGG as-is; there is no browser-quality gain
                # from converting it again merely because it cannot be split.
                try:
                    codec, bitrate = _probe(source)
                except Exception:
                    codec, bitrate = "", 0
                if (
                    source.suffix.lower() == ".ogg"
                    and codec in {"opus", "vorbis"}
                    and 0 < bitrate <= WEB_AUDIO_MAX_BITRATE
                ):
                    current = store.read(job_id)
                    current["audios"] = audios
                    current["audio"] = full_audio
                    current.setdefault("metadata", {}).update(metadata)
                    if hasattr(store, "update_media"):
                        store.update_media(
                            job_id,
                            audios=audios,
                            audio=full_audio,
                            metadata=metadata,
                        )
                    else:
                        store.write(job_id, current)
                    report(100, "Đã giữ Audio Full đã tối ưu")
                    return 0
        except Exception as exc:
            # Keep the uploaded full recording usable when automatic
            # alignment is uncertain or a clip fails; normalization below
            # still gives the browser a compact OGG fallback.
            logger.exception("TOEIC audio auto-cut failed for job %s", job_id)
            metadata["audio_autocut"] = {
                "status": "fallback",
                "reason": "processing_error",
                "message": str(exc)[:500],
            }

    report(1, "Đang chuẩn hóa audio")

    converted = 0
    audio_count = len(audios)
    for index, audio in enumerate(audios, start=1):
        source_id = str(audio.get("id") or "")
        source = store.audio_path(job_id, source_id)
        codec, bitrate = _probe(source)
        if (
            source.suffix.lower() == ".ogg"
            and codec in {"opus", "vorbis"}
            and 0 < bitrate <= WEB_AUDIO_MAX_BITRATE
        ):
            report(
                round(index * 10 / audio_count),
                f"Đã kiểm tra audio {index}/{audio_count}",
            )
            continue

        web_id = f"{uuid.uuid4().hex}.{WEB_AUDIO_FORMAT}"
        destination = source.parent / web_id
        temporary = source.parent / f".{web_id}.tmp"
        try:
            subprocess.run(
                [
                    ffmpeg,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    "-vn",
                    "-map_metadata",
                    "-1",
                    "-threads",
                    "1",
                    "-codec:a",
                    WEB_AUDIO_CODEC,
                    "-b:a",
                    WEB_AUDIO_TARGET_BITRATE,
                    "-vbr",
                    "on",
                    "-application",
                    "audio",
                    # The atomic temporary name ends in ``.tmp``.  FFmpeg
                    # cannot infer the muxer from that suffix, so make the
                    # OGG container explicit instead of failing with exit
                    # code 234.
                    "-f",
                    WEB_AUDIO_FORMAT,
                    str(temporary),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=15 * 60,
                **_subprocess_options(),
            )
            if not temporary.is_file() or temporary.stat().st_size <= 0:
                raise RuntimeError(f"Audio conversion created an empty file: {source_id}")
            temporary.replace(destination)
        except subprocess.CalledProcessError as exc:
            details = str(exc.stderr or "").strip()
            raise RuntimeError(
                f"Không thể chuyển audio sang OGG (mã lỗi {exc.returncode})"
                + (f": {details[-1000:]}" if details else ".")
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Chuyển audio sang OGG quá thời gian cho phép.") from exc
        finally:
            temporary.unlink(missing_ok=True)

        audio["id"] = web_id
        audio["url"] = f"/api/extractions/{job_id}/audio/{web_id}"
        audio["filename"] = f"{Path(str(audio.get('filename') or source_id)).stem}.web.ogg"
        audio["content_type"] = "audio/ogg"
        audio["size"] = destination.stat().st_size
        audio["source_original_id"] = source_id
        converted += 1
        report(
            round(index * 10 / audio_count),
            f"Đã chuẩn hóa audio {index}/{audio_count}",
        )

    changed = converted > 0
    metadata = state.setdefault("metadata", {})
    if converted:
        metadata["web_audio"] = {
            "format": "ogg-opus",
            "codec": WEB_AUDIO_CODEC,
            "target_bitrate": WEB_AUDIO_TARGET_BITRATE,
            "converted": converted,
        }

    if changed:
        # Merge under the store's lock/transaction so concurrent remote OCR
        # progress cannot be rolled back by a stale audio state snapshot.
        if hasattr(store, "update_media"):
            store.update_media(
                job_id,
                audios=audios,
                audio=full_audio,
                metadata=metadata,
            )
        else:
            current = store.read(job_id)
            current["audios"] = audios
            current["audio"] = full_audio
            current.setdefault("metadata", {}).update(metadata)
            store.write(job_id, current)
    report(100, "Đã xử lý xong audio")
    return converted
