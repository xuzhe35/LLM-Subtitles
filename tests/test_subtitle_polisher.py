import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from utils import subtitle_polisher


def _global_context():
    return {
        "summary": "A short conversation.",
        "tone_and_register": "Natural spoken language.",
        "language_rules": ["Use concise Simplified Chinese."],
        "terms": [],
        "recurring_expressions": [],
        "uncertainties": [],
    }


class FakeResponses:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return SimpleNamespace(output_text=json.dumps(output, ensure_ascii=False))


class FakeClient:
    def __init__(self, outputs):
        self.responses = FakeResponses(outputs)


class TestPolishWindowPlanning(unittest.TestCase):
    def test_plan_covers_every_cue_exactly_once(self):
        translated = [
            {"start": index * 2, "end": index * 2 + 2, "text": f"字幕{index}。"}
            for index in range(17)
        ]
        cues = subtitle_polisher.build_cues(translated)

        windows = subtitle_polisher.plan_windows(
            cues,
            max_cues=5,
            max_duration_sec=20,
            context_cues=2,
        )

        covered = [
            index
            for window in windows
            for index in range(window.core_start, window.core_end)
        ]
        self.assertEqual(list(range(17)), covered)
        self.assertTrue(all(window.context_start <= window.core_start for window in windows))
        self.assertTrue(all(window.context_end >= window.core_end for window in windows))

    def test_realistic_fragment_metrics_detect_short_and_leading_cues(self):
        metrics = subtitle_polisher.subtitle_quality_metrics([
            {"start": 0, "end": 1, "text": "这"},
            {"start": 1, "end": 3, "text": ",是一句话。"},
        ])

        self.assertEqual(1, metrics["short_fragment_count"])
        self.assertEqual(1, metrics["leading_continuation_count"])
        self.assertEqual(0, metrics["overlap_count"])


class TestPolishWindowValidation(unittest.TestCase):
    def setUp(self):
        self.cues = subtitle_polisher.build_cues([
            {"start": 10, "end": 12, "text": "这"},
            {"start": 12, "end": 14, "text": "是一句"},
            {"start": 14, "end": 17, "text": "完整的话。"},
        ])

    def test_materializes_timing_from_source_ids(self):
        result = subtitle_polisher.validate_and_materialize_window({
            "cues": [
                {"source_ids": ["cue_000000", "cue_000001"], "text": "这是一句"},
                {"source_ids": ["cue_000002"], "text": "完整的话。"},
            ],
            "issues": [],
        }, self.cues)

        self.assertEqual(10, result[0]["start"])
        self.assertEqual(14, result[0]["end"])
        self.assertEqual(17, result[-1]["end"])

    def test_rejects_skipped_or_duplicate_ids(self):
        with self.assertRaisesRegex(ValueError, "exactly once"):
            subtitle_polisher.validate_and_materialize_window({
                "cues": [
                    {"source_ids": ["cue_000000", "cue_000000"], "text": "重复"},
                    {"source_ids": ["cue_000002"], "text": "跳过"},
                ],
                "issues": [],
            }, self.cues)

    def test_rejects_non_adjacent_merge(self):
        with self.assertRaisesRegex(ValueError, "adjacent"):
            subtitle_polisher.validate_and_materialize_window({
                "cues": [
                    {"source_ids": ["cue_000000", "cue_000002"], "text": "错误合并"},
                    {"source_ids": ["cue_000001"], "text": "中间"},
                ],
                "issues": [],
            }, self.cues)

    def test_rejects_context_id(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            subtitle_polisher.validate_and_materialize_window({
                "cues": [
                    {"source_ids": ["cue_999999"], "text": "越界"},
                ],
                "issues": [],
            }, self.cues)

    def test_rejects_merge_with_excessive_time_span(self):
        long_cues = subtitle_polisher.build_cues([
            {"start": 0, "end": 12, "text": "第一段"},
            {"start": 12, "end": 20, "text": "第二段"},
        ])
        with self.assertRaisesRegex(ValueError, "at most 15 seconds"):
            subtitle_polisher.validate_and_materialize_window({
                "cues": [{
                    "source_ids": ["cue_000000", "cue_000001"],
                    "text": "第一段，第二段。",
                }],
                "issues": [],
            }, long_cues)


class TestResumablePolishing(unittest.TestCase):
    def setUp(self):
        self.raw = [
            {"start": 0, "end": 2, "text": "第"},
            {"start": 2, "end": 4, "text": "一句。"},
            {"start": 4, "end": 6, "text": "第"},
            {"start": 6, "end": 8, "text": "二句。"},
        ]

    def test_failed_second_window_resumes_without_repeating_completed_calls(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            checkpoint_path = os.path.join(tmp_dir, "sample.polish.resume.json")
            first_client = FakeClient([
                _global_context(),
                {
                    "cues": [{
                        "source_ids": ["cue_000000", "cue_000001"],
                        "text": "第一句。",
                    }],
                    "issues": [],
                },
                RuntimeError("temporary network error"),
            ])

            with self.assertRaisesRegex(RuntimeError, "temporary network error"):
                subtitle_polisher.polish_segments(
                    first_client,
                    self.raw,
                    checkpoint_path=checkpoint_path,
                    max_cues=2,
                    max_duration_sec=10,
                    context_cues=1,
                    max_retries=0,
                    progress_callback=lambda _message: None,
                )

            with open(checkpoint_path, "r", encoding="utf-8") as checkpoint_file:
                checkpoint = json.load(checkpoint_file)
            self.assertEqual("complete", checkpoint["global_context"]["status"])
            self.assertEqual("complete", checkpoint["windows"][0]["status"])
            self.assertEqual("pending", checkpoint["windows"][1]["status"])

            resumed_client = FakeClient([{
                "cues": [{
                    "source_ids": ["cue_000002", "cue_000003"],
                    "text": "第二句。",
                }],
                "issues": [],
            }])
            result = subtitle_polisher.polish_segments(
                resumed_client,
                self.raw,
                checkpoint_path=checkpoint_path,
                max_cues=2,
                max_duration_sec=10,
                context_cues=1,
                max_retries=0,
                progress_callback=lambda _message: None,
            )

            self.assertEqual(1, len(resumed_client.responses.calls))
            self.assertEqual(["第一句。", "第二句。"], [
                item["text"] for item in result.translated_segments
            ])
            self.assertEqual(0, result.quality_report["polished"]["short_fragment_count"])
            with open(checkpoint_path, "r", encoding="utf-8") as checkpoint_file:
                complete = json.load(checkpoint_file)
            self.assertTrue(complete["complete"])

    def test_uses_responses_api_with_strict_json_schema(self):
        client = FakeClient([
            _global_context(),
            {
                "cues": [{
                    "source_ids": [
                        "cue_000000", "cue_000001", "cue_000002", "cue_000003"
                    ],
                    "text": "第一句。第二句。",
                }],
                "issues": [],
            },
        ])

        subtitle_polisher.polish_segments(
            client,
            self.raw,
            model="gpt-5.6",
            max_cues=10,
            max_retries=0,
            progress_callback=lambda _message: None,
        )

        self.assertEqual(2, len(client.responses.calls))
        for call in client.responses.calls:
            self.assertEqual("json_schema", call["text"]["format"]["type"])
            self.assertTrue(call["text"]["format"]["strict"])
            self.assertEqual({"effort": "medium"}, call["reasoning"])
            self.assertFalse(call["store"])


if __name__ == "__main__":
    unittest.main()
