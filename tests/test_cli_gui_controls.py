import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import main as gui_main
import youtube_subtitle_trans


class TestVideoSpecificArtifactNames(unittest.TestCase):
    def test_same_title_different_video_ids_do_not_share_audio_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = os.path.join(tmp, "original")
            os.makedirs(original)
            dirs = youtube_subtitle_trans.OutputDirs(
                root=tmp,
                original=original,
                translated=os.path.join(tmp, "translated"),
            )
            videos = [
                youtube_subtitle_trans.VideoSource(
                    info={}, title="Same title", safe_title="Same title", video_id="id-one"
                ),
                youtube_subtitle_trans.VideoSource(
                    info={}, title="Same title", safe_title="Same title", video_id="id-two"
                ),
            ]
            output_bases = []

            def fake_download(_url, output_base, progress_hook=None):
                output_bases.append(output_base)
                path = output_base + ".m4a"
                with open(path, "wb") as handle:
                    handle.write(b"audio")
                return path

            with mock.patch.object(
                youtube_subtitle_trans.downloader,
                "download_audio",
                side_effect=fake_download,
            ):
                paths = []
                for index, video in enumerate(videos):
                    request = mock.Mock(url=f"https://example.com/{index}")
                    paths.append(youtube_subtitle_trans._load_or_download_audio(
                        request,
                        video,
                        dirs,
                        lambda _message: None,
                        None,
                    ))

        self.assertEqual(2, len(output_bases))
        self.assertNotEqual(output_bases[0], output_bases[1])
        self.assertNotEqual(paths[0], paths[1])
        self.assertIn("id-one", paths[0])
        self.assertIn("id-two", paths[1])


class TestAudioEnhancementCli(unittest.TestCase):
    def test_cli_defaults_leave_audio_enhancement_off(self):
        parser = youtube_subtitle_trans.build_arg_parser()
        args = parser.parse_args(["https://example.com/video"])

        self.assertFalse(args.enhance_audio)
        self.assertEqual("mild", args.enhance_mode)

    def test_cli_parses_audio_enhancement_options(self):
        parser = youtube_subtitle_trans.build_arg_parser()
        args = parser.parse_args([
            "https://example.com/video",
            "--enhance-audio",
            "--enhance-mode",
            "strong_ffmpeg",
        ])

        self.assertTrue(args.enhance_audio)
        self.assertEqual("strong_ffmpeg", args.enhance_mode)

    def test_cli_accepts_realtime_translate_model(self):
        parser = youtube_subtitle_trans.build_arg_parser()
        args = parser.parse_args([
            "https://example.com/video",
            "--model",
            "gpt-realtime-translate",
        ])

        self.assertEqual("gpt-realtime-translate", args.model)

    def test_project_default_translation_model_is_realtime(self):
        self.assertEqual(
            "gpt-realtime-translate",
            youtube_subtitle_trans.DEFAULT_TRANSLATION_MODEL,
        )

    def test_cli_global_polish_controls(self):
        parser = youtube_subtitle_trans.build_arg_parser()
        defaults = parser.parse_args(["https://example.com/video"])
        disabled = parser.parse_args([
            "https://example.com/video",
            "--no-polish-realtime",
            "--polish-model",
            "gpt-5.6-terra",
        ])

        self.assertIsNone(defaults.polish_realtime)
        self.assertIsNone(defaults.polish_model)
        self.assertFalse(disabled.polish_realtime)
        self.assertEqual("gpt-5.6-terra", disabled.polish_model)

    def test_processing_request_defaults_to_global_polish(self):
        request = youtube_subtitle_trans._resolve_request(
            {},
            url="https://example.com/video",
            lang=None,
            model=None,
            force_audio=False,
            source_lang="th",
            use_vad=False,
            whisper_prompt=None,
            max_segment_sec=None,
            engine="whisper",
            output_dir=None,
        )

        self.assertTrue(request.polish_realtime)
        self.assertEqual("gpt-5.6", request.polish_model)


