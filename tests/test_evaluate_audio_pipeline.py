import json
import os
import tempfile
import unittest

from tools import evaluate_audio_pipeline


class TestEvaluateAudioPipeline(unittest.TestCase):
    def test_transcript_metrics_counts_empty_repeated_and_duration(self):
        segments = [
            {"start": 0.0, "end": 1.0, "text": "hello"},
            {"start": 1.0, "end": 2.5, "text": ""},
            {"start": 2.5, "end": 4.0, "text": "hello"},
            {"start": 4.0, "end": 4.5, "text": "world"},
        ]

        metrics = evaluate_audio_pipeline.transcript_metrics(segments)

        self.assertEqual(4, metrics["segment_count"])
        self.assertEqual(1, metrics["empty_segment_count"])
        self.assertEqual(1, metrics["repeated_text_count"])
        self.assertEqual(2, metrics["unique_text_count"])
        self.assertEqual(4.5, metrics["speech_seconds"])
        self.assertEqual(4.5, metrics["span_seconds"])

    def test_load_segments_json_accepts_dict_payload(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, "segments.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "segments": [
                        {"start": 0, "end": 1, "text": "a"},
                    ]
                }, f)

            segments = evaluate_audio_pipeline.load_segments_json(path)

        self.assertEqual([{"start": 0.0, "end": 1.0, "text": "a"}], segments)

    def test_render_markdown_report_contains_mode_rows(self):
        report = evaluate_audio_pipeline.render_markdown_report({
            "raw": {
                "segment_count": 2,
                "empty_segment_count": 0,
                "repeated_text_count": 1,
                "unique_text_count": 1,
                "speech_seconds": 3.0,
                "span_seconds": 3.5,
            }
        })

        self.assertIn("| raw | 2 | 0 | 1 | 1 | 3.000 | 3.500 |", report)
        self.assertIn("Strong denoise", report)

    def test_parse_transcript_arg_requires_mode_and_path(self):
        self.assertEqual(
            ("mild", "/tmp/mild.json"),
            evaluate_audio_pipeline.parse_transcript_arg("mild=/tmp/mild.json"),
        )
        with self.assertRaises(Exception):
            evaluate_audio_pipeline.parse_transcript_arg("/tmp/no-mode.json")


if __name__ == "__main__":
    unittest.main()
