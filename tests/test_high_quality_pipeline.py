import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from utils import high_quality_pipeline as hq


def _quiet(_message):
    pass


def _write_audio(path, size_bytes=2048):
    with open(path, "wb") as handle:
        handle.write(b"a" * size_bytes)


def _whisper_response(segments):
    """segments: list of (start, end, text). Words are spread evenly."""
    response_segments = []
    words = []
    for start, end, text in segments:
        response_segments.append(
            SimpleNamespace(start=start, end=end, text=text)
        )
        tokens = text.split()
        step = (end - start) / max(1, len(tokens))
        for index, token in enumerate(tokens):
            words.append(SimpleNamespace(
                word=token,
                start=round(start + index * step, 3),
                end=round(start + (index + 1) * step, 3),
            ))
    return SimpleNamespace(segments=response_segments, words=words)


def _diarized_response(segments):
    """segments: list of (start, end, text, speaker)."""
    return SimpleNamespace(segments=[
        SimpleNamespace(start=start, end=end, text=text, speaker=speaker)
        for start, end, text, speaker in segments
    ])


class TestResolveTimingModel(unittest.TestCase):
    def test_auto_defaults_to_whisper(self):
        self.assertEqual("whisper-1", hq.resolve_timing_model("auto"))

    def test_auto_with_speakers_selects_diarize(self):
        self.assertEqual(
            "gpt-4o-transcribe-diarize",
            hq.resolve_timing_model("auto", multi_speaker=True),
        )

    def test_explicit_choice_wins(self):
        self.assertEqual(
            "whisper-1",
            hq.resolve_timing_model("whisper-1", multi_speaker=True),
        )

    def test_invalid_model_rejected(self):
        with self.assertRaises(ValueError):
            hq.resolve_timing_model("nova-3")


class FakeTimingTranscriptions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        recorded = dict(kwargs)
        recorded["file"] = "<file>"
        self.calls.append(recorded)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeTimingClient:
    def __init__(self, responses):
        self.audio = SimpleNamespace(transcriptions=FakeTimingTranscriptions(responses))


class TestTimingBackbone(unittest.TestCase):
    def test_whisper_words_and_segments_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = os.path.join(tmp, "audio.m4a")
            _write_audio(audio)
            client = FakeTimingClient([
                _whisper_response([(0.0, 2.0, "hello world"), (2.0, 4.0, "second segment")]),
            ])
            artifact = hq.transcribe_timing(
                client, audio, model="whisper-1", progress_callback=_quiet
            )

        call = client.audio.transcriptions.calls[0]
        self.assertEqual("whisper-1", call["model"])
        self.assertEqual("verbose_json", call["response_format"])
        self.assertEqual(["word", "segment"], call["timestamp_granularities"])

        segments = artifact["segments"]
        self.assertEqual(2, len(segments))
        self.assertEqual("timing_000001", segments[0]["id"])
        self.assertEqual(2, len(segments[0]["words"]))
        self.assertEqual(0.0, segments[0]["words"][0]["start"])
        self.assertIsNone(segments[0]["speaker"])

    def test_chunked_timestamps_become_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = os.path.join(tmp, "audio.m4a")
            _write_audio(audio, size_bytes=3000)
            client = FakeTimingClient([
                _whisper_response([(0.0, 10.0, "first chunk text")]),
                _whisper_response([(0.0, 10.0, "second chunk text")]),
            ])

            extracted = []

            def fake_extract(_path, _start, _end, output_path):
                extracted.append((_start, _end))
                _write_audio(output_path, 10)
                return output_path

            artifact = hq.transcribe_timing(
                client,
                audio,
                model="whisper-1",
                max_bytes=2000,
                get_duration=lambda _p: 20.0,
                extract_chunk=fake_extract,
                work_dir=tmp,
                progress_callback=_quiet,
            )

        segments = artifact["segments"]
        self.assertEqual(2, len(segments))
        self.assertEqual([(0.0, 10.0), (6.0, 20.0)], extracted)
        self.assertEqual(0.0, segments[0]["start"])
        self.assertEqual(6.0, segments[1]["start"])
        self.assertEqual(16.0, segments[1]["end"])
        # Word times use the overlapped chunk's absolute media offset too.
        self.assertEqual(6.0, segments[1]["words"][0]["start"])

    def test_timing_cut_snaps_to_supplied_natural_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = os.path.join(tmp, "audio.m4a")
            _write_audio(audio, size_bytes=3000)
            client = FakeTimingClient([
                _whisper_response([(0.0, 12.0, "first chunk text")]),
                _whisper_response([(4.0, 10.0, "second chunk text")]),
            ])
            extracted = []

            def fake_extract(_path, start, end, output_path):
                extracted.append((start, end))
                _write_audio(output_path, 10)
                return output_path

            hq.transcribe_timing(
                client,
                audio,
                model="whisper-1",
                max_bytes=2000,
                natural_boundaries=[12.0],
                get_duration=lambda _p: 20.0,
                extract_chunk=fake_extract,
                work_dir=tmp,
                progress_callback=_quiet,
            )

        self.assertEqual([(0.0, 12.0), (8.0, 20.0)], extracted)

    def test_diarized_speakers_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = os.path.join(tmp, "audio.m4a")
            _write_audio(audio)
            client = FakeTimingClient([
                _diarized_response([
                    (0.0, 2.0, "hello", "spk_0"),
                    (2.0, 4.0, "hi there", "spk_1"),
                ]),
            ])
            artifact = hq.transcribe_timing(
                client, audio, model="gpt-4o-transcribe-diarize", progress_callback=_quiet
            )

        call = client.audio.transcriptions.calls[0]
        self.assertEqual("diarized_json", call["response_format"])
        self.assertEqual("auto", call["chunking_strategy"])
        speakers = [segment["speaker"] for segment in artifact["segments"]]
        self.assertEqual(["spk_0", "spk_1"], speakers)

    def test_hallucination_flags_do_not_remove_segments(self):
        segments = [
            {"start": float(i), "end": float(i + 1), "text": "ขอบคุณครับ"}
            for i in range(6)
        ] + [{"start": 6.0, "end": 7.0, "text": "unique text"}]
        flagged = hq.flag_suspect_hallucinations(segments)

        self.assertEqual(7, len(flagged))
        self.assertTrue(all(seg["suspect_hallucination"] for seg in flagged[:6]))
        self.assertFalse(flagged[-1]["suspect_hallucination"])

    def test_timing_checkpoint_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = os.path.join(tmp, "audio.m4a")
            _write_audio(audio)
            checkpoint = os.path.join(tmp, "timing.json")
            first = FakeTimingClient([
                _whisper_response([(0.0, 2.0, "hello world")]),
            ])
            hq.transcribe_timing(
                first, audio, model="whisper-1",
                checkpoint_path=checkpoint, progress_callback=_quiet,
            )

            second = FakeTimingClient([])
            artifact = hq.transcribe_timing(
                second, audio, model="whisper-1",
                checkpoint_path=checkpoint, progress_callback=_quiet,
            )

        self.assertEqual(0, len(second.audio.transcriptions.calls))
        self.assertTrue(artifact["complete"])


