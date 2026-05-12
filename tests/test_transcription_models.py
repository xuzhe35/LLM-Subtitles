import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import youtube_subtitle_trans
from utils import transcriber


class TestTranscriptionModelResolution(unittest.TestCase):
    def test_explicit_engine_choice_wins_for_thai_source(self):
        self.assertEqual(
            transcriber.resolve_transcription_model("whisper", "th"),
            transcriber.TRANSCRIPTION_MODEL_OPENAI,
        )
        self.assertEqual(
            transcriber.resolve_transcription_model("google", "th"),
            transcriber.TRANSCRIPTION_MODEL_GOOGLE,
        )
        self.assertEqual(
            transcriber.resolve_transcription_model("typhoon", "en"),
            transcriber.TRANSCRIPTION_MODEL_TYPHOON,
        )
        self.assertEqual(
            transcriber.resolve_transcription_model("gpt-4o-transcribe-diarize", "th"),
            transcriber.TRANSCRIPTION_MODEL_OPENAI_GPT4O_DIARIZE,
        )

    def test_auto_engine_still_routes_thai_source_to_typhoon(self):
        self.assertEqual(
            transcriber.resolve_transcription_model("auto", "th"),
            transcriber.TRANSCRIPTION_MODEL_TYPHOON,
        )

    def test_non_thai_source_keeps_existing_engine_behavior(self):
        self.assertEqual(
            transcriber.resolve_transcription_model("whisper", "en"),
            transcriber.TRANSCRIPTION_MODEL_OPENAI,
        )
        self.assertEqual(
            transcriber.resolve_transcription_model("google", "en"),
            transcriber.TRANSCRIPTION_MODEL_GOOGLE,
        )


