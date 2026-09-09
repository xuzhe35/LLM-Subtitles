import json
import os
import tempfile
import unittest

from tools import evaluate_transcription_routes as evaluator


def _write(path, payload):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return path


class TestEvaluateTranscriptionRoutes(unittest.TestCase):
    def test_report_compares_realtime_and_high_quality_arms(self):
        with tempfile.TemporaryDirectory() as tmp:
            realtime = _write(os.path.join(tmp, "realtime.json"), {
                "translated_segments": [
                    {"start": 0.0, "end": 2.0, "text": "你好"},
                    {"start": 2.0, "end": 4.0, "text": "世界"},
                ],
            })
            high_quality = _write(os.path.join(tmp, "hq.json"), {
                "degraded_semantic": False,
                "cues": [
                    {"start": 0.0, "end": 2.0, "text": "你好",
                     "source_ids": ["cue_000001"]},
                    {"start": 2.0, "end": 4.0, "text": "世界",
                     "source_ids": ["cue_000002"]},
                ],
                "escalations": [],
                "issues": [],
            })

            report = evaluator.build_report({
                "realtime": realtime,
                "transcribe_llm": high_quality,
            })

        self.assertEqual(2, report["arms"]["realtime"]["structural"]["cue_count"])
        self.assertTrue(report["gates"]["realtime"]["zero_invalid_durations"])
        self.assertTrue(report["gates"]["transcribe_llm"]["full_source_coverage"])
        self.assertEqual(0, report["arms"]["transcribe_llm"]["escalations_count"])
        self.assertFalse(report["arms"]["transcribe_llm"]["degraded_semantic"])

    def test_structural_gates_catch_defects(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = _write(os.path.join(tmp, "broken.json"), {
                "translated_segments": [
                    {"start": 5.0, "end": 4.0, "text": "负时长"},
                    {"start": 1.0, "end": 2.0, "text": "乱序"},
                ],
            })
            report = evaluator.build_report({"broken": broken})

        gates = report["gates"]["broken"]
        self.assertFalse(gates["zero_invalid_durations"])
        self.assertFalse(gates["monotonic"])

    def test_arm_argument_parsing(self):
        arms = evaluator.parse_arms(["a=/tmp/a.json", "b = /tmp/b.json"])
        self.assertEqual({"a": "/tmp/a.json", "b": "/tmp/b.json"}, arms)
        with self.assertRaises(ValueError):
            evaluator.parse_arms(["missing-equals"])


if __name__ == "__main__":
    unittest.main()
