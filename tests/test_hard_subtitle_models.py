import math
import tempfile
import unittest
from pathlib import Path
from codex_subtitles.hard_subtitle_models import *


class ModelTests(unittest.TestCase):
    def test_roundtrips(self):
        records = [Region(),
                   Frame('f1', 0, 'frames/f.png', Region()), OCRLine('hello', .9, Region()),
                   Observation('o1', 'f1', 0, 'en', 'hello', .9, 'fake'),
                   Cue('c1', 0, 1, 'hello', .9, ['o1'])]
        for record in records:
            with self.subTest(record=record):
                self.assertEqual(type(record).from_dict(record.to_dict()), record)
                for version in [0, 2, True, None]:
                    with self.assertRaises(ValueError):
                        type(record).from_dict({**record.to_dict(), 'schema_version': version})

    def test_invalid_coordinates_and_times(self):
        for value in [-1, float('nan'), float('inf'), True, '1']:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    Region(x=value)
                with self.assertRaises(ValueError):
                    Frame('f1', value, 'f.png', Region())
        for region in [(0, 0, 0, 1), (.9, 0, .2, 1), (0, .9, 1, .2)]:
            with self.assertRaises(ValueError):
                Region(*region)
        for start, end in [(1, 0), (0, 0), (-1, 1)]:
            with self.assertRaises(ValueError):
                Cue('c', start, end, 'hi', .9, ['o'])
        with self.assertRaises(ValueError):
            Frame('f', 0, '../bad.png', Region())

    def test_referential_integrity(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            f = Frame('f', .5, 'f.png', Region())
            o = Observation('o', 'f', .5, 'en', 'hi', .9, 'fake')
            c = Cue('c', 0, 1, 'hi', .9, ['o'])
            with self.assertRaisesRegex(ValueError, 'missing evidence'):
                validate_records([f], [o], [c], root=root, duration=2)
            (root / 'f.png').write_bytes(b'image')
            validate_records([f], [o], [c], root=root, duration=2)
            with self.assertRaises(ValueError):
                validate_records([f], [], [c], root=root, duration=2)
            with self.assertRaises(ValueError):
                validate_records([f], [o], [c, c], root=root, duration=2)
            early = Observation('early', 'f', .1, 'en', 'hi', .9, 'fake')
            with self.assertRaisesRegex(ValueError, 'unsorted'):
                Cue('c', 0, 1, 'hi', .9, ['o', 'early']).validate_observations({'o': o, 'early': early})
