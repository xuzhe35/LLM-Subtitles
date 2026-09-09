import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from utils import contextual_translator


def _source_context():
    return {
        "summary": "A short tech talk.",
        "narrative_progression": "Intro, demo, wrap-up.",
        "languages": ["en"],
        "code_switching_notes": "",
        "speakers": [],
        "tone_and_register": "Casual",
        "audience": "Developers",
        "entities": [],
        "numbers_and_identifiers": [],
        "transliteration_rules": [],
        "recurring_expressions": [],
        "uncertainties": [],
        "attention_spans": [],
    }


def _target_policy():
    return {
        "language": "Simplified Chinese",
        "script_and_orthography": "简体中文",
        "register": "口语",
        "transliteration": "保留英文品牌名",
        "punctuation": "全角标点",
        "subtitle_style": "两行以内",
        "terminology": [],
        "notes": [],
    }


def _cues(count=4, speaker=None, step=2.0):
    cues = []
    for index in range(count):
        cues.append({
            "id": f"cue_{index + 1:06d}",
            "index": index,
            "start": index * step,
            "end": (index + 1) * step,
            "text": f"source sentence {index + 1}",
            "speaker": speaker,
            "alignment_confidence": 1.0,
            "flags": [],
        })
    return cues


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


def _window_output(ids, texts=None, issues=None):
    cues = []
    for position, entry in enumerate(ids):
        source_ids = entry if isinstance(entry, list) else [entry]
        text = (texts or {}).get(position, f"译文 {position + 1}")
        cues.append({"source_ids": source_ids, "text": text})
    return {"cues": cues, "issues": issues or []}


def _quiet(_message):
    pass


class TestSchemas(unittest.TestCase):
    def test_source_context_call_uses_strict_schema(self):
        client = FakeClient([_source_context()])
        result = contextual_translator.analyze_source_context(
            client, _cues(), progress_callback=_quiet
        )

        call = client.responses.calls[0]
        self.assertEqual("json_schema", call["text"]["format"]["type"])
        self.assertTrue(call["text"]["format"]["strict"])
        self.assertEqual("source_context_pack", call["text"]["format"]["name"])
        self.assertFalse(call["store"])
        self.assertEqual({"effort": "medium"}, call["reasoning"])
        self.assertEqual("gpt-5.6-terra", call["model"])
        self.assertEqual("A short tech talk.", result["summary"])

    def test_target_policy_call_shape(self):
        client = FakeClient([_target_policy()])
        result = contextual_translator.build_target_policy(
            client,
            _source_context(),
            target_language="Simplified Chinese",
            progress_callback=_quiet,
        )

        call = client.responses.calls[0]
        self.assertEqual("target_policy", call["text"]["format"]["name"])
        payload = json.loads(call["input"])
        self.assertEqual("Simplified Chinese", payload["target_language"])
        self.assertEqual("Simplified Chinese", result["language"])

    def test_large_transcript_synthesizes_sections(self):
        client = FakeClient([
            _source_context(),
            _source_context(),
            _source_context(),
        ])
        contextual_translator.analyze_source_context(
            client,
            _cues(count=10),
            section_size=5,
            progress_callback=_quiet,
        )

        self.assertEqual(3, len(client.responses.calls))
        final_payload = json.loads(client.responses.calls[-1]["input"])
        self.assertIn("section_packs", final_payload)
        self.assertEqual(2, len(final_payload["section_packs"]))


