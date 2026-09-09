import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from utils import semantic_transcriber


class FakeTranscriptions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        recorded = dict(kwargs)
        file_handle = recorded.get("file")
        if file_handle is not None:
            recorded["file_name"] = getattr(file_handle, "name", None)
            recorded["file"] = "<file>"
        self.calls.append(recorded)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    def __init__(self, responses):
        self.audio = SimpleNamespace(transcriptions=FakeTranscriptions(responses))


def _write_audio(path, size_bytes):
    with open(path, "wb") as handle:
        handle.write(b"a" * size_bytes)


def _quiet(_message):
    pass


class TestNormalization(unittest.TestCase):
    def test_languages_accept_iso_codes_and_dedupe(self):
        self.assertEqual(
            ["th", "en", "zh-Hans"],
            semantic_transcriber.normalize_languages(["TH", "en", "th", "zh-Hans"]),
        )

    def test_languages_accept_comma_separated_string(self):
        self.assertEqual(
            ["th", "en"],
            semantic_transcriber.normalize_languages("th, en"),
        )

    def test_invalid_language_code_rejected_before_request(self):
        with self.assertRaises(ValueError):
            semantic_transcriber.normalize_languages(["Thai language"])

    def test_keywords_must_be_single_line(self):
        with self.assertRaises(ValueError):
            semantic_transcriber.normalize_keywords(["ok", "bad\nkeyword"])

    def test_keywords_trim_and_dedupe(self):
        self.assertEqual(
            ["Bangkok", "ACME"],
            semantic_transcriber.normalize_keywords([" Bangkok ", "ACME", "Bangkok", ""]),
        )

    def test_prompt_combines_base_and_previous_tail(self):
        prompt = semantic_transcriber.build_prompt("Show about cooking", "previous text tail")
        self.assertIn("Show about cooking", prompt)
        self.assertIn("previous text tail", prompt)

    def test_empty_prompt_is_none(self):
        self.assertIsNone(semantic_transcriber.build_prompt("", ""))


class TestChunkPlanning(unittest.TestCase):
    def test_small_file_uses_whole_file_path(self):
        self.assertIsNone(semantic_transcriber.plan_chunks(600.0, 10 * 1024 * 1024))

    def test_large_file_is_chunked_with_overlap(self):
        plan = semantic_transcriber.plan_chunks(
            1200.0, 50 * 1024 * 1024, max_bytes=24 * 1024 * 1024
        )
        self.assertEqual(3, len(plan))
        self.assertEqual(0.0, plan[0][0])
        self.assertEqual(1200.0, plan[-1][1])
        # Later chunks start slightly before the previous cut (overlap).
        self.assertLess(plan[1][0], plan[0][1])
        self.assertAlmostEqual(
            semantic_transcriber.CHUNK_OVERLAP_SEC,
            plan[0][1] - plan[1][0],
            places=3,
        )

    def test_cut_points_snap_to_natural_boundaries(self):
        plan = semantic_transcriber.plan_chunks(
            1000.0,
            30 * 1024 * 1024,
            max_bytes=24 * 1024 * 1024,
            natural_boundaries=[492.5],
        )
        self.assertEqual(2, len(plan))
        self.assertEqual(492.5, plan[0][1])

    def test_boundary_outside_search_radius_is_ignored(self):
        plan = semantic_transcriber.plan_chunks(
            1000.0,
            30 * 1024 * 1024,
            max_bytes=24 * 1024 * 1024,
            natural_boundaries=[100.0],
        )
        self.assertEqual(500.0, plan[0][1])


class TestOverlapStitching(unittest.TestCase):
    def test_overlap_is_deduplicated(self):
        stitched = semantic_transcriber.stitch_chunk_texts([
            "the quick brown fox jumps over the lazy dog",
            "over the lazy dog and runs away",
        ])
        self.assertEqual(
            "the quick brown fox jumps over the lazy dog and runs away",
            stitched,
        )

    def test_no_false_dedup_when_no_overlap(self):
        stitched = semantic_transcriber.stitch_chunk_texts([
            "first part.",
            "second part.",
        ])
        self.assertEqual("first part. second part.", stitched)

    def test_cjk_join_has_no_space(self):
        stitched = semantic_transcriber.stitch_chunk_texts(["你好世界", "今天天气不错"])
        self.assertEqual("你好世界今天天气不错", stitched)

    def test_fully_duplicated_chunk_is_dropped(self):
        stitched = semantic_transcriber.stitch_chunk_texts([
            "alpha beta gamma delta",
            "beta gamma delta",
        ])
        self.assertEqual("alpha beta gamma delta", stitched)


