import tempfile
import unittest
from pathlib import Path
from codex_subtitles.hard_subtitle_models import Observation
from codex_subtitles.subtitle_timeline_service import reconstruct_timeline
from codex_subtitles.hard_subtitle_models import Frame, Region
from codex_subtitles.video_frame_service import file_checksum
from tests.hard_subtitle_fixtures import english_image


def timeline_fixture(root, texts, confidence=.95, step=.5):
    frames, records, samples = [], {}, []
    for i, text in enumerate(texts):
        image = root / f'f{i}.png'
        english_image(image)
        frames.append(Frame(f'f{i}', i*step, image.name, Region()).to_dict())
        obs = Observation(f'o{i}', f'f{i}', i*step, 'en', text, confidence, 'fake')
        records[f'f{i}'] = {'status': 'complete', 'observations': [obs.to_dict()]}
        samples.append({'timestamp': i*step, 'frame_id': f'f{i}'})
    index = {'schema_version': 1, 'status': 'valid', 'fingerprint': 'frames', 'frames': frames,
             'samples': samples, 'duration': len(texts)*step, 'sample_range': [0, len(texts)*step],
             'image_checksums': {f['frame_id']: file_checksum(root / f['image']) for f in frames}}
    ocr = {'schema_version': 1, 'status': 'valid', 'fingerprint': 'ocr', 'frame_fingerprint': 'frames', 'records': records}
    return index, ocr


class TimelineTests(unittest.TestCase):
    def test_table(self):
        rows = [(['hello world']*4, 1), (['hello world', 'hello world!']*2, 1),
                (['a long subtitle sentence here', 'a long subtltle sentence here']*2, 1),
                (['hello world', 'hello\nworld']*2, 1),
                (['first line', 'first line\nsecond line', 'second line'] ,3),
                (['watermark']*100, 0), (['']*4, 0)]
        for texts, count in rows:
            with self.subTest(texts=texts[:4]), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                index, ocr = timeline_fixture(root, texts)
                result = reconstruct_timeline(index, ocr, root)
                self.assertEqual(len(result['cues']), count)
                previous = 0
                for cue in result['cues']:
                    self.assertGreaterEqual(cue['start'], previous)
                    self.assertGreater(cue['end'], cue['start'])
                    self.assertTrue(cue['text'])
                    previous = cue['end']

    def test_short_blank_gap_and_review(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            index, ocr = timeline_fixture(root, ['hello world']*3 + [''] + ['hello world']*3, .7, .1)
            result = reconstruct_timeline(index, ocr, root)
            self.assertEqual(len(result['cues']), 1)
            self.assertEqual(result['cues'][0]['review_status'], 'required')
            self.assertAlmostEqual(result['cues'][0]['end'], .7)


    def test_transient_corrupt_frames_are_bridged_with_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            texts = ['Our own mind, whether good or bad.']*4 + ['@@ garbled'] + ['Our own mind, whether good or bad.']*6
            index, ocr = timeline_fixture(root, texts, step=.1)
            result = reconstruct_timeline(index, ocr, root)
            self.assertEqual(len(result['cues']), 1)
            cue = result['cues'][0]
            self.assertEqual(cue['text'], texts[0])
            self.assertAlmostEqual(cue['end'], 1.1)
            self.assertEqual(len(cue['observation_ids']), 11)
            self.assertIn('transient_ocr_interference', cue['issues'])

    def test_another_stable_subtitle_is_never_bridged(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            texts = ['Hello world']*4 + ['A different subtitle']*4 + ['Hello world']*4
            index, ocr = timeline_fixture(root, texts, step=.1)
            result = reconstruct_timeline(index, ocr, root)
            self.assertEqual([c['text'] for c in result['cues']], ['Hello world','A different subtitle','Hello world'])
