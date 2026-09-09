"""Opt-in real Apple Vision vertical slice on generated subtitle video."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from codex_subtitles.ocr_backend import VisionBackend
from codex_subtitles.recorded_video_ocr import extract_recorded_subtitles
from codex_subtitles.source_service import load_subtitle
from tests.hard_subtitle_fixtures import english_image


@unittest.skipUnless(os.environ.get('HARD_SUBTITLE_VISION_EXECUTABLE'), 'opt-in native Vision integration test')
class NativeWorkflowTests(unittest.TestCase):
    def test_synthetic_video_with_reference_and_cache(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            english_image(root/'a.png')
            english_image(root/'b.png', text='WORLD HELLO')
            video = root/'input.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-loop', '1', '-t', '1', '-i', str(root/'a.png'),
                            '-loop', '1', '-t', '1', '-i', str(root/'b.png'), '-filter_complex',
                            '[0:v][1:v]concat=n=2:v=1:a=0,pad=480:384:0:288:color=black', '-r', '30',
                            '-c:v', 'mpeg4', '-y', str(video)], capture_output=True, timeout=30, check=True)
            backend = VisionBackend(os.environ['HARD_SUBTITLE_VISION_EXECUTABLE'])
            result = extract_recorded_subtitles(video, language='en', region='auto', backend=backend, output_root=root/'out')
            cues = load_subtitle(result['srt'])
            self.assertEqual([c['text'] for c in cues], ['HELLO WORLD', 'WORLD HELLO'])
            self.assertEqual(len(cues), 2)
            for cue, start, end in zip(cues, (0,1), (1,2)):
                self.assertLessEqual(abs(cue['start']-start), .3)
                self.assertLessEqual(abs(cue['end']-end), .3)
            second = extract_recorded_subtitles(video, language='en', region='auto', backend=backend, output_root=root/'out')
            self.assertTrue(second['frame_cache_hit'])
            self.assertTrue(second['ocr_cache_hit'])
            self.assertFalse((Path(result['job_dir'])/'source.json').exists())
