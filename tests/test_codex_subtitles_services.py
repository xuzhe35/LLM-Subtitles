import ast
import tempfile
import unittest
from pathlib import Path

from codex_subtitles.export_service import export_job
from codex_subtitles.source_service import normalize_source_segments
from codex_subtitles.storage import atomic_write_json, job_paths, update_manifest
from codex_subtitles.translation_service import copy_source_to_targets, plan_translation, translation_status
from codex_subtitles.video_service import select_caption
from codex_subtitles.workflow_service import import_source_job


class CaptionSelectionTests(unittest.TestCase):
    def test_target_automatic_caption_avoids_translation(self):
        choice = select_caption(
            {
                "subtitles": {"en": [{}]},
                "automatic_captions": {"zh-Hans": [{}], "th": [{}]},
            },
            target_language="Simplified Chinese",
            source_language="th",
        )
        self.assertEqual("zh-Hans", choice.language)
        self.assertEqual("automatic", choice.kind)
        self.assertTrue(choice.already_target)

    def test_requested_automatic_source_beats_unrelated_automatic(self):
        choice = select_caption(
            {"subtitles": {}, "automatic_captions": {"ja": [{}], "th": [{}]}},
            target_language="Simplified Chinese",
            source_language="th",
        )
        self.assertEqual("th", choice.language)
        self.assertFalse(choice.already_target)


class CostBoundaryTests(unittest.TestCase):
    def test_native_services_do_not_import_openai_sdk(self):
        package_dir = Path(__file__).parents[1] / "codex_subtitles"
        imported_modules = set()
        for source_path in package_dir.glob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_modules.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_modules.add(node.module.split(".")[0])
        self.assertNotIn("openai", imported_modules)


class SourceNormalizationTests(unittest.TestCase):
    def test_tags_whitespace_and_adjacent_duplicates_are_cleaned(self):
        segments = normalize_source_segments([
            {"start": 0, "end": 1, "text": "<c>Hello&nbsp; world</c>"},
            {"start": 0.8, "end": 2, "text": "Hello  world"},
        ])
        self.assertEqual(1, len(segments))
        self.assertEqual("Hello world", segments[0]["text"])
        self.assertEqual(2.0, segments[0]["end"])
        self.assertEqual("c000001", segments[0]["id"])


class WorkflowTests(unittest.TestCase):
    def _make_job(self, root: str):
        source = Path(root) / "sample.vtt"
        source.write_text(
            "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello world.\n\n"
            "00:00:02.000 --> 00:00:04.000\nHow are you?\n\n"
            "00:00:04.000 --> 00:00:06.000\nThank you.\n",
            encoding="utf-8",
        )
        return import_source_job(
            source,
            target_language="Simplified Chinese",
            source_language="en",
            output_root=Path(root) / "jobs",
        )

    def test_plan_validate_and_export_merged_cues(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = self._make_job(temp_dir)
            job_dir = manifest["job_dir"]
            entries = plan_translation(job_dir)
            self.assertEqual(1, len(entries))
            target_path = job_paths(job_dir)["windows"] / entries[0]["target"]
            atomic_write_json(target_path, {
                "schema_version": 1,
                "job_id": manifest["job_id"],
                "window_id": "0001",
                "target_language": "Simplified Chinese",
                "cues": [
                    {"source_ids": ["c000001"], "text": "你好，世界。"},
                    {"source_ids": ["c000002", "c000003"], "text": "你好吗？谢谢。"},
                ],
            })
            status = translation_status(job_dir)
            self.assertEqual("complete", status["state"])
            result = export_job(job_dir)
            self.assertEqual(2, result["translated_cue_count"])
            self.assertIn("你好，世界。", Path(result["translated_srt"]).read_text(encoding="utf-8"))
            bilingual = Path(result["bilingual_srt"]).read_text(encoding="utf-8")
            self.assertIn("How are you? Thank you.", bilingual)

    def test_invalid_window_is_reported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = self._make_job(temp_dir)
            job_dir = manifest["job_dir"]
            entry = plan_translation(job_dir)[0]
            atomic_write_json(job_paths(job_dir)["windows"] / entry["target"], {
                "schema_version": 1,
                "job_id": manifest["job_id"],
                "window_id": "0001",
                "target_language": "Simplified Chinese",
                "cues": [{"source_ids": ["c000001"], "text": "只有一条"}],
            })
            status = translation_status(job_dir)
            self.assertEqual("in_progress", status["state"])
            self.assertEqual("0001", status["invalid"][0]["window_id"])

    def test_source_already_target_can_be_copied_deterministically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = self._make_job(temp_dir)
            job_dir = manifest["job_dir"]
            update_manifest(job_dir, source_already_target=True)
            plan_translation(job_dir)
            self.assertEqual(1, copy_source_to_targets(job_dir))
            self.assertEqual("complete", translation_status(job_dir)["state"])


if __name__ == "__main__":
    unittest.main()
