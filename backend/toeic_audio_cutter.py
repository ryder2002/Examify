"""Deterministic FFmpeg-only cutter for a standard 100-question TOEIC audio.

The silence threshold intentionally reproduces the small algorithm from
``jinjor/wave-cutter-for-toeic`` at revision
``4e4ce393864d2d7aa8944c5efa0c9350ea5ea8c6``.  The original browser tool
only produced raw waves and expected manual delete/merge operations.  This
module adds a fail-closed structural alignment for the fixed TOEIC layout; it
does not claim to understand speech.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


logger = logging.getLogger(__name__)

REFERENCE_REPOSITORY = "https://github.com/jinjor/wave-cutter-for-toeic"
REFERENCE_REVISION = "4e4ce393864d2d7aa8944c5efa0c9350ea5ea8c6"
REFERENCE_SILENT_SAMPLES = 60_000
SILENCE_NOISE_DB = -40
MIN_ALIGNMENT_CONFIDENCE = 0.70
MAX_RAW_WAVES = 220
EXPECTED_ATOMIC_ROLES = 123
FINE_SILENCE_SECONDS = 0.45
# Re-encoding full TOEIC audio is CPU intensive.  Two independent FFmpeg
# processes keep a 5-vCPU worker busy while leaving headroom for the OCR
# branch, which runs concurrently in the same Celery task.  The cap prevents
# a long listening upload from creating 55 decoder/encoder processes at once.
DEFAULT_CUT_WORKERS = 2
MAX_CUT_WORKERS = 3

ProgressCallback = Callable[[int, str], None]

_SILENCE_END = re.compile(r"silence_end:\s*([0-9]+(?:\.[0-9]+)?)")


def _subprocess_options() -> dict[str, int]:
    """Keep bundled FFmpeg silent on Windows desktop builds."""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": creationflags} if creationflags else {}


def _cut_workers(output_count: int) -> int:
    """Return a bounded FFmpeg clip-conversion parallelism level."""

    try:
        configured = int(os.getenv("AUDIO_CUT_WORKERS", str(DEFAULT_CUT_WORKERS)))
    except ValueError:
        configured = DEFAULT_CUT_WORKERS
    return max(1, min(MAX_CUT_WORKERS, output_count, configured))


@dataclass(frozen=True)
class AudioInfo:
    sample_rate: int
    duration: float


@dataclass(frozen=True)
class Wave:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class AtomicRole:
    key: str
    kind: str
    question_numbers: tuple[int, ...]
    part: str
    group_id: str | None = None


@dataclass(frozen=True)
class RoleMatch:
    role: AtomicRole
    first_wave: int
    last_wave: int
    start: float
    end: float
    cost: float


@dataclass(frozen=True)
class SkippedWave:
    wave_index: int
    before_role: int


@dataclass(frozen=True)
class Alignment:
    matches: tuple[RoleMatch, ...]
    skipped: tuple[SkippedWave, ...]
    total_cost: float
    confidence: float
    profile: str


@dataclass(frozen=True)
class OutputSpan:
    key: str
    part: str
    scope: str
    question_numbers: tuple[int, ...]
    group_id: str | None
    start: float
    end: float


@dataclass(frozen=True)
class AutoCutResult:
    audios: tuple[dict[str, Any], ...]
    metadata: dict[str, Any]


def expected_atomic_roles() -> tuple[AtomicRole, ...]:
    """Return the 123-role ``All+`` layout documented by the reference tool."""

    roles: list[AtomicRole] = []
    for number in range(1, 32):
        part = "part_1" if number <= 6 else "part_2"
        kind = "part1_question" if number <= 6 else "part2_question"
        roles.append(AtomicRole(f"q{number}", kind, (number,), part))
    for start in range(32, 101, 3):
        numbers = tuple(range(start, start + 3))
        part = "part_3" if start <= 68 else "part_4"
        group_id = f"listening-{start}-{start + 2}"
        roles.append(AtomicRole(f"g{start}-passage", "passage", numbers, part, group_id))
        for number in numbers:
            roles.append(AtomicRole(f"q{number}-prompt", "prompt", (number,), part, group_id))
    if len(roles) != EXPECTED_ATOMIC_ROLES:
        raise AssertionError(f"Unexpected TOEIC role count: {len(roles)}")
    return tuple(roles)


def probe_audio(path: Path, *, ffprobe: str | None = None) -> AudioInfo:
    executable = ffprobe or shutil.which("ffprobe")
    if not executable:
        raise RuntimeError("ffprobe is required to analyse TOEIC audio")
    result = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate:format=duration",
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
    try:
        sample_rate = int(stream.get("sample_rate"))
        duration = float((payload.get("format") or {}).get("duration"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("ffprobe did not return a valid sample rate/duration") from exc
    if sample_rate < 8_000 or not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("Audio sample rate or duration is invalid")
    return AudioInfo(sample_rate=sample_rate, duration=duration)


def parse_silence_waves(stderr: str, duration: float) -> tuple[Wave, ...]:
    """Convert FFmpeg silence ends to the reference tool's raw wave intervals."""

    boundaries = sorted(
        {
            min(float(match.group(1)), duration)
            for match in _SILENCE_END.finditer(stderr)
            if 0.001 < float(match.group(1)) < duration - 0.001
        }
    )
    points = [0.0, *boundaries, duration]
    return tuple(
        Wave(start, end)
        for start, end in zip(points, points[1:])
        if end - start >= 0.02
    )


