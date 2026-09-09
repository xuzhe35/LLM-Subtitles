import json
import io
import os
import queue
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import certifi
import websocket

import youtube_subtitle_trans
from utils import realtime_translator


class TestRealtimeLanguageCodes(unittest.TestCase):
    def test_common_language_names_are_mapped(self):
        self.assertEqual("zh", realtime_translator.resolve_target_language_code("Simplified Chinese"))
        self.assertEqual("zh", realtime_translator.resolve_target_language_code("Traditional Chinese"))
        self.assertEqual("ja", realtime_translator.resolve_target_language_code("Japanese"))
        self.assertEqual("th", realtime_translator.resolve_target_language_code("Thai"))

    def test_iso_codes_are_normalized(self):
        self.assertEqual("zh", realtime_translator.resolve_target_language_code("zh-cn"))
        self.assertEqual("th", realtime_translator.resolve_target_language_code("TH"))

    def test_unknown_language_name_is_rejected(self):
        with self.assertRaises(ValueError):
            realtime_translator.resolve_target_language_code("A Language With No Code")


class TestRealtimeTranscriptCollector(unittest.TestCase):
    def test_collects_both_transcripts_and_builds_timed_segments(self):
        collector = realtime_translator.RealtimeTranscriptCollector()
        collector.handle_event({"type": "session.input_transcript.delta", "delta": "Well, "}, 1.0)
        collector.handle_event({"type": "session.input_transcript.delta", "delta": "hello!"}, 3.0)
        collector.handle_event({"type": "session.output_transcript.delta", "delta": "嗯，"}, 2.0)
        collector.handle_event({"type": "session.output_transcript.delta", "delta": "你好！"}, 4.0)
        collector.handle_event({"type": "session.output_audio.delta", "delta": "base64-audio"}, 4.0)
        collector.handle_event({"type": "session.closed"}, 6.0)

        result = collector.build_result(duration=6.0, target_language="zh-CN")

        self.assertEqual("Well, hello!", result.source_text)
        self.assertEqual("嗯，你好！", result.translated_text)
        self.assertTrue(result.translated_segments)
        self.assertEqual(len(result.translated_segments), len(result.source_segments))
        self.assertTrue(collector.closed.is_set())
        self.assertFalse(any(item["type"] == "session.output_audio.delta" for item in result.transcript_events))

    def test_late_batched_deltas_fall_back_to_proportional_timing(self):
        deltas = [
            realtime_translator.TimedTextDelta(60.0, "第一句。"),
            realtime_translator.TimedTextDelta(60.0, "第二句。"),
        ]

        segments = realtime_translator.build_timed_segments(deltas, duration=60.0)

        self.assertEqual(2, len(segments))
        self.assertEqual(0.0, segments[0]["start"])
        self.assertEqual(60.0, segments[-1]["end"])
        self.assertLess(segments[0]["end"], segments[1]["end"])

    def test_api_error_is_retained(self):
        collector = realtime_translator.RealtimeTranscriptCollector()
        collector.handle_event({"type": "error", "error": {"message": "bad request"}}, 0.0)
        self.assertEqual(1, len(collector.errors))
        self.assertIn("bad request", collector.errors[0])


