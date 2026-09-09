import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_subtitles.cli import build_parser
from codex_subtitles.hard_subtitle_models import OCRLine, Region
from codex_subtitles.recorded_video_ocr import extract_recorded_subtitles, export_ocr_srt, srt_timestamp, recorded_ocr_status
from codex_subtitles.source_service import load_subtitle
from codex_subtitles.storage import read_json
from codex_subtitles.subtitle_timeline_service import reconstruct_timeline
from tests.test_subtitle_timeline_service import timeline_fixture


class RecordedOCRTests(unittest.TestCase):
    def test_low_confidence_intermediate_srt_and_offset(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            frames, ocr = timeline_fixture(root, ['hello world']*2 + ['goodbye now']*2, confidence=.65)
            timeline = reconstruct_timeline(frames, ocr, root)
            output = root/'recording.ocr.en.srt'
            report = export_ocr_srt(timeline, frames, ocr, root, output, language='en', time_offset=10)
            parsed = load_subtitle(output)
            self.assertEqual([c['text'] for c in parsed], ['hello world','goodbye now'])
            self.assertEqual([(c['start'],c['end']) for c in parsed], [(10,11),(11,12)])
            self.assertEqual(report['cues_needing_visual_check'], 2)
            quality = read_json(report['quality_report'])
            self.assertEqual(quality['cues'][0]['recording_start'], 0)
            self.assertTrue(quality['cues'][0]['evidence_images'])
            self.assertFalse((root/'source.json').exists())
            original = output.read_bytes()
            with self.assertRaises(ValueError):
                export_ocr_srt(timeline, frames, ocr, root, output, language='en', time_offset=-10)
            self.assertEqual(output.read_bytes(), original)

    def test_recording_only_and_cache_without_network(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            video=root/'recording.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=black:s=320x180:r=10:d=2','-c:v','mpeg4','-y',str(video)], check=True, capture_output=True, timeout=30)
            class Fake:
                identity='fake-v1'
                def recognize(self, image, language=None):
                    return [OCRLine('hello world', .95, Region(.2,.2,.6,.2))]
            with patch('socket.socket', side_effect=AssertionError('network forbidden')):
                first=extract_recorded_subtitles(video, language='en', region='bottom', backend=Fake(), output_root=root/'out')
                second=extract_recorded_subtitles(video, language='en', region='bottom', backend=Fake(), output_root=root/'out', time_offset=5)
            self.assertEqual(first['job_dir'], second['job_dir'])
            self.assertTrue(second['frame_cache_hit'])
            self.assertTrue(second['ocr_cache_hit'])
            self.assertEqual(load_subtitle(second['srt'])[0]['start'],5)
            self.assertEqual(recorded_ocr_status(second['job_dir'])['status'], 'complete')
            Path(second['srt']).write_text('edited')
            self.assertEqual(recorded_ocr_status(second['job_dir'])['status'], 'stale')
            self.assertFalse((Path(first['job_dir'])/'fusion').exists())

    def test_time_format_and_cli_scope(self):
        self.assertEqual(srt_timestamp(59.9996),'00:01:00,000')
        self.assertEqual(srt_timestamp(3600.123),'01:00:00,123')
        parser=build_parser()
        args=parser.parse_args(['ocr-video','recording.mov','--language','en','--region','bottom'])
        self.assertEqual(args.language,'en')
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for old in ('prepare-local','align-sources','evaluate-hard-subs','materialize-hard-subs'):
                with self.assertRaises(SystemExit): parser.parse_args([old,'job'])
        with self.assertRaises(ValueError): extract_recorded_subtitles('https://youtube.com/watch?v=123')
