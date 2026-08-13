import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from toeic_audio_cutter import (
    AudioInfo,
    AutoCutResult,
    Wave,
    align_waves,
    auto_cut_full_audio,
    build_output_spans,
    detect_silence_waves,
    expected_atomic_roles,
    parse_silence_waves,
    refine_suspicious_waves,
    suspicious_matches,
    _cut_workers,
)


ROLE_DURATIONS = {
    "part1_question": 25.5,
    "part2_question": 20.0,
    "passage": 46.0,
    "prompt": 12.7,
}

# Coarse ``silencedetect`` durations measured from File_TEST/Test 01.mp3.  Keep
# the lightweight timing signature in git; the 44 MB binary remains a manual
# integration fixture rather than making every CI job decode a full CD.
TEST01_COARSE_DURATIONS = [
    6.531, 58.421, 30.136, 26.559, 25.656, 6.672, 24.946, 27.183,
    27.593, 27.122, 7.24, 47.005, 18.519, 19.889, 20.131, 19.488,
    17.784, 20.251, 19.801, 21.113, 19.435, 20.748, 20.45, 19.596,
    19.764, 21.62, 21.369, 19.732, 20.009, 20.403, 21.363, 21.805,
    21.723, 22.17, 23.59, 22.978, 65.303, 13.067, 12.051, 12.26,
    36.775, 13.225, 12.93, 12.682, 43.525, 11.861, 12.214, 12.311,
    46.741, 11.66, 12.482, 13.352, 6.69, 38.608, 11.718, 13.404,
    12.286, 44.705, 12.21, 12.185, 12.618, 39.007, 11.918, 12.204,
    11.989, 49.27, 11.863, 13.423, 14.08, 51.126, 12.804, 11.479,
    12.397, 40.029, 11.932, 15.159, 12.925, 45.08, 18.423, 12.078,
    12.378, 6.665, 49.525, 13.311, 18.42, 12.63, 42.123, 11.613,
    13.206, 20.151, 66.227, 12.491, 12.444, 11.718, 33.616, 11.426,
    12.538, 15.168, 41.711, 11.92, 13.133, 14.605, 38.649, 11.167,
    14.912, 12.914, 6.664, 47.942, 12.674, 12.613, 12.314, 44.174,
    11.951, 14.618, 12.919, 45.921, 12.603, 11.848, 11.833, 38.831,
    13.037, 12.289, 15.056, 40.257, 12.537, 12.056, 18.043, 40.285,
    11.788, 18.402, 13.052, 2.62,
]


def waves_from_durations(durations: list[float]) -> tuple[Wave, ...]:
    cursor = 0.0
    waves: list[Wave] = []
    for duration in durations:
        waves.append(Wave(cursor, cursor + duration))
        cursor += duration
    return tuple(waves)


def realistic_134_waves() -> tuple[Wave, ...]:
    # Five opening direction waves plus six transition waves gives the 11
    # extras observed by the user over the 123 All+ roles.
    durations = [2.0, 70.0, 20.0, 15.0, 4.0]
    extras = {
        2: [3.0],
        6: [4.0, 30.0],
        31: [5.0, 35.0],
        83: [28.0],
    }
    for index, role in enumerate(expected_atomic_roles()):
        durations.extend(extras.get(index, []))
        durations.append(ROLE_DURATIONS[role.kind])
    assert len(durations) == 134
    return waves_from_durations(durations)