def detect_silence_waves(
    path: Path,
    info: AudioInfo,
    *,
    ffmpeg: str | None = None,
    minimum_silence_seconds: float | None = None,
) -> tuple[Wave, ...]:
    executable = ffmpeg or shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("ffmpeg is required to analyse TOEIC audio")
    minimum_silence = (
        minimum_silence_seconds
        if minimum_silence_seconds is not None
        else REFERENCE_SILENT_SAMPLES / info.sample_rate
    )
    result = subprocess.run(
        [
            executable,
            "-nostdin",
            "-hide_banner",
            "-i",
            str(path),
            "-vn",
            "-af",
            f"silencedetect=noise={SILENCE_NOISE_DB}dB:d={minimum_silence:.9f}",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=15 * 60,
        **_subprocess_options(),
    )
    return parse_silence_waves(result.stderr, info.duration)


def _match_cost(role: AtomicRole, duration: float) -> float:
    targets = {
        # Measured on the supplied 46-minute CD: a Part 1 item is normally
        # 25-27s, Part 2 is 18-23s and each Part 3/4 prompt is 11-15s.
        "part1_question": (25.5, 0.48),
        "part2_question": (20.0, 0.48),
        "passage": (46.0, 0.62),
        "prompt": (12.7, 0.48),
    }
    target, spread = targets[role.kind]
    if duration <= 0.15:
        return 30.0
    cost = (math.log(duration / target) / spread) ** 2
    if role.kind == "passage" and duration < 10:
        cost += (10 - duration) * 1.5
    elif role.kind == "prompt" and duration > 24:
        cost += (duration - 24) * 0.5
    elif role.kind in {"part1_question", "part2_question"} and duration > 55:
        cost += (duration - 55) * 0.2
    return min(cost, 40.0)


def _skip_cost(wave: Wave, before_role: int) -> float:
    duration = wave.duration
    # Part boundaries often contain spoken directions.  They are valid audio
    # but not one of the 123 All+ roles, so allow the decoder to skip them.
    if before_role in {0, 6, 31, 83}:
        if duration <= 8:
            return 0.08
        if duration <= 45:
            return 0.32
        return 0.75 + (duration - 45) / 90
    if duration <= 3:
        return 0.10
    if duration <= 7.5:
        return 0.24
    if duration <= 12:
        return 0.85
    return 2.4 + min(duration / 30, 3.0)


def _alignment_for_prefix(
    waves: tuple[Wave, ...],
    roles: tuple[AtomicRole, ...],
    prefix: int,
    initial_cost: float,
) -> Alignment | None:
    wave_count = len(waves)
    role_count = len(roles)
    infinity = float("inf")
    costs = [[infinity] * (role_count + 1) for _ in range(wave_count + 1)]
    previous: list[list[tuple[int, int, str, int, float] | None]] = [
        [None] * (role_count + 1) for _ in range(wave_count + 1)
    ]
    costs[prefix][0] = initial_cost

    for wave_index in range(prefix, wave_count + 1):
        for role_index in range(role_count + 1):
            current = costs[wave_index][role_index]
            if not math.isfinite(current):
                continue
            if wave_index < wave_count:
                skipped_cost = _skip_cost(waves[wave_index], role_index)
                candidate = current + skipped_cost
                if candidate < costs[wave_index + 1][role_index]:
                    costs[wave_index + 1][role_index] = candidate
                    previous[wave_index + 1][role_index] = (
                        wave_index,
                        role_index,
                        "skip",
                        1,
                        skipped_cost,
                    )
            if role_index >= role_count:
                continue
            # A raw split may occur inside spoken content.  Merge at most three
            # adjacent waves for one role, with an explicit complexity penalty.
            for consumed in (1, 2, 3):
                end_index = wave_index + consumed
                if end_index > wave_count:
                    break
                duration = waves[end_index - 1].end - waves[wave_index].start
                match_cost = _match_cost(roles[role_index], duration) + (consumed - 1) * 0.65
                candidate = current + match_cost
                if candidate < costs[end_index][role_index + 1]:
                    costs[end_index][role_index + 1] = candidate
                    previous[end_index][role_index + 1] = (
                        wave_index,
                        role_index,
                        "match",
                        consumed,
                        match_cost,
                    )

    if not math.isfinite(costs[wave_count][role_count]):
        return None
    cursor = (wave_count, role_count)
    matches: list[RoleMatch] = []
    skipped: list[SkippedWave] = [
        SkippedWave(index, 0) for index in range(prefix)
    ]
    while cursor != (prefix, 0):
        item = previous[cursor[0]][cursor[1]]
        if item is None:
            return None
        old_wave, old_role, operation, consumed, operation_cost = item
        if operation == "skip":
            skipped.append(SkippedWave(old_wave, old_role))
        else:
            matches.append(
                RoleMatch(
                    role=roles[old_role],
                    first_wave=old_wave,
                    last_wave=old_wave + consumed - 1,
                    start=waves[old_wave].start,
                    end=waves[old_wave + consumed - 1].end,
                    cost=operation_cost,
                )
            )
        cursor = (old_wave, old_role)
    matches.reverse()
    skipped.sort(key=lambda item: item.wave_index)
    if len(matches) != role_count:
        return None

    mean_match_cost = sum(item.cost for item in matches) / role_count
    duration_quality = math.exp(-mean_match_cost / 2.6)
    pattern_hits = 0
    pattern_count = 0
    for offset in range(31, role_count, 4):
        passage = matches[offset].end - matches[offset].start
        prompts = [matches[offset + item].end - matches[offset + item].start for item in (1, 2, 3)]
        pattern_count += 1
        if passage >= max(8.0, sorted(prompts)[1] * 1.25):
            pattern_hits += 1
    pattern_quality = pattern_hits / pattern_count if pattern_count else 0.0
    plausible_skips = sum(
        1
        for item in skipped
        if waves[item.wave_index].duration <= 12 or item.before_role in {0, 6, 31, 83}
    )
    skip_quality = plausible_skips / len(skipped) if skipped else 1.0
    confidence = max(
        0.0,
        min(1.0, 0.45 * duration_quality + 0.40 * pattern_quality + 0.15 * skip_quality),
    )
    profile = "jinjor-134" if 132 <= wave_count <= 136 and prefix == 5 else "generic"
    return Alignment(
        matches=tuple(matches),
        skipped=tuple(skipped),
        total_cost=costs[wave_count][role_count],
        confidence=confidence,
        profile=profile,
    )


def align_waves(
    waves: Iterable[Wave],
    *,
    preferred_prefix: int | None = None,
    profile_wave_count: int | None = None,
) -> Alignment | None:
    """Align raw waves to the fixed TOEIC structure without speech recognition."""

    normalized = tuple(waves)
    roles = expected_atomic_roles()
    if len(normalized) < len(roles) or len(normalized) > MAX_RAW_WAVES:
        return None
    # Five leading raw waves are a stable signature reported across the user's
    # five 134-wave CDs.  Keep it as a prior, not a forced index: a materially
    # better structural alignment is still allowed to select another prefix.
    prefixes = list(range(0, min(10, len(normalized) - len(roles)) + 1))
    signature_count = profile_wave_count or len(normalized)
    candidates = [
        candidate
        for prefix in prefixes
        if (
            candidate := _alignment_for_prefix(
                normalized,
                roles,
                prefix,
                (
                    abs(prefix - (preferred_prefix if preferred_prefix is not None else 5)) * 1.5
                    + sum(_skip_cost(normalized[index], 0) for index in range(prefix)) * 0.1
                    if preferred_prefix is not None or 132 <= signature_count <= 136
                    else sum(_skip_cost(normalized[index], 0) for index in range(prefix))
                ),
            )
        )
        is not None
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item.total_cost, -item.confidence))


