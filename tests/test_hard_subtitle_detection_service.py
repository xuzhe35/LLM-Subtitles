import unittest
from dataclasses import asdict
from codex_subtitles.hard_subtitle_detection_service import rank_region, decide_region
from codex_subtitles.hard_subtitle_models import OCRLine, Region


class DetectionTests(unittest.TestCase):
    def candidate(self, texts, name='bottom', corner=False):
        box = Region(.02, .1, .15, .2) if corner else Region(.2, .3, .6, .2)
        samples = [{'timestamp': i, 'lines': [OCRLine(t, .95, box)] if t else []} for i, t in enumerate(texts)]
        return {'name': name, 'region': asdict(Region() if name == 'bottom' else Region(0, 0, 1, .25)), **rank_region(samples)}

    def test_positive_top_bottom(self):
        for name in ('top', 'bottom'):
            candidate = self.candidate(['hello world']*3 + ['another phrase']*3 + ['last phrase']*3, name)
            result = decide_region([self.candidate(['']*9, 'top' if name == 'bottom' else 'bottom'), candidate])
            self.assertTrue(result['detected'])
            self.assertEqual(result['selected']['name'], name)
            self.assertEqual(len(result['rejected_alternatives']), 1)

    def test_negatives_and_forced(self):
        for texts, corner in [(['']*9, False), (['a watermark']*9, False),
                              (['one title'] + ['']*8, False), (['logo one', 'logo two', 'logo three']*3, True)]:
            candidate = self.candidate(texts, corner=corner)
            self.assertFalse(decide_region([candidate])['proceed'])
            forced = decide_region([candidate], forced=True)
            self.assertTrue(forced['proceed'])
            self.assertIsNotNone(forced['warning'])