class TestPipelineRouting(unittest.TestCase):
    def test_default_model_infers_realtime_pipeline(self):
        self.assertEqual(
            youtube_subtitle_trans.PIPELINE_REALTIME,
            youtube_subtitle_trans.resolve_pipeline(None, {}, "gpt-realtime-translate"),
        )

    def test_other_model_infers_legacy_pipeline(self):
        self.assertEqual(
            youtube_subtitle_trans.PIPELINE_LEGACY,
            youtube_subtitle_trans.resolve_pipeline(None, {}, "gpt-4o"),
        )

    def test_config_pipeline_beats_inference(self):
        self.assertEqual(
            youtube_subtitle_trans.PIPELINE_TRANSCRIBE_LLM,
            youtube_subtitle_trans.resolve_pipeline(
                None, {"pipeline": "transcribe_llm"}, "gpt-realtime-translate"
            ),
        )

    def test_explicit_pipeline_beats_config(self):
        self.assertEqual(
            youtube_subtitle_trans.PIPELINE_REALTIME,
            youtube_subtitle_trans.resolve_pipeline(
                "realtime", {"pipeline": "transcribe_llm"}, "gpt-4o"
            ),
        )

    def test_dashed_spelling_is_normalized(self):
        self.assertEqual(
            youtube_subtitle_trans.PIPELINE_TRANSCRIBE_LLM,
            youtube_subtitle_trans.resolve_pipeline("transcribe-llm", {}, "gpt-4o"),
        )

    def test_invalid_pipeline_rejected(self):
        with self.assertRaises(ValueError):
            youtube_subtitle_trans.resolve_pipeline("warp-speed", {}, "gpt-4o")

    def test_request_without_pipeline_key_keeps_current_behavior(self):
        request = youtube_subtitle_trans._resolve_request(
            {},
            url="https://example.com/video",
            lang=None,
            model=None,
            force_audio=False,
            source_lang=None,
            use_vad=False,
            whisper_prompt=None,
            max_segment_sec=None,
            engine="whisper",
            output_dir=None,
        )
        self.assertEqual(youtube_subtitle_trans.PIPELINE_REALTIME, request.pipeline)

    def test_request_merges_config_and_overrides(self):
        request = youtube_subtitle_trans._resolve_request(
            {
                "pipeline": "transcribe_llm",
                "high_quality": {
                    "semantic_model": "gpt-transcribe",
                    "translation_model": "gpt-5.6-terra",
                    "source_languages": "th,en",
                },
            },
            url="https://example.com/video",
            lang=None,
            model=None,
            force_audio=False,
            source_lang=None,
            use_vad=False,
            whisper_prompt=None,
            max_segment_sec=None,
            engine="whisper",
            output_dir=None,
            high_quality_overrides={
                "translation_model": "gpt-5.6",
                "timing_model": None,
            },
        )

        self.assertEqual(youtube_subtitle_trans.PIPELINE_TRANSCRIBE_LLM, request.pipeline)
        self.assertEqual("gpt-transcribe", request.high_quality["semantic_model"])
        # Explicit override wins; None overrides are ignored.
        self.assertEqual("gpt-5.6", request.high_quality["translation_model"])
        self.assertNotIn("timing_model", request.high_quality)
        self.assertEqual(["th", "en"], request.high_quality["source_languages"])

    def test_realtime_route_never_imports_high_quality_orchestrator(self):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = (
            "import sys; import youtube_subtitle_trans; "
            "assert 'utils.high_quality_pipeline' not in sys.modules, "
            "'high-quality orchestrator must be imported lazily'; "
            "print('ok')"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=project_root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("ok", completed.stdout)


class TestProcessVideoRouting(unittest.TestCase):
    def _run(self, **kwargs):
        video = youtube_subtitle_trans.VideoSource(
            info={}, title="Video", safe_title="Video"
        )
        with mock.patch.object(youtube_subtitle_trans, "load_config", return_value={}), \
             mock.patch.object(
                 youtube_subtitle_trans, "_resolve_openai_api_key",
                 return_value="sk-test",
             ), \
             mock.patch.object(
                 youtube_subtitle_trans, "_fetch_video_source", return_value=video
             ), \
             mock.patch.object(
                 youtube_subtitle_trans, "_ensure_output_dirs",
                 return_value=youtube_subtitle_trans.OutputDirs(
                     root="/tmp/x", original="/tmp/x/original",
                     translated="/tmp/x/translated",
                 ),
             ), \
             mock.patch.object(
                 youtube_subtitle_trans, "_process_realtime_translation",
                 return_value="realtime-artifacts",
             ) as realtime_mock, \
             mock.patch.object(
                 youtube_subtitle_trans, "_process_transcribe_llm",
                 return_value="hq-artifacts",
             ) as hq_mock:
            result = youtube_subtitle_trans.process_video(
                "https://example.com/video",
                progress_callback=lambda _msg: None,
                **kwargs,
            )
        return result, realtime_mock, hq_mock

    def test_default_model_routes_to_realtime_only(self):
        result, realtime_mock, hq_mock = self._run()
        self.assertEqual("realtime-artifacts", result)
        self.assertEqual(1, realtime_mock.call_count)
        self.assertEqual(0, hq_mock.call_count)

    def test_explicit_transcribe_llm_routes_to_high_quality(self):
        result, realtime_mock, hq_mock = self._run(pipeline="transcribe-llm")
        self.assertEqual("hq-artifacts", result)
        self.assertEqual(0, realtime_mock.call_count)
        self.assertEqual(1, hq_mock.call_count)

    def test_realtime_model_never_reaches_high_quality_even_with_hq_config(self):
        result, realtime_mock, hq_mock = self._run(
            model="gpt-realtime-translate",
            high_quality_overrides={"translation_model": "gpt-5.6-terra"},
        )
        self.assertEqual("realtime-artifacts", result)
        self.assertEqual(1, realtime_mock.call_count)
        self.assertEqual(0, hq_mock.call_count)


class TestHighQualityCli(unittest.TestCase):
    def test_cli_parses_high_quality_flags(self):
        parser = youtube_subtitle_trans.build_arg_parser()
        args = parser.parse_args([
            "https://example.com/video",
            "--pipeline", "transcribe-llm",
            "--semantic-model", "gpt-transcribe",
            "--timing-model", "gpt-4o-transcribe-diarize",
            "--source-languages", "th,en",
            "--transcription-prompt", "Cooking show",
            "--transcription-keyword", "ACME",
            "--transcription-keyword", "Bangkok",
            "--context-model", "gpt-5.6-terra",
            "--llm-translation-model", "gpt-5.6-terra",
            "--translation-escalation-model", "gpt-5.6-sol",
            "--strict-high-quality",
        ])

        overrides = youtube_subtitle_trans.build_high_quality_overrides(args)
        self.assertEqual("transcribe-llm", args.pipeline)
        self.assertEqual("gpt-transcribe", overrides["semantic_model"])
        self.assertEqual("gpt-4o-transcribe-diarize", overrides["timing_model"])
        self.assertEqual("th,en", overrides["source_languages"])
        self.assertEqual(["ACME", "Bangkok"], overrides["keywords"])
        self.assertEqual("gpt-5.6-sol", overrides["translation_escalation_model"])
        self.assertTrue(overrides["strict"])
        self.assertIsNone(overrides["enable_selective_escalation"])

    def test_cli_defaults_leave_high_quality_unset(self):
        parser = youtube_subtitle_trans.build_arg_parser()
        args = parser.parse_args(["https://example.com/video"])
        overrides = youtube_subtitle_trans.build_high_quality_overrides(args)

        self.assertIsNone(args.pipeline)
        self.assertTrue(all(value is None for value in overrides.values()))

    def test_no_translation_escalation_flag(self):
        parser = youtube_subtitle_trans.build_arg_parser()
        args = parser.parse_args([
            "https://example.com/video",
            "--no-translation-escalation",
        ])
        overrides = youtube_subtitle_trans.build_high_quality_overrides(args)
        self.assertFalse(overrides["enable_selective_escalation"])

    def test_keywords_file_json_and_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "keywords.json")
            with open(json_path, "w", encoding="utf-8") as handle:
                json.dump(["ACME", "Bangkok"], handle)
            self.assertEqual(
                ["ACME", "Bangkok"],
                youtube_subtitle_trans.load_keywords_file(json_path),
            )

            text_path = os.path.join(tmp, "keywords.txt")
            with open(text_path, "w", encoding="utf-8") as handle:
                handle.write("ACME\n\nBangkok\n")
            self.assertEqual(
                ["ACME", "Bangkok"],
                youtube_subtitle_trans.load_keywords_file(text_path),
            )