def suspicious_matches(alignment: Alignment) -> tuple[RoleMatch, ...]:
    """Find unusually long non-passage roles that may hide a short gap."""

    limits = {
        "part1_question": 40.0,
        "part2_question": 35.0,
        "prompt": 21.0,
    }
    return tuple(
        match
        for match in alignment.matches
        if match.role.kind in limits and match.end - match.start > limits[match.role.kind]
    )


def refine_suspicious_waves(
    coarse_waves: tuple[Wave, ...],
    fine_waves: tuple[Wave, ...],
    alignment: Alignment,
) -> tuple[Wave, ...]:
    """Add short-silence boundaries only inside structurally suspicious waves."""

    suspect_indexes = {
        index
        for match in suspicious_matches(alignment)
        for index in range(match.first_wave, match.last_wave + 1)
    }
    if not suspect_indexes:
        return coarse_waves
    fine_boundaries = [wave.end for wave in fine_waves[:-1]]
    boundaries = {wave.end for wave in coarse_waves[:-1]}
    for index in sorted(suspect_indexes):
        coarse = coarse_waves[index]
        cursor = coarse.start
        for boundary in fine_boundaries:
            if boundary <= cursor + 2.0:
                continue
            if boundary >= coarse.end - 2.0:
                break
            boundaries.add(boundary)
            cursor = boundary
    points = [0.0, *sorted(boundaries), coarse_waves[-1].end]
    return tuple(
        Wave(start, end)
        for start, end in zip(points, points[1:])
        if end - start >= 0.02
    )