class ToeicAudioCutterTests(unittest.TestCase):
    def test_reference_layout_has_123_atomic_roles(self) -> None:
        roles = expected_atomic_roles()
        self.assertEqual(len(roles), 123)
        self.assertEqual(sum(role.kind == "passage" for role in roles), 23)
        self.assertEqual(sum(role.kind == "prompt" for role in roles), 69)

    def test_silence_end_is_the_wave_boundary(self) -> None:
        stderr = """
        [silencedetect] silence_start: 8.0
        [silencedetect] silence_end: 9.5 | silence_duration: 1.5
        [silencedetect] silence_start: 18.0
        [silencedetect] silence_end: 20.0 | silence_duration: 2.0
        """
        waves = parse_silence_waves(stderr, 30.0)
        self.assertEqual(waves, (Wave(0.0, 9.5), Wave(9.5, 20.0), Wave(20.0, 30.0)))

    def test_ffmpeg_threshold_matches_60000_samples(self) -> None:
        completed = type("Completed", (), {"stderr": ""})()
        with patch("toeic_audio_cutter.subprocess.run", return_value=completed) as run:
            detect_silence_waves(
                Path("input.mp3"),
                AudioInfo(sample_rate=44_100, duration=60.0),
                ffmpeg="ffmpeg",
            )
        command = run.call_args.args[0]
        filter_value = command[command.index("-af") + 1]
        self.assertIn("noise=-40dB", filter_value)
        self.assertIn("d=1.360544218", filter_value)

    def test_audio_cut_worker_count_is_bounded(self) -> None:
        with patch.dict(os.environ, {"AUDIO_CUT_WORKERS": "99"}):
            self.assertEqual(_cut_workers(55), 3)
        with patch.dict(os.environ, {"AUDIO_CUT_WORKERS": "0"}):
            self.assertEqual(_cut_workers(55), 1)

    def test_134_wave_profile_removes_extras_and_keeps_toeic_order(self) -> None:
        waves = realistic_134_waves()
        alignment = align_waves(waves)
        self.assertIsNotNone(alignment)
        assert alignment is not None
        self.assertEqual(alignment.profile, "jinjor-134")
        self.assertGreaterEqual(alignment.confidence, 0.70)
        self.assertEqual(len(alignment.matches), 123)
        self.assertEqual(len(alignment.skipped), 11)
        self.assertEqual(alignment.matches[0].first_wave, 5)
        mapped = {item.role.key: item.first_wave + 1 for item in alignment.matches}
        self.assertEqual((mapped["q1"], mapped["q2"], mapped["q3"]), (6, 7, 9))
        self.assertEqual(mapped["q7"], 15)
        self.assertIn(8, {item.wave_index + 1 for item in alignment.skipped})

    def test_real_test01_coarse_signature_anchors_every_part(self) -> None:
        waves = waves_from_durations(TEST01_COARSE_DURATIONS)
        alignment = align_waves(waves)
        assert alignment is not None
        mapped = {item.role.key: item.first_wave + 1 for item in alignment.matches}
        self.assertGreater(alignment.confidence, 0.95)
        self.assertEqual(
            (mapped["q1"], mapped["q2"], mapped["q3"], mapped["q6"]),
            (4, 5, 7, 10),
        )
        self.assertEqual(mapped["q7"], 12)
        self.assertEqual(mapped["q31"], 36)
        self.assertEqual(mapped["g32-passage"], 37)
        self.assertEqual(mapped["q34-prompt"], 40)
        self.assertEqual(
            [item.role.key for item in suspicious_matches(alignment)],
            ["q7"],
        )

    def test_part_3_and_4_assets_end_after_the_third_prompt(self) -> None:
        waves = realistic_134_waves()
        alignment = align_waves(waves)
        assert alignment is not None
        spans = build_output_spans(alignment, waves)
        quiz_spans = [span for span in spans if span.scope != "part"]
        self.assertEqual(len(quiz_spans), 54)
        question_7 = next(span for span in spans if span.question_numbers == (7,))
        transition_before_part_2 = [
            item for item in alignment.skipped if item.before_role == 6
        ]
        self.assertEqual(
            question_7.start,
            waves[transition_before_part_2[0].wave_index].start,
        )
        group = next(span for span in spans if span.question_numbers == (32, 33, 34))
        third_prompt = next(
            item for item in alignment.matches if item.role.key == "q34-prompt"
        )
        next_passage = next(
            item for item in alignment.matches if item.role.key == "g35-passage"
        )
        self.assertEqual(group.end, third_prompt.end)
        self.assertLessEqual(group.end, next_passage.start)

    def test_short_silence_refinement_recovers_two_merged_part_2_questions(self) -> None:
        fine_waves = realistic_134_waves()
        original = align_waves(fine_waves)
        assert original is not None
        question_7 = next(item for item in original.matches if item.role.key == "q7")
        # Simulate the reference 1.36-second pass missing the q7/q8 pause.
        points = [0.0, *(wave.end for wave in fine_waves)]
        points = [point for point in points if point != question_7.end]
        coarse_waves = tuple(
            Wave(start, end) for start, end in zip(points, points[1:])
        )
        coarse = align_waves(coarse_waves)
        assert coarse is not None
        suspicious_keys = [item.role.key for item in suspicious_matches(coarse)]
        self.assertEqual(len(suspicious_keys), 1)
        self.assertIn(suspicious_keys[0], {"q7", "q8"})

        refined_waves = refine_suspicious_waves(coarse_waves, fine_waves, coarse)
        refined = align_waves(
            refined_waves,
            preferred_prefix=coarse.matches[0].first_wave,
            profile_wave_count=len(coarse_waves),
        )
        assert refined is not None
        self.assertEqual(suspicious_matches(refined), ())
        recovered = {
            item.role.key: item.end - item.start
            for item in refined.matches
            if item.role.key in {"q7", "q8"}
        }
        self.assertEqual(recovered, {"q7": 20.0, "q8": 20.0})

    def test_low_structure_confidence_does_not_publish_wrong_clips(self) -> None:
        waves = waves_from_durations([6.5] * 134)
        alignment = align_waves(waves)
        self.assertIsNotNone(alignment)
        assert alignment is not None
        self.assertLess(alignment.confidence, 0.70)

    def test_successful_cut_returns_54_quiz_assets_and_direction(self) -> None:
        waves = realistic_134_waves()
        progress_events: list[tuple[int, str]] = []
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "full.mp3"
            source.write_bytes(b"source")

            def fake_cut(_ffmpeg: str, _source: Path, destination: Path, _span) -> None:
                destination.write_bytes(b"ogg")

            with (
                patch(
                    "toeic_audio_cutter.probe_audio",
                    return_value=AudioInfo(44_100, waves[-1].end),
                ),
                patch("toeic_audio_cutter.detect_silence_waves", return_value=waves),
                patch("toeic_audio_cutter._cut_span", side_effect=fake_cut),
            ):
                result = auto_cut_full_audio(
                    source,
                    {"id": "full.mp3", "filename": "ETS Test.mp3"},
                    job_id="job-1",
                    ffmpeg="ffmpeg",
                    ffprobe="ffprobe",
                    progress=lambda percent, stage: progress_events.append(
                        (percent, stage)
                    ),
                )

        self.assertIsInstance(result, AutoCutResult)
        assert result is not None
        self.assertEqual(result.metadata["status"], "ready")
        self.assertEqual(result.metadata["raw_wave_count"], 134)
        self.assertEqual(len(result.audios), 55)
        quiz_audio = [audio for audio in result.audios if audio["scope"] != "part"]
        self.assertEqual(len(quiz_audio), 54)
        self.assertTrue(all(audio["id"].endswith(".ogg") for audio in result.audios))
        self.assertTrue(all(audio["content_type"] == "audio/ogg" for audio in result.audios))
        group = next(audio for audio in result.audios if audio["question_numbers"] == [32, 33, 34])
        self.assertEqual(group["scope"], "group")
        self.assertEqual(group["part"], "part_3")
        self.assertEqual(progress_events[0], (1, "Kiểm tra Audio Full"))
        self.assertEqual(progress_events[-1], (100, "Đã xử lý xong 55 audio"))
        self.assertTrue(
            all(
                current[0] <= following[0]
                for current, following in zip(progress_events, progress_events[1:])
            )
        )
        self.assertTrue(any(stage == "Đang cắt audio 55/55" for _, stage in progress_events))


if __name__ == "__main__":
    unittest.main()