class TestWholeFileTranscription(unittest.TestCase):
    def test_whole_file_request_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = os.path.join(tmp, "audio.m4a")
            _write_audio(audio, 1024)
            client = FakeClient([
                SimpleNamespace(text="hello world", language="en"),
            ])

            artifact = semantic_transcriber.transcribe_semantic(
                client,
                audio,
                prompt="Cooking show",
                keywords=["ACME"],
                languages=["th", "en"],
                progress_callback=_quiet,
            )

        call = client.audio.transcriptions.calls[0]
        self.assertEqual("gpt-transcribe", call["model"])
        self.assertEqual("json", call["response_format"])
        self.assertEqual("Cooking show", call["prompt"])
        self.assertEqual(["ACME"], call["extra_body"]["keywords"])
        self.assertEqual(["th", "en"], call["extra_body"]["languages"])
        self.assertNotIn("language", call)
        self.assertNotIn("language", call.get("extra_body", {}))
        self.assertEqual("hello world", artifact["canonical_text"])
        self.assertEqual([{"code": "en"}], artifact["languages_detected"])
        self.assertTrue(artifact["complete"])
        self.assertTrue(artifact["chunks"][0]["whole_file"])

    def test_languages_field_never_sent_with_legacy_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = os.path.join(tmp, "audio.m4a")
            _write_audio(audio, 1024)
            client = FakeClient([SimpleNamespace(text="ok")])
            semantic_transcriber.transcribe_semantic(
                client, audio, languages=["th"], progress_callback=_quiet
            )
        call = client.audio.transcriptions.calls[0]
        self.assertNotIn("language", call)
        self.assertEqual(["th"], call["extra_body"]["languages"])


class TestChunkedTranscription(unittest.TestCase):
    def _run(self, tmp, client, checkpoint=None, size=50 * 1024 * 1024):
        audio = os.path.join(tmp, "audio.m4a")
        if not os.path.exists(audio):
            _write_audio(audio, size)
        extracted = []

        def fake_extract(path, start, end, output_path):
            extracted.append((start, end))
            with open(output_path, "wb") as handle:
                handle.write(b"chunk")
            return output_path

        artifact = semantic_transcriber.transcribe_semantic(
            client,
            audio,
            checkpoint_path=checkpoint,
            max_retries=0,
            get_duration=lambda _path: 1200.0,
            extract_chunk=fake_extract,
            work_dir=tmp,
            progress_callback=_quiet,
        )
        return artifact, extracted

    def test_chunks_processed_in_order_with_context_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient([
                SimpleNamespace(text="alpha beta gamma"),
                SimpleNamespace(text="beta gamma delta epsilon"),
                SimpleNamespace(text="delta epsilon zeta eta"),
            ])
            artifact, extracted = self._run(tmp, client)

        self.assertEqual(3, len(extracted))
        self.assertEqual(
            "alpha beta gamma delta epsilon zeta eta",
            artifact["canonical_text"],
        )
        # Second and third requests carry the previous chunk's tail as prompt.
        self.assertIn("alpha beta gamma", client.audio.transcriptions.calls[1]["prompt"])
        self.assertIn("beta gamma delta epsilon", client.audio.transcriptions.calls[2]["prompt"])

    def test_failed_chunk_preserves_completed_chunks_and_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = os.path.join(tmp, "semantic.resume.json")
            failing = FakeClient([
                SimpleNamespace(text="alpha beta gamma"),
                RuntimeError("API down"),
            ])
            with self.assertRaises(semantic_transcriber.SemanticTranscriptionError):
                self._run(tmp, failing, checkpoint=checkpoint)

            with open(checkpoint, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertEqual("complete", saved["chunks"][0]["status"])
            self.assertEqual("pending", saved["chunks"][1]["status"])
            self.assertFalse(saved["complete"])

            resumed = FakeClient([
                SimpleNamespace(text="beta gamma delta"),
                SimpleNamespace(text="gamma delta zeta"),
            ])
            artifact, _ = self._run(tmp, resumed, checkpoint=checkpoint)

        # Only the two remaining chunks were requested on resume.
        self.assertEqual(2, len(resumed.audio.transcriptions.calls))
        self.assertEqual("alpha beta gamma delta zeta", artifact["canonical_text"])
        self.assertTrue(artifact["complete"])

    def test_identity_change_invalidates_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = os.path.join(tmp, "semantic.resume.json")
            first = FakeClient([
                SimpleNamespace(text="alpha"),
                SimpleNamespace(text="beta"),
                SimpleNamespace(text="gamma"),
            ])
            self._run(tmp, first, checkpoint=checkpoint)

            audio = os.path.join(tmp, "audio.m4a")
            _write_audio(audio, 50 * 1024 * 1024 + 17)

            second = FakeClient([
                SimpleNamespace(text="new alpha"),
                SimpleNamespace(text="new beta"),
                SimpleNamespace(text="new gamma"),
            ])
            artifact, _ = self._run(tmp, second, checkpoint=checkpoint)

        self.assertEqual(3, len(second.audio.transcriptions.calls))
        self.assertIn("new", artifact["canonical_text"])

    def test_completed_artifact_is_reused_without_new_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = os.path.join(tmp, "semantic.resume.json")
            first = FakeClient([
                SimpleNamespace(text="alpha"),
                SimpleNamespace(text="beta"),
                SimpleNamespace(text="gamma"),
            ])
            self._run(tmp, first, checkpoint=checkpoint)

            second = FakeClient([])
            artifact, extracted = self._run(tmp, second, checkpoint=checkpoint)

        self.assertEqual(0, len(second.audio.transcriptions.calls))
        self.assertEqual(0, len(extracted))
        self.assertTrue(artifact["complete"])

    def test_empty_chunk_text_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient([
                SimpleNamespace(text=""),
                SimpleNamespace(text="unused"),
                SimpleNamespace(text="unused"),
            ])
            with self.assertRaises(semantic_transcriber.SemanticTranscriptionError):
                self._run(tmp, client)


if __name__ == "__main__":
    unittest.main()