SEMANTIC_TEXT = (
    "Hello world this is a test. We are aligning text now with numbers like 42."
)
WHISPER_SEGMENTS = [
    (0.0, 3.0, "hello world this is a test"),
    (3.0, 6.0, "we are aligning text now with numbers like 42"),
]
DIARIZE_SEGMENTS = [
    (0.0, 3.0, "hello world this is a test", "spk_0"),
    (3.0, 6.0, "we are aligning text now with numbers like 42", "spk_1"),
]


def _context_pack():
    return {
        "summary": "Test program.",
        "narrative_progression": "One scene.",
        "languages": ["en"],
        "code_switching_notes": "",
        "speakers": [],
        "tone_and_register": "Neutral",
        "audience": "General",
        "entities": [],
        "numbers_and_identifiers": ["42"],
        "transliteration_rules": [],
        "recurring_expressions": [],
        "uncertainties": [],
        "attention_spans": [],
    }


def _policy_pack(target="Simplified Chinese"):
    return {
        "language": target,
        "script_and_orthography": "简体",
        "register": "口语",
        "transliteration": "保留",
        "punctuation": "全角",
        "subtitle_style": "两行",
        "terminology": [],
        "notes": [],
    }


class FakePipelineClient:
    """Mocked OpenAI client covering both audio and Responses APIs."""

    def __init__(self, *, semantic_text=SEMANTIC_TEXT, fail_semantic=False,
                 fail_translation=False, drop_cue_ids=()):
        self.semantic_text = semantic_text
        self.fail_semantic = fail_semantic
        self.fail_translation = fail_translation
        self.drop_cue_ids = set(drop_cue_ids)
        self.audio_calls = []
        self.response_calls = []
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._audio_create))
        self.responses = SimpleNamespace(create=self._responses_create)

    def counts(self):
        audio_models = [call["model"] for call in self.audio_calls]
        response_names = [
            call["text"]["format"]["name"] for call in self.response_calls
        ]
        return {
            "semantic": audio_models.count("gpt-transcribe"),
            "whisper": audio_models.count("whisper-1"),
            "diarize": audio_models.count("gpt-4o-transcribe-diarize"),
            "context": response_names.count("source_context_pack"),
            "policy": response_names.count("target_policy"),
            "translation": response_names.count("translated_subtitle_window"),
        }

    def _audio_create(self, **kwargs):
        recorded = dict(kwargs)
        recorded["file"] = "<file>"
        self.audio_calls.append(recorded)
        model = kwargs["model"]
        if model == "gpt-transcribe":
            if self.fail_semantic:
                raise RuntimeError("semantic API down")
            return SimpleNamespace(text=self.semantic_text, language="en")
        if model == "whisper-1":
            return _whisper_response(WHISPER_SEGMENTS)
        if model == "gpt-4o-transcribe-diarize":
            return _diarized_response(DIARIZE_SEGMENTS)
        raise AssertionError(f"Unexpected audio model {model}")

    def _responses_create(self, **kwargs):
        self.response_calls.append(kwargs)
        name = kwargs["text"]["format"]["name"]
        if name == "source_context_pack":
            payload = _context_pack()
        elif name == "target_policy":
            request_payload = json.loads(kwargs["input"])
            payload = _policy_pack(request_payload["target_language"])
        elif name == "translated_subtitle_window":
            if self.fail_translation:
                raise RuntimeError("translation API down")
            request_payload = json.loads(kwargs["input"])
            payload = {
                "cues": [
                    {
                        "source_ids": [core_id],
                        "text": ("" if core_id in self.drop_cue_ids
                                 else f"译文-{core_id}"),
                    }
                    for core_id in request_payload["core_ids"]
                ],
                "issues": [],
            }
        else:
            raise AssertionError(f"Unexpected schema {name}")
        return SimpleNamespace(output_text=json.dumps(payload, ensure_ascii=False))


