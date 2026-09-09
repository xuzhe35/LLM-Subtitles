import ast
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from codex_subtitles.ocr_backend import VisionBackend, choose_backend
from codex_subtitles.hard_subtitle_errors import StageError
from codex_subtitles.video_frame_service import png_gray


class BackendTests(unittest.TestCase):
    def test_contract_normalization_and_order(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'image.png'
            png_gray(path, bytes(200), 20, 10)
            def runner(command, **kwargs):
                self.assertIsInstance(command, list)
                return subprocess.CompletedProcess(command, 0, json.dumps([
                    {'text': 'second.', 'confidence': .8, 'box': {'x': .1, 'y': .6, 'width': .8, 'height': .2}},
                    {'text': 'First,', 'confidence': .9, 'box': {'x': .1, 'y': .1, 'width': .8, 'height': .2}}]))
            backend = VisionBackend(path, runner=runner)
            self.assertEqual([l.text for l in backend.recognize(path, 'en')], ['First,', 'second.'])
            with self.assertRaises(StageError):
                backend.recognize(Path(d) / 'absent.png')

    def test_missing_backend_and_no_hosted_clients(self):
        with patch('codex_subtitles.ocr_backend.available_backends', return_value=[]):
            with self.assertRaisesRegex(StageError, 'local OCR'):
                choose_backend()
        forbidden = {'openai', 'anthropic', 'google', 'boto3', 'azure', 'requests', 'httpx'}
        for path in (Path(__file__).parents[1] / 'codex_subtitles').rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
                self.assertFalse({n.split('.')[0] for n in names} & forbidden, path)


import os
from tests.hard_subtitle_fixtures import english_image


@unittest.skipUnless(os.environ.get('HARD_SUBTITLE_VISION_EXECUTABLE'), 'opt-in native Vision integration test')
class NativeBackendTests(unittest.TestCase):
    def test_generated_english_offline(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'english.png'
            expected = english_image(path)
            backend = VisionBackend(os.environ['HARD_SUBTITLE_VISION_EXECUTABLE'])
            self.assertEqual(' '.join(l.text for l in backend.recognize(path, 'en')), expected)
