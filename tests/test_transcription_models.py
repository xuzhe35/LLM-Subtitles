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
        # Diarize now runs through the local-chunking + ThreadPoolExecutor
        # path (see DEFAULT_DIARIZE_MAX_SEGMENT_MS). For a 2.5s audio that
        # collapses to a single chunk worker, but the worker still calls
        # _extract_segment + _transcribe_diarize_file, so we stub the chunk
        # extraction to "succeed" by copying the source file to the chunk
        # path.
        import shutil

        def fake_extract_segment(src_path, start_ms, end_ms, dst_path):
            shutil.copyfile(src_path, dst_path)
            return True

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

            with patch.object(transcriber.audio_splitter, "get_audio_duration", return_value=2.5), \
                 patch.object(transcriber, "_extract_segment", side_effect=fake_extract_segment):
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


class TestOpenAIClientTimeoutAndRetries(unittest.TestCase):
    """
    The OpenAI SDK's default request timeout (600s) is too short for
    gpt-4o-transcribe-diarize on long audio, and `max_retries=0` (the old
    setting) means a single ReadTimeout kills the whole job. _build_openai_client
    pins both: timeout >= 30 min, max_retries >= 2.
    """

    def test_timeout_is_at_least_30_minutes(self):
        captured = {}

        def fake_openai(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace()

        with patch.object(youtube_subtitle_trans, "OpenAI", side_effect=fake_openai):
            youtube_subtitle_trans._build_openai_client("sk-test")

        self.assertIn("timeout", captured)
        self.assertGreaterEqual(
            float(captured["timeout"]),
            1800.0,
            f"OpenAI client timeout must be >= 1800s; got {captured.get('timeout')!r}",
        )

    def test_max_retries_is_nonzero(self):
        captured = {}

        def fake_openai(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace()

        with patch.object(youtube_subtitle_trans, "OpenAI", side_effect=fake_openai):
            youtube_subtitle_trans._build_openai_client("sk-test")

        self.assertIn("max_retries", captured)
        self.assertGreaterEqual(
            int(captured["max_retries"]),
            2,
            f"OpenAI max_retries must be >= 2 to ride out transient ReadTimeouts; "
            f"got {captured.get('max_retries')!r}",
        )

    def test_api_key_is_forwarded(self):
        captured = {}

        def fake_openai(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace()

        with patch.object(youtube_subtitle_trans, "OpenAI", side_effect=fake_openai):
            youtube_subtitle_trans._build_openai_client("sk-my-key")

        self.assertEqual(captured.get("api_key"), "sk-my-key")


class TestDiarizeLocalChunking(unittest.TestCase):
    """
    Long diarize jobs used to be sent as a single big upload to
    _transcribe_diarize_file, which routinely hit the OpenAI SDK request
    timeout. transcribe_audio() must now force local chunking for diarize:
    multiple parallel `_transcribe_single_segment_diarize` workers, each
    bounded by DEFAULT_DIARIZE_MAX_SEGMENT_MS.
    """

    def _make_audio(self, tmpdir):
        path = os.path.join(tmpdir, "audio.mp3")
        with open(path, "wb") as f:
            f.write(b"fake-audio-bytes")
        return path

    def test_diarize_splits_long_audio_into_short_chunks(self):
        worker_calls = []
        whole_file_calls = []

        def fake_worker(client, audio_file_path, seg_index, start_ms, end_ms, *args, **kwargs):
            worker_calls.append((seg_index, start_ms, end_ms))
            return (seg_index, [])

        def fake_whole_file(*args, **kwargs):
            whole_file_calls.append((args, kwargs))
            return []

        with tempfile.TemporaryDirectory() as tmp:
            audio_path = self._make_audio(tmp)
            # 30-minute audio: well past any single-request safe window.
            with patch.object(transcriber.audio_splitter, "get_audio_duration", return_value=1800.0), \
                 patch.object(transcriber, "_transcribe_single_segment_diarize", side_effect=fake_worker), \
                 patch.object(transcriber, "_transcribe_diarize_file", side_effect=fake_whole_file):
                result = transcriber.transcribe_audio(
                    client=MagicMock(),
                    audio_file_path=audio_path,
                    engine="gpt-4o-transcribe-diarize",
                    transcription_model=transcriber.TRANSCRIPTION_MODEL_OPENAI_GPT4O_DIARIZE,
                )

        self.assertIsNotNone(result, "transcribe_audio should not have returned None")
        self.assertEqual(
            whole_file_calls, [],
            "Diarize must not send the whole audio file in one request anymore.",
        )
        self.assertGreater(
            len(worker_calls), 1,
            "30-minute diarize audio should be split into multiple chunks.",
        )
        for _seg_index, start_ms, end_ms in worker_calls:
            chunk_duration_ms = end_ms - start_ms
            self.assertLessEqual(
                chunk_duration_ms,
                transcriber.DEFAULT_DIARIZE_MAX_SEGMENT_MS,
                f"Diarize chunk {chunk_duration_ms}ms exceeds the "
                f"{transcriber.DEFAULT_DIARIZE_MAX_SEGMENT_MS}ms cap.",
            )

    def test_diarize_default_chunk_cap_is_short_enough_for_timeout(self):
        # Defensive: if someone bumps DEFAULT_DIARIZE_MAX_SEGMENT_MS to a value
        # that's likely to blow the OpenAI client timeout again, fail loudly.
        # 8 minutes is the upper bound where diarize routinely fits inside a
        # 30-minute request budget with retries.
        self.assertLessEqual(
            transcriber.DEFAULT_DIARIZE_MAX_SEGMENT_MS,
            8 * 60 * 1000,
            "DEFAULT_DIARIZE_MAX_SEGMENT_MS is too high — diarize requests will time out again.",
        )


if __name__ == "__main__":
    unittest.main()
