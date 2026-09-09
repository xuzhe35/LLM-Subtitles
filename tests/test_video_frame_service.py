import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from codex_subtitles.video_frame_service import ExtractionConfig, extract_frames, validate_frame_index
from codex_subtitles.hard_subtitle_models import Region


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class ExtractionTests(unittest.TestCase):
    def test_small_subtitle_change_not_diluted_by_background(self):
        from codex_subtitles.video_frame_service import change_score
        before = bytes(10000)
        after = bytearray(before)
        after[5000:5005] = bytes([255]*5)
        self.assertGreater(change_score(before, after), ExtractionConfig().change_threshold)

    def test_changes_dimensions_cache_and_interruption(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            video = root / 'video.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=320x180:r=30:d=4',
                            '-vf', "drawbox=x=80:y=150:w=160:h=12:color=white:t=fill:enable='between(t,1,2)'",
                            '-c:v', 'mpeg4', '-y', str(video)], check=True, timeout=30)
            out = root / 'out'
            with patch('codex_subtitles.video_frame_service.png_gray', side_effect=RuntimeError('interrupted')):
                with self.assertRaises(RuntimeError):
                    extract_frames(video, out)
            self.assertFalse((out / 'frames.index.json').exists())
            result = extract_frames(video, out)
            timestamps = [f['timestamp'] for f in result['frames']]
            for boundary in [1, 2]:
                self.assertTrue(any(abs(t-boundary) <= .15 for t in timestamps))
            first = out / result['frames'][0]['image']
            self.assertEqual(struct.unpack('>II', first.read_bytes()[16:24]), (320, 45))
            self.assertLess(result['retained_frames'], result['inspected_frames'])
            mtime = first.stat().st_mtime_ns
            self.assertTrue(extract_frames(video, out)['cache_hit'])
            self.assertEqual(first.stat().st_mtime_ns, mtime)
            first.write_bytes(b'corrupt')
            with self.assertRaises(ValueError):
                validate_frame_index(result, out)
            self.assertFalse(extract_frames(video, out)['cache_hit'])
            changed = extract_frames(video, out, region=Region(0, 0, 1, .25))
            self.assertNotEqual(changed['fingerprint'], result['fingerprint'])
            self.assertEqual(changed['retained_frames'], 1)

    def test_config_validation(self):
        for kwargs in [{'fps': 0}, {'fps': float('nan')}, {'refine_fps': 1}, {'start': 4, 'end': 2}]:
            with self.assertRaises(ValueError):
                ExtractionConfig(**kwargs)
