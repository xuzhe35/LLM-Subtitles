import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from codex_subtitles.hard_subtitle_models import Frame, OCRLine, Region
from codex_subtitles.hard_subtitle_ocr_service import run_ocr, OCRConfig, normalize_ocr_text
from codex_subtitles.video_frame_service import file_checksum
from tests.hard_subtitle_fixtures import english_image


def frame_fixture(root):
    frames = []
    for i in range(2):
        path = root / f'f{i}.png'
        english_image(path, bright=bool(i))
        frames.append(Frame(f'f{i}', i, path.name, Region()).to_dict())
    return {'schema_version': 1, 'status': 'valid', 'fingerprint': 'frames-v1', 'duration': 2,
            'frames': frames, 'samples': [{'timestamp': i, 'frame_id': f'f{i}'} for i in range(2)],
            'sample_range': [0, 2], 'settings': {'config': {'fps': 3}},
            'image_checksums': {f['frame_id']: file_checksum(root / f['image']) for f in frames}}


class OCRTests(unittest.TestCase):
    def test_adaptive_variants_and_failed_frame_resume(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            frames = frame_fixture(root)
            calls = []
            class Backend:
                identity = 'fake-v1'
                fail = True
                def recognize(self, path, language=None):
                    calls.append(path.name)
                    if path.name == 'f1.png' and self.fail:
                        raise RuntimeError('interrupted')
                    return [OCRLine('First,', .96, Region(.1, .1, .8, .2)), OCRLine('second.', .95, Region(.1, .5, .8, .2))]
            backend = Backend()
            result = run_ocr(frames, root, backend=backend)
            self.assertEqual(result['failed_frames'], 1)
            backend.fail = False
            result = run_ocr(frames, root, backend=backend)
            self.assertEqual(result['cached_frames'], 1)
            self.assertEqual(calls.count('f0.png'), 1)
            self.assertEqual(result['records']['f0']['observations'][0]['text'], 'First,\nsecond.')
            self.assertTrue(run_ocr(frames, root, backend=backend)['cache_hit'])
            self.assertFalse(run_ocr(frames, root, backend=backend, config=OCRConfig(language='en'))['cache_hit'])
            self.assertEqual(len(list(root.glob('f?.png'))), 2)

    def test_preprocessing_and_punctuation(self):
        from codex_subtitles.hard_subtitle_ocr_service import preprocess
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            english_image(root / 'original.png')
            for variant in ('upscale-2x', 'contrast', 'threshold'):
                self.assertTrue(preprocess(root / 'original.png', variant, root / f'{variant}.png').is_file())
        self.assertEqual(normalize_ocr_text('  Hello,   world!\n Yes. '), 'Hello, world!\nYes.')


class ConcurrentOCRTests(unittest.TestCase):
    def test_parallel_execution_preserves_order_and_cache_identity(self):
        import threading
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            frames = frame_fixture(root)
            barrier = threading.Barrier(2)
            class ParallelBackend:
                identity = 'parallel-fixture'
                max_workers = 2
                def recognize(self, image, language=None):
                    barrier.wait(timeout=5)
                    return [OCRLine(image.stem, .99, Region(.1,.1,.8,.2))]
            result = run_ocr(frames, root, backend=ParallelBackend())
            self.assertEqual(list(result['records']), ['f0','f1'])
            self.assertEqual(result['failed_frames'], 0)
            # A serial repeat must use the same cache without invoking the barrier.
            self.assertTrue(run_ocr(frames, root, backend=ParallelBackend(), workers=1)['cache_hit'])