class TestValidation(unittest.TestCase):
    def test_all_cue_ids_covered_exactly_once(self):
        cues = _cues(3)
        materialized, issues = contextual_translator.validate_translation_window(
            _window_output(["cue_000001", "cue_000002", "cue_000003"]), cues
        )
        self.assertEqual(3, len(materialized))
        self.assertEqual([], issues)

        with self.assertRaises(contextual_translator.TranslationValidationError):
            contextual_translator.validate_translation_window(
                _window_output(["cue_000001", "cue_000003"]), cues
            )

    def test_no_context_only_id_emitted(self):
        cues = _cues(2)
        with self.assertRaises(contextual_translator.TranslationValidationError):
            contextual_translator.validate_translation_window(
                _window_output(["cue_000001", "cue_000002", "cue_999999"]), cues
            )

    def test_only_ordered_adjacent_merges(self):
        cues = _cues(3)
        with self.assertRaises(contextual_translator.TranslationValidationError):
            contextual_translator.validate_translation_window(
                _window_output([["cue_000001", "cue_000003"], "cue_000002"]), cues
            )

    def test_merge_duration_limit_15_seconds(self):
        cues = _cues(2, step=10.0)  # merging spans 20 seconds
        with self.assertRaises(contextual_translator.TranslationValidationError):
            contextual_translator.validate_translation_window(
                _window_output([["cue_000001", "cue_000002"]]), cues
            )

    def test_no_cross_speaker_merges(self):
        cues = _cues(2)
        cues[0]["speaker"] = "spk_0"
        cues[1]["speaker"] = "spk_1"
        with self.assertRaises(contextual_translator.TranslationValidationError):
            contextual_translator.validate_translation_window(
                _window_output([["cue_000001", "cue_000002"]]), cues
            )

    def test_timestamps_rebuilt_from_trusted_cues(self):
        cues = _cues(2)
        materialized, _ = contextual_translator.validate_translation_window(
            _window_output([["cue_000001", "cue_000002"]]), cues
        )
        self.assertEqual(cues[0]["start"], materialized[0]["start"])
        self.assertEqual(cues[1]["end"], materialized[0]["end"])

    def test_overlapping_merge_contains_every_source_evidence_span(self):
        cues = _cues(2)
        cues[0].update(start=0.0, end=10.0)
        cues[1].update(start=5.0, end=6.0)

        materialized, _ = contextual_translator.validate_translation_window(
            _window_output([["cue_000001", "cue_000002"]]), cues
        )

        self.assertEqual(0.0, materialized[0]["start"])
        self.assertEqual(10.0, materialized[0]["end"])

    def test_number_consistency_check(self):
        cues = _cues(1)
        cues[0]["text"] = "the price is 4200 dollars"
        _materialized, issues = contextual_translator.validate_translation_window(
            _window_output(["cue_000001"], texts={0: "价格是四千二"}), cues
        )
        self.assertEqual(1, len(issues))
        self.assertEqual("number_inconsistency", issues[0]["type"])

        _materialized, ok_issues = contextual_translator.validate_translation_window(
            _window_output(["cue_000001"], texts={0: "价格是 4,200 美元"}), cues
        )
        self.assertEqual([], ok_issues)

    def test_empty_text_is_rejected_even_when_ids_are_covered(self):
        cues = _cues(3)
        with self.assertRaises(contextual_translator.TranslationValidationError):
            contextual_translator.validate_translation_window(
                _window_output(
                    ["cue_000001", "cue_000002", "cue_000003"], texts={1: ""}
                ),
                cues,
            )

    def test_numbered_cue_cannot_bypass_validation_with_empty_text(self):
        cues = _cues(1)
        cues[0]["text"] = "watermark 12345 loop"
        with self.assertRaises(contextual_translator.TranslationValidationError):
            contextual_translator.validate_translation_window(
                _window_output(["cue_000001"], texts={0: ""}), cues
            )

    def test_empty_merge_cannot_bypass_duration_or_speaker_rules(self):
        cues = _cues(2, step=10.0)
        cues[0]["speaker"] = "spk_0"
        cues[1]["speaker"] = "spk_1"
        with self.assertRaises(contextual_translator.TranslationValidationError):
            contextual_translator.validate_translation_window(
                _window_output([["cue_000001", "cue_000002"]], texts={0: ""}), cues
            )