class TestRealtimeResume(unittest.TestCase):
    @staticmethod
    def _chunk_result(duration, text):
        return realtime_translator.RealtimeTranslationResult(
            source_segments=[{"start": 0.0, "end": duration, "text": f"source-{text}"}],
            translated_segments=[{"start": 0.0, "end": duration, "text": text}],
            source_text=f"source-{text}",
            translated_text=text,
            duration=duration,
            target_language="zh-CN",
        )

    def test_chunk_plan_has_overlap_only_at_next_chunk_start(self):
        chunks = realtime_translator._build_chunks(1_250, 600, 10)

        self.assertEqual(3, len(chunks))
        self.assertEqual((0.0, 600.0, 0.0), (chunks[0].start, chunks[0].end, chunks[0].stream_start))
        self.assertEqual((600.0, 1_200.0, 590.0), (chunks[1].start, chunks[1].end, chunks[1].stream_start))
        self.assertEqual((1_200.0, 1_250.0, 1_190.0), (chunks[2].start, chunks[2].end, chunks[2].stream_start))

    def test_failed_run_resumes_after_last_completed_chunk(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            audio_path = os.path.join(tmp_dir, "audio.m4a")
            checkpoint_path = os.path.join(tmp_dir, "translation.resume.json")
            with open(audio_path, "wb") as audio_file:
                audio_file.write(b"stable-audio")

            first = self._chunk_result(5.0, "第一段")
            second = self._chunk_result(6.0, "第二段")
            third = self._chunk_result(3.0, "第三段")
            with patch.object(realtime_translator, "_probe_audio_duration", return_value=12.0), \
                 patch.object(
                     realtime_translator,
                     "_translate_audio_session",
                     side_effect=[first, OSError("network lost")],
                 ) as first_run:
                with self.assertRaisesRegex(RuntimeError, "Resume will restart this chunk"):
                    realtime_translator.translate_audio(
                        api_key="sk-test",
                        audio_file_path=audio_path,
                        target_language="zh-CN",
                        checkpoint_path=checkpoint_path,
                        segment_duration_sec=5,
                        segment_overlap_sec=1,
                        max_retries=0,
                        retry_backoff_sec=0,
                        progress_callback=lambda _message: None,
                    )
            self.assertEqual(2, first_run.call_count)

            with open(checkpoint_path, "r", encoding="utf-8") as checkpoint_file:
                partial = json.load(checkpoint_file)
            self.assertEqual("complete", partial["chunks"][0]["status"])
            self.assertEqual("pending", partial["chunks"][1]["status"])

            with patch.object(realtime_translator, "_probe_audio_duration", return_value=12.0), \
                 patch.object(
                     realtime_translator,
                     "_translate_audio_session",
                     side_effect=[second, third],
                 ) as resumed_run:
                result = realtime_translator.translate_audio(
                    api_key="sk-test",
                    audio_file_path=audio_path,
                    target_language="zh-CN",
                    checkpoint_path=checkpoint_path,
                    segment_duration_sec=5,
                    segment_overlap_sec=1,
                    max_retries=0,
                    retry_backoff_sec=0,
                    progress_callback=lambda _message: None,
                )

            self.assertEqual(2, resumed_run.call_count)
            self.assertEqual(4.0, resumed_run.call_args_list[0].kwargs["start_sec"])
            self.assertEqual(9.0, resumed_run.call_args_list[1].kwargs["start_sec"])
            self.assertEqual(12.0, result.duration)
            self.assertEqual(["第一段", "第二段", "第三段"], [
                item["text"] for item in result.translated_segments
            ])
            with open(checkpoint_path, "r", encoding="utf-8") as checkpoint_file:
                complete = json.load(checkpoint_file)
            self.assertTrue(complete["complete"])

    def test_chunk_retries_before_failing_the_run(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            audio_path = os.path.join(tmp_dir, "audio.m4a")
            checkpoint_path = os.path.join(tmp_dir, "translation.resume.json")
            with open(audio_path, "wb") as audio_file:
                audio_file.write(b"stable-audio")

            finished = self._chunk_result(4.0, "完成")
            with patch.object(realtime_translator, "_probe_audio_duration", return_value=4.0), \
                 patch.object(
                     realtime_translator,
                     "_translate_audio_session",
                     side_effect=[OSError("temporary"), finished],
                 ) as session:
                result = realtime_translator.translate_audio(
                    api_key="sk-test",
                    audio_file_path=audio_path,
                    target_language="zh-CN",
                    checkpoint_path=checkpoint_path,
                    max_retries=1,
                    retry_backoff_sec=0,
                    progress_callback=lambda _message: None,
                )

            self.assertEqual(2, session.call_count)
            self.assertEqual("完成", result.translated_text)

    def test_non_retryable_api_error_stops_after_first_attempt(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            audio_path = os.path.join(tmp_dir, "audio.m4a")
            checkpoint_path = os.path.join(tmp_dir, "translation.resume.json")
            with open(audio_path, "wb") as audio_file:
                audio_file.write(b"stable-audio")

            api_error = realtime_translator.RealtimeAPIError(
                "invalid_request_error: invalid_value",
                retryable=False,
            )
            with patch.object(realtime_translator, "_probe_audio_duration", return_value=4.0), \
                 patch.object(
                     realtime_translator,
                     "_translate_audio_session",
                     side_effect=api_error,
                 ) as session:
                with self.assertRaisesRegex(RuntimeError, "non-retryable API error"):
                    realtime_translator.translate_audio(
                        api_key="sk-test",
                        audio_file_path=audio_path,
                        target_language="Simplified Chinese",
                        checkpoint_path=checkpoint_path,
                        max_retries=3,
                        retry_backoff_sec=0,
                        progress_callback=lambda _message: None,
                    )

            self.assertEqual(1, session.call_count)

    def test_empty_old_language_checkpoint_is_rebuilt_automatically(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            audio_path = os.path.join(tmp_dir, "audio.m4a")
            checkpoint_path = os.path.join(tmp_dir, "translation.resume.json")
            with open(audio_path, "wb") as audio_file:
                audio_file.write(b"stable-audio")

            chunks = realtime_translator._build_chunks(4.0, 600, 10)
            old_identity = realtime_translator._checkpoint_identity(
                audio_path,
                "zh-CN",
                realtime_translator.REALTIME_TRANSLATE_MODEL,
                4.0,
                600,
                10,
            )
            realtime_translator._atomic_write_json(
                checkpoint_path,
                realtime_translator._new_checkpoint(old_identity, chunks),
            )
            messages = []
            finished = self._chunk_result(4.0, "完成")
            with patch.object(realtime_translator, "_probe_audio_duration", return_value=4.0), \
                 patch.object(
                     realtime_translator,
                     "_translate_audio_session",
                     return_value=finished,
                 ):
                result = realtime_translator.translate_audio(
                    api_key="sk-test",
                    audio_file_path=audio_path,
                    target_language="Simplified Chinese",
                    checkpoint_path=checkpoint_path,
                    max_retries=0,
                    retry_backoff_sec=0,
                    progress_callback=messages.append,
                )

            self.assertEqual("zh", result.target_language)
            self.assertTrue(any("rebuilding the checkpoint" in item for item in messages))


class TestRealtimePipelineRouting(unittest.TestCase):
    def test_realtime_model_uses_audio_directly_and_writes_separate_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            audio_path = os.path.join(tmp_dir, "downloaded.mp3")
            with open(audio_path, "wb") as audio_file:
                audio_file.write(b"fake-audio")

            result = realtime_translator.RealtimeTranslationResult(
                source_segments=[{"start": 0.0, "end": 2.0, "text": "hello"}],
                translated_segments=[{"start": 0.0, "end": 2.0, "text": "你好"}],
                source_text="hello",
                translated_text="你好",
                duration=2.0,
                target_language="zh-CN",
                transcript_events=[{"type": "session.output_transcript.delta", "delta": "你好"}],
            )
            transcribe = MagicMock()
            text_translate = MagicMock()
            openai_client = MagicMock()
            manual_download = MagicMock()
            polished_result = youtube_subtitle_trans.subtitle_polisher.PolishResult(
                translated_segments=[{
                    "start": 0.0,
                    "end": 2.0,
                    "text": "你好。",
                    "source_ids": ["cue_000000"],
                }],
                global_context={"summary": "demo"},
                quality_report={"raw": {}, "polished": {}},
                model="gpt-5.6",
                target_language="Simplified Chinese",
            )
            polished_metadata = polished_result.to_metadata(result.to_metadata())

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("OPENAI_API_KEY", None)
                with patch.object(youtube_subtitle_trans, "load_config", return_value={"openai_api_key": "sk-test"}), \
                     patch.object(youtube_subtitle_trans, "OpenAI", openai_client), \
                     patch.object(youtube_subtitle_trans.downloader, "get_video_info", return_value={
                         "title": "Realtime Demo",
                         "subtitles": {"en": [{"url": "ignored"}]},
                     }), \
                     patch.object(youtube_subtitle_trans.downloader, "download_audio", return_value=audio_path), \
                     patch.object(youtube_subtitle_trans.downloader, "download_manual_subtitle", manual_download), \
                     patch.object(youtube_subtitle_trans.transcriber, "transcribe_audio", transcribe), \
                     patch.object(youtube_subtitle_trans.translator, "translate_segments", text_translate), \
                     patch.object(
                         youtube_subtitle_trans.subtitle_polisher,
                         "polish_realtime_metadata",
                         return_value=(polished_result, polished_metadata),
                     ) as polish_metadata, \
                     patch.object(
                         youtube_subtitle_trans.realtime_translator,
                         "translate_audio",
                         return_value=result,
                     ) as realtime_translate:
                    artifacts = youtube_subtitle_trans.process_video(
                        "https://example.com/video",
                        lang="Simplified Chinese",
                        model="gpt-realtime-translate",
                        output_dir=tmp_dir,
                        progress_callback=lambda _message: None,
                    )

            self.assertIsNotNone(artifacts)
            self.assertIn("gpt-realtime-translate", artifacts.translated_srt)
            self.assertTrue(os.path.exists(artifacts.translated_srt))
            self.assertTrue(os.path.exists(artifacts.bilingual_srt))
            self.assertTrue(os.path.exists(artifacts.metadata_json))
            self.assertIn("polished", artifacts.translated_srt)
            self.assertIn("polished", artifacts.metadata_json)
            with open(artifacts.metadata_json, "r", encoding="utf-8") as metadata_file:
                metadata = json.load(metadata_file)
            self.assertEqual("你好。", metadata["translated_text"])
            raw_metadata_path = artifacts.metadata_json.replace(".polished.json", ".json")
            self.assertTrue(os.path.exists(raw_metadata_path))
            realtime_kwargs = realtime_translate.call_args.kwargs
            self.assertTrue(realtime_kwargs["checkpoint_path"].endswith(".resume.json"))
            self.assertEqual(600, realtime_kwargs["segment_duration_sec"])
            self.assertEqual(10, realtime_kwargs["segment_overlap_sec"])
            transcribe.assert_not_called()
            text_translate.assert_not_called()
            openai_client.assert_called_once()
            polish_metadata.assert_called_once()
            manual_download.assert_not_called()


class TestRealtimeWebSocketProtocol(unittest.TestCase):
    def test_audio_is_sent_with_translation_session_event_names(self):
        class FakeWebSocket:
            def __init__(self):
                self.sent = []
                self.events = queue.Queue()

            def settimeout(self, _timeout):
                return None

            def send(self, payload):
                event = json.loads(payload)
                self.sent.append(event)
                if event["type"] == "session.input_audio_buffer.append":
                    self.events.put(json.dumps({
                        "type": "session.input_transcript.delta",
                        "delta": "hello",
                    }))
                    self.events.put(json.dumps({
                        "type": "session.output_transcript.delta",
                        "delta": "你好",
                    }))
                elif event["type"] == "session.close":
                    self.events.put(json.dumps({"type": "session.closed"}))

            def recv(self):
                try:
                    return self.events.get(timeout=0.05)
                except queue.Empty as exc:
                    raise websocket.WebSocketTimeoutException() from exc

            def close(self):
                return None

        class FakeProcess:
            def __init__(self):
                self.stdout = io.BytesIO(b"\x00" * 4_800)
                self.stderr = io.BytesIO()

            def wait(self, timeout=None):
                return 0

            def poll(self):
                return 0

        fake_ws = FakeWebSocket()
        with tempfile.NamedTemporaryFile() as audio_file, \
             patch.object(websocket, "create_connection", return_value=fake_ws) as create_connection, \
             patch.object(realtime_translator, "_start_ffmpeg_pcm", return_value=FakeProcess()):
            result = realtime_translator.translate_audio(
                api_key="sk-test",
                audio_file_path=audio_file.name,
                target_language="Simplified Chinese",
                pacing_rate=1_000,
                close_timeout=2,
                progress_callback=lambda _message: None,
            )

        connection_options = create_connection.call_args.kwargs
        self.assertEqual(certifi.where(), connection_options["sslopt"]["ca_certs"])
        event_types = [event["type"] for event in fake_ws.sent]
        self.assertEqual("session.update", event_types[0])
        self.assertIn("session.input_audio_buffer.append", event_types)
        self.assertEqual("session.close", event_types[-1])
        self.assertEqual("zh", fake_ws.sent[0]["session"]["audio"]["output"]["language"])
        self.assertEqual(
            "gpt-realtime-whisper",
            fake_ws.sent[0]["session"]["audio"]["input"]["transcription"]["model"],
        )
        self.assertEqual("你好", result.translated_text)

    def test_explicit_ssl_cert_file_overrides_certifi_bundle(self):
        fake_ws = MagicMock()
        with tempfile.NamedTemporaryFile() as audio_file, \
             patch.dict(os.environ, {"SSL_CERT_FILE": "/custom/company-ca.pem"}), \
             patch.object(websocket, "create_connection", return_value=fake_ws) as create_connection, \
             patch.object(realtime_translator, "_start_ffmpeg_pcm", side_effect=RuntimeError("stop")):
            with self.assertRaisesRegex(RuntimeError, "stop"):
                realtime_translator.translate_audio(
                    api_key="sk-test",
                    audio_file_path=audio_file.name,
                    target_language="Simplified Chinese",
                    progress_callback=lambda _message: None,
                )

        connection_options = create_connection.call_args.kwargs
        self.assertEqual("/custom/company-ca.pem", connection_options["sslopt"]["ca_certs"])

    def test_connection_error_preserves_underlying_reason(self):
        with tempfile.NamedTemporaryFile() as audio_file, \
             patch.object(
                 websocket,
                 "create_connection",
                 side_effect=OSError("certificate verify failed"),
             ):
            with self.assertRaisesRegex(
                RuntimeError,
                "OSError: certificate verify failed",
            ):
                realtime_translator.translate_audio(
                    api_key="sk-test",
                    audio_file_path=audio_file.name,
                    target_language="Simplified Chinese",
                    progress_callback=lambda _message: None,
                )


if __name__ == "__main__":
    unittest.main()
