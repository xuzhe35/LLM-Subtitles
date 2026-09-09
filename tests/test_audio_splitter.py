import unittest
from types import SimpleNamespace
from unittest import mock

from utils import audio_splitter


class TestSilenceBoundaries(unittest.TestCase):
    def test_returns_midpoints_of_detected_silence_gaps(self):
        stderr = """
[silencedetect] silence_start: 9.5
[silencedetect] silence_end: 10.5 | silence_duration: 1
[silencedetect] silence_start: 19.0
[silencedetect] silence_end: 21.0 | silence_duration: 2
"""
        with mock.patch.object(
            audio_splitter.subprocess,
            "run",
            return_value=SimpleNamespace(stdout="", stderr=stderr, returncode=0),
        ):
            boundaries = audio_splitter.find_silence_boundaries("audio.m4a")

        self.assertEqual([10.0, 20.0], boundaries)

    def test_ffmpeg_failure_falls_back_to_no_boundaries(self):
        with mock.patch.object(
            audio_splitter.subprocess,
            "run",
            side_effect=OSError("ffmpeg missing"),
        ):
            self.assertEqual([], audio_splitter.find_silence_boundaries("audio.m4a"))


if __name__ == "__main__":
    unittest.main()
