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
            transcriber.resolve_transcription_model("gpt-4o-transcribe-diarize", "th"),
            transcriber.TRANSCRIPTION_MODEL_OPENAI_GPT4O_DIARIZE,
        )

    def test_auto_engine_routes_thai_source_to_openai(self):
        self.assertEqual(
            transcriber.resolve_transcription_model("auto", "th"),
            transcriber.TRANSCRIPTION_MODEL_OPENAI,
        )

    def test_unknown_engine_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported transcription engine"):
            transcriber.resolve_transcription_model("removed-local-engine", "th")

    def test_non_thai_source_keeps_existing_engine_behavior(self):
        self.assertEqual(
            transcriber.resolve_transcription_model("whisper", "en"),
            transcriber.TRANSCRIPTION_MODEL_OPENAI,
        )
        self.assertEqual(
            transcriber.resolve_transcription_model("google", "en"),
            transcriber.TRANSCRIPTION_MODEL_GOOGLE,
        )


class TestTempFileCleanup(unittest.TestCase):
    """
    Workers extract per-segment audio chunks to disk. These must be cleaned up
    on success, on transcription failure, and even when the worker itself raises
    an unexpected exception — otherwise long runs leak files into the user's
    output directory.
    """

    def _fake_extract(self, chunk_path, *_args, **_kwargs):
        # Pretend ffmpeg ran: write a stub file at chunk_path.
        os.makedirs(os.path.dirname(chunk_path), exist_ok=True)
        with open(chunk_path, "wb") as f:
            f.write(b"fake chunk")
        return True

    def test_whisper_worker_removes_chunk_on_success(self):
        with tempfile.TemporaryDirectory() as work_dir, tempfile.TemporaryDirectory() as src_dir:
            audio_path = os.path.join(src_dir, "audio.m4a")
            open(audio_path, "wb").close()

            client = MagicMock()
            client.audio.transcriptions.create.return_value = SimpleNamespace(
                segments=[SimpleNamespace(start=0.0, end=1.0, text="hi")]
            )

            with patch.object(transcriber, "_extract_segment",
                              side_effect=lambda audio, s, e, out: self._fake_extract(out)):
                idx, segs = transcriber._transcribe_single_segment(
                    client, audio_path, 0, 0, 1000, work_dir=work_dir,
                )

            self.assertEqual(idx, 0)
            self.assertEqual(len(segs), 1)
            # Chunk file must be gone.
            self.assertEqual(os.listdir(work_dir), [])

    def test_whisper_worker_removes_chunk_on_api_exception(self):
        with tempfile.TemporaryDirectory() as work_dir, tempfile.TemporaryDirectory() as src_dir:
            audio_path = os.path.join(src_dir, "audio.m4a")
            open(audio_path, "wb").close()

            client = MagicMock()
            client.audio.transcriptions.create.side_effect = RuntimeError("rate limit")

            with patch.object(transcriber, "_extract_segment",
                              side_effect=lambda audio, s, e, out: self._fake_extract(out)):
                idx, segs = transcriber._transcribe_single_segment(
                    client, audio_path, 3, 0, 1000, work_dir=work_dir,
                )

            self.assertEqual(idx, 3)
            self.assertEqual(segs, [])
            self.assertEqual(os.listdir(work_dir), [],
                             "chunk file must be removed even when the API call raises")

    def test_whisper_worker_removes_partial_chunk_when_extract_returns_false(self):
        """
        If _extract_segment returns False but ffmpeg already wrote a partial
        file (e.g. it was killed midway), the chunk must still be cleaned —
        previously the early return skipped cleanup.
        """
        with tempfile.TemporaryDirectory() as work_dir, tempfile.TemporaryDirectory() as src_dir:
            audio_path = os.path.join(src_dir, "audio.m4a")
            open(audio_path, "wb").close()

            def partial_extract(audio, s, e, out):
                # Write a partial file, then claim extraction failed.
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with open(out, "wb") as f:
                    f.write(b"partial")
                return False

            client = MagicMock()
            with patch.object(transcriber, "_extract_segment", side_effect=partial_extract):
                idx, segs = transcriber._transcribe_single_segment(
                    client, audio_path, 7, 0, 1000, work_dir=work_dir,
                )

            self.assertEqual(idx, 7)
            self.assertEqual(segs, [])
            self.assertEqual(os.listdir(work_dir), [],
                             "partial chunk file from failed extraction must be removed")

    def test_diarize_worker_removes_chunk_on_exception(self):
        with tempfile.TemporaryDirectory() as work_dir, tempfile.TemporaryDirectory() as src_dir:
            audio_path = os.path.join(src_dir, "audio.m4a")
            open(audio_path, "wb").close()

            client = MagicMock()
            client.audio.transcriptions.create.side_effect = RuntimeError("API down")

            with patch.object(transcriber, "_extract_segment",
                              side_effect=lambda audio, s, e, out: self._fake_extract(out)):
                idx, segs = transcriber._transcribe_single_segment_diarize(
                    client, audio_path, 0, 0, 1000, work_dir=work_dir,
                )

            self.assertEqual(segs, [])
            self.assertEqual(os.listdir(work_dir), [])

    def test_transcribe_audio_cleans_work_dir_on_unexpected_exception(self):
        """
        The top-level `try/finally` in transcribe_audio must wipe the scratch
        dir even when an exception escapes the worker logic — e.g. when the
        resolve_transcription_model call raises (unknown engine).
        """
        with tempfile.TemporaryDirectory() as src_dir:
            audio_path = os.path.join(src_dir, "audio.m4a")
            open(audio_path, "wb").close()

            created_dirs = []
            real_mkdtemp = tempfile.mkdtemp

            def tracking_mkdtemp(*a, **kw):
                d = real_mkdtemp(*a, **kw)
                created_dirs.append(d)
                # Drop a file into the work dir so we can later verify the dir
                # (and its contents) is gone.
                with open(os.path.join(d, "leak.txt"), "wb") as f:
                    f.write(b"leak")
                return d

            with patch.object(transcriber.tempfile, "mkdtemp", side_effect=tracking_mkdtemp), \
                 patch.object(transcriber, "resolve_transcription_model",
                              side_effect=ValueError("boom")):
                result = transcriber.transcribe_audio(
                    MagicMock(), audio_path, progress_callback=lambda _m: None,
                )

        self.assertIsNone(result)
        self.assertEqual(len(created_dirs), 1)
        self.assertFalse(os.path.exists(created_dirs[0]),
                         "scratch dir must be removed in the top-level finally")


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