class TestProcessingModeGui(unittest.TestCase):
    def test_gui_offers_all_processing_modes(self):
        self.assertEqual(
            {"realtime", "transcribe_llm", "legacy"},
            set(gui_main.PROCESSING_MODE_OPTIONS.values()),
        )

    def test_default_mode_is_fast_realtime(self):
        self.assertEqual(
            "realtime",
            gui_main.resolve_processing_mode_selection(
                gui_main.DEFAULT_PROCESSING_MODE
            ),
        )

    def test_unknown_mode_falls_back_to_realtime(self):
        self.assertEqual("realtime", gui_main.resolve_processing_mode_selection("???"))

    def test_high_quality_mode_maps_to_transcribe_llm(self):
        self.assertEqual(
            "transcribe_llm",
            gui_main.resolve_processing_mode_selection(
                "High Quality / Transcribe + LLM"
            ),
        )

    def test_polish_control_only_applies_to_realtime(self):
        realtime = gui_main.mode_control_states("realtime")
        high_quality = gui_main.mode_control_states("transcribe_llm")
        legacy = gui_main.mode_control_states("legacy")

        self.assertTrue(realtime["polish_enabled"])
        self.assertFalse(high_quality["polish_enabled"])
        self.assertFalse(legacy["polish_enabled"])

    def test_high_quality_controls_only_apply_to_transcribe_llm(self):
        self.assertTrue(
            gui_main.mode_control_states("transcribe_llm")["high_quality_enabled"]
        )
        self.assertFalse(
            gui_main.mode_control_states("realtime")["high_quality_enabled"]
        )

    def test_keywords_entry_parsing(self):
        self.assertEqual(
            ["ACME", "Bangkok"], gui_main.parse_keywords_entry(" ACME , Bangkok ,")
        )
        self.assertIsNone(gui_main.parse_keywords_entry("   "))


class TestAudioEnhancementGuiMapping(unittest.TestCase):
    def test_gui_off_mapping(self):
        self.assertEqual((False, "off"), gui_main.resolve_enhance_audio_selection("Off"))

    def test_gui_mild_mapping(self):
        self.assertEqual((True, "mild"), gui_main.resolve_enhance_audio_selection("Mild"))

    def test_gui_strong_mapping(self):
        self.assertEqual((True, "strong_ffmpeg"), gui_main.resolve_enhance_audio_selection("Strong FFmpeg"))

    def test_unknown_gui_mapping_falls_back_to_off(self):
        self.assertEqual((False, "off"), gui_main.resolve_enhance_audio_selection("???"))

    def test_gui_offers_realtime_translate_model(self):
        self.assertIn("gpt-realtime-translate", gui_main.TRANSLATION_MODEL_OPTIONS)

    def test_gui_defaults_to_realtime_translate(self):
        self.assertEqual("gpt-realtime-translate", gui_main.DEFAULT_TRANSLATION_MODEL)

    def test_gui_defaults_to_flagship_polish_model(self):
        self.assertEqual("gpt-5.6", gui_main.DEFAULT_POLISH_MODEL)


if __name__ == "__main__":
    unittest.main()