class TestFullPipeline(unittest.TestCase):
    def _run(self, tmp, client, target="Simplified Chinese", **setting_overrides):
        audio = os.path.join(tmp, "video_audio.m4a")
        if not os.path.exists(audio):
            _write_audio(audio)
        original_dir = os.path.join(tmp, "original")
        translated_dir = os.path.join(tmp, "translated")
        settings = {
            "api_max_retries": 0,
            "strict": True,
        }
        settings.update(setting_overrides)
        return hq.run_pipeline(
            client,
            audio_path=audio,
            original_dir=original_dir,
            translated_dir=translated_dir,
            stem="video",
            target_language=target,
            settings=settings,
            progress_callback=_quiet,
            get_duration=lambda _p: 6.0,
            concurrent_branches=False,
        )

    def test_full_route_produces_all_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakePipelineClient()
            artifacts = self._run(tmp, client)

            for path in [
                artifacts.translated_srt,
                artifacts.bilingual_srt,
                artifacts.translated_json,
                artifacts.quality_json,
                artifacts.semantic_json,
                artifacts.timing_json,
                artifacts.aligned_json,
                artifacts.source_context_json,
                artifacts.target_policy_json,
            ]:
                self.assertTrue(os.path.exists(path), path)

            with open(artifacts.quality_json, "r", encoding="utf-8") as handle:
                quality = json.load(handle)
            self.assertTrue(quality["structural_gates"]["full_source_coverage"])
            self.assertEqual(0, quality["structural_gates"]["model_owned_timestamps"])
            self.assertEqual(0, quality["structural_gates"]["invalid_durations"])
            self.assertFalse(quality["degraded_semantic"])

            with open(artifacts.translated_srt, "r", encoding="utf-8") as handle:
                srt_content = handle.read()
            self.assertIn("译文-", srt_content)
            self.assertIn("-->", srt_content)

            counts = client.counts()
            self.assertEqual(1, counts["semantic"])
            self.assertEqual(1, counts["whisper"])
            self.assertEqual(1, counts["context"])
            self.assertEqual(1, counts["policy"])
            self.assertGreaterEqual(counts["translation"], 1)

    def test_mark_lead_extensions_maps_source_flags(self):
        cues_by_id = {
            "cue_000001": {"id": "cue_000001", "flags": ["unhosted_lead_run"]},
            "cue_000002": {"id": "cue_000002", "flags": []},
        }
        segments = [
            {"start": 0.0, "end": 1.0, "text": "a", "source_ids": ["cue_000001"]},
            {"start": 1.0, "end": 2.0, "text": "b", "source_ids": ["cue_000002"]},
        ]
        hq._mark_lead_extensions(segments, cues_by_id)

        self.assertTrue(segments[0].get("extend_lead"))
        self.assertNotIn("extend_lead", segments[1])

    def test_translator_cannot_silently_drop_a_source_cue(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakePipelineClient(drop_cue_ids={"cue_000002"})
            with self.assertRaises(hq.contextual_translator.TranslationValidationError):
                self._run(tmp, client)

    def test_semantic_and_timing_branches_join_correctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakePipelineClient()
            artifacts = self._run(tmp, client)

            with open(artifacts.aligned_json, "r", encoding="utf-8") as handle:
                aligned = json.load(handle)
            # Canonical text (with punctuation) supported by whisper timing.
            self.assertGreaterEqual(len(aligned["cues"]), 2)
            for cue in aligned["cues"]:
                self.assertGreaterEqual(cue["start"], 0.0)
                self.assertLessEqual(cue["end"], 6.0)
            joined = " ".join(cue["text"] for cue in aligned["cues"])
            self.assertIn("42", joined)

    def test_second_target_language_reuses_source_stages(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakePipelineClient()
            self._run(tmp, client, target="Simplified Chinese")
            first_counts = client.counts()

            self._run(tmp, client, target="Japanese")
            second_counts = client.counts()

        # Source-side stages were not repeated for the second target.
        self.assertEqual(first_counts["semantic"], second_counts["semantic"])
        self.assertEqual(first_counts["whisper"], second_counts["whisper"])
        self.assertEqual(first_counts["context"], second_counts["context"])
        # Target-specific stages ran again.
        self.assertEqual(first_counts["policy"] + 1, second_counts["policy"])
        self.assertGreater(second_counts["translation"], first_counts["translation"])

    def test_timing_model_change_invalidates_only_required_stages(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakePipelineClient()
            self._run(tmp, client, timing_model="whisper-1")
            first_counts = client.counts()

            self._run(tmp, client, timing_model="gpt-4o-transcribe-diarize")
            second_counts = client.counts()

        # Semantic transcript reused; timing redone with the new model.
        self.assertEqual(first_counts["semantic"], second_counts["semantic"])
        self.assertEqual(1, second_counts["diarize"])
        # Alignment changed, so context and downstream stages rebuild.
        self.assertEqual(first_counts["context"] + 1, second_counts["context"])

    def test_partial_failure_resumes_at_translation_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            failing = FakePipelineClient(fail_translation=True)
            with self.assertRaises(RuntimeError):
                self._run(tmp, failing)
            failed_counts = failing.counts()
            self.assertEqual(1, failed_counts["semantic"])
            self.assertEqual(1, failed_counts["context"])

            recovered = FakePipelineClient()
            artifacts = self._run(tmp, recovered)
            counts = recovered.counts()
            self.assertTrue(os.path.exists(artifacts.translated_srt))

        # Upstream stages were reused from artifacts; only translation ran.
        self.assertEqual(0, counts["semantic"])
        self.assertEqual(0, counts["whisper"])
        self.assertEqual(0, counts["context"])
        self.assertEqual(0, counts["policy"])
        self.assertGreaterEqual(counts["translation"], 1)

    def test_strict_mode_stops_on_semantic_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakePipelineClient(fail_semantic=True)
            with self.assertRaises(hq.HighQualityPipelineError):
                self._run(tmp, client, strict=True)
            # The realtime route was never invoked as a fallback: no
            # translation or context calls happened at all.
            counts = client.counts()
            self.assertEqual(0, counts["context"])
            self.assertEqual(0, counts["translation"])

    def test_non_strict_mode_degrades_visibly(self):
        with tempfile.TemporaryDirectory() as tmp:
            messages = []
            client = FakePipelineClient(fail_semantic=True)
            audio = os.path.join(tmp, "video_audio.m4a")
            _write_audio(audio)
            artifacts = hq.run_pipeline(
                client,
                audio_path=audio,
                original_dir=os.path.join(tmp, "original"),
                translated_dir=os.path.join(tmp, "translated"),
                stem="video",
                target_language="Simplified Chinese",
                settings={"api_max_retries": 0, "strict": False},
                progress_callback=messages.append,
                get_duration=lambda _p: 6.0,
                concurrent_branches=False,
            )

            with open(artifacts.quality_json, "r", encoding="utf-8") as handle:
                quality = json.load(handle)

        self.assertTrue(quality["degraded_semantic"])
        self.assertTrue(any("DEGRADED MODE" in message for message in messages))

    def test_speakers_preserved_in_translated_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakePipelineClient()
            artifacts = self._run(
                tmp, client, timing_model="gpt-4o-transcribe-diarize"
            )
            with open(artifacts.translated_json, "r", encoding="utf-8") as handle:
                metadata = json.load(handle)

        speakers = {cue.get("speaker") for cue in metadata["cues"]}
        self.assertIn("spk_0", speakers)
        self.assertIn("spk_1", speakers)


if __name__ == "__main__":
    unittest.main()