def build_output_spans(alignment: Alignment, waves: tuple[Wave, ...]) -> tuple[OutputSpan, ...]:
    """Collapse 123 atomic roles into 31 question and 23 three-question assets."""

    by_key = {item.role.key: item for item in alignment.matches}
    transition_starts: dict[int, float] = {}
    skipped_positions = {
        (item.wave_index, item.before_role) for item in alignment.skipped
    }
    for role_index in (6, 31, 83):
        first_content_wave = alignment.matches[role_index].first_wave
        cursor = first_content_wave - 1
        while (cursor, role_index) in skipped_positions:
            cursor -= 1
        first_skipped_wave = cursor + 1
        if first_skipped_wave < first_content_wave:
            transition_duration = (
                waves[first_content_wave].start - waves[first_skipped_wave].start
            )
            if transition_duration >= 8:
                transition_starts[role_index] = waves[first_skipped_wave].start

    spans: list[OutputSpan] = []
    first = by_key["q1"]
    if first.start >= 0.5:
        spans.append(
            OutputSpan(
                key="directions-part-1",
                part="directions_part_1",
                scope="part",
                question_numbers=(),
                group_id=None,
                start=0.0,
                end=first.start,
            )
        )
    for number in range(1, 32):
        match = by_key[f"q{number}"]
        start = transition_starts.get(6 if number == 7 else -1, match.start)
        spans.append(
            OutputSpan(
                key=f"q{number:03d}",
                part=match.role.part,
                scope="question",
                question_numbers=(number,),
                group_id=None,
                start=start,
                end=match.end,
            )
        )
    for start_number in range(32, 101, 3):
        passage = by_key[f"g{start_number}-passage"]
        last_prompt = by_key[f"q{start_number + 2}-prompt"]
        role_index = 31 if start_number == 32 else 83 if start_number == 71 else -1
        start = transition_starts.get(role_index, passage.start)
        spans.append(
            OutputSpan(
                key=f"q{start_number:03d}-q{start_number + 2:03d}",
                part=passage.role.part,
                scope="group",
                question_numbers=tuple(range(start_number, start_number + 3)),
                group_id=passage.role.group_id,
                start=start,
                end=last_prompt.end,
            )
        )
    return tuple(spans)


