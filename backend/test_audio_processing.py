import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audio_processing import prepare_web_audio
from toeic_audio_cutter import AutoCutResult


class FakeStore:
    def __init__(self, root: Path, state: dict) -> None:
        self.root = root
        self.state = state
        self.writes = 0

    def read(self, _job_id: str) -> dict:
        return self.state

    def audio_path(self, _job_id: str, audio_id: str) -> Path:
        return self.root / audio_id

    def write(self, _job_id: str, state: dict) -> None:
        self.state = state
        self.writes += 1


class AudioProcessingTests(unittest.TestCase):
    def test_empty_audio_list_does_not_require_ffmpeg(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FakeStore(Path(directory), {"audios": []})
            with patch("audio_processing.shutil.which", return_value=None):
                self.assertEqual(prepare_web_audio(store, "job"), 0)

    def test_compliant_ogg_is_not_reencoded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.ogg"
            source.write_bytes(b"source")
            store = FakeStore(
                Path(directory),
                {"audios": [{"id": source.name, "part": "full"}]},
            )
            with (
                patch("audio_processing.shutil.which", return_value="/usr/bin/tool"),
                patch("audio_processing._probe", return_value=("opus", 96_000)),
                patch("audio_processing.auto_cut_full_audio", return_value=None),
                patch("audio_processing.subprocess.run") as run,
            ):
                self.assertEqual(prepare_web_audio(store, "job"), 0)
            run.assert_not_called()
            self.assertEqual(store.writes, 0)

    def test_high_bitrate_source_is_converted_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            source.write_bytes(b"original-kept")
            store = FakeStore(
                Path(directory),
                {
                    "audios": [
                        {
                            "id": source.name,
                            "part": "full",
                            "filename": "listening.wav",
                        }
                    ]
                },
            )

            def fake_ffmpeg(arguments, **_kwargs) -> None:
                Path(arguments[-1]).write_bytes(b"web-ogg")

            with (
                patch("audio_processing.shutil.which", return_value="/usr/bin/tool"),
                patch("audio_processing._probe", return_value=("pcm_s16le", 700_000)),
                patch("audio_processing.auto_cut_full_audio", return_value=None),
                patch("audio_processing.subprocess.run", side_effect=fake_ffmpeg) as run,
            ):
                self.assertEqual(prepare_web_audio(store, "job"), 1)

            audio = store.state["audios"][0]
            self.assertEqual(audio["content_type"], "audio/ogg")
            self.assertEqual(audio["source_original_id"], "source.wav")
            self.assertTrue(audio["id"].endswith(".ogg"))
            self.assertEqual((Path(directory) / audio["id"]).read_bytes(), b"web-ogg")
            command = run.call_args.args[0]
            self.assertEqual(command[command.index("-f") + 1], "ogg")
            self.assertEqual(source.read_bytes(), b"original-kept")
            self.assertEqual(store.writes, 1)

    def test_full_audio_is_replaced_by_structured_auto_cut_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.ogg"
            source.write_bytes(b"source")
            generated = {
                "id": "q001.ogg",
                "url": "/api/extractions/job/audio/q001.ogg",
                "filename": "q001.ogg",
                "content_type": "audio/ogg",
                "size": 10,
                "part": "part_1",
                "scope": "question",
                "question_numbers": [1],
                "group_id": None,
            }
            store = FakeStore(
                Path(directory),
                {"audios": [{"id": source.name, "part": "full"}], "metadata": {}},
            )
            result = AutoCutResult(
                audios=(generated,),
                metadata={"status": "ready", "raw_wave_count": 134},
            )
            with (
                patch("audio_processing.shutil.which", return_value="/usr/bin/tool"),
                patch("audio_processing._probe", return_value=("opus", 96_000)),
                patch("audio_processing.auto_cut_full_audio", return_value=result),
            ):
                self.assertEqual(prepare_web_audio(store, "job"), 1)

            self.assertEqual(store.state["audios"], [generated])
            self.assertIsNone(store.state["audio"])
            self.assertEqual(store.state["metadata"]["audio_autocut"]["status"], "ready")

    def test_full_audio_is_cut_before_full_recording_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp3"
            source.write_bytes(b"source")
            full = {"id": source.name, "part": "full", "filename": "ETS.mp3"}
            prepared = {
                "id": "q001.ogg",
                "url": "/api/extractions/job/audio/q001.ogg",
                "filename": "ETS.q001.ogg",
                "content_type": "audio/ogg",
                "size": 10,
                "part": "part_1",
                "scope": "question",
                "question_numbers": [1],
                "group_id": None,
            }
            store = FakeStore(Path(directory), {"audios": [full], "metadata": {}})
            result = AutoCutResult(
                audios=(prepared,), metadata={"status": "ready"}
            )
            with (
                patch("audio_processing.shutil.which", return_value="/usr/bin/tool"),
                patch("audio_processing.auto_cut_full_audio", return_value=result) as cut,
                patch("audio_processing.subprocess.run") as run,
            ):
                self.assertEqual(prepare_web_audio(store, "job"), 1)

            cut.assert_called_once()
            run.assert_not_called()
            self.assertEqual(store.state["audios"], [prepared])
            self.assertIsNone(store.state["audio"])
            self.assertEqual(
                store.state["metadata"]["web_audio"]["strategy"],
                "direct-cut-from-source",
            )
            self.assertEqual(store.writes, 1)

    def test_unsafe_alignment_keeps_full_audio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.ogg"
            source.write_bytes(b"source")
            full = {"id": source.name, "part": "full"}
            store = FakeStore(Path(directory), {"audios": [full], "metadata": {}})
            result = AutoCutResult(
                audios=(),
                metadata={"status": "fallback", "reason": "low_alignment_confidence"},
            )
            with (
                patch("audio_processing.shutil.which", return_value="/usr/bin/tool"),
                patch("audio_processing._probe", return_value=("opus", 96_000)),
                patch("audio_processing.auto_cut_full_audio", return_value=result),
            ):
                self.assertEqual(prepare_web_audio(store, "job"), 0)

            self.assertEqual(store.state["audios"], [full])
            self.assertEqual(store.state["audio"], full)
            self.assertEqual(store.state["metadata"]["audio_autocut"]["status"], "fallback")

    def test_full_audio_reports_real_autocut_phase_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.ogg"
            source.write_bytes(b"source")
            store = FakeStore(
                Path(directory),
                {"audios": [{"id": source.name, "part": "full"}], "metadata": {}},
            )
            events: list[tuple[int, str]] = []

            def fake_auto_cut(*_args, progress=None, **_kwargs):
                assert progress is not None
                progress(50, "Đang cắt audio 27/55")
                progress(100, "Đã xử lý xong 55 audio")
                return AutoCutResult(
                    audios=(),
                    metadata={"status": "fallback", "reason": "fixture"},
                )

            with (
                patch("audio_processing.shutil.which", return_value="/usr/bin/tool"),
                patch("audio_processing._probe", return_value=("opus", 96_000)),
                patch("audio_processing.auto_cut_full_audio", side_effect=fake_auto_cut),
            ):
                prepare_web_audio(
                    store,
                    "job",
                    progress=lambda percent, stage: events.append((percent, stage)),
                )

            self.assertIn((50, "Đang cắt audio 27/55"), events)
            self.assertEqual(events[-1], (100, "Đã giữ Audio Full đã tối ưu"))


if __name__ == "__main__":
    unittest.main()