class TestTyphoonTranscription(unittest.TestCase):
    def test_torch_device_selection_prefers_cuda_then_mps_then_cpu(self):
        torch_module = MagicMock()
        torch_module.bfloat16 = "bfloat16"
        torch_module.float16 = "float16"
        torch_module.float32 = "float32"

        torch_module.cuda.is_available.return_value = True
        self.assertEqual(("cuda:0", "bfloat16"), transcriber._select_torch_device(torch_module))

        torch_module.cuda.is_available.return_value = False
        torch_module.backends.mps.is_available.return_value = True
        self.assertEqual(("mps", "float16"), transcriber._select_torch_device(torch_module))

        torch_module.backends.mps.is_available.return_value = False
        self.assertEqual(("cpu", "float32"), transcriber._select_torch_device(torch_module))

    def test_typhoon_pipeline_chunks_are_normalized_to_segments(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            audio_path = os.path.join(tmp_dir, "audio.wav")
            with open(audio_path, "wb") as f:
                f.write(b"fake audio")

            fake_pipe = MagicMock(return_value={
                "text": "sawatdee lok",
                "chunks": [
                    {"timestamp": (0.0, 1.2), "text": " sawatdee "},
                    {"timestamp": (1.2, 2.5), "text": "lok"},
                ],
            })

            with patch.object(transcriber, "_get_typhoon_pipeline", return_value=fake_pipe):
                result = transcriber.transcribe_audio(
                    MagicMock(),
                    audio_path,
                    source_lang="th",
                    transcription_model=transcriber.TRANSCRIPTION_MODEL_TYPHOON,
                )

        self.assertEqual([
            {"start": 0.0, "end": 1.2, "text": "sawatdee"},
            {"start": 1.2, "end": 2.5, "text": "lok"},
        ], result.segments)
        fake_pipe.assert_called_once_with(
            audio_path,
            generate_kwargs={"language": "thai"},
        )

    def test_missing_typhoon_dependencies_return_clear_error_without_openai_call(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            audio_path = os.path.join(tmp_dir, "audio.wav")
            with open(audio_path, "wb") as f:
                f.write(b"fake audio")

            client = MagicMock()
            logs = []
            missing_error = RuntimeError(
                "Missing optional dependencies for Typhoon ASR. "
                "Install with: pip install transformers accelerate huggingface_hub"
            )

            with patch.object(transcriber, "_get_typhoon_pipeline", side_effect=missing_error):
                result = transcriber.transcribe_audio(
                    client,
                    audio_path,
                    source_lang="th",
                    transcription_model=transcriber.TRANSCRIPTION_MODEL_TYPHOON,
                    progress_callback=logs.append,
                )

        self.assertIsNone(result)
        self.assertFalse(client.audio.transcriptions.create.called)
        self.assertTrue(any("transformers accelerate huggingface_hub" in msg for msg in logs))


class TestOpenAIDiarizeTranscription(unittest.TestCase):
    def test_diarize_segments_are_normalized_and_prompt_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            audio_path = os.path.join(tmp_dir, "audio.wav")
            with open(audio_path, "wb") as f:
                f.write(b"fake audio")

            client = MagicMock()
            segment_1 = SimpleNamespace(start=0.0, end=1.25, text="hello")
            segment_2 = SimpleNamespace(start=1.25, end=2.5, text="world")
            transcript = SimpleNamespace(segments=[segment_1, segment_2], text="hello world")
            client.audio.transcriptions.create.return_value = transcript
            logs = []

            with patch.object(transcriber.audio_splitter, "get_audio_duration", return_value=2.5):
                result = transcriber.transcribe_audio(
                    client,
                    audio_path,
                    source_lang="en",
                    engine="gpt-4o-transcribe-diarize",
                    whisper_prompt="this should be ignored",
                    progress_callback=logs.append,
                )

        self.assertEqual([
            {"start": 0.0, "end": 1.25, "text": "hello"},
            {"start": 1.25, "end": 2.5, "text": "world"},
        ], result.segments)
        kwargs = client.audio.transcriptions.create.call_args.kwargs
        self.assertEqual(transcriber.TRANSCRIPTION_MODEL_OPENAI_GPT4O_DIARIZE, kwargs["model"])
        self.assertEqual("diarized_json", kwargs["response_format"])
        self.assertEqual("auto", kwargs["chunking_strategy"])
        self.assertNotIn("prompt", kwargs)
        self.assertTrue(any("Whisper Prompt ignored" in msg for msg in logs))


class TestProcessVideoTyphoonRouting(unittest.TestCase):
    def test_explicit_typhoon_route_does_not_require_google_api_key(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_cwd = os.getcwd()
            os.chdir(tmp_dir)
            try:
                logs = []
                transcribe_kwargs = {}

                def fake_download_audio(url, output_path, progress_hook=None):
                    path = output_path + ".mp3"
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(b"fake audio")
                    return path

                def fake_transcribe_audio(*args, **kwargs):
                    transcribe_kwargs.update(kwargs)

                    class TranscriptResult:
                        segments = [
                            {"start": 0.0, "end": 1.0, "text": "sawatdee"},
                        ]

                    return TranscriptResult()

                with patch.object(youtube_subtitle_trans, "load_config", return_value={"openai_api_key": "sk-test"}), \
                     patch.object(youtube_subtitle_trans, "OpenAI", return_value=MagicMock()), \
                     patch.object(youtube_subtitle_trans.downloader, "get_video_info", return_value={"title": "Thai Demo", "subtitles": {}}), \
                     patch.object(youtube_subtitle_trans.downloader, "download_audio", side_effect=fake_download_audio), \
                     patch.object(youtube_subtitle_trans.transcriber, "transcribe_audio", side_effect=fake_transcribe_audio), \
                     patch.object(youtube_subtitle_trans.translator, "translate_segments", return_value=[
                         {"start": 0.0, "end": 1.0, "text": "hello"},
                     ]):
                    youtube_subtitle_trans.process_video(
                        "https://example.com/video",
                        lang="Simplified Chinese",
                        model="gpt-4o",
                        force_audio=True,
                        source_lang="th",
                        engine="typhoon",
                        progress_callback=logs.append,
                    )
            finally:
                os.chdir(old_cwd)

        self.assertEqual(
            transcriber.TRANSCRIPTION_MODEL_TYPHOON,
            transcribe_kwargs["transcription_model"],
        )
        self.assertFalse(any("Google engine requires" in msg for msg in logs))
        self.assertTrue(any("Resolved transcription model: typhoon-whisper-large-v3" in msg for msg in logs))

    def test_explicit_gpt4o_diarize_route_is_respected(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_cwd = os.getcwd()
            os.chdir(tmp_dir)
            try:
                logs = []
                transcribe_kwargs = {}

                def fake_download_audio(url, output_path, progress_hook=None):
                    path = output_path + ".mp3"
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(b"fake audio")
                    return path

                def fake_transcribe_audio(*args, **kwargs):
                    transcribe_kwargs.update(kwargs)

                    class TranscriptResult:
                        segments = [
                            {"start": 0.0, "end": 1.0, "text": "hello"},
                        ]

                    return TranscriptResult()

                with patch.object(youtube_subtitle_trans, "load_config", return_value={"openai_api_key": "sk-test"}), \
                     patch.object(youtube_subtitle_trans, "OpenAI", return_value=MagicMock()), \
                     patch.object(youtube_subtitle_trans.downloader, "get_video_info", return_value={"title": "GPT Demo", "subtitles": {}}), \
                     patch.object(youtube_subtitle_trans.downloader, "download_audio", side_effect=fake_download_audio), \
                     patch.object(youtube_subtitle_trans.transcriber, "transcribe_audio", side_effect=fake_transcribe_audio), \
                     patch.object(youtube_subtitle_trans.translator, "translate_segments", return_value=[
                         {"start": 0.0, "end": 1.0, "text": "ni hao"},
                     ]):
                    youtube_subtitle_trans.process_video(
                        "https://example.com/video",
                        lang="Simplified Chinese",
                        model="gpt-4o",
                        force_audio=True,
                        source_lang="th",
                        engine="gpt-4o-transcribe-diarize",
                        progress_callback=logs.append,
                    )
            finally:
                os.chdir(old_cwd)

        self.assertEqual(
            transcriber.TRANSCRIPTION_MODEL_OPENAI_GPT4O_DIARIZE,
            transcribe_kwargs["transcription_model"],
        )
        self.assertTrue(any("Resolved transcription model: gpt-4o-transcribe-diarize" in msg for msg in logs))


if __name__ == "__main__":
    unittest.main()