def _cut_span(ffmpeg: str, source: Path, destination: Path, span: OutputSpan) -> None:
    temporary = destination.parent / f".{destination.name}.tmp"
    duration = span.end - span.start
    if duration <= 0.2:
        raise RuntimeError(f"Invalid auto-cut duration for {span.key}: {duration:.3f}s")
    try:
        subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{span.start:.6f}",
                "-i",
                str(source),
                "-t",
                f"{duration:.6f}",
                "-vn",
                "-map_metadata",
                "-1",
                "-threads",
                "1",
                "-codec:a",
                "libopus",
                "-b:a",
                "96k",
                "-vbr",
                "on",
                "-application",
                "audio",
                # ``temporary`` ends in ``.tmp`` for atomic replacement;
                # without an explicit muxer FFmpeg exits with code 234.
                "-f",
                "ogg",
                str(temporary),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5 * 60,
            **_subprocess_options(),
        )
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError(f"FFmpeg created an empty auto-cut file for {span.key}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def auto_cut_full_audio(
    source: Path,
    source_audio: dict[str, Any],
    *,
    job_id: str,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
    progress: ProgressCallback | None = None,
) -> AutoCutResult | None:
    """Create quiz-ready clips, returning ``None`` when alignment is unsafe."""

    def report(percent: int, stage: str) -> None:
        if progress is not None:
            progress(max(0, min(100, int(percent))), stage)

    report(1, "Kiểm tra Audio Full")
    ffmpeg_executable = ffmpeg or shutil.which("ffmpeg")
    ffprobe_executable = ffprobe or shutil.which("ffprobe")
    if not ffmpeg_executable or not ffprobe_executable:
        raise RuntimeError("ffmpeg and ffprobe are required to auto-cut full audio")
    info = probe_audio(source, ffprobe=ffprobe_executable)
    report(6, "Đang dò khoảng lặng trong Audio Full")
    coarse_waves = detect_silence_waves(source, info, ffmpeg=ffmpeg_executable)
    report(28, f"Đã phát hiện {len(coarse_waves)} đoạn audio")
    waves = coarse_waves
    alignment = align_waves(coarse_waves)
    refinement_used = False
    fine_wave_count: int | None = None
    if alignment is not None and suspicious_matches(alignment):
        report(30, "Đang kiểm tra lại các mốc audio chưa chắc chắn")
        fine_waves = detect_silence_waves(
            source,
            info,
            ffmpeg=ffmpeg_executable,
            minimum_silence_seconds=FINE_SILENCE_SECONDS,
        )
        fine_wave_count = len(fine_waves)
        refined_waves = refine_suspicious_waves(coarse_waves, fine_waves, alignment)
        if len(refined_waves) > len(coarse_waves):
            refined_alignment = align_waves(
                refined_waves,
                preferred_prefix=alignment.matches[0].first_wave,
                profile_wave_count=len(coarse_waves),
            )
            if (
                refined_alignment is not None
                and len(suspicious_matches(refined_alignment))
                < len(suspicious_matches(alignment))
                and refined_alignment.confidence >= alignment.confidence - 0.03
            ):
                waves = refined_waves
                alignment = refined_alignment
                refinement_used = True
        report(43, "Đã hoàn tất kiểm tra mốc audio")
    base_metadata: dict[str, Any] = {
        "engine": "ffmpeg-silencedetect+toeic-structure-v1",
        "reference_repository": REFERENCE_REPOSITORY,
        "reference_revision": REFERENCE_REVISION,
        "source_audio_id": str(source_audio.get("id") or source.name),
        "source_duration_seconds": round(info.duration, 3),
        "sample_rate": info.sample_rate,
        "noise_db": SILENCE_NOISE_DB,
        "minimum_silence_seconds": round(REFERENCE_SILENT_SAMPLES / info.sample_rate, 9),
        "raw_wave_count": len(coarse_waves),
        "alignment_wave_count": len(waves),
        "short_silence_refinement_used": refinement_used,
        "fine_silence_seconds": FINE_SILENCE_SECONDS if fine_wave_count is not None else None,
        "fine_wave_count": fine_wave_count,
        "expected_atomic_roles": EXPECTED_ATOMIC_ROLES,
    }
    if alignment is None:
        report(100, "Không đủ mốc tin cậy, giữ nguyên Audio Full")
        return AutoCutResult(
            audios=(),
            metadata={
                **base_metadata,
                "status": "fallback",
                "reason": "raw_wave_count_out_of_range",
            },
        )
    base_metadata.update(
        {
            "alignment_profile": alignment.profile,
            "alignment_confidence": round(alignment.confidence, 4),
            "alignment_cost": round(alignment.total_cost, 3),
            "skipped_wave_count": len(alignment.skipped),
            "skipped_waves": [
                {
                    "number": item.wave_index + 1,
                    "before_role": item.before_role,
                    "start": round(waves[item.wave_index].start, 3),
                    "end": round(waves[item.wave_index].end, 3),
                    "duration": round(waves[item.wave_index].duration, 3),
                }
                for item in alignment.skipped
            ],
        }
    )
    if alignment.confidence < MIN_ALIGNMENT_CONFIDENCE:
        report(100, "Mốc cắt chưa đủ tin cậy, giữ nguyên Audio Full")
        return AutoCutResult(
            audios=(),
            metadata={**base_metadata, "status": "fallback", "reason": "low_alignment_confidence"},
        )

    spans = build_output_spans(alignment, waves)
    if len([span for span in spans if span.scope != "part"]) != 54:
        raise RuntimeError("Auto-cut did not produce the required 54 TOEIC audio items")
    run_id = uuid.uuid4().hex[:12]
    created: list[Path] = []
    audio_refs: list[dict[str, Any]] = []
    source_stem = Path(str(source_audio.get("filename") or source.stem)).stem
    output_count = len(spans)
    report(46, f"Đang cắt audio 0/{output_count}")
    try:
        # Audio Full is deliberately cut directly from the original upload.
        # Normalizing it first used one complete MP3 -> OGG encode and then
        # decoded/re-encoded all 55 clips, adding a full extra pass over a
        # 40–50 minute recording without improving the output clips.
        #
        # A small bounded pool is materially faster than serial cuts but keeps
        # CPU/RAM predictable while OCR runs on its sibling thread.
        work_items = [
            (
                index,
                span,
                f"autocut-{run_id}-{span.key}.ogg",
                source.parent / f"autocut-{run_id}-{span.key}.ogg",
            )
            for index, span in enumerate(spans, start=1)
        ]
        completed = 0
        with ThreadPoolExecutor(max_workers=_cut_workers(output_count)) as executor:
            pending = iter(work_items)
            futures: dict[Any, tuple[int, OutputSpan, str, Path]] = {}

            def submit_next() -> bool:
                try:
                    index, span, audio_id, destination = next(pending)
                except StopIteration:
                    return False
                future = executor.submit(
                    _cut_span, ffmpeg_executable, source, destination, span
                )
                futures[future] = (index, span, audio_id, destination)
                return True

            for _ in range(_cut_workers(output_count)):
                if not submit_next():
                    break
            while futures:
                future = next(as_completed(futures))
                index, span, audio_id, destination = futures.pop(future)
                future.result()
                created.append(destination)
                audio_refs.append(
                    {
                        "id": audio_id,
                        "url": f"/api/extractions/{job_id}/audio/{audio_id}",
                        "filename": f"{source_stem}.{span.key}.ogg",
                        "content_type": "audio/ogg",
                        "size": destination.stat().st_size,
                        "part": span.part,
                        "scope": span.scope,
                        "question_numbers": list(span.question_numbers),
                        "group_id": span.group_id,
                    }
                )
                completed += 1
                report(
                    46 + round(completed * 54 / output_count),
                    f"Đang cắt audio {completed}/{output_count}",
                )
                submit_next()
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        for _index, _span, _audio_id, destination in locals().get("work_items", []):
            destination.unlink(missing_ok=True)
        raise

    # Completion order is intentionally parallel.  Persist in predictable
    # exam order so frontend playback/group lookup remains deterministic.
    audio_refs.sort(key=lambda item: (min(item["question_numbers"] or [0]), item["id"]))

    logger.info(
        "Auto-cut TOEIC audio source=%s raw_waves=%s skipped=%s outputs=%s confidence=%.3f",
        source.name,
        len(coarse_waves),
        len(alignment.skipped),
        len(audio_refs),
        alignment.confidence,
    )
    report(100, f"Đã xử lý xong {output_count} audio")
    return AutoCutResult(
        audios=tuple(audio_refs),
        metadata={
            **base_metadata,
            "status": "ready",
            "output_audio_count": len(audio_refs),
            "quiz_item_audio_count": 54,
        },
    )