class TestResolveOutputDir(unittest.TestCase):
    def test_default_is_project_root_output(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LLM_SUBTITLES_OUTPUT_DIR", None)
            result = youtube_subtitle_trans.resolve_output_dir({})
        self.assertEqual(result, youtube_subtitle_trans.DEFAULT_OUTPUT_DIR)
        self.assertTrue(os.path.isabs(result))

    def test_explicit_absolute_overrides(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = youtube_subtitle_trans.resolve_output_dir({}, explicit=tmp_dir)
        self.assertEqual(result, os.path.abspath(tmp_dir))

    def test_explicit_relative_resolves_to_project_root(self):
        result = youtube_subtitle_trans.resolve_output_dir({}, explicit="custom_out")
        self.assertEqual(result, os.path.join(youtube_subtitle_trans.PROJECT_ROOT, "custom_out"))

    def test_env_var_takes_precedence_over_config(self):
        with tempfile.TemporaryDirectory() as tmp_dir, \
             patch.dict(os.environ, {"LLM_SUBTITLES_OUTPUT_DIR": tmp_dir}):
            result = youtube_subtitle_trans.resolve_output_dir({"output_dir": "/should/not/be/used"})
        self.assertEqual(result, os.path.abspath(tmp_dir))

    def test_config_used_when_no_env_or_explicit(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ.pop("LLM_SUBTITLES_OUTPUT_DIR", None)
            result = youtube_subtitle_trans.resolve_output_dir({"output_dir": tmp_dir})
        self.assertEqual(result, os.path.abspath(tmp_dir))


class TestProcessVideoTranscriptionRouting(unittest.TestCase):
    def test_explicit_gpt4o_diarize_route_is_respected(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
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
                    output_dir=tmp_dir,
                )

        self.assertEqual(
            transcriber.TRANSCRIPTION_MODEL_OPENAI_GPT4O_DIARIZE,
            transcribe_kwargs["transcription_model"],
        )
        self.assertTrue(any("Resolved transcription model: gpt-4o-transcribe-diarize" in msg for msg in logs))


class TestProcessVideoAudioEnhancement(unittest.TestCase):
    def _run_process_with_audio_enhancement(self, enhance_audio=False, enhance_mode="mild",
                                            enhancer_side_effect=None):
        tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_dir.cleanup)
        logs = []
        captured = {}

        def fake_download_audio(url, output_path, progress_hook=None):
            path = output_path + ".mp3"
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"fake audio")
            captured["raw_audio_path"] = path
            return path

        def fake_transcribe_audio(*args, **kwargs):
            captured["transcribe_audio_path"] = args[1]

            class TranscriptResult:
                segments = [
                    {"start": 0.0, "end": 1.0, "text": "hello"},
                ]

            return TranscriptResult()

        enhancer = MagicMock()
        if enhancer_side_effect is not None:
            enhancer.side_effect = enhancer_side_effect
        else:
            enhancer.side_effect = lambda input_path, output_path, **_kwargs: output_path

        with patch.object(youtube_subtitle_trans, "load_config", return_value={"openai_api_key": "sk-test"}), \
             patch.object(youtube_subtitle_trans, "OpenAI", return_value=MagicMock()), \
             patch.object(youtube_subtitle_trans.downloader, "get_video_info", return_value={"title": "Noisy Thai", "subtitles": {}}), \
             patch.object(youtube_subtitle_trans.downloader, "download_audio", side_effect=fake_download_audio), \
             patch.object(youtube_subtitle_trans.audio_enhancer, "enhance_audio", enhancer), \
             patch.object(youtube_subtitle_trans.transcriber, "transcribe_audio", side_effect=fake_transcribe_audio), \
             patch.object(youtube_subtitle_trans.translator, "translate_segments", return_value=[
                 {"start": 0.0, "end": 1.0, "text": "你好"},
             ]):
            youtube_subtitle_trans.process_video(
                "https://example.com/video",
                lang="Simplified Chinese",
                model="gpt-4o",
                force_audio=True,
                source_lang="th",
                engine="whisper",
                progress_callback=logs.append,
                output_dir=tmp_dir.name,
                enhance_audio=enhance_audio,
                enhance_mode=enhance_mode,
            )

        return captured, enhancer, logs

    def test_enhancement_off_passes_raw_audio_to_transcriber(self):
        captured, enhancer, _logs = self._run_process_with_audio_enhancement(enhance_audio=False)

        self.assertEqual(captured["raw_audio_path"], captured["transcribe_audio_path"])
        enhancer.assert_not_called()

    def test_enhancement_on_passes_enhanced_audio_to_transcriber(self):
        captured, enhancer, _logs = self._run_process_with_audio_enhancement(
            enhance_audio=True,
            enhance_mode="mild",
        )

        self.assertNotEqual(captured["raw_audio_path"], captured["transcribe_audio_path"])
        self.assertTrue(captured["transcribe_audio_path"].endswith(".enhanced.mild.wav"))
        enhancer.assert_called_once()

    def test_enhancement_failure_falls_back_to_raw_audio(self):
        captured, enhancer, logs = self._run_process_with_audio_enhancement(
            enhance_audio=True,
            enhance_mode="mild",
            enhancer_side_effect=RuntimeError("ffmpeg failed"),
        )

        self.assertEqual(captured["raw_audio_path"], captured["transcribe_audio_path"])
        enhancer.assert_called_once()
        self.assertTrue(any("audio enhancement failed" in msg for msg in logs))


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