class TestTranslationFlow(unittest.TestCase):
    def test_structural_retry_on_same_model(self):
        cues = _cues(2)
        client = FakeClient([
            _window_output(["cue_000001"]),  # incomplete coverage -> retry
            _window_output(["cue_000001", "cue_000002"]),
        ])
        result = contextual_translator.translate_cues(
            client,
            cues,
            target_language="Simplified Chinese",
            source_context=_source_context(),
            target_policy=_target_policy(),
            max_retries=1,
            progress_callback=_quiet,
        )

        self.assertEqual(2, len(client.responses.calls))
        # Both calls used the primary model; no escalation.
        for call in client.responses.calls:
            self.assertEqual("gpt-5.6-terra", call["model"])
        self.assertEqual([], result.escalations)
        self.assertEqual(2, len(result.translated_segments))

    def test_empty_noise_cue_retries_and_must_return_text(self):
        cues = _cues(2)
        cues[1]["text"] = "watermark 12345 loop"
        client = FakeClient([
            _window_output(["cue_000001", "cue_000002"], texts={1: ""}),
            _window_output(
                ["cue_000001", "cue_000002"], texts={1: "水印 12345"}
            ),
        ])
        result = contextual_translator.translate_cues(
            client,
            cues,
            target_language="Simplified Chinese",
            source_context=_source_context(),
            target_policy=_target_policy(),
            max_retries=1,
            progress_callback=_quiet,
        )

        self.assertEqual(2, len(client.responses.calls))
        self.assertEqual(2, len(result.translated_segments))
        self.assertFalse(any(item.get("dropped") for item in result.translated_segments))
        self.assertEqual([], result.issues)
        self.assertEqual(0, result.quality_report["dropped_cue_count"])
        self.assertEqual(2, result.quality_report["translated_cue_count"])

    def test_selective_escalation_on_reported_ambiguity(self):
        cues = _cues(2)
        client = FakeClient([
            _window_output(
                ["cue_000001", "cue_000002"],
                issues=[{
                    "source_ids": ["cue_000001"],
                    "type": "ambiguous_name",
                    "detail": "Name could be two people.",
                }],
            ),
            _window_output(["cue_000001", "cue_000002"], texts={0: "索尔译文", 1: "第二句"}),
        ])
        result = contextual_translator.translate_cues(
            client,
            cues,
            target_language="Simplified Chinese",
            source_context=_source_context(),
            target_policy=_target_policy(),
            max_retries=0,
            progress_callback=_quiet,
        )

        self.assertEqual("gpt-5.6-terra", client.responses.calls[0]["model"])
        self.assertEqual("gpt-5.6-sol", client.responses.calls[1]["model"])
        self.assertEqual(1, len(result.escalations))
        self.assertEqual("索尔译文", result.translated_segments[0]["text"])
        self.assertEqual(1, result.quality_report["escalated_window_count"])

    def test_escalation_disabled_keeps_terra_output(self):
        cues = _cues(2)
        client = FakeClient([
            _window_output(
                ["cue_000001", "cue_000002"],
                issues=[{
                    "source_ids": ["cue_000001"],
                    "type": "ambiguous_name",
                    "detail": "unclear",
                }],
            ),
        ])
        result = contextual_translator.translate_cues(
            client,
            cues,
            target_language="Simplified Chinese",
            source_context=_source_context(),
            target_policy=_target_policy(),
            enable_escalation=False,
            max_retries=0,
            progress_callback=_quiet,
        )

        self.assertEqual(1, len(client.responses.calls))
        self.assertEqual([], result.escalations)
        self.assertIsNone(result.escalation_model)
        self.assertEqual(1, len(result.issues))

    def test_structural_failure_escalates_to_sol(self):
        cues = _cues(2)
        client = FakeClient([
            _window_output(["cue_000001"]),  # invalid
            _window_output(["cue_000001"]),  # invalid retry
            _window_output(["cue_000001", "cue_000002"]),  # sol succeeds
        ])
        result = contextual_translator.translate_cues(
            client,
            cues,
            target_language="Simplified Chinese",
            source_context=_source_context(),
            target_policy=_target_policy(),
            max_retries=1,
            progress_callback=_quiet,
        )

        self.assertEqual("gpt-5.6-sol", client.responses.calls[-1]["model"])
        self.assertEqual(1, len(result.escalations))
        self.assertIn("structural_failure", result.escalations[0]["reason"])

    def test_escalation_budget_is_capped(self):
        cues = _cues(2)
        ambiguous = _window_output(
            ["cue_000001", "cue_000002"],
            issues=[{
                "source_ids": ["cue_000001"],
                "type": "semantic_ambiguity",
                "detail": "unclear",
            }],
        )
        client = FakeClient([ambiguous])
        result = contextual_translator.translate_cues(
            client,
            cues,
            target_language="Simplified Chinese",
            source_context=_source_context(),
            target_policy=_target_policy(),
            max_escalations=0,
            max_retries=0,
            progress_callback=_quiet,
        )

        self.assertEqual(1, len(client.responses.calls))
        self.assertEqual([], result.escalations)

    def test_checkpoint_resume_skips_completed_windows(self):
        cues = _cues(4)
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = os.path.join(tmp, "translate.resume.json")
            failing = FakeClient([
                _window_output(["cue_000001", "cue_000002"]),
                RuntimeError("network down"),
                RuntimeError("network down"),  # escalation attempt also fails
            ])
            with self.assertRaises(RuntimeError):
                contextual_translator.translate_cues(
                    failing,
                    cues,
                    target_language="Simplified Chinese",
                    source_context=_source_context(),
                    target_policy=_target_policy(),
                    checkpoint_path=checkpoint,
                    max_cues=2,
                    max_retries=0,
                    progress_callback=_quiet,
                )

            with open(checkpoint, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertEqual("complete", saved["windows"][0]["status"])
            self.assertEqual("pending", saved["windows"][1]["status"])

            resumed = FakeClient([
                _window_output(["cue_000003", "cue_000004"]),
            ])
            result = contextual_translator.translate_cues(
                resumed,
                cues,
                target_language="Simplified Chinese",
                source_context=_source_context(),
                target_policy=_target_policy(),
                checkpoint_path=checkpoint,
                max_cues=2,
                max_retries=0,
                progress_callback=_quiet,
            )

            self.assertEqual(1, len(resumed.responses.calls))
            self.assertEqual(4, len(result.translated_segments))
            with open(checkpoint, "r", encoding="utf-8") as handle:
                self.assertTrue(json.load(handle)["complete"])

    def test_model_never_returns_timestamps(self):
        cues = _cues(2)
        client = FakeClient([
            _window_output(["cue_000001", "cue_000002"]),
        ])
        result = contextual_translator.translate_cues(
            client,
            cues,
            target_language="Simplified Chinese",
            source_context=_source_context(),
            target_policy=_target_policy(),
            max_retries=0,
            progress_callback=_quiet,
        )

        # Output timing comes only from the trusted source cues.
        self.assertEqual(0.0, result.translated_segments[0]["start"])
        self.assertEqual(2.0, result.translated_segments[0]["end"])
        # The response schema has no timestamp fields at all.
        schema = contextual_translator.TRANSLATION_WINDOW_SCHEMA
        cue_props = schema["properties"]["cues"]["items"]["properties"]
        self.assertNotIn("start", cue_props)
        self.assertNotIn("end", cue_props)


if __name__ == "__main__":
    unittest.main()
